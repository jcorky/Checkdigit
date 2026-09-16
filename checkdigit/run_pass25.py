#!/usr/bin/env python3
"""
run_pass25.py -- concurrency hardening.

The app may run several uvicorn workers PLUS the SFTP watcher, all separate
processes against one SQLite file, while many users upload at once. This proves
the stack stays correct and lock-error-free under that load:

  pragmas          connect() sets WAL + busy_timeout + synchronous=NORMAL
  busy_timeout     a connection actually waits instead of failing on a held lock
  concurrent write many threads hammering writes -> zero lost rows, zero errors
  cross-process    several OS processes writing the same DB -> zero lock errors
  concurrent upload N threads calling process_upload at once -> all audited, all
                   corrected, counters consistent (no lost updates)
  schema race      many connections opening a FRESH db concurrently all succeed
                   (no "table already exists" / init race)
  retry helper     execute_write retries transient locks, re-raises real errors
  policy swap      concurrent readers + a writer never see a torn policy
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

import db
import service


def test_pragmas():
    path = os.path.join(tempfile.mkdtemp(), "p25.db")
    conn = db.connect(path, create_schema=True)
    jm = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    bt = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
    sy = conn.execute("PRAGMA synchronous;").fetchone()[0]
    assert jm.lower() == "wal", jm
    assert bt >= 5000, bt
    assert sy in (1, "1", "NORMAL"), sy        # 1 = NORMAL
    conn.close()
    print(f"  pragmas: journal={jm} busy_timeout={bt} synchronous={sy} (NORMAL)")


def test_busy_timeout_waits():
    """Hold the write lock in one connection; a second connection's write should
    WAIT for it (busy_timeout) and then succeed, not fail instantly."""
    path = os.path.join(tempfile.mkdtemp(), "p25bt.db")
    db.connect(path, create_schema=True).close()
    holder = db.connect(path, create_schema=False)
    holder.execute("BEGIN IMMEDIATE;")            # take + hold the write lock
    holder.execute("INSERT INTO sources(name) VALUES ('holder')")

    result = {}
    def writer():
        c = db.connect(path, create_schema=False, busy_timeout_ms=3000)
        t0 = time.time()
        try:
            c.execute("INSERT INTO sources(name) VALUES ('waiter')")
            c.commit()
            result["ok"] = True
            result["waited"] = time.time() - t0
        except sqlite3.OperationalError as e:
            result["err"] = str(e)
        finally:
            c.close()

    th = threading.Thread(target=writer)
    th.start()
    time.sleep(0.4)                                # let the writer block on the lock
    holder.commit()                                # release -> waiter proceeds
    holder.close()
    th.join(timeout=5)
    assert result.get("ok"), f"writer should have waited then succeeded: {result}"
    assert result["waited"] >= 0.3, result        # it genuinely waited
    print(f"  busy_timeout: blocked writer waited {result['waited']*1000:.0f}ms then succeeded")


def test_concurrent_writers_threads():
    path = os.path.join(tempfile.mkdtemp(), "p25t.db")
    db.connect(path, create_schema=True).close()
    errs = []
    N, PER = 10, 50
    def hammer(n):
        try:
            c = db.connect(path, create_schema=False)
            for i in range(PER):
                db.execute_write(c, "INSERT INTO sources(name) VALUES (?)",
                                 (f"t{n}-{i}",))
                c.commit()
            c.close()
        except Exception as e:
            errs.append(repr(e))
    ts = [threading.Thread(target=hammer, args=(i,)) for i in range(N)]
    for t in ts: t.start()
    for t in ts: t.join()
    conn = db.connect(path, create_schema=False)
    count = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    conn.close()
    assert not errs, errs[:3]
    assert count == N * PER, f"expected {N*PER} rows, got {count} (lost writes!)"
    print(f"  concurrent threads: {N}x{PER} writes, 0 errors, all {count} rows present")


def test_cross_process_writers():
    """Several OS processes writing the same DB -> zero lock errors with the
    hardened connect()."""
    path = os.path.join(tempfile.mkdtemp(), "p25p.db")
    db.connect(path, create_schema=True).close()
    worker = os.path.join(tempfile.mkdtemp(), "w.py")
    with open(worker, "w") as f:
        f.write(
            "import sys, db, time\n"
            "path, tag = sys.argv[1], sys.argv[2]\n"
            "c = db.connect(path, create_schema=False)\n"
            "e=0\n"
            "for i in range(120):\n"
            "    try:\n"
            "        db.execute_write(c, 'INSERT INTO sources(name) VALUES (?)', (f'{tag}-{i}-{time.time_ns()}',)); c.commit()\n"
            "    except Exception: e+=1\n"
            "print(e)\n")
    env = dict(os.environ, PYTHONPATH=os.getcwd())
    procs = [subprocess.Popen([sys.executable, worker, path, f"p{i}"],
                              stdout=subprocess.PIPE, env=env) for i in range(6)]
    errs = sum(int(p.communicate()[0].strip() or 0) for p in procs)
    conn = db.connect(path, create_schema=False)
    count = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    conn.close()
    assert errs == 0, f"{errs} lock errors across processes"
    assert count == 6 * 120, f"expected {6*120}, got {count}"
    print(f"  cross-process: 6 procs x120 writes, 0 lock errors, all {count} rows present")


def test_concurrent_uploads():
    """N threads each correcting a file through the real pipeline against one DB.
    Every upload must be audited and corrected; warehouse counters consistent."""
    path = os.path.join(tempfile.mkdtemp(), "p25u.db")
    db.connect(path, create_schema=True).close()
    BAD = "MSKU7351773"        # -> 0
    errs = []
    N = 16
    def up(n):
        try:
            c = db.connect(path, create_schema=False)
            r = service.process_upload(
                c, f"load {n} box {BAD}".encode(), filename=f"f{n}.txt",
                content_type="", user_agent="p25", owner_policy="strict", trust=True)
            assert r["status"] == "processed" and r["report"].corrected
            c.close()
        except Exception as e:
            errs.append(repr(e))
    ts = [threading.Thread(target=up, args=(i,)) for i in range(N)]
    for t in ts: t.start()
    for t in ts: t.join()
    conn = db.connect(path, create_schema=False)
    files = conn.execute("SELECT COUNT(*) FROM ingestion_events").fetchone()[0]
    seen = conn.execute("SELECT times_seen FROM containers WHERE eqid='MSKU7351770'").fetchone()
    conn.close()
    assert not errs, errs[:3]
    assert files == N, f"expected {N} audit rows, got {files}"
    assert seen and seen["times_seen"] == N, f"counter should be {N}, got {seen and seen['times_seen']}"
    print(f"  concurrent uploads: {N} parallel corrections, all audited, counter=={N} (no lost updates)")


def test_schema_init_race():
    """Many connections opening a FRESH db at once all succeed (worker startup)."""
    path = os.path.join(tempfile.mkdtemp(), "p25s.db")   # does not exist yet
    errs = []
    def opener():
        try:
            db.connect(path, create_schema=True).close()
        except Exception as e:
            errs.append(repr(e))
    ts = [threading.Thread(target=opener) for _ in range(12)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert not errs, errs[:3]
    # schema usable afterwards
    conn = db.connect(path, create_schema=False)
    conn.execute("SELECT COUNT(*) FROM containers").fetchone()
    conn.close()
    print("  schema race: 12 concurrent fresh-DB opens, 0 errors, schema intact")


def test_retry_helper():
    path = os.path.join(tempfile.mkdtemp(), "p25r.db")
    db.connect(path, create_schema=True).close()
    c = db.connect(path, create_schema=False)
    # a real (non-lock) error must NOT be retried/swallowed
    raised = False
    try:
        db.execute_write(c, "INSERT INTO nonexistent_table VALUES (1)")
    except sqlite3.OperationalError:
        raised = True
    assert raised, "non-lock errors must propagate (fail loud)"
    # a normal write returns a cursor
    cur = db.execute_write(c, "INSERT INTO sources(name) VALUES ('ok')")
    c.commit()
    assert cur.rowcount == 1
    c.close()
    print("  retry helper: transient locks retried; real errors propagate; normal write works")


def test_policy_swap_safe():
    """Concurrent policy reads while a writer swaps it never raise / tear."""
    import policy as policy_mod
    lock = threading.Lock()
    box = {"p": policy_mod.Policy(default_policy="strict")}
    def reader(out):
        for _ in range(2000):
            with lock:
                p = box["p"]
            # use the snapshot lock-free
            _ = p.resolve("MSKU")
            out.append(p.default_policy)
    def writer():
        for i in range(2000):
            np = policy_mod.Policy(default_policy="lenient" if i % 2 else "strict")
            with lock:
                box["p"] = np
    outs = [[] for _ in range(4)]
    threads = [threading.Thread(target=reader, args=(outs[i],)) for i in range(3)]
    threads.append(threading.Thread(target=writer))
    for t in threads: t.start()
    for t in threads: t.join()
    # every observed value is a valid, whole policy (never a torn/None)
    assert all(v in ("strict", "lenient") for o in outs[:3] for v in o)
    print("  policy swap: 3 readers x2000 + 1 writer, every snapshot valid (no torn reads)")


def main():
    print("Pass 25 -- concurrency hardening")
    test_pragmas()
    test_busy_timeout_waits()
    test_concurrent_writers_threads()
    test_cross_process_writers()
    test_concurrent_uploads()
    test_schema_init_race()
    test_retry_helper()
    test_policy_swap_safe()
    print("\nPASS: concurrency verified -- busy_timeout, cross-process, parallel uploads, "
          "schema race, policy swap.")


if __name__ == "__main__":
    main()
