"""
bench.py
========
Reproducible fleet dataset with ground truth, the recorded benchmark, and an
independent verifier.

    python3 -m workspace.bench generate --records 3000000 --out ../bench/fleet_3m.csv
    python3 -m workspace.bench run --dataset ../bench/fleet_3m.csv --root ../bench/runs/r3m --crash-after 1000000
    python3 -m workspace.bench verify --dataset ../bench/fleet_3m.csv --root ../bench/runs/r3m

The generator is seeded, so the same arguments produce the same bytes and the
same truth file. The run records hardware, bytes, throughput, peak working
set, storage, query latency and export time, and exercises restart after a
simulated crash, cancellation, concurrent publishers and an interrupted
export. The verifier recounts the raw file with the kernel alone and hashes
the exported file; it shares no code path with the workspace pipeline.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import random
import sys
import threading
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import equipment_checkdigit as kernel  # noqa: E402

OWNERS = ["MSKU", "MSCU", "CMAU", "HLCU", "TCLU", "CSQU", "OOLU", "EGHU", "APZU", "ONEU", "YMLU", "TGHU", "GESU",
          "TRHU", "BMOU", "CAIU", "FCIU", "SEGU", "TEMU", "UACU"]
REMARKS = ["ok", "yard", "gate in", "gate out", "on chassis", "damaged door", "reefer 2°C", "Zürich", "São Paulo",
           "北京", "😀 checked", "long remark " * 12, "line1\nline2", 'say "hi"', "à la carte"]
CATEGORIES = {"valid": 0.85, "wrong_check": 0.08, "wrong_body": 0.03, "missing": 0.01, "duplicate": 0.015,
              "conflict": 0.005, "junk": 0.01}
HEADER = "ref,container,size_type,gross_kg,remark\r\n"


def _bic(rng: random.Random) -> str:
    body = rng.choice(OWNERS) + f"{rng.randrange(0, 1_000_000):06d}"
    return body + str(kernel.iso6346_check_digit(body))


def _csv_cell(s: str) -> str:
    if any(c in s for c in ',"\r\n'):
        return '"' + s.replace('"', '""') + '"'
    return s


def generate(out_path: str, records: int, seed: int = 20260916) -> Dict[str, Any]:
    rng = random.Random(seed)
    cats = list(CATEGORIES)
    weights = [CATEGORIES[c] for c in cats]
    counts = {c: 0 for c in cats}
    truth_h = hashlib.sha256()          # expected bytes when every corrected proposal is approved
    src_h = hashlib.sha256()
    expected_corrected = 0
    prev: List[str] = []
    with open(out_path, "wb") as fh:
        fh.write(HEADER.encode("utf-8"))
        src_h.update(HEADER.encode("utf-8"))
        truth_h.update(HEADER.encode("utf-8"))
        buf: List[bytes] = []
        tbuf: List[bytes] = []
        for i in range(records):
            cat = rng.choices(cats, weights)[0]
            remark = rng.choice(REMARKS)
            size = rng.choice(["22G1", "42G1", "45G1", "22R1", "L5G1"])
            kg = str(rng.randrange(2000, 30480))
            eol = "\r\n" if (i % 7) else "\n"
            if cat == "valid":
                c = _bic(rng)
                shown, fixed = c, c
                prev.append(c)
            elif cat == "wrong_check":
                c = _bic(rng)
                wrong = str((int(c[-1]) + rng.randrange(1, 10)) % 10)
                shown, fixed = c[:-1] + wrong, c
                expected_corrected += 1
            elif cat == "wrong_body":
                c = _bic(rng)
                shown = c[:4] + f"{(int(c[4:10]) + 1) % 1_000_000:06d}" + c[-1]
                good = str(kernel.iso6346_check_digit(shown[:10]))
                fixed = shown if good == shown[-1] else shown[:10] + good
                if fixed != shown:
                    expected_corrected += 1
            elif cat == "missing":
                shown, fixed = "", ""
            elif cat == "duplicate" and prev:
                c = prev[rng.randrange(len(prev))]
                shown, fixed = c, c
            elif cat == "conflict" and prev:
                c = prev[rng.randrange(len(prev))]
                shown, fixed = c[:-1] + str((int(c[-1]) + 1) % 10), c
                expected_corrected += 1
            else:
                cat = "junk"
                shown = rng.choice(["ABC", "12345", "MSKU-12", "N/A", "TCLU12345678"])
                fixed = shown
            counts[cat] += 1
            if len(prev) > 50_000:
                prev = prev[-25_000:]
            line = ",".join([str(i + 1), _csv_cell(shown), size, kg, _csv_cell(remark)]) + eol
            tline = ",".join([str(i + 1), _csv_cell(fixed), size, kg, _csv_cell(remark)]) + eol
            buf.append(line.encode("utf-8"))
            tbuf.append(tline.encode("utf-8"))
            if len(buf) >= 20_000:
                data = b"".join(buf)
                fh.write(data)
                src_h.update(data)
                truth_h.update(b"".join(tbuf))
                buf, tbuf = [], []
        if buf:
            data = b"".join(buf)
            fh.write(data)
            src_h.update(data)
            truth_h.update(b"".join(tbuf))
    truth = {"records": records, "seed": seed, "categories": counts, "expected_corrected_edits": expected_corrected,
             "source_sha256": src_h.hexdigest(), "expected_corrected_sha256": truth_h.hexdigest(),
             "size_bytes": os.path.getsize(out_path)}
    with open(out_path + ".truth.json", "w", encoding="utf-8") as fh:
        json.dump(truth, fh, indent=2, sort_keys=True)
    return truth


def generate_delta(out_path: str, records: int, seed: int) -> List[str]:
    """A small file of new, valid containers with a distinct owner code."""
    rng = random.Random(seed)
    keys = []
    with open(out_path, "wb") as fh:
        fh.write(HEADER.encode("utf-8"))
        for i in range(records):
            body = "ZZZU" + f"{rng.randrange(0, 1_000_000):06d}"
            c = body + str(kernel.iso6346_check_digit(body))
            keys.append(c)
            fh.write(f"{i + 1},{c},22G1,{rng.randrange(2000, 30480)},delta\r\n".encode("utf-8"))
    return keys


# --------------------------------------------------------------------------- #
# Process metrics without third-party packages
# --------------------------------------------------------------------------- #

def peak_rss_bytes() -> Optional[int]:
    if sys.platform == "win32":
        class PMC(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        handle = k32.GetCurrentProcess()
        for fn in (getattr(k32, "K32GetProcessMemoryInfo", None), getattr(ctypes.windll.psapi, "GetProcessMemoryInfo", None)):
            if fn is None:
                continue
            fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), ctypes.c_uint32]
            fn.restype = ctypes.c_int
            if fn(handle, ctypes.byref(pmc), pmc.cb):
                return int(pmc.PeakWorkingSetSize)
        return None
    try:
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(ru * (1 if sys.platform == "darwin" else 1024))
    except Exception:  # noqa: BLE001
        return None


def hardware() -> Dict[str, Any]:
    import sqlite3
    out = {"platform": platform.platform(), "machine": platform.machine(), "processor": platform.processor(),
           "python": platform.python_version(), "cpu_count": os.cpu_count(), "sqlite": sqlite3.sqlite_version}
    if sys.platform == "win32":
        try:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
                            ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                            ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                            ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                            ("ullAvailExtendedVirtual", ctypes.c_uint64)]
            ms = MEMORYSTATUSEX()
            ms.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            out["ram_bytes"] = int(ms.ullTotalPhys)
        except Exception:  # noqa: BLE001
            pass
    return out


def dir_bytes(path: str) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def _pct(xs: List[float], p: float) -> Optional[float]:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(p * len(xs)))] * 1000, 2)


# --------------------------------------------------------------------------- #
# Benchmark run
# --------------------------------------------------------------------------- #

def run(dataset: str, root: str, *, crash_after_records: Optional[int] = None, delta_records: int = 200,
        log=print) -> Dict[str, Any]:
    from . import export as export_mod, ingest, jobs as jobq, reconcile, review, runner, store
    os.makedirs(root, exist_ok=True)
    db_path = os.path.join(root, "workspace.db")
    conn = store.connect(db_path)
    store.ensure_workspace(conn, "bench", "Benchmark")
    truth = json.load(open(dataset + ".truth.json", encoding="utf-8"))
    rec: Dict[str, Any] = {"hardware": hardware(), "dataset": {"path": dataset, **truth}, "steps": {}, "checks": {}}
    t0 = time.perf_counter()

    def step(name: str, t: float) -> None:
        rec["steps"][name] = round(time.perf_counter() - t, 3)
        log(f"[bench] {name}: {rec['steps'][name]} s")

    # 1. register the source (hash + codec sniff in one read)
    t = time.perf_counter()
    sid = ingest.register_source(conn, root, "bench", os.path.basename(dataset), dataset)
    step("register_source_s", t)

    # 2. main load: incremental onto an empty fleet (the file carries quarantined
    #    rows, so a full snapshot would correctly be withheld from publication)
    iid, _dup = ingest.create_intent(conn, "bench", sid, mode="incremental", declared_record_count=truth["records"])
    sub = runner.submit(conn, workspace_id="bench", source_file_id=sid, intent_id=iid, options={"columns": ["container"]})
    job_id = sub["job_id"]
    rec["job_id"] = job_id
    t = time.perf_counter()
    attempts = 0
    if crash_after_records:
        try:
            runner.run_once(conn, root, wid="worker-crashed", fail_after_records=crash_after_records, lease_seconds=0.5)
        except ingest._SimulatedCrash as exc:
            rec["steps"]["simulated_crash"] = str(exc)
            log(f"[bench] {exc}; waiting for the lease to expire")
        attempts += 1
        time.sleep(0.6)
    while True:
        r = runner.run_once(conn, root, wid="worker-main")
        attempts += 1
        if r is None or r["state"] in ("awaiting_review", "failed", "cancelled"):
            break
    step("ingest_validate_s", t)
    rec["steps"]["attempts"] = attempts
    summ = review.summary(conn, job_id)
    rec["job"] = {"state": summ["state"], "counters": summ["counters"], "findings": summ["findings"],
                  "blocking": summ["blocking"], "attempts": summ["attempts"]}
    ingest_s = rec["steps"]["ingest_validate_s"]
    rec["throughput"] = {"records_per_s": round(truth["records"] / ingest_s, 1),
                         "mib_per_s": round(truth["size_bytes"] / ingest_s / (1024 * 1024), 2)}
    obs = conn.execute("SELECT COUNT(*) FROM observations WHERE job_id = ?", (job_id,)).fetchone()[0]
    rec["checks"]["observations_equal_records"] = (obs, truth["records"])

    # 3. review queries
    lat: Dict[str, List[float]] = {}
    for name, flt in (("status_corrected", {"status": "corrected"}), ("prefix_MSKU", {"prefix": "MSKU"}),
                      ("invalid_structure", {"status": "invalid_structure"}), ("unfiltered", {})):
        cursor = -1
        samples = []
        for _ in range(25):
            t = time.perf_counter()
            page = review.observations_page(conn, job_id, flt, after=cursor, limit=200)
            samples.append(time.perf_counter() - t)
            if page["next_cursor"] is None:
                break
            cursor = int(page["next_cursor"])
        lat[name] = samples
    t = time.perf_counter()
    n_corrected = review.count(conn, job_id, {"status": "corrected"})
    count_s = time.perf_counter() - t
    rec["query_latency_ms"] = {k: {"p50": _pct(v, 0.5), "p95": _pct(v, 0.95), "pages": len(v)} for k, v in lat.items()}
    rec["query_latency_ms"]["count_corrected"] = {"ms": round(count_s * 1000, 2), "n": n_corrected}
    log(f"[bench] review queries: {rec['query_latency_ms']}")

    # 4. publish the main generation (fleet materialization)
    gen = reconcile.effects(conn, sub["generation_id"])
    rec["generation"] = {"id": gen["id"], "state": gen["state"], "effects": gen["effects"]}
    t = time.perf_counter()
    pub = reconcile.publish(conn, sub["generation_id"], "bench", operation_id="bench-publish-main")
    step("publish_main_s", t)
    rec["publish_main"] = pub
    fleet_after_main = pub["fleet_count"]

    # 5. small incremental delta against the published fleet
    delta_path = os.path.join(root, "delta.csv")
    generate_delta(delta_path, delta_records, seed=7)
    dsid = ingest.register_source(conn, root, "bench", "delta.csv", delta_path)
    diid, _ = ingest.create_intent(conn, "bench", dsid, mode="incremental", baseline_generation_id=sub["generation_id"],
                                   declared_record_count=delta_records)
    dsub = runner.submit(conn, workspace_id="bench", source_file_id=dsid, intent_id=diid, options={"columns": ["container"]})
    t = time.perf_counter()
    runner.run_once(conn, root, wid="worker-delta")
    step("delta_ingest_compare_s", t)
    dfx = reconcile.effects(conn, dsub["generation_id"])["effects"]
    t = time.perf_counter()
    dpub = reconcile.publish(conn, dsub["generation_id"], "bench", operation_id="bench-publish-delta")
    step("delta_publish_s", t)
    untouched = conn.execute("SELECT COUNT(*) FROM fleet WHERE workspace_id = 'bench' AND generation_id = ?",
                             (sub["generation_id"],)).fetchone()[0]
    rec["delta"] = {"effects": dfx, "publish": dpub, "untouched_rows_from_main": untouched}
    rec["checks"]["delta_added"] = (dfx["added"], delta_records)
    rec["checks"]["delta_retired"] = (dfx["retire"], 0)
    rec["checks"]["untouched_after_delta"] = (untouched, fleet_after_main)
    rec["checks"]["fleet_after_delta"] = (dpub["fleet_count"], fleet_after_main + delta_records)

    # 6. concurrent publishers on the same baseline
    racers = {}
    for name in ("a", "b"):
        p = os.path.join(root, f"race_{name}.csv")
        generate_delta(p, 50, seed=11 if name == "a" else 13)
        rs = ingest.register_source(conn, root, "bench", f"race_{name}.csv", p)
        ri, _ = ingest.create_intent(conn, "bench", rs, mode="incremental", baseline_generation_id=dsub["generation_id"])
        racers[name] = runner.submit(conn, workspace_id="bench", source_file_id=rs, intent_id=ri, options={"columns": ["container"]})
        runner.run_once(conn, root, wid=f"worker-race-{name}")
    outcomes: Dict[str, Any] = {}

    def race(name: str) -> None:
        c = store.connect(db_path)
        try:
            outcomes[name] = reconcile.publish(c, racers[name]["generation_id"], name, operation_id=f"race-{name}")["state"]
        except reconcile.PublishError as exc:
            outcomes[name] = exc.code
        finally:
            c.close()
    threads = [threading.Thread(target=race, args=(n,)) for n in racers]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    rec["concurrent_publish"] = outcomes
    rec["checks"]["one_publisher_wins"] = (sorted(outcomes.values()), ["BASELINE_CONFLICT", "published"])
    winner = [n for n, v in outcomes.items() if v == "published"][0]
    replay = reconcile.publish(conn, racers[winner]["generation_id"], winner, operation_id=f"race-{winner}")
    rec["checks"]["replay_is_noop"] = (replay["replayed"], True)
    rec["checks"]["fleet_after_race"] = (reconcile.fleet_count(conn, "bench"), fleet_after_main + delta_records + 50)

    # 7. cancellation of a running job on the large file
    csub = runner.submit(conn, workspace_id="bench", source_file_id=sid, intent_id=None, options={"columns": ["container"]})
    stop_at: Dict[str, float] = {}

    def cancelled_worker() -> None:
        c = store.connect(db_path)
        try:
            r = runner.run_once(c, root, wid="worker-cancel")
            stop_at["state"] = r["state"] if r else None
            stop_at["t"] = time.perf_counter()
        finally:
            c.close()
    th = threading.Thread(target=cancelled_worker)
    th.start()
    time.sleep(2.0)
    t_cancel = time.perf_counter()
    jobq.cancel(conn, csub["job_id"])
    th.join()
    rec["cancellation"] = {"state": stop_at.get("state"), "stop_latency_s": round(stop_at["t"] - t_cancel, 3),
                           "records_committed": jobq.counters(conn, csub["job_id"]).get("records_total", 0)}
    rec["checks"]["cancelled_state"] = (stop_at.get("state"), "cancelled")

    # 8. approval of every corrected proposal through a storage-backed selection
    t = time.perf_counter()
    apr = review.decide(conn, job_id, {"status": "corrected"}, "approved", "bench", expected_count=n_corrected)
    step("approve_selection_s", t)
    rec["approval"] = {"id": apr["id"], "selection_count": apr["selection_count"], "digest": apr["selection_digest"]}

    # 9. interrupted export, then the real export
    t = time.perf_counter()
    rec["interrupted_export"] = _interrupted_export(conn, root, job_id, apr["id"], export_mod)
    step("interrupted_export_and_retry_s", t)
    rec["checks"]["interrupted_export_not_ready"] = (rec["interrupted_export"]["state_after_interruption"], "failed")
    t = time.perf_counter()
    art = export_mod.build_artifact(conn, root, job_id, apr["id"], "bench", idempotency_key="bench-export-1")
    step("export_s", t)
    rec["artifact"] = {"id": art["id"], "state": art["state"], "sha256": art["sha256"], "size_bytes": art["size_bytes"],
                       "edits_applied": art["edits_applied"], "exceptions": art["manifest"]["exceptions"]}
    rec["checks"]["artifact_sha256_matches_truth"] = (art["sha256"], truth["expected_corrected_sha256"])
    rec["checks"]["edits_match_truth"] = (art["edits_applied"], truth["expected_corrected_edits"])
    rec["checks"]["retry_matches"] = (rec["interrupted_export"]["retry_sha256"], art["sha256"])

    rec["total_s"] = round(time.perf_counter() - t0, 3)
    rec["peak_working_set_bytes"] = peak_rss_bytes()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    rec["storage_bytes"] = {"database": os.path.getsize(db_path), "sources": dir_bytes(os.path.join(root, "sources")),
                            "artifacts": dir_bytes(os.path.join(root, "artifacts"))}
    rec["all_checks_pass"] = all(a == b for a, b in rec["checks"].values())
    conn.close()
    with open(os.path.join(root, "benchmark.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2, sort_keys=True)
    return rec


def _interrupted_export(conn, root, job_id, approval_id, export_mod) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        export_mod.build_artifact(conn, root, job_id, approval_id, "bench", idempotency_key="bench-export-interrupted",
                                  fail_after_edits=1000)
    except export_mod.ExportError as exc:
        out["error"] = exc.code
    row = conn.execute("SELECT state, sha256 FROM export_artifacts WHERE idempotency_key = ?",
                       ("bench-export-interrupted",)).fetchone()
    out["state_after_interruption"] = row["state"] if row else None
    art = export_mod.build_artifact(conn, root, job_id, approval_id, "bench", idempotency_key="bench-export-interrupted")
    out["state_after_retry"] = art["state"]
    out["retry_sha256"] = art["sha256"]
    return out


# --------------------------------------------------------------------------- #
# Independent verification
# --------------------------------------------------------------------------- #

def verify(dataset: str, root: str) -> Dict[str, Any]:
    """Recount the raw file with the kernel only; compare with the recorded run."""
    import csv
    rec = json.load(open(os.path.join(root, "benchmark.json"), encoding="utf-8"))
    truth = json.load(open(dataset + ".truth.json", encoding="utf-8"))
    counts = {"records": 0, "valid": 0, "corrected": 0, "invalid_structure": 0, "flagged": 0, "missing": 0}
    keys = set()
    csv.field_size_limit(10_000_000)
    with open(dataset, "r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        next(reader)
        for row in reader:
            counts["records"] += 1
            tok = row[1]
            if tok == "":
                counts["missing"] += 1
                continue
            res = kernel.correct_identifier(tok, kernel.FieldContext.EQUIPMENT_ID)
            counts[res.status.value] = counts.get(res.status.value, 0) + 1
            if res.status.value in ("valid", "corrected", "flagged"):
                keys.add(res.normalized)
    job = rec["job"]["counters"]
    checks = {
        "records_total": (job.get("records_total"), counts["records"]),
        "status_valid": (job.get("status_valid", 0), counts["valid"]),
        "status_corrected": (job.get("status_corrected", 0), counts["corrected"]),
        "status_invalid_structure": (job.get("status_invalid_structure", 0), counts["invalid_structure"]),
        "identifiers_missing": (job.get("identifiers_missing", 0), counts["missing"]),
        "edits_applied": (rec["artifact"]["edits_applied"], counts["corrected"]),
        "truth_edits": (truth["expected_corrected_edits"], counts["corrected"]),
        "fleet_after_main_equals_distinct_keys": (rec["publish_main"]["fleet_count"], len(keys)),
    }
    art_dir = os.path.join(root, "artifacts", "bench", rec["artifact"]["id"])
    corrected = None
    for name in os.listdir(art_dir):
        if ".CORRECTED" in name:
            corrected = os.path.join(art_dir, name)
    h = hashlib.sha256()
    with open(corrected, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    checks["corrected_sha256_vs_truth"] = (h.hexdigest(), truth["expected_corrected_sha256"])
    checks["corrected_sha256_vs_recorded"] = (h.hexdigest(), rec["artifact"]["sha256"])
    ok = all(a == b for a, b in checks.values())
    out = {"ok": ok, "checks": checks, "records_recounted": counts["records"], "distinct_keys": len(keys)}
    with open(os.path.join(root, "verify.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--records", type=int, default=3_000_000)
    g.add_argument("--seed", type=int, default=20260916)
    g.add_argument("--out", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dataset", required=True)
    r.add_argument("--root", required=True)
    r.add_argument("--crash-after", type=int, default=None)
    r.add_argument("--delta-records", type=int, default=200)
    v = sub.add_parser("verify")
    v.add_argument("--dataset", required=True)
    v.add_argument("--root", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "generate":
        t = time.perf_counter()
        truth = generate(args.out, args.records, args.seed)
        print(json.dumps({**truth, "generate_s": round(time.perf_counter() - t, 1)}, indent=2))
    elif args.cmd == "run":
        rec = run(args.dataset, args.root, crash_after_records=args.crash_after, delta_records=args.delta_records)
        print(json.dumps({k: rec[k] for k in ("steps", "throughput", "query_latency_ms", "checks", "all_checks_pass",
                                               "total_s", "peak_working_set_bytes", "storage_bytes")}, indent=2))
        return 0 if rec["all_checks_pass"] else 1
    else:
        out = verify(args.dataset, args.root)
        print(json.dumps(out, indent=2))
        return 0 if out["ok"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
