"""
run_pass8.py
============
End-to-end exercise of the service core (no HTTP): detection -> correction ->
SQLite persistence -> audit. Processes a representative file per format, queries
the audit log and container registry back out, and verifies:

  * format detection is CONTENT-based (the EDIFACT files have a .txt extension,
    the X12 file is .edi, the SNX file is .xml -- each is detected correctly);
  * the audit log and registry rows match the corrections;
  * oversized uploads and non-SNX XML are rejected (and recorded as rejected);
  * filenames are reduced to a basename (no path traversal);
  * NO table stores a client IP.
"""
import os
import tempfile

import db
import service

SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")
JOBS = [
    (os.path.join(SAMPLES, "USER01_baplie_edi.txt"),       "edifact"),  # EDIFACT (.txt content)
    (os.path.join(SAMPLES, "4Containers_snx_Example.xml"), "snx"),
    (os.path.join(SAMPLES, "Example_1_X12.edi"),           "x12"),
    (os.path.join(SAMPLES, "txtcontainers.txt"),           "txt"),
]


def assert_no_ip(conn):
    for tbl in ("ingestion_events", "event_containers", "containers"):
        cols = [r["name"].lower() for r in conn.execute(f"PRAGMA table_info({tbl})")]
        bad = [c for c in cols if c == "ip" or "ip_" in c or c.endswith("_ip") or "ipaddr" in c]
        assert not bad, f"{tbl} has an IP-like column: {bad}"


def main():
    tmp = tempfile.mkdtemp()
    conn = db.connect(os.path.join(tmp, "checkdigit.db"))

    print("=== processing (content-based detection) ===")
    for path, expected_fmt in JOBS:
        if not os.path.exists(path):
            print(f"  SKIP {os.path.basename(path)} (sample not present)")
            continue
        with open(path, "rb") as fh:
            content = fh.read()
        res = service.process_upload(conn, content, filename=path,
                                     content_type="application/octet-stream",
                                     user_agent="pass8-harness/1.0")
        assert res["status"] == "processed", res
        assert res["detected_format"] == expected_fmt, \
            f"{os.path.basename(path)}: detected {res['detected_format']}, expected {expected_fmt}"
        print(f"  {os.path.basename(path):<32} -> {res['detected_format']:<8} {res['summary']}")

    # rejection paths
    big = service.process_upload(conn, b"X" * (service.MAX_BYTES + 1),
                                 filename="huge.txt", user_agent="h")
    assert big["status"] == "rejected" and "size" in big["reason"], big
    notsnx = service.process_upload(conn, b"<?xml version='1.0'?><foo><bar/></foo>",
                                    filename="x.xml", user_agent="h")
    assert notsnx["status"] == "rejected" and notsnx["detected_format"] == "unsupported", notsnx
    # filename sanitization
    trav = service.process_upload(conn, b"MSKU7351770\n", filename="../../etc/passwd",
                                  user_agent="h")
    row = conn.execute("SELECT filename FROM ingestion_events WHERE id=?",
                       (trav["event_id"],)).fetchone()
    assert row["filename"] == "passwd", row["filename"]
    print("  rejection + sanitization paths -> OK")

    assert_no_ip(conn)
    print("  no IP column in any table -> OK")

    print("\n=== ingestion_events (audit log) ===")
    print(f"  {'id':<3} {'format':<8} {'status':<10} {'cont':<5} {'corr':<5} {'flag':<5} {'file':<28} ua")
    for e in conn.execute("""SELECT id, detected_format, status, total_containers, corrected,
                                    flagged, filename, user_agent FROM ingestion_events ORDER BY id"""):
        print(f"  {e['id']:<3} {str(e['detected_format']):<8} {e['status']:<10} "
              f"{str(e['total_containers'] or ''):<5} {str(e['corrected'] or ''):<5} "
              f"{str(e['flagged'] or ''):<5} {str(e['filename'])[:28]:<28} {e['user_agent']}")

    # spot-check specific events
    snx = conn.execute("SELECT * FROM ingestion_events WHERE detected_format='snx'").fetchone()
    if snx:
        assert (snx["corrected"], snx["valid"], snx["total_containers"]) == (3, 1, 4), dict(snx)
    txt = conn.execute("SELECT * FROM ingestion_events WHERE detected_format='txt'").fetchone()
    if txt:
        assert (txt["total_containers"], txt["flagged"], txt["valid"]) == (41, 38, 3), dict(txt)
    x12 = conn.execute("SELECT * FROM ingestion_events WHERE detected_format='x12'").fetchone()
    if x12:
        assert (x12["corrected"], x12["flagged"]) == (0, 2), dict(x12)

    n_cont = conn.execute("SELECT COUNT(*) AS n FROM containers").fetchone()["n"]
    n_links = conn.execute("SELECT COUNT(*) AS n FROM event_containers").fetchone()["n"]
    print(f"\n=== registry ===\n  distinct containers: {n_cont}   event_container rows: {n_links}")
    print("  sample (most-seen):")
    for c in conn.execute("""SELECT eqid, owner, id_type, times_seen FROM containers
                             ORDER BY times_seen DESC, eqid LIMIT 5"""):
        print(f"    {c['eqid']:<13} owner={c['owner']:<4} {c['id_type']:<8} times_seen={c['times_seen']}")

    print("\nPASS: detection, persistence, audit (no IP), rejection + sanitization all verified.")


if __name__ == "__main__":
    main()
