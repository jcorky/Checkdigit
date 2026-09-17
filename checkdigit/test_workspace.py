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
    from fastapi import FastAPI, Request
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
              declared=None, empty_decision=None, options=None, scope_kind="source_fleet", scope_value="all", **kw):
    path = _write(root, filename, data)
    sid = ingest.register_source(conn, root, ws, filename, path)
    iid = None
    if mode:
        iid, _dup = ingest.create_intent(conn, ws, sid, mode=mode, baseline_generation_id=baseline,
                                         declared_record_count=declared, empty_scope_decision=empty_decision,
                                         scope_kind=scope_kind, scope_value=scope_value, **kw)
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
    path = _write(root, "x.zip", b"PK\x03\x04" + b"\x00" * 60)
    sid = ingest.register_source(conn, root, "ws", "x.zip", path)
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
    assert review.reanalyze(conn, sub["job_id"], "policy changed")["approvals_stale"] == 1
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
    assert art["state"] == "ready" and art["counts"]["edits_applied"] == 50
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
    assert r.status_code == 409 and r.json()["detail"]["code"] == "GENERATION_PUBLISHED"
    assert client.get(f"/v1/workspaces/acme/approvals/{apr}").json()["check"]["applicable"] is True


# --------------------------------------------------------------------------- #
# 7. Coverage scope, attribute columns, operation ids, reopening
# --------------------------------------------------------------------------- #

def test_full_snapshot_retires_only_within_its_scope():
    root = _root()
    conn = _ws(root)
    a = _run_full(conn, root, "ws", "a.csv", _fleet_csv(100), mode="full_snapshot", declared=100,
                  scope_kind="terminal", scope_value="USLAX")
    reconcile.publish(conn, a["generation_id"], "t")
    b = _run_full(conn, root, "ws", "b.csv", _fleet_csv(50, start=1000), mode="full_snapshot", declared=50,
                  scope_kind="terminal", scope_value="USOAK", baseline=a["generation_id"])
    fx = reconcile.effects(conn, b["generation_id"])["effects"]
    assert fx["removed"] == 0 and fx["retire"] == 0 and fx["baseline_in_scope"] == 0 and fx["baseline"] == 100
    pub = reconcile.publish(conn, b["generation_id"], "t")
    assert pub["fleet_count"] == 150 and pub["fleet_in_scope"] == 50
    # a smaller USLAX snapshot retires only USLAX members
    a2 = _run_full(conn, root, "ws", "a2.csv", _fleet_csv(90), mode="full_snapshot", declared=90,
                   scope_kind="terminal", scope_value="USLAX", baseline=b["generation_id"])
    fx = reconcile.effects(conn, a2["generation_id"])["effects"]
    assert fx["removed"] == 10 and fx["retire"] == 10 and fx["unchanged"] == 90
    pub = reconcile.publish(conn, a2["generation_id"], "t")
    assert pub["applied"] == {"upserted": 0, "retired": 10} and pub["fleet_count"] == 140
    assert reconcile.fleet_count(conn, "ws", "terminal", "USOAK") == 50
    # a member listed by another terminal's snapshot moves scope instead of being duplicated
    moved = _run_full(conn, root, "ws", "mv.csv", _fleet_csv(50, start=1000) + _fleet_csv(1)[len("ref,container,remark\r\n"):],
                      mode="full_snapshot", declared=51, scope_kind="terminal", scope_value="USOAK",
                      baseline=a2["generation_id"])
    fx = reconcile.effects(conn, moved["generation_id"])["effects"]
    assert fx["changed"] == 1 and fx["unchanged"] == 50 and fx["added"] == 0
    pub = reconcile.publish(conn, moved["generation_id"], "t")
    assert pub["fleet_count"] == 140 and pub["fleet_in_scope"] == 51
    assert reconcile.fleet_member(conn, "ws", _bic("MSKU", 0))["scope_value"] == "USOAK"
    assert reconcile.fleet_count(conn, "ws", "terminal", "USLAX") == 89
    try:
        ingest.create_intent(conn, "ws", conn.execute("SELECT source_file_id FROM jobs LIMIT 1").fetchone()[0],
                             mode="full_snapshot", scope_kind="fleet")
        assert False
    except ingest.IngestError as exc:
        assert "scope kind" in str(exc)


def test_attribute_columns_option_limits_identity_comparison():
    root = _root()
    conn = _ws(root)
    data = ("ref,container,size,remark\r\n" + "".join(f"{i},{_bic('MSKU', i)},22G1,r{i}\r\n" for i in range(20))).encode()
    base = _run_full(conn, root, "ws", "base.csv", data, mode="full_snapshot", declared=20,
                     options={"attribute_columns": ["size"]})
    reconcile.publish(conn, base["generation_id"], "t")
    changed = data.replace(b",r5\r\n", b",moved\r\n").replace(b"5,", b"55,", 1)
    again = _run_full(conn, root, "ws", "again.csv", changed, mode="full_snapshot", declared=20,
                      baseline=base["generation_id"], options={"attribute_columns": ["size"]})
    fx = reconcile.effects(conn, again["generation_id"])["effects"]
    assert fx["unchanged"] == 20 and fx["changed"] == 0, "row number and remark are not identity attributes"
    sized = data.replace(b",22G1,r5", b",45G1,r5")
    third = _run_full(conn, root, "ws", "third.csv", sized, mode="full_snapshot", declared=20,
                      baseline=base["generation_id"], options={"attribute_columns": ["size"]})
    fx = reconcile.effects(conn, third["generation_id"])["effects"]
    assert fx["changed"] == 1 and fx["unchanged"] == 19
    dup = data + f"21,{_bic('MSKU', 3)},22G1,other\r\n".encode()
    d = _run_full(conn, root, "ws", "dup.csv", dup, mode="full_snapshot", declared=21, baseline=base["generation_id"],
                  options={"attribute_columns": ["size"]})
    assert review.summary(conn, d["job_id"])["counters"].get("identity_collisions", 0) == 0


def test_operation_ids_are_job_scoped_and_rejected_rows_can_be_reopened():
    root = _root()
    conn = _ws(root)
    j1 = _run_full(conn, root, "ws", "one.csv", _fleet_csv(20, wrong_every=2), mode=None)
    j2 = _run_full(conn, root, "ws", "two.csv", _fleet_csv(20, wrong_every=2, start=500), mode=None)
    a1 = review.decide(conn, j1["job_id"], {"status": "corrected"}, "rejected", "r", operation_id="same")
    a2 = review.decide(conn, j2["job_id"], {"status": "corrected"}, "approved", "r", operation_id="same")
    assert a1["id"] != a2["id"] and a1["operation_id"] == a2["operation_id"] == "same"
    assert review.decide(conn, j1["job_id"], {}, "approved", "r", operation_id="same")["replayed"] is True
    try:
        review.decide(conn, j1["job_id"], {"status": "corrected"}, "approved", "r")
        assert False
    except review.ReviewError as exc:
        assert exc.code == "EMPTY_SELECTION"
    reopened = review.decide(conn, j1["job_id"], {"status": "corrected", "decision": "rejected"}, "approved", "r",
                             expected_count=10)
    assert reopened["selection_count"] == 10
    assert review.count(conn, j1["job_id"], {"decision": "approved"}) == 10
    art = export_mod.build_artifact(conn, root, j1["job_id"], reopened["id"], "r", idempotency_key="k")
    assert art["edits_applied"] == 10


# --------------------------------------------------------------------------- #
# 8. Re-analysis with the operator policy
# --------------------------------------------------------------------------- #

def test_reanalysis_reruns_the_kernel_under_the_current_policy():
    import policy as policy_mod
    root = _root()
    conn = _ws(root)
    sub = _run_full(conn, root, "ws", "f.csv", _fleet_csv(30, wrong_every=3), mode="incremental")
    job = sub["job_id"]
    apr = review.decide(conn, job, {"status": "corrected"}, "approved", "r", expected_count=10)
    deny = policy_mod.Policy.from_dict({"default_policy": "strict", "deny": ["MSK*"]})
    out = review.reanalyze(conn, job, "deny MSK", policy=deny)
    assert out["approvals_stale"] == 1 and out["changed"] == 30 and out["analysis_version"] == 2
    s = review.summary(conn, job)
    assert s["counters"]["status_flagged"] == 30 and s["counters"].get("status_corrected", 0) == 0
    assert s["decisions"] == {}, "no proposals remain under the deny policy"
    assert conn.execute("SELECT policy_fingerprint FROM jobs WHERE id = ?", (job,)).fetchone()[0] == ingest.policy_fingerprint(deny)
    assert "APPROVAL_STALE" in review.check_approval(conn, apr["id"])["problems"]
    gen = reconcile.effects(conn, sub["generation_id"])
    assert gen["state"] == "publishable" and gen["member_count"] == 30
    back = review.reanalyze(conn, job, "policy removed")
    assert back["changed"] == 30 and back["analysis_version"] == 3
    s = review.summary(conn, job)
    assert s["counters"]["status_corrected"] == 10 and s["decisions"] == {"proposed": 10}
    apr2 = review.decide(conn, job, {"status": "corrected"}, "approved", "r", expected_count=10)
    art = export_mod.build_artifact(conn, root, job, apr2["id"], "r", idempotency_key="after")
    assert art["edits_applied"] == 10
    reconcile.publish(conn, sub["generation_id"], "t")
    try:
        review.reanalyze(conn, job, "too late")
        assert False
    except review.ReviewError as exc:
        assert exc.code == "GENERATION_PUBLISHED"


# --------------------------------------------------------------------------- #
# 9. Roles, contract documents, budgets, retention
# --------------------------------------------------------------------------- #

def _client(root, identity=None):
    app = FastAPI()
    if identity is None:
        app.include_router(build_router(lambda: None, root))
    else:
        def ident(request: Request) -> str:
            return request.headers.get("x-user", identity)
        app.include_router(build_router(ident, root))
    return TestClient(app)


def _api_job(client, ws="acme", n=100, wrong_every=10, user=None, mode="full_snapshot"):
    h = {"x-user": user} if user else {}
    data = _fleet_csv(n, wrong_every=wrong_every)
    up = client.post(f"/v1/workspaces/{ws}/uploads", json={"filename": "fleet.csv", "size": len(data)}, headers=h).json()["upload_id"]
    assert client.put(f"/v1/workspaces/{ws}/uploads/{up}?offset=0", content=data, headers=h).status_code == 200
    sid = client.post(f"/v1/workspaces/{ws}/uploads/{up}/complete", json={}, headers=h).json()["source_file_id"]
    iid = client.post(f"/v1/workspaces/{ws}/intents", json={"source_file_id": sid, "mode": mode, "declared_record_count": n,
                                                             "scope_kind": "terminal", "scope_value": "USLAX"}, headers=h).json()["import_intent_id"]
    r = client.post(f"/v1/workspaces/{ws}/jobs", json={"source_file_id": sid, "import_intent_id": iid,
                                                       "options": {"columns": ["container"]}}, headers=h)
    job = r.json()["job_id"]
    assert client.post(f"/v1/workspaces/{ws}/jobs/{job}/run", headers=h).json()["state"] == "awaiting_review"
    return sid, iid, job, r.json()["generation_id"]


def test_roles_are_enforced_per_workspace():
    if not HAVE_FASTAPI:
        return
    root = _root()
    client = _client(root, identity="root")
    ws = "/v1/workspaces/acme"
    assert client.get(ws).status_code == 200, "no assignments: any admin-session user administers"
    assert client.put(f"{ws}/roles/root", json={"role": "administrator"}).status_code == 200
    assert client.put(f"{ws}/roles/bob", json={"role": "viewer"}).status_code == 200
    assert client.put(f"{ws}/roles/rita", json={"role": "reviewer"}).status_code == 200
    assert client.put(f"{ws}/roles/x", json={"role": "boss"}).status_code == 422
    _sid, _iid, job, gen = _api_job(client)
    assert client.get(f"{ws}/jobs/{job}", headers={"x-user": "bob"}).status_code == 200
    assert client.get(f"{ws}/jobs/{job}", headers={"x-user": "carol"}).status_code == 403
    assert client.get("/v1/workspaces/other", headers={"x-user": "carol"}).status_code == 200, "other workspace has no roles"
    r = client.post(f"{ws}/jobs/{job}/decisions", json={"filter": {"status": "corrected"}, "decision": "approved"},
                    headers={"x-user": "bob"})
    assert r.status_code == 403 and r.json()["detail"]["required"] == "reviewer"
    r = client.post(f"{ws}/jobs/{job}/decisions", json={"filter": {"status": "corrected"}, "decision": "approved"},
                    headers={"x-user": "rita"})
    assert r.status_code == 200 and r.json()["approver"] == "rita"
    assert client.post(f"{ws}/generations/{gen}/publish", json={}, headers={"x-user": "rita"}).status_code == 403
    assert client.post(f"{ws}/generations/{gen}/publish", json={}, headers={"x-user": "root"}).status_code == 200
    assert client.post(f"{ws}/uploads", json={"filename": "x", "size": 1}, headers={"x-user": "bob"}).status_code == 403
    assert client.delete(f"{ws}/roles/bob", headers={"x-user": "rita"}).status_code == 403
    assert client.delete(f"{ws}/roles/bob").status_code == 200
    assert client.get(f"{ws}/jobs/{job}", headers={"x-user": "bob"}).status_code == 403
    assert [r["role"] for r in client.get(f"{ws}/roles").json()["items"]] == ["reviewer", "administrator"]


def test_api_documents_validate_against_the_shared_contracts():
    if not HAVE_FASTAPI:
        return
    import contracts
    root = _root()
    client = _client(root)
    ws = "/v1/workspaces/acme"
    sid, iid, job, gen = _api_job(client)
    apr = client.post(f"{ws}/jobs/{job}/decisions", json={"filter": {"status": "corrected"}, "decision": "approved",
                                                         "operation_id": "d1"}).json()
    art = client.post(f"{ws}/jobs/{job}/exports", json={"approval_id": apr["id"], "idempotency_key": "e1"}).json()
    docs = {
        "workspace": client.get(ws).json(),
        "source_file": client.get(f"{ws}/sources/{sid}").json(),
        "import_intent": client.get(f"{ws}/intents/{iid}").json(),
        "job": client.get(f"{ws}/jobs/{job}").json(),
        "generation": client.get(f"{ws}/generations/{gen}").json(),
        "change_set": client.get(f"{ws}/jobs/{job}/change-set").json(),
        "approval": client.get(f"{ws}/approvals/{apr['id']}").json(),
        "export_artifact": client.get(f"{ws}/artifacts/{art['id']}").json(),
    }
    for name, doc in docs.items():
        problems = contracts.validate_entity(name, doc)
        assert not problems, (name, problems)
    findings = client.get(f"{ws}/jobs/{job}/findings").json()["items"]
    for f in findings:
        assert not contracts.validate_entity("finding", f), f
    assert docs["job"]["dependency_pins"]["parser_version"] == ingest.PARSER_VERSION
    assert docs["approval"]["selection_manifest"]["count"] == 10 and docs["approval"]["state"] == "approved"
    assert docs["export_artifact"]["lineage"]["source_file_id"] == sid
    assert docs["generation"]["scope"] == {"kind": "terminal", "value": "USLAX"}
    assert docs["import_intent"]["job_id"] == job
    roles = client.get(f"{ws}/roles").json()["items"]
    assert roles == []
    client.put(f"{ws}/roles/admin", json={"role": "administrator"})   # "admin" is the anonymous identity here
    for r in client.get(f"{ws}/roles").json()["items"]:
        assert not contracts.validate_entity("user_role", r)


def test_storage_budget_and_disk_pressure_fail_cleanly():
    root = _root()
    conn = _ws(root)
    path = _write(root, "f.csv", _fleet_csv(200, wrong_every=4))
    try:
        ingest.register_source(conn, root, "ws", "f.csv", path, max_bytes=100)
        assert False
    except store.BudgetExceeded as exc:
        assert "budget" in exc.detail
    assert not os.path.exists(os.path.join(root, "sources", "ws")) or not os.listdir(os.path.join(root, "sources", "ws"))
    sid = ingest.register_source(conn, root, "ws", "f.csv", path)
    sub = runner.submit(conn, workspace_id="ws", source_file_id=sid, intent_id=None, options={"columns": ["container"]})
    r = runner.run_once(conn, root, min_free_bytes=1 << 62)
    assert r["state"] == "failed"
    s = review.summary(conn, sub["job_id"])
    assert s["findings"] == {"RESOURCE_BUDGET_EXCEEDED": 1} and s["attempts"] == 1, "budget failures do not retry"
    sub2 = runner.submit(conn, workspace_id="ws", source_file_id=sid, intent_id=None, options={"columns": ["container"]})
    assert runner.run_once(conn, root)["state"] == "awaiting_review"
    apr = review.decide(conn, sub2["job_id"], {"status": "corrected"}, "approved", "r")
    try:
        export_mod.build_artifact(conn, root, sub2["job_id"], apr["id"], "r", idempotency_key="k", min_free_bytes=1 << 62)
        assert False
    except export_mod.ExportError as exc:
        assert exc.code == "RESOURCE_BUDGET_EXCEEDED"
    row = conn.execute("SELECT state, error, sha256 FROM export_artifacts WHERE idempotency_key = 'k'").fetchone()
    assert row["state"] == "failed" and row["error"].startswith("RESOURCE_BUDGET_EXCEEDED") and row["sha256"] is None
    assert review.summary(conn, sub2["job_id"])["findings"].get("RESOURCE_BUDGET_EXCEEDED") == 1
    assert conn.execute("SELECT state FROM jobs WHERE id = ?", (sub2["job_id"],)).fetchone()[0] == "awaiting_review"
    art = export_mod.build_artifact(conn, root, sub2["job_id"], apr["id"], "r", idempotency_key="k")
    assert art["state"] == "ready" and art["edits_applied"] == 50
    if HAVE_FASTAPI:
        client = _client(root)
        saved = store.MAX_BYTES
        store.MAX_BYTES = 1000
        try:
            r = client.post("/v1/workspaces/acme/uploads", json={"filename": "big.csv", "size": 5000})
            assert r.status_code == 413 and r.json()["detail"]["code"] == "RESOURCE_BUDGET_EXCEEDED"
            assert client.get("/v1/workspaces/acme/usage").json()["budget_bytes"] == 1000
        finally:
            store.MAX_BYTES = saved


def test_purge_applies_retention_and_keeps_history():
    import datetime as dt
    from workspace import maintenance
    root = _root()
    conn = _ws(root)
    base = _run_full(conn, root, "ws", "base.csv", _fleet_csv(40, wrong_every=4), mode="full_snapshot", declared=40)
    reconcile.publish(conn, base["generation_id"], "t")
    job = base["job_id"]
    apr = review.decide(conn, job, {"status": "corrected"}, "approved", "r")
    first = export_mod.build_artifact(conn, root, job, apr["id"], "r", idempotency_key="one")
    second = export_mod.build_artifact(conn, root, job, apr["id"], "r", idempotency_key="two")
    assert conn.execute("SELECT state FROM export_artifacts WHERE id = ?", (first["id"],)).fetchone()[0] == "superseded"
    assert second["previous_artifact_id"] == first["id"]
    assert export_mod.artifact_file(conn, first["id"], "corrected")
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
    out = maintenance.purge(conn, root, "ws", now=later, retention_days=0)
    assert out["artifact_files"] == 1 and out["jobs"] == 1 and out["sources"] == 1 and out["bytes_freed"] > 0
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM export_artifacts").fetchone()[0] == 0
    assert reconcile.fleet_count(conn, "ws") == 40, "published state is kept"
    g = conn.execute("SELECT state, job_id FROM generations WHERE id = ?", (base["generation_id"],)).fetchone()
    assert g["state"] == "published" and g["job_id"] is None
    assert conn.execute("SELECT COUNT(*) FROM memberships WHERE generation_id = ?", (base["generation_id"],)).fetchone()[0] == 40
    assert conn.execute("SELECT COUNT(*) FROM audit_events WHERE action = 'job.purge'").fetchone()[0] == 1
    assert store.usage(root, "ws")["total"] == 0
    kept = _run_full(conn, root, "ws", "k.csv", _fleet_csv(5), mode=None)
    out = maintenance.purge(conn, root, "ws", now=later, retention_days=30)
    assert out["jobs"] == 0 and conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1, kept


# --------------------------------------------------------------------------- #
# 10. X12 and container XML on the streaming path
# --------------------------------------------------------------------------- #

X12_SYNTH = (
    "ST*322*0001~\n"
    + "*".join(["N7", "MSCU", "123456"] + [""] * 15 + ["0"] + [""] * 3 + ["22G1"]) + "~\n"
    + "*".join(["N7", "TCLU", "456789"] + [""] * 15 + [""] + [""] * 3 + ["45G1"]) + "~\n"
    + "N7*HLBU*112233~\n"
    + "N9*EQ*MSCU1234560~\n"
    + "N9*BM*SSLMSCU1234560001~\n"
    + "SE*6*0001~\n"
)


def test_x12_streaming_matches_whole_file_corrector():
    import x12_corrector
    root = _root()
    conn = _ws(root)
    here = os.path.dirname(os.path.abspath(__file__))
    real = open(os.path.join(here, "samples", "Example_1_X12.edi"), encoding="utf-8").read()
    for name, text, options in (("synth.edi", X12_SYNTH, {"format": "x12"}), ("real.edi", real, {})):
        ref = x12_corrector.correct_x12(text)
        sub = _run_full(conn, root, "ws", name, text.encode("utf-8"), mode=None, options=options)
        s = review.summary(conn, sub["job_id"])
        assert s["counters"].get("status_corrected", 0) == len(ref.corrected), (name, s["counters"], len(ref.corrected))
        assert s["counters"].get("status_flagged", 0) == len(ref.flagged), (name, s["counters"], len(ref.flagged))
        if ref.corrected:
            apr = review.decide(conn, sub["job_id"], {"status": "corrected"}, "approved", "r")
            art = export_mod.build_artifact(conn, root, sub["job_id"], apr["id"], "r", idempotency_key=name)
            out = open(export_mod.artifact_file(conn, art["id"], "corrected"), encoding="utf-8", newline="").read()
            assert out == ref.corrected_text, name
    page = review.observations_page(conn, conn.execute("SELECT id FROM jobs ORDER BY created_at LIMIT 1").fetchone()[0], {})["items"]
    tokens = [(o["token"], o["raw"], o["status"], o["candidate"]) for o in page]
    assert tokens[0] == ("MSCU*123456*0", "0", "corrected", "7") or tokens[0][0] == "MSCU*123456*0"
    assert [t[2] for t in tokens] == ["corrected", "corrected", "flagged", "corrected"], tokens
    assert tokens[1][1] == "" and tokens[1][3] is not None, "empty N7-18 slot is filled by insertion"
    assert tokens[2][0] == "HLBU*112233", "absent slot is flagged, not restructured"
    out = review.reanalyze(conn, conn.execute("SELECT id FROM jobs ORDER BY created_at LIMIT 1").fetchone()[0], "recheck")
    assert out["changed"] == 3, "re-analysis re-proposes the same X12 corrections"


def test_xml_streaming_matches_whole_file_corrector_and_refuses_doctype():
    import snx_corrector
    root = _root()
    conn = _ws(root)
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("4Containers_snx_Example.xml", "COPRAR_Discharge.xml"):
        text = open(os.path.join(here, "samples", name), encoding="utf-8").read()
        ref = snx_corrector.correct_snx(text)
        tags_ref = list(stream.xml_start_tags(iter([text])))
        for size in (1, 2, 3, 7, 64, 1000):
            assert list(stream.xml_start_tags(_chunks(text, size))) == tags_ref, (name, size)
        for off, tag in tags_ref:
            assert text[off:off + len(tag)] == tag
        sub = _run_full(conn, root, "ws", name, text.encode("utf-8"), mode=None)
        s = review.summary(conn, sub["job_id"])
        occurrences = sum(len(c.occurrences) for c in ref.corrected)
        assert s["counters"].get("status_corrected", 0) == occurrences, (name, s["counters"], occurrences)
        assert s["counters"]["identifiers_total"] == ref.total_containers + sum(len(c.occurrences) - 1 for c in ref.corrected) \
            or s["counters"]["identifiers_total"] >= ref.total_containers
        if occurrences:
            apr = review.decide(conn, sub["job_id"], {"status": "corrected"}, "approved", "r")
            art = export_mod.build_artifact(conn, root, sub["job_id"], apr["id"], "r", idempotency_key=name)
            out = open(export_mod.artifact_file(conn, art["id"], "corrected"), encoding="utf-8", newline="").read()
            assert out == ref.corrected_text, name
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "x">]><snx><container eqid="MSKU9070323"/></snx>'
    path = _write(root, "evil.xml", evil)
    sid = ingest.register_source(conn, root, "ws", "evil.xml", path)
    sub = runner.submit(conn, workspace_id="ws", source_file_id=sid, intent_id=None, options={})
    assert runner.run_once(conn, root)["state"] == "failed"
    assert review.summary(conn, sub["job_id"])["findings"] == {"UNSUPPORTED_INPUT": 1}
    tricky = ('<r><!-- <container eqid="MSKU0000001"/> --><![CDATA[<container eqid="MSKU0000002"/>]]>'
              '<?pi <container eqid="MSKU0000003"/> ?><container eqid="MSKU9070320" note="a > b"/>'
              '<unit id="CSQU3054383" unique-key="CSQU3054383"/></r>')
    tags = list(stream.xml_start_tags(_chunks(tricky, 5)))
    assert [t[1][:10] for t in tags] == ["<r>", "<container", "<unit id=\""]
    sub = _run_full(conn, root, "ws", "tricky.xml", tricky.encode("utf-8"), mode=None)
    page = review.observations_page(conn, sub["job_id"], {})["items"]
    assert [(o["raw"], o["status"]) for o in page] == [("MSKU9070320", "corrected"), ("CSQU3054383", "valid"),
                                                       ("CSQU3054383", "valid")]


def test_artifact_lists_supersession_and_abandon_via_api():
    if not HAVE_FASTAPI:
        return
    root = _root()
    client = _client(root)
    ws = "/v1/workspaces/acme"
    _sid, _iid, job, gen = _api_job(client)
    apr = client.post(f"{ws}/jobs/{job}/decisions", json={"filter": {"status": "corrected"}, "decision": "approved"}).json()
    a1 = client.post(f"{ws}/jobs/{job}/exports", json={"approval_id": apr["id"], "idempotency_key": "one"}).json()
    a2 = client.post(f"{ws}/jobs/{job}/exports", json={"approval_id": apr["id"], "idempotency_key": "two"}).json()
    items = client.get(f"{ws}/jobs/{job}/artifacts").json()["items"]
    assert [(i["id"], i["state"]) for i in items] == [(a1["id"], "superseded"), (a2["id"], "ready")]
    assert a2["lineage"]["previous_artifact_id"] == a1["id"]
    assert client.get(f"{ws}/artifacts/{a1['id']}/files/corrected").status_code == 200, "superseded files stay until purge"
    approvals = client.get(f"{ws}/jobs/{job}/approvals").json()["items"]
    assert [a["id"] for a in approvals] == [apr["id"]]
    gens = client.get(f"{ws}/generations").json()["items"]
    assert [g["id"] for g in gens] == [gen] and gens[0]["state"] == "publishable"
    assert client.post(f"{ws}/generations/{gen}/abandon").json()["state"] == "abandoned"
    r = client.post(f"{ws}/generations/{gen}/publish", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PUBLICATION_BLOCKED"
    assert client.get(f"{ws}/fleet/MSKU0000000").status_code == 404
    purge = client.post(f"{ws}/maintenance/purge").json()
    assert purge["artifact_files"] == 1
    assert client.get(f"{ws}/artifacts/{a1['id']}/files/corrected").status_code == 409
    assert client.get(f"{ws}/usage").json()["rows"]["jobs"] == 1
    assert client.patch(ws, json={"retention_days": 7}).json()["retention_policy"]["source_retention_days"] == 7


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
