#!/usr/bin/env python3
"""
run_pass16.py -- enrichment warehouse + counters.

Proves the relational storage design end-to-end:

  migration        a pre-warehouse DB gains the new tables/columns, data intact
  counters         times_seen (occurrence-weighted) vs times_corrected /
                   times_flagged (event-weighted: +1 per FILE) increment right
  no-drift         recompute_counters() from event_containers == cached counters
  owners dim       prefix row auto-created at sight (name NULL), identity filled
                   by enrichment via COALESCE, shared across containers (dedup)
  fetch log        every successful return stored WHOLE; JSON round-trips exactly
  events fact      DCSA events normalized; vessels/locations/equipment deduped
                   ACROSS containers; re-polling inserts zero duplicates while
                   still appending the fetch (provenance preserved)
  fk integrity     bad references are rejected by SQLite, loudly
  token safety     a failed adapter's OAuth-token payload never reaches storage
  insights         owner fix-rate league table + top locations/vessels compute
"""
import json
import os
import sqlite3
import tempfile

import db
import enrichment as E
import service

DCSA_A = {"events": [
    {"eventType": "EQUIPMENT", "eventDateTime": "2026-02-01T10:00:00Z",
     "equipmentEventTypeCode": "LOAD", "ISOEquipmentCode": "22GP",
     "eventLocation": {"UNLocationCode": "FRLEH", "locationName": "Le Havre"},
     "transportCall": {"vessel": {"vesselName": "CMA CGM TEST", "vesselIMONumber": "9839179"}}},
    {"eventType": "EQUIPMENT", "eventDateTime": "2026-02-09T18:30:00Z",
     "equipmentEventTypeCode": "DISC",
     "eventLocation": {"UNLocationCode": "USLAX"},
     "transportCall": {"vessel": {"vesselName": "CMA CGM TEST", "vesselIMONumber": "9839179"}}}]}
DCSA_B = {"events": [
    {"eventType": "EQUIPMENT", "eventDateTime": "2026-02-09T19:00:00Z",
     "equipmentEventTypeCode": "DISC",
     "eventLocation": {"UNLocationCode": "FRLEH"},
     "transportCall": {"vessel": {"vesselName": "CMA CGM TEST", "vesselIMONumber": "9839179"}}}]}

OLD_SCHEMA = """
CREATE TABLE containers (eqid TEXT PRIMARY KEY, owner TEXT, category TEXT,
    id_type TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    times_seen INTEGER NOT NULL DEFAULT 0);
CREATE TABLE ingestion_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
    filename TEXT, content_type TEXT, detected_format TEXT, file_size INTEGER,
    user_agent TEXT, owner_policy TEXT, status TEXT NOT NULL, reason TEXT,
    total_containers INTEGER, corrected INTEGER, flagged INTEGER, valid INTEGER,
    invalid INTEGER, empty_id INTEGER);
CREATE TABLE event_containers (id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES ingestion_events(id),
    eqid TEXT NOT NULL REFERENCES containers(eqid), as_found TEXT, role TEXT,
    printed_check TEXT, computed_check TEXT, occurrences INTEGER);
CREATE TABLE container_enrichment (eqid TEXT PRIMARY KEY REFERENCES containers(eqid),
    owner_name TEXT, owner_city TEXT, owner_country TEXT, source TEXT,
    details TEXT, enriched_at TEXT NOT NULL);
"""


def test_migration():
    path = os.path.join(tempfile.mkdtemp(), "old.db")
    raw = sqlite3.connect(path)
    raw.executescript(OLD_SCHEMA)
    raw.execute("INSERT INTO containers VALUES ('MSKU7351770','MSK','U','iso6346',"
                "'2026-01-01','2026-01-01',3)")
    raw.commit(); raw.close()
    conn = db.connect(path, create_schema=True)        # migrate
    cols = {c[1] for c in conn.execute("PRAGMA table_info(containers)")}
    assert {"times_corrected", "times_flagged"} <= cols
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"owners", "sources", "vessels", "locations", "equipment_types",
            "enrichment_fetches", "container_events"} <= tables
    row = conn.execute("SELECT * FROM containers WHERE eqid='MSKU7351770'").fetchone()
    assert row["times_seen"] == 3 and row["times_corrected"] == 0
    db.connect(path, create_schema=True).close()       # second run: idempotent
    conn.close()
    print("  migration: old DB gains columns+tables, data intact, idempotent")


def test_counters_and_owners():
    path = os.path.join(tempfile.mkdtemp(), "w.db")
    conn = db.connect(path, create_schema=True)
    reg = E.OwnerRegistry.from_rows([{"code": "MSK", "company": "Maersk Line",
                                      "country": "DK"}])
    svc = E.EnrichmentService(registry=reg)
    # Two files where the bad check is FIXED (trust), one where it is FLAGGED.
    # The flag run passes enrichment=None: with MSK registered, owner
    # corroboration would otherwise correct it even at low trust (pass9).
    for f in ("a.txt", "b.txt"):
        service.process_upload(conn, b"MSKU7351773 bad", filename=f,
                               content_type="text/plain", user_agent="p16",
                               owner_policy="strict", trust=True, enrichment=svc)
    service.process_upload(conn, b"MSKU7351773 bad", filename="c.txt",
                           content_type="text/plain", user_agent="p16",
                           owner_policy="strict", trust=False, enrichment=None)
    # Semantics made explicit: a CORRECTED sighting records under the FIXED
    # canonical (MSKU7351770); a FLAGGED sighting records under its as-found
    # form (MSKU7351773). Two identifiers, two rows, two counter sets.
    fixed = conn.execute("SELECT * FROM containers WHERE eqid='MSKU7351770'").fetchone()
    assert (fixed["times_seen"], fixed["times_corrected"], fixed["times_flagged"]) == (2, 2, 0), \
        dict(fixed)
    flagged = conn.execute("SELECT * FROM containers WHERE eqid='MSKU7351773'").fetchone()
    assert (flagged["times_seen"], flagged["times_corrected"], flagged["times_flagged"]) == (1, 0, 1), \
        dict(flagged)
    # No-drift proof: junction truth reproduces the cached counters, per row.
    assert db.recompute_counters(conn, "MSKU7351770") == {
        "times_seen": 2, "times_corrected": 2, "times_flagged": 0}
    assert db.recompute_counters(conn, "MSKU7351773") == {
        "times_seen": 1, "times_corrected": 0, "times_flagged": 1}
    # Owners dimension: auto-row at sight, identity filled once, shared/deduped.
    own = conn.execute("SELECT * FROM owners WHERE prefix='MSK'").fetchone()
    assert own["name"] == "Maersk Line" and own["country"] == "DK"
    db.upsert_enrichment(conn, "MSKU7351770", {"vessel_name": "X", "source": "maersk"})
    own2 = conn.execute("SELECT name FROM owners WHERE prefix='MSK'").fetchone()
    assert own2["name"] == "Maersk Line"               # COALESCE kept identity
    service.process_upload(conn, b"MSKU2967654 ok", filename="d.txt",
                           content_type="text/plain", user_agent="p16",
                           owner_policy="strict", trust=False, enrichment=svc)
    n = conn.execute("SELECT COUNT(*) c FROM owners WHERE prefix='MSK'").fetchone()["c"]
    assert n == 1                                      # third MSK box, same owner row
    print("  counters: corrected->canonical row (2,2,0); flagged->as-found row (1,0,1)")
    print("  no-drift: junction recompute matches both rows' cached counters")
    print("  owners: one deduped MSK row, identity COALESCE-protected, shared by 3 boxes")
    return conn


def test_warehouse(conn):
    # Container X: two fetches of the same payload -> events deduped, fetches kept.
    t1 = [("cma-cgm", {"last_event_code": "DISC"}, DCSA_A)]
    s1 = db.persist_enrichment_payloads(conn, "MSKU7351770", t1)
    s2 = db.persist_enrichment_payloads(conn, "MSKU7351770", t1)   # re-poll
    assert s1 == {"fetches": 1, "events": 2} and s2 == {"fetches": 1, "events": 0}, (s1, s2)
    # Container Y shares the vessel and Le Havre -> dimensions dedupe ACROSS boxes.
    db.persist_enrichment_payloads(conn, "MSKU2967654",
                                   [("cma-cgm", {"last_event_code": "DISC"}, DCSA_B)])
    assert conn.execute("SELECT COUNT(*) c FROM vessels").fetchone()["c"] == 1
    v = conn.execute("SELECT * FROM vessels").fetchone()
    assert v["imo"] == "9839179" and v["name"] == "CMA CGM TEST"
    locs = {r["unlocode"]: r["name"] for r in conn.execute("SELECT * FROM locations")}
    assert set(locs) == {"FRLEH", "USLAX"} and locs["FRLEH"] == "Le Havre"
    assert conn.execute("SELECT COUNT(*) c FROM container_events").fetchone()["c"] == 3
    assert conn.execute("SELECT COUNT(*) c FROM enrichment_fetches").fetchone()["c"] == 3
    # Payload fidelity: stored JSON round-trips to the exact original structure.
    stored = conn.execute("SELECT payload FROM enrichment_fetches ORDER BY id").fetchone()
    assert json.loads(stored["payload"]) == DCSA_A
    # FK integrity is live: a fact row pointing at a nonexistent source must fail.
    try:
        conn.execute("INSERT INTO container_events (eqid, source_id, fetch_id, nat_key)"
                     " VALUES ('MSKU7351770', 9999, 1, 'bad')")
        raise AssertionError("FK violation should raise")
    except sqlite3.IntegrityError:
        conn.rollback()
    print("  warehouse: fetches append, events dedupe on re-poll, dims shared across boxes")
    print("  fidelity: raw payload JSON round-trips exactly; FK violations rejected")


def test_token_safety(conn):
    class Failing(E._HttpEnricher):
        name = "boxtech-like"
        enabled = True
        def enrich(self, eqid):
            self._last_payload = {"access_token": "SECRET", "token_type": "bearer"}
            raise RuntimeError("container call failed after token")
    class Good(E._HttpEnricher):
        name = "good"
        enabled = True
        def enrich(self, eqid):
            self._last_payload = DCSA_B
            return {"last_event_code": "DISC", "source": "good"}
    svc = E.EnrichmentService(extra=[Failing(), Good()])
    # Full payloads are warehoused only by explicit opt-in (StoragePolicy); this
    # driver opts the good source in to exercise the warehouse path.
    svc._env = {"CHECKDIGIT_ENRICH_PERSIST_PAYLOADS": "good"}
    triples, merged = svc.enrich_detailed("MSKU7351770")
    assert [t[0] for t in triples] == ["good"] and merged["source"] == "good"
    db.persist_enrichment_payloads(conn, "MSKU7351770", triples)
    leak = conn.execute("SELECT COUNT(*) c FROM enrichment_fetches "
                        "WHERE payload LIKE '%access_token%'").fetchone()["c"]
    assert leak == 0
    print("  token safety: failed source's OAuth payload never reaches storage")


def test_insights(conn):
    oq = db.insight_owner_quality(conn)
    msk = next(r for r in oq if r["prefix"] == "MSK")
    assert msk["owner_name"] == "Maersk Line" and msk["boxes"] == 3
    assert msk["fixes"] == 2 and msk["flags"] >= 1
    assert msk["fix_rate"] == round(msk["fixes"] / (msk["fixes"] + msk["flags"]), 3)
    # Event arithmetic across the whole run: X gained FRLEH-LOAD + USLAX-DISC
    # (cma-cgm) and FRLEH-DISC (token-safety's 'good' source); Y gained FRLEH-DISC.
    top_loc = db.insight_top_locations(conn)
    assert top_loc[0]["unlocode"] == "FRLEH" and top_loc[0]["events"] == 3 \
        and top_loc[0]["containers"] == 2, top_loc
    tv = db.insight_top_vessels(conn)
    assert tv[0]["imo"] == "9839179" and tv[0]["events"] == 4 \
        and tv[0]["containers"] == 2, tv
    print("  insights: owner fix-rate league, top locations (FRLEH x2 boxes), vessel reuse")


def test_api_static():
    src = open("api.py", encoding="utf-8").read()
    assert '@app.get("/insights"' in src              # admin-gated since Pass C
    assert "enrich_detailed" in src and "persist_enrichment_payloads" in src
    import ast
    ast.parse(src)
    print("  api: /insights route + warehoused background task (static check)")


def main():
    print("Pass 16 -- enrichment warehouse + counters")
    test_migration()
    conn = test_counters_and_owners()
    test_warehouse(conn)
    test_token_safety(conn)
    test_insights(conn)
    conn.close()
    test_api_static()
    print("\nPASS: warehouse schema, counters, dedup, fidelity, and insights verified.")


if __name__ == "__main__":
    main()
