"""
db.py
=====
SQLite persistence + audit log for the correction service.

Design choices locked with the user:
  * SQLite single-file DB (homelab), opened in WAL mode so Litestream can stream
    a continuous backup of the write-ahead log.
  * The audit log deliberately does NOT store client IP or any IP-bearing header
    (X-Forwarded-For / CF-Connecting-IP). It records user-agent, filename, file
    type, detected format, size, timestamp, and correction counts only. This is a
    privacy-preserving deviation from the original spec's "log client IP".

Tables:
  containers          -- registry of distinct containers ever seen (keyed on the
                         canonical/corrected number), with first/last seen + count
  ingestion_events    -- one row per upload (the audit log), no IP
  event_containers    -- which containers appeared in which event, and how
(container_enrichment is intentionally deferred to the enrichment pass.)
"""
from __future__ import annotations

import csv
import datetime
import io
import json
import sqlite3
from typing import Optional

from correction_report import CorrectionReport

SCHEMA = """
CREATE TABLE IF NOT EXISTS containers (
    eqid            TEXT PRIMARY KEY,     -- canonical (corrected) container number
    owner           TEXT,                 -- 3-letter owner prefix; joins owners(prefix)
    category        TEXT,
    id_type         TEXT,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    times_seen      INTEGER NOT NULL DEFAULT 0,  -- occurrence-weighted (every sighting in every file)
    times_corrected INTEGER NOT NULL DEFAULT 0,  -- event-weighted (+1 per file where we FIXED its check digit)
    times_flagged   INTEGER NOT NULL DEFAULT 0   -- event-weighted (+1 per file where it was flagged)
);
-- The three counters are documented denormalizations: event_containers is the
-- auditable truth and recompute_counters() proves they agree. times_seen counts
-- occurrences (an SNX file with 4 synced attrs adds 4); the corrected/flagged
-- counters count FILES, because "we fixed it" is a per-run decision.

CREATE TABLE IF NOT EXISTS ingestion_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT NOT NULL,
    filename         TEXT,
    content_type     TEXT,
    detected_format  TEXT,
    file_size        INTEGER,
    user_agent       TEXT,                -- stored; client IP intentionally NOT stored
    owner_policy     TEXT,
    status           TEXT NOT NULL,       -- processed | rejected
    reason           TEXT,
    total_containers INTEGER,
    corrected        INTEGER,
    flagged          INTEGER,
    valid            INTEGER,
    invalid          INTEGER,
    empty_id         INTEGER
);

CREATE TABLE IF NOT EXISTS event_containers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id       INTEGER NOT NULL REFERENCES ingestion_events(id),
    eqid           TEXT NOT NULL REFERENCES containers(eqid),
    as_found       TEXT,
    role           TEXT,                  -- valid | corrected | flagged | invalid_structure
    printed_check  TEXT,
    computed_check TEXT,
    occurrences    INTEGER
);

CREATE INDEX IF NOT EXISTS ix_evc_eqid  ON event_containers(eqid);
CREATE INDEX IF NOT EXISTS ix_evc_event ON event_containers(event_id);
CREATE INDEX IF NOT EXISTS ix_evt_ts    ON ingestion_events(ts);

-- Current-state cache for the dossier UI: ONE merged row per container,
-- COALESCE-updated. Derived; the tables below are the system of record.
CREATE TABLE IF NOT EXISTS container_enrichment (
    eqid          TEXT PRIMARY KEY REFERENCES containers(eqid),
    owner_name    TEXT,
    owner_city    TEXT,
    owner_country TEXT,
    source        TEXT,
    details       TEXT,            -- JSON: extra fields from external sources
    enriched_at   TEXT NOT NULL
);

-- ──────────────── dimensions (deduplicated reference entities) ────────────────
CREATE TABLE IF NOT EXISTS owners (
    prefix     TEXT PRIMARY KEY,          -- BIC/ILU owner code (e.g. MSK, APL)
    name       TEXT,                      -- filled by registry/BoxTech; NULL until known
    city       TEXT,
    country    TEXT,
    source     TEXT,                      -- where the identity came from
    first_seen TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- containers.owner joins owners.prefix. (Soft join, enforced by the write path:
-- SQLite cannot ALTER a foreign key onto the pre-existing containers table.)

CREATE TABLE IF NOT EXISTS sources (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE             -- 'maersk', 'cma-cgm', 'bic-boxtech', ...
);

CREATE TABLE IF NOT EXISTS vessels (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    imo        TEXT,                      -- IMO number when the carrier supplied one
    name       TEXT,
    vessel_key TEXT NOT NULL UNIQUE       -- imo if present, else 'name:<NAME>' (dedupe key)
);

CREATE TABLE IF NOT EXISTS locations (
    unlocode TEXT PRIMARY KEY,            -- UN/LOCODE, e.g. FRLEH, USLAX
    name     TEXT                         -- filled when a payload carries it
);

CREATE TABLE IF NOT EXISTS equipment_types (
    iso_code    TEXT PRIMARY KEY,         -- ISO size/type or group, e.g. 22G1 / 22GP
    description TEXT
);

-- ──────────────── facts (append-only; FKs into the dimensions) ────────────────
-- One row per SUCCESSFUL external API return: the complete raw JSON, so nothing
-- a source told us is ever lost, even fields the app does not model yet.
CREATE TABLE IF NOT EXISTS enrichment_fetches (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    eqid       TEXT NOT NULL REFERENCES containers(eqid),
    source_id  INTEGER NOT NULL REFERENCES sources(id),
    fetched_at TEXT NOT NULL,
    payload    TEXT NOT NULL              -- full raw JSON of the return
);
CREATE INDEX IF NOT EXISTS ix_fetch_eqid ON enrichment_fetches(eqid);
CREATE INDEX IF NOT EXISTS ix_fetch_src  ON enrichment_fetches(source_id, fetched_at);

-- Normalized transport events extracted from fetch payloads. Deduplicated by a
-- natural key, so re-polling a carrier inserts nothing new; fetch_id records
-- which fetch FIRST delivered each event (provenance).
CREATE TABLE IF NOT EXISTS container_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    eqid       TEXT NOT NULL REFERENCES containers(eqid),
    source_id  INTEGER NOT NULL REFERENCES sources(id),
    fetch_id   INTEGER NOT NULL REFERENCES enrichment_fetches(id),
    event_time TEXT,
    event_type TEXT,                      -- EQUIPMENT | TRANSPORT | SHIPMENT
    event_code TEXT,                      -- LOAD | DISC | GTIN | ...
    unlocode   TEXT REFERENCES locations(unlocode),
    vessel_id  INTEGER REFERENCES vessels(id),
    iso_code   TEXT REFERENCES equipment_types(iso_code),
    nat_key    TEXT NOT NULL UNIQUE       -- eqid|source|time|type|code|loc -> dedupe
);
CREATE INDEX IF NOT EXISTS ix_ce_eqid ON container_events(eqid, event_time);
CREATE INDEX IF NOT EXISTS ix_ce_loc  ON container_events(unlocode);
CREATE INDEX IF NOT EXISTS ix_ce_ves  ON container_events(vessel_id);
"""


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def connect(path: str, *, create_schema: bool = True, busy_timeout_ms: int = 5000
            ) -> sqlite3.Connection:
    """Open a SQLite connection tuned for concurrent multi-process access.

    Concurrency posture (this app may run several uvicorn workers PLUS the SFTP
    watcher, all separate processes sharing one DB file):
      * WAL journal       -> concurrent readers never block, single writer at a
                             time (also required for Litestream replication).
      * busy_timeout       -> a writer that meets a momentarily-held lock WAITS up
                             to busy_timeout_ms instead of failing instantly with
                             "database is locked" (default is 0 = fail immediately;
                             this is the single most important concurrency fix).
      * synchronous=NORMAL -> the WAL-recommended durability level: safe across
                             app crashes, faster than FULL, fewer lock windows.
                             (A power loss can lose the last WAL frames, not the DB.)
      * check_same_thread=False -> the connection object is only ever used by the
                             one request/thread that created it (FastAPI threadpool
                             pattern), but disabling the guard avoids false
                             positives if a connection is handed across threads.
    The PRAGMAs are idempotent and cheap; we set them on every connect."""
    conn = sqlite3.connect(path, timeout=busy_timeout_ms / 1000.0,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")      # required for Litestream replication
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)};")
    conn.execute("PRAGMA synchronous=NORMAL;")    # WAL-recommended; durable + fast
    conn.execute("PRAGMA foreign_keys=ON;")
    if create_schema:
        # Serialize first-run schema creation across concurrent worker startups.
        # Multiple uvicorn workers (separate processes) may hit a fresh DB at once.
        # executescript() issues its own implicit COMMIT, so we can't wrap it in a
        # transaction; instead every statement in SCHEMA is CREATE ... IF NOT
        # EXISTS and _migrate is idempotent, so concurrent runs converge safely.
        # The retry guards the one window that can still error: two processes
        # initializing WAL simultaneously.
        for _attempt in range(4):
            try:
                conn.executescript(SCHEMA)
                _migrate(conn)
                conn.commit()
                break
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    import time as _t
                    _t.sleep(0.05 * (2 ** _attempt))
                    continue
                raise
    return conn


def execute_write(conn: sqlite3.Connection, sql: str, params=(), *, retries: int = 3,
                  backoff: float = 0.05):
    """Execute a writing statement with a small retry on transient 'database is
    locked'/'busy' -- a belt-and-braces complement to busy_timeout for the rare
    case where a WAL checkpoint or a long concurrent write outlasts the timeout.
    Returns the cursor. Non-lock OperationalErrors are re-raised immediately
    (fail loud); only genuine contention is retried."""
    import time as _time
    attempt = 0
    while True:
        try:
            cur = conn.execute(sql, params)
            return cur
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if ("locked" in msg or "busy" in msg) and attempt < retries:
                _time.sleep(backoff * (2 ** attempt))   # 50ms,100ms,200ms
                attempt += 1
                continue
            raise


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for databases created before the warehouse schema.
    CREATE TABLE IF NOT EXISTS adds the new tables; pre-existing tables need
    ALTER for new columns. Idempotent: 'duplicate column' is the no-op signal."""
    for coldef in ("times_corrected INTEGER NOT NULL DEFAULT 0",
                   "times_flagged   INTEGER NOT NULL DEFAULT 0"):
        try:
            conn.execute(f"ALTER TABLE containers ADD COLUMN {coldef}")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise                                  # real problem: stay loud


def record_event(conn: sqlite3.Connection, *, filename: str, content_type: str,
                 detected_format: str, file_size: int, user_agent: str,
                 owner_policy: str, status: str, reason: str,
                 report: Optional[CorrectionReport]) -> int:
    """Insert one ingestion event (+ container rows when a report is present). No IP."""
    ts = _now()
    s = report.summary() if report else {}
    cur = conn.execute(
        """INSERT INTO ingestion_events
           (ts, filename, content_type, detected_format, file_size, user_agent,
            owner_policy, status, reason, total_containers, corrected, flagged,
            valid, invalid, empty_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ts, filename, content_type, detected_format, file_size, user_agent,
         owner_policy, status, reason,
         s.get("containers", 0), s.get("corrected", 0), s.get("flagged", 0),
         s.get("valid", 0), s.get("invalid", 0), s.get("empty_id", 0)))
    event_id = cur.lastrowid

    if report:
        for c in report.containers:
            if c.owner:                                # owners dimension: row per prefix,
                conn.execute(                          # identity filled by enrichment later
                    """INSERT INTO owners (prefix, first_seen, updated_at)
                       VALUES (?,?,?) ON CONFLICT(prefix) DO NOTHING""",
                    (c.owner, ts, ts))
            fixed = 1 if c.status == "corrected" else 0
            flagged = 1 if c.status == "flagged" else 0
            conn.execute(
                """INSERT INTO containers (eqid, owner, category, id_type,
                       first_seen, last_seen, times_seen, times_corrected, times_flagged)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(eqid) DO UPDATE SET
                       last_seen = excluded.last_seen,
                       times_seen = times_seen + excluded.times_seen,
                       times_corrected = times_corrected + excluded.times_corrected,
                       times_flagged = times_flagged + excluded.times_flagged,
                       owner = excluded.owner,
                       category = excluded.category,
                       id_type = excluded.id_type""",
                (c.canonical, c.owner, c.category, c.id_type, ts, ts,
                 c.occurrences, fixed, flagged))
            conn.execute(
                """INSERT INTO event_containers
                       (event_id, eqid, as_found, role, printed_check, computed_check, occurrences)
                   VALUES (?,?,?,?,?,?,?)""",
                (event_id, c.canonical, c.as_found, c.status,
                 c.printed_check, c.computed_check, c.occurrences))
    conn.commit()
    return event_id


_OWNER_KEYS = ("owner_name", "owner_city", "owner_country", "source")


def upsert_enrichment(conn: sqlite3.Connection, eqid: str, data: dict) -> None:
    """Store/refresh enrichment for a container. Only called for known containers.

    Owner identity goes in dedicated columns; any other fields contributed by
    external sources (size_type, vessel_name, last_event_*, etc.) are serialized to
    the JSON `details` column. COALESCE is used so a later source that lacks a field
    (e.g. a carrier event response has no owner_name) does not erase a value an
    earlier source already supplied.
    """
    if not data:
        return
    details = {k: v for k, v in data.items() if k not in _OWNER_KEYS and v is not None}
    conn.execute(
        """INSERT INTO container_enrichment
               (eqid, owner_name, owner_city, owner_country, source, details, enriched_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(eqid) DO UPDATE SET
               owner_name    = COALESCE(excluded.owner_name,    container_enrichment.owner_name),
               owner_city    = COALESCE(excluded.owner_city,    container_enrichment.owner_city),
               owner_country = COALESCE(excluded.owner_country, container_enrichment.owner_country),
               source        = COALESCE(excluded.source,        container_enrichment.source),
               details       = COALESCE(excluded.details,       container_enrichment.details),
               enriched_at   = excluded.enriched_at""",
        (eqid, data.get("owner_name"), data.get("owner_city"), data.get("owner_country"),
         data.get("source"), json.dumps(details) if details else None, _now()))
    # Propagate identity into the deduplicated owners dimension: stored ONCE per
    # prefix, shared by every container with that owner. COALESCE keeps the first
    # authoritative identity (a carrier event with no owner can't erase it).
    if data.get("owner_name"):
        row = conn.execute("SELECT owner FROM containers WHERE eqid=?", (eqid,)).fetchone()
        prefix = row["owner"] if row else None
        if prefix:
            now = _now()
            conn.execute(
                """INSERT INTO owners (prefix, name, city, country, source, first_seen, updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(prefix) DO UPDATE SET
                       name    = COALESCE(owners.name,    excluded.name),
                       city    = COALESCE(owners.city,    excluded.city),
                       country = COALESCE(owners.country, excluded.country),
                       source  = COALESCE(owners.source,  excluded.source),
                       updated_at = excluded.updated_at""",
                (prefix, data.get("owner_name"), data.get("owner_city"),
                 data.get("owner_country"), data.get("source"), now, now))
    conn.commit()


def enrichment_for(conn: sqlite3.Connection, eqid: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM container_enrichment WHERE eqid = ?", (eqid,)).fetchone()
    if not row:
        return None
    rec = dict(row)
    raw = rec.pop("details", None)
    rec["details"] = json.loads(raw) if raw else {}
    return rec


def export_event_csv(conn: sqlite3.Connection, event_id: int) -> Optional[str]:
    """
    The container table of one past ingestion event as CSV text (header + one row
    per distinct container), for handoff to a counterparty. Returns None when the
    event does not exist so the API can 404 instead of shipping an empty file.
    Built from event_containers + containers; the corrected file itself is
    deliberately not stored, but this per-run verdict table is.
    """
    if conn.execute("SELECT 1 FROM ingestion_events WHERE id = ?", (event_id,)).fetchone() is None:
        return None
    rows = conn.execute(
        """SELECT ec.eqid, ec.as_found, ec.role AS status, c.owner, c.category,
                  c.id_type, ec.printed_check, ec.computed_check, ec.occurrences
           FROM event_containers ec
           JOIN containers c ON c.eqid = ec.eqid
           WHERE ec.event_id = ?
           ORDER BY ec.eqid""", (event_id,)).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")           # CRLF: Excel-friendly
    w.writerow(["container", "as_found", "status", "owner", "category",
                "id_type", "printed_check", "computed_check", "occurrences"])
    for r in rows:
        w.writerow([r["eqid"], r["as_found"], r["status"], r["owner"], r["category"],
                    r["id_type"], r["printed_check"], r["computed_check"], r["occurrences"]])
    return buf.getvalue()


def container_history(conn: sqlite3.Connection, eqid: str) -> list:
    """
    Every ingestion event in which this container appeared -- the dossier's
    'appearances' table. Newest first. Empty list when the container is unknown
    (the API layer decides whether that is a 404 via the containers row).
    """
    rows = conn.execute(
        """SELECT ec.as_found, ec.role, ec.printed_check, ec.computed_check,
                  ec.occurrences, e.id AS event_id, e.ts, e.filename, e.detected_format
           FROM event_containers ec
           JOIN ingestion_events e ON e.id = ec.event_id
           WHERE ec.eqid = ? ORDER BY e.id DESC""", (eqid,)).fetchall()
    return [dict(r) for r in rows]


def candidate_pool(conn: sqlite3.Connection) -> list:
    """
    Near-miss candidate pool: every distinct ISO 6346 / ILU container previously
    recorded, with its frequency. Callers must additionally filter for check
    validity before suggesting (a flagged container's canonical form is its
    as-found, check-invalid form, and must never be offered as a fix).
    """
    rows = conn.execute(
        "SELECT eqid, times_seen FROM containers "
        "WHERE id_type IN ('iso6346', 'ilu') ORDER BY times_seen DESC").fetchall()
    return [(r["eqid"], r["times_seen"]) for r in rows]


# ─────────────────── enrichment warehouse (fetch log + facts) ───────────────────
def get_source_id(conn: sqlite3.Connection, name: str) -> int:
    conn.execute("INSERT INTO sources (name) VALUES (?) ON CONFLICT(name) DO NOTHING", (name,))
    return conn.execute("SELECT id FROM sources WHERE name=?", (name,)).fetchone()["id"]


def record_fetch(conn: sqlite3.Connection, eqid: str, source: str, payload: dict) -> int:
    """Append one SUCCESSFUL API return, whole. Nothing a source told us is lost."""
    sid = get_source_id(conn, source)
    cur = conn.execute(
        "INSERT INTO enrichment_fetches (eqid, source_id, fetched_at, payload) VALUES (?,?,?,?)",
        (eqid, sid, _now(), json.dumps(payload, ensure_ascii=False, sort_keys=True)))
    return cur.lastrowid


def _vessel_id(conn, name: Optional[str], imo: Optional[str]) -> Optional[int]:
    if not (name or imo):
        return None
    key = imo if imo else f"name:{(name or '').upper()}"
    conn.execute(
        """INSERT INTO vessels (imo, name, vessel_key) VALUES (?,?,?)
           ON CONFLICT(vessel_key) DO UPDATE SET
               name = COALESCE(vessels.name, excluded.name),
               imo  = COALESCE(vessels.imo,  excluded.imo)""",
        (imo, name, key))
    return conn.execute("SELECT id FROM vessels WHERE vessel_key=?", (key,)).fetchone()["id"]


def _location(conn, unlocode: Optional[str], name: Optional[str] = None) -> Optional[str]:
    if not unlocode:
        return None
    conn.execute(
        """INSERT INTO locations (unlocode, name) VALUES (?,?)
           ON CONFLICT(unlocode) DO UPDATE SET name = COALESCE(locations.name, excluded.name)""",
        (unlocode, name))
    return unlocode


def _equipment_type(conn, iso_code: Optional[str]) -> Optional[str]:
    if not iso_code:
        return None
    conn.execute("INSERT INTO equipment_types (iso_code) VALUES (?) "
                 "ON CONFLICT(iso_code) DO NOTHING", (iso_code,))
    return iso_code


def record_carrier_events(conn: sqlite3.Connection, eqid: str, source: str,
                          fetch_id: int, payload: dict) -> int:
    """Normalize DCSA-shaped events out of a fetch payload into container_events,
    deduplicating vessels/locations/equipment dimensions AND the events themselves
    (re-polls insert nothing; fetch_id is the first fetch that delivered each).
    Returns how many NEW events were inserted."""
    events = payload.get("events")
    if not isinstance(events, list):
        return 0
    sid = get_source_id(conn, source)
    new = 0
    for ev in events:
        if not isinstance(ev, dict):
            continue
        etime = ev.get("eventDateTime") or ev.get("eventCreatedDateTime")
        etype = ev.get("eventType")
        ecode = (ev.get("equipmentEventTypeCode") or ev.get("transportEventTypeCode")
                 or ev.get("shipmentEventTypeCode"))
        loc = ev.get("eventLocation") or {}
        unlocode = _location(conn, loc.get("UNLocationCode"), loc.get("locationName"))
        vessel = (ev.get("transportCall") or {}).get("vessel") or {}
        vid = _vessel_id(conn, vessel.get("vesselName"), vessel.get("vesselIMONumber"))
        iso = _equipment_type(conn, ev.get("ISOEquipmentCode"))
        nat = f"{eqid}|{source}|{etime}|{etype}|{ecode}|{unlocode or ''}"
        cur = conn.execute(
            """INSERT INTO container_events
                   (eqid, source_id, fetch_id, event_time, event_type, event_code,
                    unlocode, vessel_id, iso_code, nat_key)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(nat_key) DO NOTHING""",
            (eqid, sid, fetch_id, etime, etype, ecode, unlocode, vid, iso, nat))
        new += cur.rowcount
    return new


def persist_enrichment_payloads(conn: sqlite3.Connection, eqid: str,
                                triples) -> dict:
    """Warehouse one container's successful returns: for each (source, flat, payload)
    append the fetch (full JSON), normalize any carrier events, and harvest the
    equipment-type dimension from technical sources (BoxTech size_type)."""
    stats = {"fetches": 0, "events": 0}
    for source, flat, payload in triples:
        if not flat and not payload:
            continue
        fetch_id = record_fetch(conn, eqid, source, payload if payload else {"flat": flat})
        stats["fetches"] += 1
        if payload:
            stats["events"] += record_carrier_events(conn, eqid, source, fetch_id, payload)
        for k in ("size_type", "group_type", "iso_equipment_code"):
            if flat and flat.get(k):
                _equipment_type(conn, flat[k])
    conn.commit()
    return stats


def recompute_counters(conn: sqlite3.Connection, eqid: str) -> dict:
    """Recompute the denormalized counters from event_containers (the truth).
    Used by tests and audits to prove the cached counters cannot drift."""
    row = conn.execute(
        """SELECT COALESCE(SUM(occurrences),0) AS seen,
                  SUM(CASE WHEN role='corrected' THEN 1 ELSE 0 END) AS corrected,
                  SUM(CASE WHEN role='flagged'   THEN 1 ELSE 0 END) AS flagged
           FROM event_containers WHERE eqid=?""", (eqid,)).fetchone()
    return {"times_seen": row["seen"], "times_corrected": row["corrected"] or 0,
            "times_flagged": row["flagged"] or 0}


# ─────────────────────────── canned insight queries ───────────────────────────
def insight_owner_quality(conn: sqlite3.Connection, limit: int = 25) -> list:
    """Data-quality league table per owner prefix: whose numbers arrive broken?
    fix_rate = files where we corrected / files where we corrected-or-flagged."""
    rows = conn.execute(
        """SELECT c.owner AS prefix, o.name AS owner_name,
                  COUNT(*) AS boxes,
                  SUM(c.times_seen) AS sightings,
                  SUM(c.times_corrected) AS fixes,
                  SUM(c.times_flagged) AS flags,
                  ROUND(1.0*SUM(c.times_corrected) /
                        NULLIF(SUM(c.times_corrected)+SUM(c.times_flagged),0), 3) AS fix_rate
           FROM containers c LEFT JOIN owners o ON o.prefix = c.owner
           WHERE c.owner IS NOT NULL
           GROUP BY c.owner ORDER BY (fixes+flags) DESC, sightings DESC
           LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]


def insight_top_locations(conn: sqlite3.Connection, limit: int = 25) -> list:
    rows = conn.execute(
        """SELECT l.unlocode, l.name, COUNT(*) AS events,
                  COUNT(DISTINCT e.eqid) AS containers
           FROM container_events e JOIN locations l ON l.unlocode = e.unlocode
           GROUP BY l.unlocode ORDER BY events DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]


def insight_top_vessels(conn: sqlite3.Connection, limit: int = 25) -> list:
    rows = conn.execute(
        """SELECT v.name, v.imo, COUNT(*) AS events,
                  COUNT(DISTINCT e.eqid) AS containers
           FROM container_events e JOIN vessels v ON v.id = e.vessel_id
           GROUP BY v.id ORDER BY events DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Pass 22 -- analytics dashboard queries.
# These power the dashboard SPA. All read straight from the existing warehouse
# tables (ingestion_events + container_events + dimensions); no new storage.
# --------------------------------------------------------------------------- #
def insight_error_trend(conn: sqlite3.Connection, days: int = 30) -> list:
    """Per-day ingestion volume and error counts over the last `days` days.
    A 'day' is the date portion of ingestion_events.ts. Returns chronological
    rows so the SPA can draw a time series of files/containers/corrected/flagged."""
    rows = conn.execute(
        """SELECT substr(ts,1,10) AS day,
                  COUNT(*)                         AS files,
                  SUM(COALESCE(total_containers,0)) AS containers,
                  SUM(COALESCE(corrected,0))        AS corrected,
                  SUM(COALESCE(flagged,0))          AS flagged,
                  SUM(COALESCE(invalid,0))          AS invalid,
                  SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) AS rejected
           FROM ingestion_events
           WHERE ts >= datetime('now', ?)
           GROUP BY day ORDER BY day ASC""",
        (f"-{int(days)} days",)).fetchall()
    return [dict(r) for r in rows]


def insight_by_format(conn: sqlite3.Connection) -> list:
    """Fix-rate and volume grouped by detected file format. Answers 'which file
    types arrive dirtiest?' fix_rate = corrected / (corrected + flagged)."""
    rows = conn.execute(
        """SELECT COALESCE(detected_format,'(unknown)') AS format,
                  COUNT(*)                          AS files,
                  SUM(COALESCE(total_containers,0))  AS containers,
                  SUM(COALESCE(corrected,0))         AS corrected,
                  SUM(COALESCE(flagged,0))           AS flagged,
                  SUM(COALESCE(invalid,0))           AS invalid,
                  SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) AS rejected,
                  ROUND(1.0*SUM(COALESCE(corrected,0)) /
                        NULLIF(SUM(COALESCE(corrected,0))+SUM(COALESCE(flagged,0)),0), 3)
                                                     AS fix_rate
           FROM ingestion_events
           GROUP BY detected_format ORDER BY files DESC""").fetchall()
    return [dict(r) for r in rows]


def insight_totals(conn: sqlite3.Connection) -> dict:
    """Headline KPI counters for the dashboard cards."""
    row = conn.execute(
        """SELECT COUNT(*)                          AS files,
                  SUM(CASE WHEN status='processed' THEN 1 ELSE 0 END) AS processed,
                  SUM(CASE WHEN status='rejected'  THEN 1 ELSE 0 END) AS rejected,
                  SUM(COALESCE(total_containers,0))  AS containers,
                  SUM(COALESCE(corrected,0))         AS corrected,
                  SUM(COALESCE(flagged,0))           AS flagged,
                  SUM(COALESCE(invalid,0))           AS invalid
           FROM ingestion_events""").fetchone()
    out = dict(row) if row else {}
    distinct = conn.execute("SELECT COUNT(*) AS n FROM containers").fetchone()
    out["distinct_containers"] = distinct["n"] if distinct else 0
    tot = (out.get("corrected") or 0) + (out.get("flagged") or 0)
    out["overall_fix_rate"] = round((out.get("corrected") or 0) / tot, 3) if tot else None
    return out


def insight_lanes(conn: sqlite3.Connection, limit: int = 25) -> list:
    """Busiest origin->destination lanes from consecutive container_events.
    For each container, order its located events by time and pair each with the
    next to form a directed leg (origin UNLOCODE -> destination UNLOCODE); count
    distinct containers and legs per lane. Pure warehouse data (DCSA events that
    were stored from enrichment); empty until carrier events are present."""
    rows = conn.execute(
        """WITH located AS (
               SELECT eqid, unlocode, event_time,
                      ROW_NUMBER() OVER (PARTITION BY eqid ORDER BY event_time) AS rn
               FROM container_events
               WHERE unlocode IS NOT NULL AND event_time IS NOT NULL
           )
           SELECT a.unlocode AS origin, b.unlocode AS destination,
                  COUNT(*) AS legs, COUNT(DISTINCT a.eqid) AS containers
           FROM located a JOIN located b
             ON b.eqid = a.eqid AND b.rn = a.rn + 1
           WHERE a.unlocode <> b.unlocode
           GROUP BY a.unlocode, b.unlocode
           ORDER BY legs DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]
