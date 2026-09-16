#!/usr/bin/env python3
"""
run_pass21.py -- batch / bulk correction.

Proves the bulk layer over the per-file pipeline:

  list batch       many (name,bytes) -> one zip with corrected/<name> each
  zip batch        a .zip of inputs is flattened and processed
  mixed formats    edifact + snx + txt + xlsx in one batch, each corrected right
  consolidated     report.csv (one row/file) + manifest.json totals match
  failure isolate  a rejected (docx) and a crashing member are recorded + skipped,
                   the rest still process; one bad file never sinks the batch
  binary member    xlsx member emerges as corrected/<stem>.corrected.xlsx (valid zip)
  name dedup       two inputs sharing a basename get distinct output names
  audit            every member writes an ingestion_event (batch UA)
  policy           a batch-wide deny is honored across members
"""
import base64
import io
import os
import tempfile
import zipfile

import batch as batch_mod
import db
import equipment_checkdigit as k
from policy import Policy
from run_pass14 import build_xlsx

GOOD = "MSKU7351770"
BAD = "MSKU7351773"
SNX_BAD = ('<?xml version="1.0"?><snx xmlns:tos="urn:tos:container-xml">'
           '<container eqid="APLU9192812" unit-id="U1" unique-key="K1"/></snx>')


def _open_zip(b: bytes):
    zf = zipfile.ZipFile(io.BytesIO(b))
    return zf, set(zf.namelist())


def test_list_batch_mixed():
    conn = db.connect(os.path.join(tempfile.mkdtemp(), "p21.db"), create_schema=True)
    edi = ("UNB+UNOA:2+S+R+260609:1200+1'UNH+1+COPRAR:D:95B:UN'"
           f"EQD+CN+{BAD}+22G1'UNT+3+1'UNZ+1+1'")
    files = [
        ("a.edi", edi.encode()),
        ("b.xml", SNX_BAD.encode()),
        ("c.txt", f"yard {BAD} note".encode()),
        ("d.xlsx", build_xlsx()),
    ]
    res = batch_mod.process_batch(conn, files, owner_policy="strict", trust=True)
    zf, names = _open_zip(res.zip_bytes)
    assert "report.csv" in names and "manifest.json" in names
    # each input produced a corrected/ artifact
    outs = {n for n in names if n.startswith("corrected/")}
    assert len(outs) == 4, outs
    # edifact corrected in place (only the check digit)
    assert zf.read("corrected/a.corrected.edi").decode() == edi.replace(BAD, GOOD)
    # xlsx member is a valid workbook
    xb = zf.read("corrected/d.corrected.xlsx")
    with zipfile.ZipFile(io.BytesIO(xb)) as xz:
        assert xz.testzip() is None
    # totals add up
    assert res.totals["files"] == 4 and res.totals["processed"] == 4
    assert res.totals["corrected"] >= 3
    conn.close()
    print("  list batch: edifact+snx+txt+xlsx each corrected; report+manifest present")


def test_zip_batch_and_csv():
    conn = db.connect(os.path.join(tempfile.mkdtemp(), "p21b.db"), create_schema=True)
    # build an input zip
    inb = io.BytesIO()
    with zipfile.ZipFile(inb, "w") as z:
        z.writestr("loads/a.txt", f"box {BAD}".encode())
        z.writestr("loads/b.txt", f"box {GOOD}".encode())
        z.writestr("__MACOSX/junk", b"ignore me")        # junk member skipped
    res = batch_mod.process_batch_zip(conn, inb.getvalue(), trust=True)
    zf, names = _open_zip(res.zip_bytes)
    assert res.totals["files"] == 2                       # junk skipped
    body = zf.read("report.csv").decode()
    assert body.splitlines()[0].startswith("file,status,detected_format")
    assert "a.txt" in body and "b.txt" in body
    conn.close()
    print("  zip batch: members flattened, junk skipped, report.csv consolidated")


def test_failure_isolation():
    conn = db.connect(os.path.join(tempfile.mkdtemp(), "p21c.db"), create_schema=True)
    docx = io.BytesIO()
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml", "<d/>")
    # monkeypatch to force a crash on one specific member
    real = batch_mod.service.process_upload
    def boom(conn_, content, **kw):
        if kw.get("filename") == "bomb.txt":
            raise RuntimeError("synthetic crash")
        return real(conn_, content, **kw)
    batch_mod.service.process_upload = boom
    try:
        files = [
            ("ok1.txt", f"box {BAD}".encode()),
            ("doc.docx", docx.getvalue()),               # rejected
            ("bomb.txt", b"box MSKU0000000"),            # crashes
            ("ok2.txt", f"box {GOOD}".encode()),
        ]
        res = batch_mod.process_batch(conn, files, trust=True)
    finally:
        batch_mod.service.process_upload = real
    by = {m.name: m for m in res.members}
    assert by["doc.docx"].status == "rejected" and "Word" in by["doc.docx"].reason
    assert by["bomb.txt"].status == "error" and "synthetic crash" in by["bomb.txt"].reason
    assert by["ok1.txt"].status == "processed" and by["ok2.txt"].status == "processed"
    zf, names = _open_zip(res.zip_bytes)
    outs = {n for n in names if n.startswith("corrected/")}
    assert len(outs) == 2                                 # only the 2 good ones
    assert res.totals == {**res.totals, "rejected": 1, "errored": 1, "processed": 2,
                          "files": 4}
    conn.close()
    print("  isolation: rejected + crashing members recorded & skipped; rest processed")


def test_name_dedup():
    conn = db.connect(os.path.join(tempfile.mkdtemp(), "p21d.db"), create_schema=True)
    files = [("load.txt", f"a {BAD}".encode()), ("load.txt", f"b {BAD}".encode())]
    res = batch_mod.process_batch(conn, files, trust=True)
    _, names = _open_zip(res.zip_bytes)
    outs = sorted(n for n in names if n.startswith("corrected/"))
    assert outs == ["corrected/load.corrected(2).txt", "corrected/load.corrected.txt"], outs
    conn.close()
    print("  dedup: colliding basenames get distinct output names")


def test_audit_and_policy():
    conn = db.connect(os.path.join(tempfile.mkdtemp(), "p21e.db"), create_schema=True)
    files = [("a.txt", f"box {GOOD}".encode()), ("b.txt", f"box {GOOD}".encode())]
    # batch-wide deny on MSK: both GOOD boxes are valid but must be flagged
    res = batch_mod.process_batch(conn, files, trust=True, policy=Policy(deny={"MSK"}))
    assert res.totals["flagged"] >= 2 and res.totals["corrected"] == 0
    # every member audited under the batch UA
    rows = conn.execute("SELECT user_agent, status FROM ingestion_events").fetchall()
    assert len(rows) == 2 and all(r["user_agent"] == "checkdigit-batch/1" for r in rows)
    conn.close()
    print("  audit+policy: batch deny honored; every member writes an audit row")


def test_api_static():
    src = open("api.py", encoding="utf-8").read()
    assert '@app.post("/correct/batch")' in src and "process_batch_zip" in src
    import ast
    ast.parse(src)
    print("  api: /correct/batch route present (static check)")


def main():
    print("Pass 21 -- batch / bulk correction")
    test_list_batch_mixed()
    test_zip_batch_and_csv()
    test_failure_isolation()
    test_name_dedup()
    test_audit_and_policy()
    test_api_static()
    print("\nPASS: batch verified -- mixed formats, zip I/O, failure isolation, audit.")


if __name__ == "__main__":
    main()
