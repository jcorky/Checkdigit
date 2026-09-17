"""
test_workspace.py
=================
Phase C regression tests: streaming readers, the durable job queue,
staged reconciliation, storage-backed selections, optimistic versions,
atomic artifact publication and the versioned API.

Run:  python3 test_workspace.py
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import csv_corrector                               # noqa: E402
from workspace import (export as export_mod, ingest, jobs as jobq, reconcile, review, runner,  # noqa: E402
                       store, stream)
from workspace.api import build_router             # noqa: E402

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    HAVE_FASTAPI = True
except ImportError:                                # pragma: no cover
    HAVE_FASTAPI = False

_TMP = []


def _root() -> str:
    d = tempfile.mkdtemp(prefix="ws_test_")
    _TMP.append(d)
    return d


def _ws(root: str, name: str = "ws"):
    conn = store.connect(os.path.join(root, "ws.db"))
    store.ensure_workspace(conn, name)
    return conn


def _write(root: str, name: str, data: bytes) -> str:
    p = os.path.join(root, name)
    with open(p, "wb") as fh:
        fh.write(data)
    return p


def _bic(owner: str, serial: int) -> str:
    import equipment_checkdigit as k
    body = f"{owner}{serial:06d}"
    return body + str(k.iso6346_check_digit(body))


def _fleet_csv(n: int, wrong_every: int = 0, start: int = 0) -> bytes:
    """Rows keyed by serial: the same container carries the same attributes in every file."""
    lines = ["ref,container,remark"]
    for i in range(n):
        c = _bic("MSKU", start + i)
        if wrong_every and i % wrong_every == 1:
            c = c[:-1] + str((int(c[-1]) + 1) % 10)
        lines.append(f"{start + i + 1},{c},r{start + i}")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def _run_full(conn, root, ws, filename, data, *, mode="comparison_only", columns=("container",), baseline=None,
              declared=None, empty_decision=None, options=None, **kw):
    path = _write(root, filename, data)
    sid = ingest.register_source(conn, root, ws, filename, path)
    iid = None
    if mode:
        iid, _dup = ingest.create_intent(conn, ws, sid, mode=mode, baseline_generation_id=baseline,
                                         declared_record_count=declared, empty_scope_decision=empty_decision, **kw)
    opts = {"columns": list(columns)}
    opts.update(options or {})
    sub = runner.submit(conn, workspace_id=ws, source_file_id=sid, intent_id=iid, options=opts)
    r = runner.run_once(conn, root)
    assert r is not None and r["state"] == "awaiting_review", r
    return sub


# --------------------------------------------------------------------------- #
# 1. Streaming readers keep state across chunk boundaries
# --------------------------------------------------------------------------- #

def _chunks(text: str, size: int):
    return (text[i:i + size] for i in range(0, len(text), size))


def test_csv_reader_matches_whole_file_parser_at_every_chunk_size():
    text = ('a,b,c\r\n1,"x\r\ny",é\n2,"say ""hi""",😀\r\n3,,\n"4","",last')
    ref = None
    for size in range(1, len(text) + 1):
        recs = list(stream.csv_records(_chunks(text, size), ","))
        got = [[f.value for f in r.fields] for r in recs]
        if ref is None:
            ref = got
            import csv
            expect = list(csv.reader(io.StringIO(text, newline="")))
            assert got == expect, (got, expect)
        assert got == ref, (size, got)
        for r in recs:
            for f in r.fields:
                raw = text[f.start:f.end]
                if f.quoted:
                    assert raw.startswith('"') and raw.endswith('"'), (size, raw)
                    assert raw[1:-1].replace('""', '"') == f.value
                else:
                    assert raw == f.value, (size, raw, f.value)


def test_text_chunks_never_split_multibyte_sequences():
    root = _root()
    text = "MSKU9070323 é 😀 北京\n" * 3000
    path = _write(root, "u.txt", text.encode("utf-8"))
    for chunk in (1, 2, 3, 5, 7, 4096):
        joined = "".join(t for _c, t in stream.text_chunks(path, chunk_bytes=chunk))
        assert joined == text, chunk
    latin = "MSKU9070323 caf\xe9\n".encode("latin-1") * 10
    path2 = _write(root, "l.txt", latin)
    items = list(stream.text_chunks(path2, chunk_bytes=5))
    assert items[0][0] == "latin-1" and "".join(t for _c, t in items) == latin.decode("latin-1")
    late = ("MSKU9070323 ok\n" * 5000).encode("utf-8") + b"caf\xe9\n"
    path3 = _write(root, "late.txt", late)
    assert stream.sniff_codec(path3, chunk_bytes=1024) == "latin-1"
    assert "".join(t for _c, t in stream.text_chunks(path3, chunk_bytes=1024)) == late.decode("latin-1")
    sha, size, codec = ingest.hash_and_sniff(path3)
    assert codec == "latin-1" and size == len(late) and sha == hashlib.sha256(late).hexdigest()


def test_line_and_edifact_readers_across_boundaries():
    text = "one\r\ntwo\nthree\r\n\r\nfour"
    ref = list(stream.line_records(iter([text])))
    for size in range(1, len(text) + 1):
        assert list(stream.line_records(_chunks(text, size))) == ref, size
    assert [t for _n, _s, t in ref] == ["one", "two", "three", "", "four"]
    edi = "UNA:+.? 'UNB+UNOA:2+A+B'EQD+CN+MSKU9070323+22G1'FTX+AAA+++it?'s escaped'EQD+CN+CSQU3054383'UNZ+1'"
    ref = list(stream.edifact_segments(iter([edi]), "?", "'"))
    for size in range(1, len(edi) + 1):
        assert list(stream.edifact_segments(_chunks(edi, size), "?", "'")) == ref, size
    segs = [s for _o, s in ref]
    assert segs[2] == "FTX+AAA+++it?'s escaped" and segs[3].startswith("EQD+CN+CSQU")
    for off, seg in ref:
        assert edi[off:off + len(seg)] == seg
    plain = edi[9:]
    assert [s for _o, s in stream.edifact_segments(_chunks(plain, 4), "?", "'")] == segs


# --------------------------------------------------------------------------- #
# 2. Durable jobs: leases, crash recovery without double counting, cancel
# --------------------------------------------------------------------------- #

def test_job_resumes_after_crash_without_double_counting():
    root = _root()
    conn = _ws(root)
    path = _write(root, "fleet.csv", _fleet_csv(1000, wrong_every=10))
    sid = ingest.register_source(conn, root, "ws", "fleet.csv", path)
    iid, _ = ingest.create_intent(conn, "ws", sid, mode="full_snapshot", declared_record_count=1000)
    sub = runner.submit(conn, workspace_id="ws", source_file_id=sid, intent_id=iid, options={"columns": ["container"]})
    try:
        runner.run_once(conn, root, wid="w1", batch_records=100, fail_after_records=300, lease_seconds=0.2)
        assert False, "expected the simulated crash"
    except ingest._SimulatedCrash:
        pass
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (sub["job_id"],)).fetchone()
    assert job["state"] == "validating" and job["lease_owner"] == "w1"
    assert runner.run_once(conn, root, wid="w2", lease_seconds=5) is None, "lease still held: nothing claimable"
    time.sleep(0.25)
    r = runner.run_once(conn, root, wid="w2", batch_records=100)
    assert r is not None and r["state"] == "awaiting_review" and r["worker"] == "w2"
    c = jobq.counters(conn, sub["job_id"])
    assert c["records_total"] == 1000 and c["identifiers_total"] == 1000, c
    assert c["status_corrected"] == 100 and c["status_valid"] == 900, c
    n = conn.execute("SELECT COUNT(*) FROM observations WHERE job_id = ?", (sub["job_id"],)).fetchone()[0]
    assert n == 1000
    m = conn.execute("SELECT COUNT(*) FROM memberships WHERE generation_id = ?", (sub["generation_id"],)).fetchone()[0]
    assert m == 1000
    assert conn.execute("SELECT attempts FROM jobs WHERE id = ?", (sub["job_id"],)).fetchone()[0] == 2
    gen = reconcile.effects(conn, sub["generation_id"])
    assert gen["state"] == "publishable" and gen["effects"]["added"] == 1000


def test_cancellation_and_retry_exhaustion():
    root = _root()
    conn = _ws(root)
    path = _write(root, "fleet.csv", _fleet_csv(50))
    sid = ingest.register_source(conn, root, "ws", "fleet.csv", path)
    sub = runner.submit(conn, workspace_id="ws", source_file_id=sid, intent_id=None, options={"columns": ["container"]})
    jobq.cancel(conn, sub["job_id"])
    assert runner.run_once(conn, root) is None, "a cancelled job is never claimed"
    sub2 = runner.submit(conn, workspace_id="ws", source_file_id=sid, intent_id=None, options={"columns": ["nosuch"]})
    states = [runner.run_once(conn, root)["state"] for _ in range(3)]
    assert states == ["queued", "queued", "failed"], states
    job = conn.execute("SELECT attempts, error FROM jobs WHERE id = ?", (sub2["job_id"],)).fetchone()
    assert job["attempts"] == 3 and "not found in header" in job["error"]
    assert runner.run_once(conn, root) is None


def test_unsupported_format_is_refused_with_finding():
    root = _root()
    conn = _ws(root)
    path = _write(root, "x.xml", b"<?xml version='1.0'?><root/>")
    sid = ingest.register_source(conn, root, "ws", "x.xml", path)
    sub = runner.submit(conn, workspace_id="ws", source_file_id=sid, intent_id=None, options={})
    assert runner.run_once(conn, root)["state"] == "failed"
    assert review.summary(conn, sub["job_id"])["findings"] == {"UNSUPPORTED_INPUT": 1}


# --------------------------------------------------------------------------- #
# 3. Reconciliation scenarios
# --------------------------------------------------------------------------- #

def test_incremental_delta_leaves_the_rest_untouched():
    root = _root()
    conn = _ws(root)
    base = _run_full(conn, root, "ws", "base.csv", _fleet_csv(3000), mode="full_snapshot", declared=3000)
    pub = reconcile.publish(conn, base["generation_id"], "t", operation_id="p1")
    assert pub["state"] == "published" and reconcile.current_generation(conn, "ws") == base["generation_id"]
    delta = _run_full(conn, root, "ws", "delta.csv", _fleet_csv(200, start=2900), mode="incremental",
                      baseline=base["generation_id"])
    fx = reconcile.effects(conn, delta["generation_id"])["effects"]
    assert fx == {**fx, "added": 100, "unchanged": 100, "changed": 0, "removed": 2900, "retire": 0,
                  "retire_withheld": 0, "complete": True, "blocked": []}, fx
    pub = reconcile.publish(conn, delta["generation_id"], "t")
    assert pub["applied"] == {"upserted": 100, "retired": 0} and pub["fleet_count"] == 3100
    assert reconcile.current_generation(conn, "ws") == delta["generation_id"]
    assert conn.execute("SELECT state FROM generations WHERE id = ?", (base["generation_id"],)).fetchone()[0] == "superseded"
    by_gen = {r[0]: r[1] for r in conn.execute("SELECT generation_id, COUNT(*) FROM fleet GROUP BY generation_id")}
    assert by_gen == {base["generation_id"]: 3000, delta["generation_id"]: 100}, "untouched rows keep their origin"
    changed = _run_full(conn, root, "ws", "chg.csv", _fleet_csv(10).replace(b",r5\r\n", b",moved\r\n"),
                        mode="incremental", baseline=delta["generation_id"])
    fx = reconcile.effects(conn, changed["generation_id"])["effects"]
    assert fx["changed"] == 1 and fx["unchanged"] == 9 and fx["added"] == 0
    pub = reconcile.publish(conn, changed["generation_id"], "t")
    assert pub["applied"] == {"upserted": 1, "retired": 0} and pub["fleet_count"] == 3100
    stale = _run_full(conn, root, "ws", "stale.csv", _fleet_csv(5, start=5000), mode="incremental",
                      baseline=delta["generation_id"])
    fx = reconcile.effects(conn, stale["generation_id"])["effects"]
    assert fx["blocked"] == ["BASELINE_CONFLICT"], "a job analysed against a superseded baseline cannot publish"


def test_partial_or_quarantined_snapshot_retires_nothing():
    root = _root()
    conn = _ws(root)
    base = _run_full(conn, root, "ws", "base.csv", _fleet_csv(100), mode="full_snapshot", declared=100)
    reconcile.publish(conn, base["generation_id"], "t")
    short = _run_full(conn, root, "ws", "short.csv", _fleet_csv(60), mode="full_snapshot", declared=100,
                      baseline=base["generation_id"])
    fx = reconcile.effects(conn, short["generation_id"])
    assert fx["state"] == "candidate" and fx["effects"]["removed"] == 40 and fx["effects"]["retire"] == 0
    assert fx["effects"]["retire_withheld"] == 40 and "SNAPSHOT_INCOMPLETE" in fx["effects"]["blocked"]
    try:
        reconcile.publish(conn, short["generation_id"], "t")
        assert False
    except reconcile.PublishError as exc:
        assert exc.code == "PUBLICATION_BLOCKED" and "SNAPSHOT_INCOMPLETE" in exc.detail
    data = _fleet_csv(100).replace(b"MSKU000005", b"BADTOKEN12")
    quar = _run_full(conn, root, "ws", "quar.csv", data, mode="full_snapshot", declared=100, baseline=base["generation_id"])
    fx = reconcile.effects(conn, quar["generation_id"])["effects"]
    assert fx["retire"] == 0 and fx["retire_withheld"] == 1 and fx["blocked"] == ["SNAPSHOT_INCOMPLETE"]
    assert reconcile.current_generation(conn, "ws") == base["generation_id"]
    assert reconcile.fleet_count(conn, "ws") == 100
    rm = _run_full(conn, root, "ws", "rm.csv", _fleet_csv(7, start=3), mode="explicit_removal", baseline=base["generation_id"])
    fx = reconcile.effects(conn, rm["generation_id"])["effects"]
    assert fx["retire"] == 7 and fx["blocked"] == []
    pub = reconcile.publish(conn, rm["generation_id"], "t")
    assert pub["applied"] == {"upserted": 0, "retired": 7} and pub["fleet_count"] == 93


def test_empty_snapshot_needs_an_explicit_decision():
    root = _root()
    conn = _ws(root)
    base = _run_full(conn, root, "ws", "base.csv", _fleet_csv(10), mode="full_snapshot", declared=10)
    reconcile.publish(conn, base["generation_id"], "t")
    empty = _run_full(conn, root, "ws", "empty.csv", b"ref,container,remark\r\n", mode="full_snapshot", declared=0,
                      baseline=base["generation_id"])
    fx = reconcile.effects(conn, empty["generation_id"])["effects"]
    assert fx["blocked"] == ["EMPTY_SNAPSHOT_DECISION_REQUIRED"] and fx["retire"] == 0
    decided = _run_full(conn, root, "ws", "empty2.csv", b"ref,container,remark\r\n\r\n", mode="full_snapshot",
                        declared=0, baseline=base["generation_id"], empty_decision="scope_is_empty")
    fx = reconcile.effects(conn, decided["generation_id"])["effects"]
    assert fx["blocked"] == [] and fx["retire"] == 10 and fx["complete"] is True
    assert reconcile.fleet_count(conn, "ws") == 10
    pub = reconcile.publish(conn, decided["generation_id"], "t")
    assert pub["applied"] == {"upserted": 0, "retired": 10} and pub["fleet_count"] == 0
    removal = _run_full(conn, root, "ws", "rm.csv", _fleet_csv(3), mode="explicit_removal",
                        baseline=decided["generation_id"])
    assert reconcile.effects(conn, removal["generation_id"])["effects"]["retire"] == 0, "nothing to remove from an empty fleet"


def test_two_publishers_cannot_both_overwrite_and_replay_applies_once():
    root = _root()
    conn = _ws(root)
    base = _run_full(conn, root, "ws", "base.csv", _fleet_csv(20), mode="full_snapshot", declared=20)
    reconcile.publish(conn, base["generation_id"], "t")
    a = _run_full(conn, root, "ws", "a.csv", _fleet_csv(21), mode="full_snapshot", declared=21, baseline=base["generation_id"])
    b = _run_full(conn, root, "ws", "b.csv", _fleet_csv(22), mode="full_snapshot", declared=22, baseline=base["generation_id"])
    results = {}

    def publish(name, gid):
        c = store.connect(os.path.join(root, "ws.db"))
        try:
            results[name] = reconcile.publish(c, gid, name, operation_id=f"op-{name}")
        except reconcile.PublishError as exc:
            results[name] = exc.code
        finally:
            c.close()
    threads = [threading.Thread(target=publish, args=(n, g)) for n, g in (("a", a["generation_id"]), ("b", b["generation_id"]))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    outcomes = sorted(str(v) if isinstance(v, str) else v["state"] for v in results.values())
    assert outcomes == ["BASELINE_CONFLICT", "published"], results
    winner = [k for k, v in results.items() if not isinstance(v, str)][0]
    gid = (a if winner == "a" else b)["generation_id"]
    assert reconcile.current_generation(conn, "ws") == gid
    again = reconcile.publish(conn, gid, winner, operation_id=f"op-{winner}")
    assert again["replayed"] is True
    assert conn.execute("SELECT COUNT(*) FROM audit_events WHERE action = 'generation.publish'").fetchone()[0] == 2
    dup = runner.submit(conn, workspace_id="ws", source_file_id=conn.execute(
        "SELECT source_file_id FROM jobs WHERE id = ?", (a["job_id"],)).fetchone()[0],
        intent_id=conn.execute("SELECT import_intent_id FROM jobs WHERE id = ?", (a["job_id"],)).fetchone()[0],
        options={"columns": ["container"]})
    assert dup["duplicate"] is True and dup["job_id"] == a["job_id"]
    assert review.summary(conn, a["job_id"])["findings"].get("DUPLICATE_APPLICATION_PREVENTED") == 1


def test_identity_collision_and_comparison_only_intent():
    root = _root()
    conn = _ws(root)
    data = _fleet_csv(5) + b"9," + _bic("MSKU", 1).encode() + b",different\r\n"
    sub = _run_full(conn, root, "ws", "c.csv", data, mode="full_snapshot", declared=6)
    s = review.summary(conn, sub["job_id"])
    assert s["findings"].get("IDENTITY_COLLISION") == 1 and s["counters"]["identity_collisions"] == 1
    cmp_only = _run_full(conn, root, "ws", "c2.csv", _fleet_csv(5), mode="comparison_only")
    assert cmp_only["generation_id"] is None
    assert review.summary(conn, cmp_only["job_id"])["findings"] == {"IMPORT_INTENT_UNRESOLVED": 1}


# --------------------------------------------------------------------------- #
# 4. Storage-backed selections, approval validity, versions
# --------------------------------------------------------------------------- #

def test_selection_is_frozen_and_version_checked():
    root = _root()
    conn = _ws(root)
    sub = _run_full(conn, root, "ws", "f.csv", _fleet_csv(100, wrong_every=5), mode=None)
    job = sub["job_id"]
    assert review.count(conn, job, {"status": "corrected"}) == 20
    try:
        review.decide(conn, job, {"status": "corrected"}, "approved", "r1", expected_count=19)
        assert False
    except review.ReviewError as exc:
        assert exc.code == "SELECTION_DRIFT"
    apr = review.decide(conn, job, {"status": "corrected", "ordinal_to": 49}, "approved", "r1", expected_count=10,
                        operation_id="op-1")
    assert apr["selection_count"] == 10 and apr["state"] == "recorded"
    replay = review.decide(conn, job, {"status": "corrected"}, "approved", "r1", operation_id="op-1")
    assert replay["replayed"] is True and replay["selection_count"] == 10
    members = conn.execute("SELECT ordinal, version FROM selection_members WHERE approval_id = ? ORDER BY ordinal",
                           (apr["id"],)).fetchall()
    assert [m["ordinal"] for m in members] == [o for o in range(1, 50, 5)]
    assert all(m["version"] == 2 for m in members)
    digest = hashlib.sha256("".join(f"{m['ordinal']}:{m['version']}\n" for m in members).encode()).hexdigest()
    assert apr["selection_digest"] == digest
    assert review.check_approval(conn, apr["id"])["applicable"] is True
    rest = review.decide(conn, job, {"status": "corrected"}, "rejected", "r2")
    assert rest["selection_count"] == 10, "already-decided proposals are not re-decided"
    assert review.check_approval(conn, apr["id"])["applicable"] is True
    conn.execute("UPDATE observations SET version = version + 1 WHERE job_id = ? AND ordinal = 1", (job,))
    chk = review.check_approval(conn, apr["id"])
    assert chk["applicable"] is False and chk["problems"] == ["SELECTION_DRIFT"] and chk["drifted_members"] == 1
    try:
        export_mod.build_artifact(conn, root, job, apr["id"], "r1", idempotency_key="k")
        assert False
    except export_mod.ExportError as exc:
        assert exc.code == "SELECTION_DRIFT"
    assert conn.execute("SELECT state FROM export_artifacts").fetchone() is None


def test_reanalysis_stales_approvals():
    root = _root()
    conn = _ws(root)
    sub = _run_full(conn, root, "ws", "f.csv", _fleet_csv(30, wrong_every=3), mode=None)
    apr = review.decide(conn, sub["job_id"], {"status": "corrected"}, "approved", "r1")
    assert review.reanalyze(conn, sub["job_id"], "policy changed") == 1
    chk = review.check_approval(conn, apr["id"])
    assert chk["applicable"] is False and "APPROVAL_STALE" in chk["problems"]
    a = conn.execute("SELECT state, stale_reason FROM approvals WHERE id = ?", (apr["id"],)).fetchone()
    assert a["state"] == "stale" and a["stale_reason"] == "policy changed"
    try:
        export_mod.build_artifact(conn, root, sub["job_id"], apr["id"], "r1", idempotency_key="k")
        assert False
    except export_mod.ExportError as exc:
        assert exc.code == "APPROVAL_STALE"


# --------------------------------------------------------------------------- #
# 5. Export: parity with the whole-file corrector, atomic publication
# --------------------------------------------------------------------------- #

def test_streamed_export_matches_whole_file_corrector_and_is_atomic():
    root = _root()
    conn = _ws(root)
    rows = ["ref,container,remark"]
    for i in range(400):
        c = _bic("TCLU", i)
        if i % 4 == 0:
            c = c[:-1] + str((int(c[-1]) + 3) % 10)
        if i % 9 == 0:
            c = f'"{c}"'
        remark = ['"multi\r\nline"', "é 😀 北京", "plain", '"q ""x"""'][i % 4]
        rows.append(f"{i + 1},{c},{remark}")
    text = "\r\n".join(rows[:200]) + "\n" + "\r\n".join(rows[200:]) + "\r\n"
    data = text.encode("utf-8")
    ref = csv_corrector.correct_csv(text, ["container"], trust=True)
    sub = _run_full(conn, root, "ws", "big.csv", data, mode=None)
    job = sub["job_id"]
    n = review.count(conn, job, {"status": "corrected"})
    assert n == len(ref.corrected) == 100, (n, len(ref.corrected))
    apr = review.decide(conn, job, {"status": "corrected"}, "approved", "r", expected_count=n)
    try:
        export_mod.build_artifact(conn, root, job, apr["id"], "r", idempotency_key="k1", fail_after_edits=40)
        assert False
    except export_mod.ExportError as exc:
        assert exc.code == "SIMULATED_INTERRUPTION"
    row = conn.execute("SELECT state, path, sha256 FROM export_artifacts WHERE idempotency_key = 'k1'").fetchone()
    assert row["state"] == "failed" and row["sha256"] is None
    try:
        export_mod.artifact_file(conn, conn.execute("SELECT id FROM export_artifacts").fetchone()[0], "corrected")
        assert False
    except export_mod.ExportError as exc:
        assert exc.code == "ARTIFACT_NOT_READY"
    assert conn.execute("SELECT state FROM jobs WHERE id = ?", (job,)).fetchone()[0] == "awaiting_review"
    art = export_mod.build_artifact(conn, root, job, apr["id"], "r", idempotency_key="k1")
    assert art["state"] == "ready" and art["edits_applied"] == 100 and art["replayed"] is False
    out = open(export_mod.artifact_file(conn, art["id"], "corrected"), "rb").read()
    assert out.decode("utf-8") == ref.corrected_text
    assert hashlib.sha256(out).hexdigest() == art["sha256"] and len(out) == art["size_bytes"]
    again = export_mod.build_artifact(conn, root, job, apr["id"], "r", idempotency_key="k1")
    assert again["replayed"] is True and again["sha256"] == art["sha256"]
    assert conn.execute("SELECT COUNT(*) FROM export_artifacts").fetchone()[0] == 1
    manifest = json.load(open(export_mod.artifact_file(conn, art["id"], "manifest"), encoding="utf-8"))
    assert manifest["output"]["sha256"] == art["sha256"] and manifest["source"]["sha256"] == hashlib.sha256(data).hexdigest()
    ledger = open(export_mod.artifact_file(conn, art["id"], "ledger"), encoding="utf-8").read().splitlines()
    assert len(ledger) == 101
    assert sorted(os.listdir(art["path"])) == ["big.CORRECTED.csv", "exceptions.csv", "ledger.csv", "manifest.json"]
    assert conn.execute("SELECT state FROM jobs WHERE id = ?", (job,)).fetchone()[0] == "completed"


def test_mapped_column_evaluates_every_cell_with_kernel_normalization():
    """A declared identifier column is evaluated cell by cell: presentational
    spaces and hyphens are normalized by the kernel and the whole trimmed cell
    is the splice span. The whole-file scanner (csv_locator, whole_cell=True)
    only locates bare 4+7 tokens; the two paths differ here on purpose."""
    root = _root()
    conn = _ws(root)
    good = _bic("TCLU", 7)
    bad = good[:-1] + str((int(good[-1]) + 1) % 10)
    data = ("ref,container\r\n"
            f"1, {bad[:4]} {bad[4:10]} {bad[10]} \r\n"
            f"2,{bad[:4]}-{bad[4:]}\r\n"
            "3,  \r\n").encode("utf-8")
    sub = _run_full(conn, root, "ws", "sp.csv", data, mode=None)
    page = review.observations_page(conn, sub["job_id"], {})["items"]
    assert [o["status"] for o in page] == ["corrected", "corrected", "invalid_structure"]
    assert page[0]["raw"] == f"{bad[:4]} {bad[4:10]} {bad[10]}" and page[0]["char_offset"] == len("ref,container\r\n1, ")
    apr = review.decide(conn, sub["job_id"], {"status": "corrected"}, "approved", "r")
    art = export_mod.build_artifact(conn, root, sub["job_id"], apr["id"], "r", idempotency_key="sp")
    out = open(export_mod.artifact_file(conn, art["id"], "corrected"), encoding="utf-8", newline="").read()
    assert out == f"ref,container\r\n1, {good} \r\n2,{good}\r\n3,  \r\n"


def test_export_without_approval_changes_nothing():
    root = _root()
    conn = _ws(root)
    data = _fleet_csv(20, wrong_every=2)
    sub = _run_full(conn, root, "ws", "f.csv", data, mode=None)
    art = export_mod.build_artifact(conn, root, sub["job_id"], None, "r", idempotency_key="none")
    assert art["edits_applied"] == 0 and art["sha256"] == hashlib.sha256(data).hexdigest()
    exc = open(export_mod.artifact_file(conn, art["id"], "exceptions"), encoding="utf-8").read().splitlines()
    assert len(exc) == 11, "10 undecided proposals are listed as exceptions"


def test_edifact_streaming_job():
    root = _root()
    conn = _ws(root)
    segs = ["UNB+UNOA:2+A+B+200101:1200+1", "UNH+1+BAPLIE:D:95B:UN:SMDG20"]
    expect = []
    for i in range(50):
        c = _bic("HLCU", i)
        if i % 5 == 0:
            c = c[:-1] + str((int(c[-1]) + 1) % 10)
            expect.append(c)
        segs.append(f"LOC+147+0{i:05d}::5")
        segs.append(f"EQD+CN+{c}+22G1+++5")
        segs.append("FTX+AAA+++free?'text with MSKU9070320 that is not EQD")
    segs += ["UNT+2+1", "UNZ+1+1"]
    text = "'\n".join(segs) + "'\n"
    sub = _run_full(conn, root, "ws", "bay.edi", text.encode("utf-8"), mode=None, options={"columns": []})
    s = review.summary(conn, sub["job_id"])
    assert s["counters"]["identifiers_total"] == 50 and s["counters"]["status_corrected"] == 10, s["counters"]
    apr = review.decide(conn, sub["job_id"], {"status": "corrected"}, "approved", "r")
    art = export_mod.build_artifact(conn, root, sub["job_id"], apr["id"], "r", idempotency_key="e")
    out = open(export_mod.artifact_file(conn, art["id"], "corrected"), encoding="utf-8").read()
    import edifact_corrector
    ref = edifact_corrector.correct_edifact(text)
    assert out == ref.corrected_text and "MSKU9070320" in out


# --------------------------------------------------------------------------- #
# 6. Versioned API
# --------------------------------------------------------------------------- #

def test_api_upload_job_review_export_with_cursors():
    if not HAVE_FASTAPI:
        print("  (fastapi missing: skipped)")
        return
    root = _root()
    app = FastAPI()
    app.include_router(build_router(lambda: None, root))
    client = TestClient(app)
    data = _fleet_csv(500, wrong_every=10)
    r = client.post("/v1/workspaces/acme/uploads", json={"filename": "fleet.csv", "size": len(data)})
    up = r.json()["upload_id"]
    r = client.put(f"/v1/workspaces/acme/uploads/{up}?offset=0", content=data[:300])
    assert r.status_code == 200 and r.json()["received_bytes"] == 300
    r = client.put(f"/v1/workspaces/acme/uploads/{up}?offset=100", content=data[100:300])
    assert r.status_code == 409 and r.json()["detail"]["expected_offset"] == 300
    r = client.post(f"/v1/workspaces/acme/uploads/{up}/complete", json={})
    assert r.status_code == 409
    r = client.put(f"/v1/workspaces/acme/uploads/{up}?offset=300", content=data[300:])
    assert r.status_code == 200
    r = client.post(f"/v1/workspaces/acme/uploads/{up}/complete", json={"sha256": "00"})
    assert r.status_code == 422
    r = client.post(f"/v1/workspaces/acme/uploads/{up}/complete", json={"sha256": hashlib.sha256(data).hexdigest()})
    assert r.status_code == 200, r.text
    sid = r.json()["source_file_id"]
    assert client.post(f"/v1/workspaces/acme/uploads/{up}/complete", json={}).json()["replayed"] is True
    r = client.post("/v1/workspaces/acme/intents", json={"source_file_id": sid, "mode": "full_snapshot",
                                                          "declared_record_count": 500})
    iid = r.json()["import_intent_id"]
    assert client.post("/v1/workspaces/acme/intents", json={"source_file_id": sid, "mode": "full_snapshot",
                                                             "declared_record_count": 500}).json()["duplicate"] is True
    assert client.post("/v1/workspaces/other/intents", json={"source_file_id": sid, "mode": "full_snapshot"}).status_code == 404
    r = client.post("/v1/workspaces/acme/jobs", json={"source_file_id": sid, "import_intent_id": iid,
                                                       "options": {"columns": ["container"]}})
    assert r.status_code == 202
    job = r.json()["job_id"]
    gen = r.json()["generation_id"]
    r = client.post(f"/v1/workspaces/acme/jobs/{job}/run")
    assert r.json()["state"] == "awaiting_review", r.text
    assert client.get(f"/v1/workspaces/other/jobs/{job}").status_code == 404
    seen = []
    cursor = "-1"
    while True:
        r = client.get(f"/v1/workspaces/acme/jobs/{job}/observations", params={"status": "corrected", "limit": 7, "cursor": cursor})
        page = r.json()
        seen += [it["ordinal"] for it in page["items"]]
        if page["next_cursor"] is None:
            break
        cursor = page["next_cursor"]
    assert seen == list(range(1, 500, 10))
    r = client.post(f"/v1/workspaces/acme/jobs/{job}/decisions", json={"filter": {"status": "corrected"}, "decision": "approved",
                                                                        "expected_count": 49, "operation_id": "d1"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SELECTION_DRIFT"
    r = client.post(f"/v1/workspaces/acme/jobs/{job}/decisions", json={"filter": {"status": "corrected"}, "decision": "approved",
                                                                        "expected_count": 50, "operation_id": "d1"})
    assert r.status_code == 200, r.text
    apr = r.json()["id"]
    r = client.post(f"/v1/workspaces/acme/jobs/{job}/exports", json={"approval_id": apr, "idempotency_key": "x1"})
    assert r.status_code == 201, r.text
    art = r.json()
    assert art["state"] == "ready" and art["edits_applied"] == 50
    r = client.get(f"/v1/workspaces/acme/artifacts/{art['id']}/files/corrected")
    assert r.status_code == 200 and hashlib.sha256(r.content).hexdigest() == art["sha256"]
    assert client.get(f"/v1/workspaces/acme/artifacts/{art['id']}/files/nope").status_code == 404
    r = client.get(f"/v1/workspaces/acme/generations/{gen}")
    assert r.json()["state"] == "publishable"
    r = client.post(f"/v1/workspaces/acme/generations/{gen}/publish", json={"operation_id": "pub1"})
    assert r.status_code == 200 and r.json()["state"] == "published"
    assert client.post(f"/v1/workspaces/acme/generations/{gen}/publish", json={"operation_id": "pub1"}).json()["replayed"] is True
    r = client.get(f"/v1/workspaces/acme/generations/{gen}/members", params={"limit": 200})
    assert len(r.json()["items"]) == 200 and r.json()["next_cursor"]
    assert client.get("/v1/workspaces/acme").json()["current_generation_id"] == gen
    r = client.post(f"/v1/workspaces/acme/jobs/{job}/reanalyze")
    assert r.json()["approvals_stale"] == 1
    assert client.get(f"/v1/workspaces/acme/approvals/{apr}").json()["check"]["applicable"] is False


# --------------------------------------------------------------------------- #
# Plain-script runner
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    tests = [v for kname, v in sorted(globals().items()) if kname.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
