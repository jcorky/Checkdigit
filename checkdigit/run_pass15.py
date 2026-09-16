#!/usr/bin/env python3
"""
run_pass15.py -- watch-folder worker tests.

Drives run_once/select_ready directly against a temp root and the real service
+ SQLite, proving:

  stability gate     a growing file is not picked up; only size-stable + aged
  temp suffixes      .filepart never processed
  byte fidelity      a latin-1 EDIFACT file (0xE9 in FTX) comes out of the
                     outbox differing ONLY at the spliced check digit
  xlsx drop          .corrected.xlsx + .report.csv land in the outbox
  rejection routing  a PDF goes to failed/ with a reason file, audited
  crash isolation    a worker exception quarantines that file with a traceback
                     and the NEXT file still processes
  audit parity       events carry the worker user-agent; corroboration via the
                     shared from_app_env registry works headlessly
  CLI                --help exits 0; missing root fails loudly
"""
import base64
import io
import os
import subprocess
import sys
import tempfile
import zipfile

import db
import enrichment as enrich_mod
import equipment_checkdigit as k
import watch_folder as wf
from run_pass14 import build_xlsx


def _setup():
    root = tempfile.mkdtemp()
    dirs = wf.ensure_dirs(os.path.join(root, "sftp"))
    conn = db.connect(os.path.join(root, "wf.db"), create_schema=True)
    reg = os.path.join(root, "reg.csv")
    with open(reg, "w", encoding="utf-8") as fh:
        fh.write("code,company,city,country\nAPL,APL Co,,SG\nMSK,Maersk Line,,DK\n")
    enr = enrich_mod.from_app_env({"CHECKDIGIT_OWNER_REGISTRY": reg})
    return dirs, conn, enr


def _drop(dirs, name, data: bytes):
    with open(os.path.join(dirs.inbox, name), "wb") as fh:
        fh.write(data)


def _tick(conn, dirs, state, enr, **kw):
    base = dict(owner_policy="strict", trust=False, enrichment=enr,
                stable_scans=2, min_age_s=0.0)
    base.update(kw)
    return wf.run_once(conn, dirs, state, **base)


def test_stability_and_temp(dirs, conn, enr):
    state = {}
    _drop(dirs, "grow.txt", b"first half MSKU73517")
    assert _tick(conn, dirs, state, enr) == 0          # scan 1: first sight (stable=1)
    with open(os.path.join(dirs.inbox, "grow.txt"), "ab") as fh:
        fh.write(b"70 done")                           # grew between scans
    assert _tick(conn, dirs, state, enr) == 0          # size changed: gate resets
    assert _tick(conn, dirs, state, enr) == 1          # 2nd same-size sighting: processed
    assert os.path.exists(os.path.join(dirs.outbox, "grow.corrected.txt"))
    assert not os.path.exists(os.path.join(dirs.inbox, "grow.txt"))
    assert any(f.endswith("_grow.txt") for f in os.listdir(dirs.processed))

    _drop(dirs, "upload.txt.filepart", b"MSKU7351770")
    s2 = {}
    for _ in range(4):
        assert _tick(conn, dirs, s2, enr) == 0         # temp suffix: never eligible
    print("  stability: growth resets the gate; temp suffixes ignored")


def test_latin1_fidelity(dirs, conn, enr):
    # EDIFACT with a latin-1 0xE9 in an FTX and a bad APL check (corroborated).
    src = ("UNB+UNOA:2+S+R+260609:1200+1'UNH+1+COPRAR:D:95B:UN'"
           "FTX+AAA+++caf\xe9 dock'EQD+CN+APLU9192812+22G1'UNT+4+1'UNZ+1+1'"
           ).encode("latin-1")
    assert src.decode("latin-1") and b"\xe9" in src
    _drop(dirs, "latin.edi", src)
    state = {}
    _tick(conn, dirs, state, enr); n = _tick(conn, dirs, state, enr)
    assert n == 1
    with open(os.path.join(dirs.outbox, "latin.corrected.edi"), "rb") as fh:
        out = fh.read()
    assert out == src.replace(b"APLU9192812", b"APLU9192819"), \
        "output must differ ONLY at the spliced token (latin-1 preserved)"
    assert b"caf\xe9" in out
    print("  fidelity: latin-1 bytes preserved; only the check digit changed")


def test_xlsx_drop(dirs, conn, enr):
    _drop(dirs, "loadlist.xlsx", build_xlsx())
    state = {}
    _tick(conn, dirs, state, enr); assert _tick(conn, dirs, state, enr) == 1
    out = os.path.join(dirs.outbox, "loadlist.corrected.xlsx")
    with zipfile.ZipFile(out) as z:                    # real, sound ZIP out
        assert z.testzip() is None
        assert b"APLU9192819" in z.read("xl/sharedStrings.xml")  # APL corroborated
    csvp = os.path.join(dirs.outbox, "loadlist.report.csv")
    with open(csvp, encoding="utf-8") as fh:
        body = fh.read()
    assert "APLU9192819,APLU9192812,corrected" in body
    print("  xlsx: corrected workbook + verdict CSV in outbox")


def test_rejection_and_crash(dirs, conn, enr):
    _drop(dirs, "doc.pdf", b"%PDF-1.7 nonsense")
    state = {}
    _tick(conn, dirs, state, enr); assert _tick(conn, dirs, state, enr) == 1
    failed = [f for f in os.listdir(dirs.failed) if f.endswith("_doc.pdf")]
    assert failed and "PDF" in open(
        os.path.join(dirs.failed, failed[0] + ".reason.txt"), encoding="utf-8").read()

    # Crash isolation: first file explodes inside the pipeline, second succeeds.
    real = wf.service.process_upload
    def boom(conn_, content, **kw):
        if kw.get("filename") == "bomb.txt":
            raise RuntimeError("synthetic worker crash")
        return real(conn_, content, **kw)
    wf.service.process_upload = boom
    try:
        _drop(dirs, "bomb.txt", b"MSKU7351770")
        _drop(dirs, "after.txt", b"MSKU7351770 fine")
        s = {}
        _tick(conn, dirs, s, enr); n = _tick(conn, dirs, s, enr)
        assert n == 2
    finally:
        wf.service.process_upload = real
    bombed = [f for f in os.listdir(dirs.failed) if f.endswith("_bomb.txt")]
    assert bombed and "synthetic worker crash" in open(
        os.path.join(dirs.failed, bombed[0] + ".reason.txt"), encoding="utf-8").read()
    assert os.path.exists(os.path.join(dirs.outbox, "after.corrected.txt"))
    print("  isolation: rejection routed with reason; crash quarantined, line kept moving")


def test_audit(conn):
    rows = conn.execute(
        "SELECT filename, status, user_agent, detected_format FROM ingestion_events "
        "ORDER BY id").fetchall()
    assert all(r["user_agent"] == wf.USER_AGENT for r in rows)
    by = {r["filename"]: r for r in rows}
    assert by["doc.pdf"]["status"] == "rejected"
    assert by["loadlist.xlsx"]["detected_format"] == "xlsx"
    assert "bomb.txt" not in by                        # crashed pre-audit: quarantined only
    print(f"  audit: {len(rows)} worker events recorded under '{wf.USER_AGENT}'")


def test_cli():
    out = subprocess.run([sys.executable, "watch_folder.py", "--help"],
                         capture_output=True, text=True)
    assert out.returncode == 0 and "inbox" in out.stdout
    bad = subprocess.run([sys.executable, "watch_folder.py", "--once"],
                         capture_output=True, text=True,
                         env={**os.environ, "CHECKDIGIT_WATCH_ROOT": ""})
    assert bad.returncode != 0 and "watch root" in (bad.stderr + bad.stdout)
    print("  cli: --help works; missing root fails loudly")


def main():
    print("Pass 15 -- watch-folder worker")
    dirs, conn, enr = _setup()
    test_stability_and_temp(dirs, conn, enr)
    test_latin1_fidelity(dirs, conn, enr)
    test_xlsx_drop(dirs, conn, enr)
    test_rejection_and_crash(dirs, conn, enr)
    test_audit(conn)
    conn.close()
    test_cli()
    print("\nPASS: watch-folder worker verified end-to-end against the real pipeline.")


if __name__ == "__main__":
    main()
