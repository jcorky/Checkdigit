"""
review.py
=========
Proposals, storage-backed selections, approvals and optimistic versions.

Every observation with a candidate is a proposal. A reviewer approves or
rejects a selection expressed as a filter; the exact set of (ordinal, version)
pairs the filter matched is frozen in selection_members at decision time and
digested, so later reads and the export act on the recorded membership rather
than on a re-evaluated filter. The approval is bound to the job's change-set
fingerprint; re-analysis produces a new fingerprint and marks earlier
approvals stale. Each observation carries a version that any decision
increments; an approval whose members no longer have their recorded versions
is reported as SELECTION_DRIFT and is not applied.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from .store import audit, iter_rows, new_id, now_iso, transaction

FINGERPRINT_VERSION = "1"
ALLOWED_FILTERS = {"status", "scheme", "prefix", "decision", "ordinal_from", "ordinal_to", "location"}


class ReviewError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _where(job_id: str, flt: Dict[str, Any]) -> Tuple[str, List[Any]]:
    unknown = set(flt) - ALLOWED_FILTERS
    if unknown:
        raise ReviewError("BAD_FILTER", f"unknown filter keys {sorted(unknown)}")
    sql = ["job_id = ?"]
    params: List[Any] = [job_id]
    if "status" in flt:
        sql.append("status = ?")
        params.append(flt["status"])
    if "scheme" in flt:
        sql.append("scheme = ?")
        params.append(flt["scheme"])
    if "decision" in flt:
        sql.append("decision = ?")
        params.append(flt["decision"])
    if "prefix" in flt:
        p = str(flt["prefix"]).upper()
        sql.append("normalized >= ? AND normalized < ?")
        params.extend([p, p + "￿"])
    if "ordinal_from" in flt:
        sql.append("ordinal >= ?")
        params.append(int(flt["ordinal_from"]))
    if "ordinal_to" in flt:
        sql.append("ordinal <= ?")
        params.append(int(flt["ordinal_to"]))
    if "location" in flt:
        sql.append("location = ?")
        params.append(flt["location"])
    return " AND ".join(sql), params


def observations_page(conn: sqlite3.Connection, job_id: str, flt: Dict[str, Any], *,
                      after: int = -1, limit: int = 200) -> Dict[str, Any]:
    where, params = _where(job_id, flt)
    limit = max(1, min(int(limit), 1000))
    rows = conn.execute(f"SELECT * FROM observations WHERE {where} AND ordinal > ? ORDER BY ordinal LIMIT ?",
                        params + [after, limit + 1]).fetchall()
    items = [dict(r) for r in rows[:limit]]
    return {"items": items, "next_cursor": str(items[-1]["ordinal"]) if len(rows) > limit else None}


def count(conn: sqlite3.Connection, job_id: str, flt: Dict[str, Any]) -> int:
    where, params = _where(job_id, flt)
    return conn.execute(f"SELECT COUNT(*) FROM observations WHERE {where}", params).fetchone()[0]


def change_set_fingerprint(conn: sqlite3.Connection, job_id: str) -> Tuple[str, int]:
    """Hash of every proposal (ordinal, offset, raw, candidate) in file order."""
    h = hashlib.sha256()
    n = 0
    for row in iter_rows(conn, "SELECT ordinal, char_offset, raw, candidate FROM observations WHERE job_id = ? "
                               "AND candidate IS NOT NULL ORDER BY ordinal", (job_id,)):
        h.update(f"{row[0]}\t{row[1]}\t{row[2]}\t{row[3]}\n".encode("utf-8"))
        n += 1
    return h.hexdigest(), n


def ensure_change_set(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    job = conn.execute("SELECT analysis_version FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise ReviewError("UNKNOWN_JOB")
    row = conn.execute("SELECT * FROM change_sets WHERE job_id = ? AND analysis_version = ?",
                       (job_id, job["analysis_version"])).fetchone()
    if row is not None:
        return row
    fp, n = change_set_fingerprint(conn, job_id)
    cid = new_id("cs")
    with transaction(conn):
        conn.execute("INSERT INTO change_sets (id, job_id, analysis_version, fingerprint, fingerprint_version, "
                     "edit_count, created_at) VALUES (?,?,?,?,?,?,?)",
                     (cid, job_id, job["analysis_version"], fp, FINGERPRINT_VERSION, n, now_iso()))
    return conn.execute("SELECT * FROM change_sets WHERE id = ?", (cid,)).fetchone()


def decide(conn: sqlite3.Connection, job_id: str, flt: Dict[str, Any], decision: str, approver: str, *,
           operation_id: Optional[str] = None, authorization_scope: str = "draft",
           expected_count: Optional[int] = None) -> Dict[str, Any]:
    """Approve or reject the proposals a filter matches now.

    The filter is restricted to proposals (candidate IS NOT NULL) that are
    still undecided or deferred. `expected_count`, when given, must equal the
    number matched, so a reviewer who looked at N rows cannot silently decide
    a different N. The selection is frozen in storage before any state
    changes, then every member's version is bumped in the same transaction.
    """
    if decision not in ("approved", "rejected", "deferred"):
        raise ReviewError("BAD_DECISION", decision)
    if operation_id:
        prior = conn.execute("SELECT id FROM approvals WHERE job_id = ? AND id = ?", (job_id, operation_id)).fetchone()
        if prior is not None:
            return approval_view(conn, prior["id"], replayed=True)
    cs = ensure_change_set(conn, job_id)
    where, params = _where(job_id, flt)
    aid = operation_id or new_id("apr")
    with transaction(conn):
        n = conn.execute(f"SELECT COUNT(*) FROM observations WHERE {where} AND candidate IS NOT NULL "
                         f"AND decision IN ('proposed','deferred')", params).fetchone()[0]
        if expected_count is not None and expected_count != n:
            raise ReviewError("SELECTION_DRIFT", f"filter matches {n} proposals, reviewer expected {expected_count}")
        if n == 0:
            raise ReviewError("EMPTY_SELECTION", "the filter matches no undecided proposals")
        conn.execute("INSERT INTO approvals (id, job_id, change_set_id, change_set_fingerprint, approver, "
                     "authorization_scope, decision, state, selection_count, selection_digest, filter_json, decided_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (aid, job_id, cs["id"], cs["fingerprint"], approver, authorization_scope, decision, "recorded",
                      n, "", json.dumps(flt, sort_keys=True), now_iso()))
        conn.execute(f"INSERT INTO selection_members (approval_id, ordinal, version) "
                     f"SELECT ?, ordinal, version + 1 FROM observations WHERE {where} AND candidate IS NOT NULL "
                     f"AND decision IN ('proposed','deferred') ORDER BY ordinal", [aid] + params)
        conn.execute(f"UPDATE observations SET decision = ?, version = version + 1 WHERE {where} "
                     f"AND candidate IS NOT NULL AND decision IN ('proposed','deferred')", [decision] + params)
        digest = _selection_digest(conn, aid)
        conn.execute("UPDATE approvals SET selection_digest = ? WHERE id = ?", (digest, aid))
        ws = conn.execute("SELECT workspace_id FROM jobs WHERE id = ?", (job_id,)).fetchone()["workspace_id"]
        audit(conn, ws, approver, f"proposal.{decision}", aid, f"job={job_id} count={n} digest={digest}")
    return approval_view(conn, aid)


def _selection_digest(conn: sqlite3.Connection, approval_id: str) -> str:
    h = hashlib.sha256()
    for row in iter_rows(conn, "SELECT ordinal, version FROM selection_members WHERE approval_id = ? ORDER BY ordinal",
                         (approval_id,)):
        h.update(f"{row[0]}:{row[1]}\n".encode("ascii"))
    return h.hexdigest()


def approval_view(conn: sqlite3.Connection, approval_id: str, replayed: bool = False) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    if row is None:
        raise ReviewError("UNKNOWN_APPROVAL")
    out = dict(row)
    out["filter"] = json.loads(out.pop("filter_json"))
    out["replayed"] = replayed
    return out


def check_approval(conn: sqlite3.Connection, approval_id: str) -> Dict[str, Any]:
    """Is this approval still applicable? Reports staleness and version drift."""
    apr = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    if apr is None:
        raise ReviewError("UNKNOWN_APPROVAL")
    problems: List[str] = []
    cs = ensure_change_set(conn, apr["job_id"])
    if cs["fingerprint"] != apr["change_set_fingerprint"]:
        problems.append("APPROVAL_STALE")
    drift = conn.execute(
        "SELECT COUNT(*) FROM selection_members s LEFT JOIN observations o ON o.job_id = ? AND o.ordinal = s.ordinal "
        "WHERE s.approval_id = ? AND (o.ordinal IS NULL OR o.version <> s.version)",
        (apr["job_id"], approval_id)).fetchone()[0]
    if drift:
        problems.append("SELECTION_DRIFT")
    if apr["state"] == "stale":
        problems.append("APPROVAL_STALE")
    return {"approval_id": approval_id, "applicable": not problems, "problems": sorted(set(problems)),
            "drifted_members": drift, "selection_count": apr["selection_count"], "decision": apr["decision"]}


def mark_stale(conn: sqlite3.Connection, job_id: str, reason: str) -> int:
    with transaction(conn):
        cur = conn.execute("UPDATE approvals SET state = 'stale', stale_reason = ? WHERE job_id = ? AND state = 'recorded'",
                           (reason, job_id))
        conn.execute("UPDATE observations SET decision = 'stale', version = version + 1 WHERE job_id = ? "
                     "AND decision IN ('approved','rejected','deferred')", (job_id,))
    return cur.rowcount


def reanalyze(conn: sqlite3.Connection, job_id: str, reason: str) -> int:
    """Bump the analysis version (new change set) and stale every recorded approval."""
    with transaction(conn):
        conn.execute("UPDATE jobs SET analysis_version = analysis_version + 1, updated_at = ? WHERE id = ?",
                     (now_iso(), job_id))
    return mark_stale(conn, job_id, reason)


def findings_page(conn: sqlite3.Connection, job_id: str, *, after: int = 0, limit: int = 200,
                  code: Optional[str] = None) -> Dict[str, Any]:
    limit = max(1, min(int(limit), 1000))
    if code:
        rows = conn.execute("SELECT * FROM findings WHERE job_id = ? AND code = ? AND id > ? ORDER BY id LIMIT ?",
                            (job_id, code, after, limit + 1)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM findings WHERE job_id = ? AND id > ? ORDER BY id LIMIT ?",
                            (job_id, after, limit + 1)).fetchall()
    items = [dict(r) for r in rows[:limit]]
    return {"items": items, "next_cursor": str(items[-1]["id"]) if len(rows) > limit else None}


def summary(conn: sqlite3.Connection, job_id: str) -> Dict[str, Any]:
    from . import jobs as jobq
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise ReviewError("UNKNOWN_JOB")
    out = dict(job)
    out["checkpoint"] = json.loads(out.pop("checkpoint_json"))
    out["options"] = json.loads(out.pop("options_json"))
    out["counters"] = jobq.counters(conn, job_id)
    out["findings"] = {r["code"]: r["n"] for r in conn.execute(
        "SELECT code, COUNT(*) AS n FROM findings WHERE job_id = ? GROUP BY code", (job_id,))}
    out["decisions"] = {r["decision"]: r["n"] for r in conn.execute(
        "SELECT decision, COUNT(*) AS n FROM observations WHERE job_id = ? AND candidate IS NOT NULL GROUP BY decision",
        (job_id,))}
    out["blocking"] = sorted({r["code"] for r in conn.execute(
        "SELECT code FROM findings WHERE job_id = ? AND severity = 'blocking'", (job_id,))})
    return out
