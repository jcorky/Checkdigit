"""
jobs.py
=======
Durable job queue on the workspace database: leases, heartbeats, checkpoints,
retries, cancellation and recovery after interruption.

A worker claims a job by taking a lease (an atomic UPDATE that only succeeds
when the job is queued, or running with an expired lease). It heartbeats
while working, checkpoints between idempotent work units, and either
completes, fails (retrying until max_attempts) or observes a cancellation
request. A job whose worker dies is reclaimed by the next worker once its
lease expires; the checkpoint tells it where to resume, and every work unit
is idempotent, so a replay neither double-counts nor duplicates rows.

Lifecycle (contracts/enums.json job_state): uploaded, queued, inspecting,
validating, awaiting_review, exporting, completed, failed, cancelled.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, Optional

from .store import audit, new_id, now_iso, transaction

LEASE_SECONDS = 60.0


class JobCancelled(Exception):
    """Raised inside a runner when cancellation was requested."""


class LeaseLost(Exception):
    """Raised when a worker's lease was taken by another worker."""


def create_job(conn: sqlite3.Connection, *, workspace_id: str, source_file_id: str,
               intent_id: Optional[str], options: Dict[str, Any], parser_version: str,
               ruleset_version: str, profile_id: Optional[str] = None,
               profile_version: Optional[int] = None) -> str:
    job_id = new_id("job")
    ts = now_iso()
    conn.execute(
        """INSERT INTO jobs (id, workspace_id, source_file_id, profile_id, profile_version,
                             import_intent_id, state, options_json, parser_version, ruleset_version,
                             created_at, updated_at)
           VALUES (?,?,?,?,?,?,'queued',?,?,?,?,?)""",
        (job_id, workspace_id, source_file_id, profile_id, profile_version, intent_id,
         json.dumps(options, sort_keys=True), parser_version, ruleset_version, ts, ts))
    return job_id


def claim(conn: sqlite3.Connection, worker_id: str, lease_seconds: float = LEASE_SECONDS) -> Optional[sqlite3.Row]:
    """Take the oldest claimable job: queued, or running with an expired lease."""
    now = time.time()
    with transaction(conn):
        row = conn.execute(
            """SELECT id FROM jobs
               WHERE (state = 'queued' OR (state IN ('inspecting','validating','exporting')
                      AND (lease_until IS NULL OR lease_until < ?)))
                 AND attempts < max_attempts AND cancel_requested = 0
               ORDER BY created_at LIMIT 1""", (now,)).fetchone()
        if row is None:
            return None
        cur = conn.execute(
            """UPDATE jobs SET lease_owner = ?, lease_until = ?, attempts = attempts + 1,
                               state = CASE WHEN state = 'queued' THEN 'inspecting' ELSE state END,
                               updated_at = ?
               WHERE id = ? AND (state = 'queued' OR lease_until IS NULL OR lease_until < ?)""",
            (worker_id, now + lease_seconds, now_iso(), row["id"], now))
        if cur.rowcount != 1:
            return None
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()


def heartbeat(conn: sqlite3.Connection, job_id: str, worker_id: str, lease_seconds: float = LEASE_SECONDS) -> None:
    cur = conn.execute("UPDATE jobs SET lease_until = ? WHERE id = ? AND lease_owner = ?",
                       (time.time() + lease_seconds, job_id, worker_id))
    if cur.rowcount != 1:
        raise LeaseLost(job_id)
    if conn.execute("SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]:
        raise JobCancelled(job_id)


def set_step(conn: sqlite3.Connection, job_id: str, worker_id: str, state: str, step: str = "") -> None:
    cur = conn.execute("UPDATE jobs SET state = ?, step = ?, updated_at = ? WHERE id = ? AND lease_owner = ?",
                       (state, step, now_iso(), job_id, worker_id))
    if cur.rowcount != 1:
        raise LeaseLost(job_id)


def checkpoint(conn: sqlite3.Connection, job_id: str, worker_id: str, data: Dict[str, Any]) -> None:
    cur = conn.execute("UPDATE jobs SET checkpoint_json = ?, updated_at = ? WHERE id = ? AND lease_owner = ?",
                       (json.dumps(data, sort_keys=True), now_iso(), job_id, worker_id))
    if cur.rowcount != 1:
        raise LeaseLost(job_id)


def load_checkpoint(conn: sqlite3.Connection, job_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT checkpoint_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return json.loads(row["checkpoint_json"]) if row else {}


def complete(conn: sqlite3.Connection, job_id: str, worker_id: str, state: str = "awaiting_review") -> None:
    cur = conn.execute("UPDATE jobs SET state = ?, step = '', lease_owner = NULL, lease_until = NULL, updated_at = ? "
                       "WHERE id = ? AND lease_owner = ?", (state, now_iso(), job_id, worker_id))
    if cur.rowcount == 1:
        ws = conn.execute("SELECT workspace_id FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
        audit(conn, ws, worker_id, "job.complete", job_id, f"state={state}")


def fail(conn: sqlite3.Connection, job_id: str, worker_id: str, error: str) -> None:
    row = conn.execute("SELECT attempts, max_attempts FROM jobs WHERE id = ?", (job_id,)).fetchone()
    final = row is not None and row["attempts"] >= row["max_attempts"]
    conn.execute("UPDATE jobs SET state = ?, error = ?, lease_owner = NULL, lease_until = NULL, updated_at = ? "
                 "WHERE id = ? AND lease_owner = ?",
                 ("failed" if final else "queued", error[:2000], now_iso(), job_id, worker_id))


def cancel(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute("UPDATE jobs SET cancel_requested = 1, updated_at = ? WHERE id = ?", (now_iso(), job_id))


def mark_cancelled(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute("UPDATE jobs SET state = 'cancelled', lease_owner = NULL, lease_until = NULL, updated_at = ? WHERE id = ?",
                 (now_iso(), job_id))


def bump_counter(conn: sqlite3.Connection, job_id: str, name: str, delta: int) -> None:
    conn.execute("INSERT INTO job_counters (job_id, name, value) VALUES (?,?,?) "
                 "ON CONFLICT(job_id, name) DO UPDATE SET value = value + excluded.value",
                 (job_id, name, delta))


def set_counter(conn: sqlite3.Connection, job_id: str, name: str, value: int) -> None:
    conn.execute("INSERT INTO job_counters (job_id, name, value) VALUES (?,?,?) "
                 "ON CONFLICT(job_id, name) DO UPDATE SET value = excluded.value", (job_id, name, value))


def counters(conn: sqlite3.Connection, job_id: str) -> Dict[str, int]:
    return {r["name"]: r["value"] for r in conn.execute("SELECT name, value FROM job_counters WHERE job_id = ?", (job_id,))}
