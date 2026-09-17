"""
maintenance.py
==============
Storage accounting and retention.

`purge` applies the workspace's retention policy: it removes upload parts
abandoned for more than a day, the files of superseded and failed artifacts,
and everything belonging to jobs that reached a terminal state longer ago
than the retention period (observations, findings, selections, approvals,
change sets, artifacts and unpublished memberships). Source files with no
remaining job are deleted with them. Published generations, the fleet table
and the audit log are kept: they are the history the workspace exists for.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sqlite3
from typing import Any, Dict, Optional

from .store import audit, now_iso, transaction, usage

TERMINAL_JOB_STATES = ("completed", "failed", "cancelled")
UPLOAD_ABANDON_SECONDS = 24 * 3600


def _parse(ts: str) -> dt.datetime:
    return dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def _rmtree(path: Optional[str]) -> int:
    if not path or not os.path.isdir(path):
        return 0
    freed = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                freed += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)
    return freed


def purge(conn: sqlite3.Connection, root: str, workspace_id: str, actor: str = "system", *,
          now: Optional[dt.datetime] = None, retention_days: Optional[int] = None) -> Dict[str, Any]:
    ws = conn.execute("SELECT retention_days FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    if ws is None:
        raise ValueError("unknown workspace")
    days = ws["retention_days"] if retention_days is None else retention_days
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=days)
    out: Dict[str, Any] = {"uploads": 0, "artifact_files": 0, "jobs": 0, "sources": 0, "bytes_freed": 0}

    # 1. abandoned upload parts
    for up in conn.execute("SELECT id, path, created_at FROM uploads WHERE workspace_id = ? AND state = 'open'",
                           (workspace_id,)).fetchall():
        if (now - _parse(up["created_at"])).total_seconds() > UPLOAD_ABANDON_SECONDS:
            try:
                out["bytes_freed"] += os.path.getsize(up["path"])
                os.remove(up["path"])
            except OSError:
                pass
            with transaction(conn):
                conn.execute("UPDATE uploads SET state = 'abandoned' WHERE id = ?", (up["id"],))
            out["uploads"] += 1

    # 2. files of superseded and failed artifacts (rows stay as lineage)
    for art in conn.execute("SELECT a.id, a.path FROM export_artifacts a JOIN jobs j ON j.id = a.job_id "
                            "WHERE j.workspace_id = ? AND a.state IN ('superseded','failed') AND a.path IS NOT NULL",
                            (workspace_id,)).fetchall():
        out["bytes_freed"] += _rmtree(art["path"])
        with transaction(conn):
            conn.execute("UPDATE export_artifacts SET path = NULL WHERE id = ?", (art["id"],))
        out["artifact_files"] += 1

    # 3. jobs past retention
    for job in conn.execute("SELECT id, source_file_id, generation_id, updated_at FROM jobs WHERE workspace_id = ? "
                            "AND state IN (?,?,?)", (workspace_id, *TERMINAL_JOB_STATES)).fetchall():
        if _parse(job["updated_at"]) > cutoff:
            continue
        for art in conn.execute("SELECT path FROM export_artifacts WHERE job_id = ?", (job["id"],)).fetchall():
            out["bytes_freed"] += _rmtree(art["path"])
        with transaction(conn):
            conn.execute("DELETE FROM selection_members WHERE approval_id IN (SELECT id FROM approvals WHERE job_id = ?)",
                         (job["id"],))
            conn.execute("DELETE FROM export_artifacts WHERE job_id = ?", (job["id"],))
            conn.execute("DELETE FROM approvals WHERE job_id = ?", (job["id"],))
            conn.execute("DELETE FROM change_sets WHERE job_id = ?", (job["id"],))
            conn.execute("DELETE FROM findings WHERE job_id = ?", (job["id"],))
            conn.execute("DELETE FROM observations WHERE job_id = ?", (job["id"],))
            conn.execute("DELETE FROM job_counters WHERE job_id = ?", (job["id"],))
            if job["generation_id"]:
                g = conn.execute("SELECT state FROM generations WHERE id = ?", (job["generation_id"],)).fetchone()
                if g and g["state"] not in ("published", "superseded"):
                    conn.execute("DELETE FROM memberships WHERE generation_id = ?", (job["generation_id"],))
                    conn.execute("UPDATE generations SET state = 'abandoned', job_id = NULL WHERE id = ?",
                                 (job["generation_id"],))
                else:
                    conn.execute("UPDATE generations SET job_id = NULL WHERE id = ?", (job["generation_id"],))
            conn.execute("DELETE FROM jobs WHERE id = ?", (job["id"],))
            audit(conn, workspace_id, actor, "job.purge", job["id"], f"retention {days} days")
        out["jobs"] += 1

    # 4. sources no job references any more
    for src in conn.execute("SELECT id, path, received_at FROM source_files WHERE workspace_id = ? AND NOT EXISTS "
                            "(SELECT 1 FROM jobs WHERE jobs.source_file_id = source_files.id)", (workspace_id,)).fetchall():
        if _parse(src["received_at"]) > cutoff:
            continue
        try:
            out["bytes_freed"] += os.path.getsize(src["path"])
            os.remove(src["path"])
        except OSError:
            pass
        with transaction(conn):
            conn.execute("UPDATE import_intents SET source_file_id = source_file_id WHERE source_file_id = ?", (src["id"],))
            conn.execute("DELETE FROM import_intents WHERE source_file_id = ? AND NOT EXISTS "
                         "(SELECT 1 FROM jobs WHERE jobs.import_intent_id = import_intents.id)", (src["id"],))
            conn.execute("UPDATE uploads SET source_file_id = NULL WHERE source_file_id = ?", (src["id"],))
            conn.execute("DELETE FROM source_files WHERE id = ?", (src["id"],))
            audit(conn, workspace_id, actor, "source.purge", src["id"], f"retention {days} days")
        out["sources"] += 1
    out["usage"] = usage(root, workspace_id)
    out["purged_at"] = now_iso()
    return out
