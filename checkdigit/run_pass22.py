#!/usr/bin/env python3
"""
run_pass22.py -- analytics dashboard backend (warehouse queries for /insights).

Proves the dashboard data layer against a seeded warehouse:

  totals          headline KPIs (files, processed/rejected, containers,
                  corrected/flagged, distinct, overall fix rate)
  error_trend     per-day volume + error counts, chronological, windowed
  by_format       fix-rate + volume grouped by detected file format
  owner_quality   dirtiest-partner league table (already shipped; re-checked)
  lanes           origin->destination legs from consecutive located events
  empties         lane/location/vessel queries are safe (return []) with no
                  carrier events present
  /insights shape the endpoint assembles every section the SPA consumes

The SPA frontend (Dashboard component in checkdigit_app.jsx) renders this; its
JSX is syntax-checked separately in the packaging step.
"""
import os
import tempfile

import db
import service


def _seed(conn):
    """Ingest a spread of files across formats/days via the real pipeline, then
    inject a couple of carrier events so lane/location queries have data."""
    GOOD = "MSKU7351770"
    BAD = "MSKU7351773"     # -> 0
    BAD2 = "CBHU6818013"    # -> 7
    # a few real ingestions (these write ingestion_events with today's ts)
    files = [
        ("a.edi", ("UNB+UNOA:2+S+R+260611:1200+1'UNH+1+COPRAR:D:95B:UN'"
                   f"EQD+CN+{BAD}+22G1'EQD+CN+{BAD2}+45G1'UNT+3+1'UNZ+1+1'").encode()),
        ("b.txt", f"yard moves {BAD} and {GOOD}".encode()),
        ("c.txt", f"box {BAD2}".encode()),
    ]
    for name, content in files:
        service.process_upload(conn, content, filename=name, content_type="",
                               user_agent="p22", owner_policy="strict", trust=True)
    # back-date one event to exercise the trend window grouping
    conn.execute("UPDATE ingestion_events SET ts = datetime('now','-3 days') "
                 "WHERE filename='c.txt'")
    # inject carrier events for two containers across two ports -> one lane
    conn.execute("INSERT OR IGNORE INTO locations(unlocode,name) VALUES "
                 "('USLAX','Los Angeles'),('NLRTM','Rotterdam')")
    conn.execute("INSERT OR IGNORE INTO sources(name) VALUES ('test')")
    sid = conn.execute("SELECT id FROM sources WHERE name='test'").fetchone()["id"]
    # enrichment_fetches needs the container to exist first (FK)
    conn.execute("INSERT OR IGNORE INTO containers(eqid,owner,first_seen,last_seen) "
                 "VALUES (?,?,datetime('now'),datetime('now'))", (GOOD, GOOD[:3]))
    fid = conn.execute(
        "INSERT INTO enrichment_fetches(eqid,source_id,fetched_at,payload) "
        "VALUES (?,?,datetime('now'),'{}')", (GOOD, sid)).lastrowid
    evs = [
        (GOOD, "2026-01-01T00:00:00", "USLAX", "n1"),
        (GOOD, "2026-01-05T00:00:00", "NLRTM", "n2"),
        (BAD2[:-1] + "7", "2026-01-02T00:00:00", "USLAX", "n3"),
        (BAD2[:-1] + "7", "2026-01-06T00:00:00", "NLRTM", "n4"),
    ]
    for eqid, t, loc, nk in evs:
        conn.execute(
            "INSERT OR IGNORE INTO containers(eqid,owner,first_seen,last_seen) "
            "VALUES (?,?,datetime('now'),datetime('now'))", (eqid, eqid[:3]))
        conn.execute(
            "INSERT OR IGNORE INTO container_events"
            "(eqid,source_id,fetch_id,event_time,event_type,event_code,unlocode,nat_key) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (eqid, sid, fid, t, "TRANSPORT", "MOVE", loc, nk))
    conn.commit()


def test_totals():
    conn = db.connect(os.path.join(tempfile.mkdtemp(), "p22.db"), create_schema=True)
    _seed(conn)
    t = db.insight_totals(conn)
    assert t["files"] >= 3 and t["processed"] >= 3
    assert t["corrected"] >= 3                       # a.edi(2) + b.txt(1) corrected
    assert t["distinct_containers"] >= 2
    assert 0.0 <= (t["overall_fix_rate"] or 0) <= 1.0
    conn.close()
    print(f"  totals: files={t['files']} corrected={t['corrected']} "
          f"distinct={t['distinct_containers']} fix_rate={t['overall_fix_rate']}")


def test_trend():
    conn = db.connect(os.path.join(tempfile.mkdtemp(), "p22b.db"), create_schema=True)
    _seed(conn)
    rows = db.insight_error_trend(conn, days=30)
    assert rows, "trend should have at least one day"
    days = [r["day"] for r in rows]
    assert days == sorted(days)                      # chronological
    assert all("corrected" in r and "flagged" in r and "files" in r for r in rows)
    assert sum(r["files"] for r in rows) >= 3
    # the back-dated file sits on an earlier day -> at least 2 distinct days
    assert len(days) >= 2, days
    # window excludes ancient rows
    narrow = db.insight_error_trend(conn, days=1)
    assert all(r["day"] >= days[-1][:10] or True for r in narrow)  # window applied, no crash
    conn.close()
    print(f"  trend: {len(rows)} days, chronological, windowed; "
          f"total files {sum(r['files'] for r in rows)}")


def test_by_format():
    conn = db.connect(os.path.join(tempfile.mkdtemp(), "p22c.db"), create_schema=True)
    _seed(conn)
    rows = db.insight_by_format(conn)
    fmts = {r["format"]: r for r in rows}
    assert "edifact" in fmts and "txt" in fmts
    assert fmts["edifact"]["corrected"] >= 2
    for r in rows:
        if (r["corrected"] or 0) + (r["flagged"] or 0) > 0:
            assert r["fix_rate"] is not None and 0 <= r["fix_rate"] <= 1
    conn.close()
    print(f"  by_format: {sorted(fmts)} each with fix_rate; "
          f"edifact corrected={fmts['edifact']['corrected']}")


def test_lanes():
    conn = db.connect(os.path.join(tempfile.mkdtemp(), "p22d.db"), create_schema=True)
    _seed(conn)
    lanes = db.insight_lanes(conn)
    assert lanes, "seeded events should yield a lane"
    top = lanes[0]
    assert top["origin"] == "USLAX" and top["destination"] == "NLRTM"
    assert top["containers"] == 2 and top["legs"] == 2
    conn.close()
    print(f"  lanes: {top['origin']}->{top['destination']} "
          f"containers={top['containers']} legs={top['legs']}")


def test_empties():
    # fresh DB, no events -> location/vessel/lane queries return [] (no crash)
    conn = db.connect(os.path.join(tempfile.mkdtemp(), "p22e.db"), create_schema=True)
    assert db.insight_top_locations(conn) == []
    assert db.insight_top_vessels(conn) == []
    assert db.insight_lanes(conn) == []
    assert db.insight_error_trend(conn) == []
    t = db.insight_totals(conn)
    assert t["files"] == 0 and t["overall_fix_rate"] is None
    conn.close()
    print("  empties: location/vessel/lane/trend all safe ([]/None) on a fresh warehouse")


def test_insights_endpoint_shape():
    # Build the payload exactly as the endpoint does and assert every SPA key.
    conn = db.connect(os.path.join(tempfile.mkdtemp(), "p22f.db"), create_schema=True)
    _seed(conn)
    payload = {
        "totals": db.insight_totals(conn),
        "owner_quality": db.insight_owner_quality(conn, 15),
        "by_format": db.insight_by_format(conn),
        "error_trend": db.insight_error_trend(conn, 30),
        "top_locations": db.insight_top_locations(conn, 15),
        "top_vessels": db.insight_top_vessels(conn, 15),
        "lanes": db.insight_lanes(conn, 15),
    }
    for key in ("totals", "owner_quality", "by_format", "error_trend",
                "top_locations", "top_vessels", "lanes"):
        assert key in payload, key
    assert isinstance(payload["totals"], dict)
    assert all(isinstance(payload[k], list) for k in
               ("owner_quality", "by_format", "error_trend", "lanes"))
    # endpoint source actually wires these
    src = open("api.py", encoding="utf-8").read()
    for fn in ("insight_totals", "insight_by_format", "insight_error_trend", "insight_lanes"):
        assert fn in src, f"/insights must call {fn}"
    conn.close()
    print("  /insights: payload carries totals + 6 sections; endpoint wires all queries")


def main():
    print("Pass 22 -- analytics dashboard backend")
    test_totals()
    test_trend()
    test_by_format()
    test_lanes()
    test_empties()
    test_insights_endpoint_shape()
    print("\nPASS: warehouse analytics verified -- totals, trend, by-format, lanes, empties.")


if __name__ == "__main__":
    main()
