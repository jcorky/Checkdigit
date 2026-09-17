"""
store.py
========
Workspace schema and connections. SQLite in WAL mode on a single host: the
measured requirement (one operator, a few concurrent jobs, millions of rows
read by indexed queries) fits it; the schema keeps every entity of the shared
contracts (contracts/entities.schema.json) as its own table rather than a
container-number-keyed blob.

Identity rules:
  * ids are workspace-scoped text identifiers generated here (ULID-like);
  * a container number is an observed identifier of equipment, never a key;
  * every write path that must be idempotent has a natural unique key
    (intent fingerprint, (job, ordinal), (generation, key), idempotency keys).
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import threading
import time
from typing import Iterator

SCHEMA_VERSION = 1

# Page cache per connection. Membership and key indexes receive keys in random
# order; once such an index outgrows the cache every insert becomes a disk read.
# 256 MiB keeps the indexes of a three-million-row job resident (see BENCHMARK.md).
CACHE_KIB = int(os.environ.get("CHECKDIGIT_WORKSPACE_CACHE_KIB", str(256 * 1024)))

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    retention_days INTEGER NOT NULL DEFAULT 90,
    current_generation_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    role TEXT NOT NULL,
    PRIMARY KEY (user_id, workspace_id)
);

CREATE TABLE IF NOT EXISTS source_files (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    encoding TEXT,
    declared_content_type TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL,
    received_at TEXT NOT NULL,
    UNIQUE (workspace_id, sha256, size_bytes)
);

CREATE TABLE IF NOT EXISTS uploads (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    filename TEXT NOT NULL,
    declared_size INTEGER NOT NULL,
    received_bytes INTEGER NOT NULL DEFAULT 0,
    path TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',
    source_file_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feed_profiles (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    contract_json TEXT NOT NULL,
    verification_state TEXT NOT NULL DEFAULT 'unverified',
    previous_version_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, name, version)
);

CREATE TABLE IF NOT EXISTS import_intents (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    mode TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    scope_value TEXT NOT NULL,
    effective_time TEXT NOT NULL,
    baseline_generation_id TEXT,
    field_update_policy TEXT NOT NULL,
    change_volume_limit INTEGER,
    empty_scope_decision TEXT,
    declared_record_count INTEGER,
    fingerprint TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    source_file_id TEXT NOT NULL REFERENCES source_files(id),
    profile_id TEXT,
    profile_version INTEGER,
    import_intent_id TEXT REFERENCES import_intents(id),
    state TEXT NOT NULL,
    step TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    lease_owner TEXT,
    lease_until REAL,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    checkpoint_json TEXT NOT NULL DEFAULT '{}',
    options_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    parser_version TEXT NOT NULL,
    ruleset_version TEXT NOT NULL,
    analysis_version INTEGER NOT NULL DEFAULT 1,
    generation_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_jobs_state ON jobs(state, lease_until);

CREATE TABLE IF NOT EXISTS job_counters (
    job_id TEXT NOT NULL REFERENCES jobs(id),
    name TEXT NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (job_id, name)
);

-- One row per identifier observation, bound to its source location. The
-- ordinal is the job-scoped identity; it does not depend on the container number.
CREATE TABLE IF NOT EXISTS observations (
    job_id TEXT NOT NULL REFERENCES jobs(id),
    ordinal INTEGER NOT NULL,
    record_no INTEGER NOT NULL,
    location TEXT NOT NULL,
    char_offset INTEGER NOT NULL,
    raw TEXT NOT NULL,
    normalized TEXT NOT NULL,
    scheme TEXT NOT NULL,
    status TEXT NOT NULL,
    printed_check TEXT,
    computed_check TEXT,
    candidate TEXT,
    reason TEXT NOT NULL DEFAULT '',
    attrs_hash TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL DEFAULT 'proposed',
    version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (job_id, ordinal)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_obs_status ON observations(job_id, status, ordinal);
CREATE INDEX IF NOT EXISTS ix_obs_key ON observations(job_id, normalized);
CREATE INDEX IF NOT EXISTS ix_obs_decision ON observations(job_id, decision, ordinal);

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    code TEXT NOT NULL,
    severity TEXT NOT NULL,
    blocking_scope TEXT NOT NULL,
    location TEXT,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_findings_job ON findings(job_id, code, id);

CREATE TABLE IF NOT EXISTS generations (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    scope_kind TEXT NOT NULL,
    scope_value TEXT NOT NULL,
    state TEXT NOT NULL,
    baseline_generation_id TEXT,
    import_intent_id TEXT,
    job_id TEXT,
    member_count INTEGER NOT NULL DEFAULT 0,
    change_manifest_sha256 TEXT,
    change_counts_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    published_at TEXT
);

-- Membership of an identifier in a generation's declared scope. Staged in
-- batches; readers only consult published generations.
CREATE TABLE IF NOT EXISTS memberships (
    generation_id TEXT NOT NULL REFERENCES generations(id),
    key TEXT NOT NULL,
    attrs_hash TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'source',
    PRIMARY KEY (generation_id, key)
) WITHOUT ROWID;

-- Current published fleet state per workspace: the result of applying every
-- published generation in order. Changed only inside a publish transaction.
CREATE TABLE IF NOT EXISTS fleet (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    key TEXT NOT NULL,
    attrs_hash TEXT NOT NULL DEFAULT '',
    generation_id TEXT NOT NULL,
    PRIMARY KEY (workspace_id, key)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_fleet_generation ON fleet(workspace_id, generation_id);

CREATE TABLE IF NOT EXISTS change_sets (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    analysis_version INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    fingerprint_version TEXT NOT NULL DEFAULT '1',
    edit_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    change_set_id TEXT NOT NULL REFERENCES change_sets(id),
    change_set_fingerprint TEXT NOT NULL,
    approver TEXT NOT NULL,
    authorization_scope TEXT NOT NULL DEFAULT 'draft',
    decision TEXT NOT NULL,
    state TEXT NOT NULL,
    stale_reason TEXT,
    selection_count INTEGER NOT NULL,
    selection_digest TEXT NOT NULL,
    filter_json TEXT NOT NULL,
    decided_at TEXT NOT NULL
);

-- Storage-backed selection manifest: exact membership frozen at approval time.
CREATE TABLE IF NOT EXISTS selection_members (
    approval_id TEXT NOT NULL REFERENCES approvals(id),
    ordinal INTEGER NOT NULL,
    version INTEGER NOT NULL,
    PRIMARY KEY (approval_id, ordinal)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS export_artifacts (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    approval_id TEXT REFERENCES approvals(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    output_mode TEXT NOT NULL DEFAULT 'surgical',
    path TEXT,
    sha256 TEXT,
    size_bytes INTEGER,
    edits_applied INTEGER NOT NULL DEFAULT 0,
    manifest_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    built_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    subject TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
"""

_LOCK = threading.Lock()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_id(prefix: str) -> str:
    """Sortable, unguessable id: prefix + millisecond time + random suffix."""
    return f"{prefix}_{int(time.time() * 1000):x}{secrets.token_hex(6)}"


def connect(path: str, *, create: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10.0, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    with _LOCK:
        for attempt in range(6):
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    time.sleep(0.02 * (2 ** attempt))
                    continue
                raise
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute(f"PRAGMA cache_size=-{CACHE_KIB}")
        conn.execute("PRAGMA temp_store=MEMORY")
        if create:
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
                         (str(SCHEMA_VERSION),))
    return conn


def transaction(conn: sqlite3.Connection, immediate: bool = True) -> "_Tx":
    return _Tx(conn, immediate)


class _Tx:
    def __init__(self, conn: sqlite3.Connection, immediate: bool):
        self.conn = conn
        self.immediate = immediate

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE" if self.immediate else "BEGIN")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.execute("COMMIT")
        else:
            self.conn.execute("ROLLBACK")
        return False


def audit(conn: sqlite3.Connection, workspace_id: str, actor: str, action: str, subject: str, detail: str = "") -> None:
    conn.execute("INSERT INTO audit_events (workspace_id, ts, actor, action, subject, detail) VALUES (?,?,?,?,?,?)",
                 (workspace_id, now_iso(), actor, action, subject, detail))


def ensure_workspace(conn: sqlite3.Connection, workspace_id: str, name: str = "") -> None:
    conn.execute("INSERT OR IGNORE INTO workspaces (id, name, created_at) VALUES (?,?,?)",
                 (workspace_id, name or workspace_id, now_iso()))


def data_dir(root: str, *parts: str) -> str:
    path = os.path.join(root, *parts)
    os.makedirs(path, exist_ok=True)
    return path


def iter_rows(conn: sqlite3.Connection, sql: str, params=()) -> Iterator[sqlite3.Row]:
    cur = conn.execute(sql, params)
    while True:
        rows = cur.fetchmany(2000)
        if not rows:
            return
        yield from rows
