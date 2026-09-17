"""
runner.py
=========
Job submission and the worker loop. A worker is any process or thread that
owns a connection and an id; several may run against one database and the
lease protocol in jobs.py keeps them from working on the same job at once.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
import time
from typing import Any, Dict, Optional

from . import ingest, jobs as jobq, reconcile
from .store import audit, connect, transaction


def submit(conn: sqlite3.Connection, *, workspace_id: str, source_file_id: str, intent_id: Optional[str],
           options: Dict[str, Any], actor: str = "system") -> Dict[str, Any]:
    """Queue a job. An intent that already has a job is not run twice."""
    with transaction(conn):
        if intent_id:
            intent = conn.execute("SELECT * FROM import_intents WHERE id = ?", (intent_id,)).fetchone()
            if intent is None:
                raise ingest.IngestError("unknown import intent")
            prior = conn.execute("SELECT id, generation_id, state FROM jobs WHERE import_intent_id = ? "
                                 "AND state <> 'failed' AND state <> 'cancelled' ORDER BY created_at LIMIT 1",
                                 (intent_id,)).fetchone()
            if prior is not None:
                ingest.add_finding(conn, prior["id"], "DUPLICATE_APPLICATION_PREVENTED",
                                   f"intent {intent_id} already has job {prior['id']}")
                return {"job_id": prior["id"], "generation_id": prior["generation_id"], "duplicate": True}
        job_id = jobq.create_job(conn, workspace_id=workspace_id, source_file_id=source_file_id, intent_id=intent_id,
                                 options=options, parser_version=ingest.PARSER_VERSION,
                                 ruleset_version=ingest.RULESET_VERSION)
        gen_id = None
        if intent_id and intent["mode"] != "comparison_only":
            gen_id = reconcile.new_generation(conn, workspace_id, intent_id, job_id, intent["scope_kind"],
                                             intent["scope_value"], intent["baseline_generation_id"])
            conn.execute("UPDATE jobs SET generation_id = ? WHERE id = ?", (gen_id, job_id))
        audit(conn, workspace_id, actor, "job.submit", job_id, f"source={source_file_id} intent={intent_id or ''}")
    return {"job_id": job_id, "generation_id": gen_id, "duplicate": False}


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"


def run_once(conn: sqlite3.Connection, root: str, *, wid: Optional[str] = None, policy=None,
             batch_records: int = ingest.BATCH_RECORDS, fail_after_records: Optional[int] = None,
             lease_seconds: float = jobq.LEASE_SECONDS) -> Optional[Dict[str, Any]]:
    """Claim and run one job. Returns None when nothing is claimable."""
    wid = wid or worker_id()
    job = jobq.claim(conn, wid, lease_seconds)
    if job is None:
        return None
    state = ingest.run_job(conn, root, job, wid, batch_records=batch_records, policy=policy,
                           fail_after_records=fail_after_records, lease_seconds=lease_seconds)
    return {"job_id": job["id"], "state": state, "worker": wid}


def run_until_idle(conn: sqlite3.Connection, root: str, *, wid: Optional[str] = None, policy=None,
                   batch_records: int = ingest.BATCH_RECORDS, max_jobs: int = 1000) -> int:
    n = 0
    while n < max_jobs:
        if run_once(conn, root, wid=wid, policy=policy, batch_records=batch_records) is None:
            return n
        n += 1
    return n


def serve(db_path: str, root: str, poll_seconds: float = 1.0) -> None:
    """Long-running worker loop for a separate process."""
    conn = connect(db_path)
    wid = worker_id()
    while True:
        if run_once(conn, root, wid=wid) is None:
            time.sleep(poll_seconds)


if __name__ == "__main__":
    import sys
    serve(sys.argv[1], sys.argv[2])
