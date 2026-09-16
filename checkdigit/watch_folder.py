#!/usr/bin/env python3
"""
watch_folder.py
===============
Headless ingestion worker: watch a drop directory and run every completed file
through the SAME pipeline as a web upload -- detect, correct, audit, owner
corroboration, near-miss -- then write the corrected file and the per-run
verdict CSV to an outbox.

Why a watch folder and not SFTP code: EDI moves over SFTP, but writing an SFTP
server in Python would be reinventing OpenSSH badly. The deployment pattern is
DELEGATION -- a standard SFTP server container (atmoz/sftp, which wraps
OpenSSH) chroots partners into the drop directory; this worker only watches the
landing filesystem. Transport stays battle-tested; this stays stdlib and fully
testable. (Pull-polling a REMOTE SFTP server is a different topology; if you
need it, `paramiko` is the named library -- not built here because it is
unverifiable in this environment.)

Directory convention under --root / CHECKDIGIT_WATCH_ROOT (created at startup):

    inbox/       partners drop files here (the SFTP user's upload dir)
    outbox/      <stem>.corrected.<ext> + <stem>.report.csv (overwritten on
                 re-drop; partners can fetch these back over the same SFTP)
    processed/   originals after success, timestamp-prefixed (collision-safe)
    failed/      originals that were rejected or errored, + <name>.reason.txt

Upload-completion safety: SFTP writes are incremental, and picking up a
half-written BAPLIE corrupts the run. A file is processed only when its size is
unchanged for --stable-scans consecutive polls AND its mtime is at least
--min-age seconds old. Client temp suffixes (.part/.tmp/.filepart/...) and
dotfiles are ignored entirely.

Failure isolation: a rejected or crashing file is moved to failed/ with its
reason (traceback for crashes) and the loop continues; one bad file must never
stop the line. Rejections are still audited (status=rejected) exactly like web
uploads. Configuration errors (missing registry, bad root) fail LOUDLY at
startup instead.

Concurrency: the worker and the API share the same SQLite file; WAL mode (set
in db.connect) is what makes that multi-process arrangement safe.

Encoding fidelity: corrected text is re-encoded with the SAME codec the service
decoded it with (utf-8 or latin-1), so untouched bytes stay byte-identical in
the output file. Binary formats (xlsx) are written from corrected_b64.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import os
import shutil
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Dict, List, Tuple

import db
import enrichment as enrich_mod
import service

USER_AGENT = "checkdigit-watch-folder/1"
TEMP_SUFFIXES = (".part", ".tmp", ".filepart", ".partial", ".crdownload", ".swp")
SUBDIRS = ("inbox", "outbox", "processed", "failed")


def _log(msg: str) -> None:
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    print(f"[{ts}] {msg}", flush=True)


def _stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass
class Dirs:
    root: str
    inbox: str
    outbox: str
    processed: str
    failed: str


def ensure_dirs(root: str) -> Dirs:
    if not root:
        raise RuntimeError(
            "No watch root configured. Set CHECKDIGIT_WATCH_ROOT or pass --root.")
    paths = {name: os.path.join(root, name) for name in SUBDIRS}
    for p in (root, *paths.values()):
        os.makedirs(p, exist_ok=True)
    return Dirs(root=root, **paths)


def _eligible(name: str) -> bool:
    return not name.startswith(".") and not name.lower().endswith(TEMP_SUFFIXES)


def scan_inbox(inbox: str) -> Dict[str, Tuple[int, float]]:
    """name -> (size, mtime) for every eligible regular file currently present."""
    snap: Dict[str, Tuple[int, float]] = {}
    for name in sorted(os.listdir(inbox)):
        if not _eligible(name):
            continue
        path = os.path.join(inbox, name)
        if not os.path.isfile(path):
            continue
        st = os.stat(path)
        snap[name] = (st.st_size, st.st_mtime)
    return snap


def select_ready(state: Dict[str, dict], snapshot: Dict[str, Tuple[int, float]],
                 *, stable_scans: int, min_age_s: float, now: float) -> List[str]:
    """
    Mutates `state` (name -> {size, mtime, stable}) against the new snapshot and
    returns the names whose size has been identical for `stable_scans`
    consecutive scans and whose mtime is at least min_age_s old. Vanished files
    are forgotten; changed files restart their stability count.
    """
    for gone in set(state) - set(snapshot):
        del state[gone]
    ready: List[str] = []
    for name, (size, mtime) in snapshot.items():
        st = state.get(name)
        if st and st["size"] == size and st["mtime"] == mtime:
            st["stable"] += 1
        else:
            state[name] = st = {"size": size, "mtime": mtime, "stable": 1}
        if st["stable"] >= stable_scans and (now - mtime) >= min_age_s:
            ready.append(name)
    return ready


def _split_name(name: str) -> Tuple[str, str]:
    stem, ext = os.path.splitext(name)
    return stem, ext


def process_file(conn, dirs: Dirs, name: str, *, owner_policy: str, trust: bool,
                 enrichment) -> str:
    """Run one stable inbox file through the pipeline; route the outputs.
    Returns a one-line summary for the log. Raises nothing for per-file
    problems -- rejection and crashes both land in failed/ with a reason."""
    src = os.path.join(dirs.inbox, name)
    stamped = f"{_stamp()}_{name}"
    try:
        with open(src, "rb") as fh:
            content = fh.read()
        res = service.process_upload(
            conn, content, filename=name, content_type="",
            user_agent=USER_AGENT, owner_policy=owner_policy, trust=trust,
            enrichment=enrichment)
    except FileNotFoundError:
        return f"{name}: vanished before processing; skipped"
    except Exception:                                  # bug/disk: isolate, keep looping
        dst = os.path.join(dirs.failed, stamped)
        shutil.move(src, dst)
        with open(dst + ".reason.txt", "w", encoding="utf-8") as fh:
            fh.write("worker exception (not a format rejection):\n\n")
            fh.write(traceback.format_exc())
        return f"{name}: ERROR -> failed/{stamped} (traceback written)"

    if res["status"] == "rejected":
        dst = os.path.join(dirs.failed, stamped)
        shutil.move(src, dst)
        with open(dst + ".reason.txt", "w", encoding="utf-8") as fh:
            fh.write(res.get("reason", "rejected") + "\n")
        return f"{name}: rejected ({res.get('reason', '')}) -> failed/{stamped}"

    report = res["report"]
    stem, ext = _split_name(name)
    if report.corrected_b64:
        out_name = f"{stem}.corrected.xlsx"
        with open(os.path.join(dirs.outbox, out_name), "wb") as fh:
            fh.write(base64.b64decode(report.corrected_b64))
    else:
        out_name = f"{stem}.corrected{ext or '.txt'}"
        with open(os.path.join(dirs.outbox, out_name), "wb") as fh:
            fh.write(report.corrected_text.encode(res["encoding"] or "utf-8"))

    csv_text = db.export_event_csv(conn, res["event_id"])
    with open(os.path.join(dirs.outbox, f"{stem}.report.csv"), "w",
              encoding="utf-8", newline="") as fh:
        fh.write(csv_text or "")

    shutil.move(src, os.path.join(dirs.processed, stamped))
    s = res["summary"]
    return (f"{name}: {res['detected_format']} -> outbox/{out_name} "
            f"(containers {s['containers']}, corrected {s['corrected']}, "
            f"flagged {s['flagged']}, valid {s['valid']}) [event {res['event_id']}]")


def run_once(conn, dirs: Dirs, state: Dict[str, dict], *, owner_policy: str,
             trust: bool, enrichment, stable_scans: int, min_age_s: float) -> int:
    ready = select_ready(state, scan_inbox(dirs.inbox),
                         stable_scans=stable_scans, min_age_s=min_age_s,
                         now=time.time())
    for name in ready:
        _log(process_file(conn, dirs, name, owner_policy=owner_policy,
                          trust=trust, enrichment=enrichment))
        state.pop(name, None)                          # re-drops start fresh
    return len(ready)


def main(argv=None) -> int:
    env = os.environ
    ap = argparse.ArgumentParser(
        description="CHECKDIGIT watch-folder worker (pair with an SFTP server "
                    "container that chroots partners into <root>/inbox).")
    ap.add_argument("--root", default=env.get("CHECKDIGIT_WATCH_ROOT"),
                    help="watch root containing inbox/outbox/processed/failed "
                         "(env CHECKDIGIT_WATCH_ROOT)")
    ap.add_argument("--db", default=env.get("CHECKDIGIT_DB", "checkdigit.db"),
                    help="SQLite path shared with the API (env CHECKDIGIT_DB)")
    ap.add_argument("--interval", type=float,
                    default=float(env.get("CHECKDIGIT_WATCH_INTERVAL", "5")),
                    help="poll interval seconds (default 5)")
    ap.add_argument("--stable-scans", type=int,
                    default=int(env.get("CHECKDIGIT_WATCH_STABLE_SCANS", "2")),
                    help="consecutive same-size scans before a file is "
                         "considered complete (default 2)")
    ap.add_argument("--min-age", type=float,
                    default=float(env.get("CHECKDIGIT_WATCH_MIN_AGE", "3")),
                    help="minimum file age in seconds (default 3)")
    ap.add_argument("--owner-policy", choices=("strict", "lenient"),
                    default=env.get("CHECKDIGIT_WATCH_OWNER_POLICY", "strict"))
    ap.add_argument("--trust", action="store_true",
                    default=env.get("CHECKDIGIT_WATCH_TRUST", "") == "1",
                    help="treat plain-text/cell tokens as declared equipment ids "
                         "(env CHECKDIGIT_WATCH_TRUST=1)")
    ap.add_argument("--once", action="store_true",
                    help="process the current stable backlog and exit (cron mode)")
    args = ap.parse_args(argv)

    dirs = ensure_dirs(args.root)                      # fail-loud on missing root
    enrichment = enrich_mod.from_app_env(env)          # fail-loud on bad registry
    conn = db.connect(args.db, create_schema=True)     # WAL: safe alongside the API
    _log(f"watching {dirs.inbox} (interval {args.interval}s, "
         f"stable {args.stable_scans} scans, min age {args.min_age}s, "
         f"policy {args.owner_policy}, trust {args.trust}, "
         f"registry {'yes' if enrichment.owner_prefixes else 'no'})")

    state: Dict[str, dict] = {}
    try:
        while True:
            run_once(conn, dirs, state, owner_policy=args.owner_policy,
                     trust=args.trust, enrichment=enrichment,
                     stable_scans=args.stable_scans, min_age_s=args.min_age)
            if args.once:
                # Cron mode still honors stability: re-scan until nothing new
                # stabilizes within one extra interval, then exit.
                time.sleep(args.interval)
                if run_once(conn, dirs, state, owner_policy=args.owner_policy,
                            trust=args.trust, enrichment=enrichment,
                            stable_scans=args.stable_scans,
                            min_age_s=args.min_age) == 0:
                    return 0
                continue
            time.sleep(args.interval)
    except KeyboardInterrupt:
        _log("stopping")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
