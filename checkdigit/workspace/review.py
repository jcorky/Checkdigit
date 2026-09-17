"""
review.py
=========
Proposals, storage-backed selections, approvals, optimistic versions and
re-analysis.

Every observation with a candidate is a proposal. A reviewer approves or
rejects a selection expressed as a filter; the exact set of (ordinal, version)
pairs the filter matched is frozen in selection_members at decision time and
digested, so later reads and the export act on the recorded membership rather
than on a re-evaluated filter. The approval is bound to the job's change-set
fingerprint; re-analysis produces a new fingerprint and marks earlier
approvals stale. Each observation carries a version that any decision
increments; an approval whose members no longer have their recorded versions
is reported as SELECTION_DRIFT and is not applied.

Re-analysis runs the kernel over every stored observation again (with the
current operator policy), rewrites the rows that changed, resets decisions to
proposed, re-stages memberships and reruns the intent gates. The job's
analysis version increments and every earlier approval goes stale.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from .store import audit, iter_rows, new_id, now_iso, transaction

FINGERPRINT_VERSION = "1"
ALLOWED_FILTERS = {"status", "scheme", "prefix", "decision", "ordinal_from", "ordinal_to", "location", "token"}
DEFAULT_POPULATION = ("proposed", "deferred")


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
    if "token" in flt:
        sql.append("(token = ? OR raw = ? OR normalized = ?)")
        params.extend([flt["token"], flt["token"], str(flt["token"]).upper()])
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
    """Approve, reject or defer the proposals a filter matches now.

    The population is proposals (candidate IS NOT NULL) that are undecided or
    deferred, unless the filter names a `decision` explicitly, which reopens
    rows in that state (for example rejected rows). `expected_count`, when
    given, must equal the number matched, so a reviewer who looked at N rows
    cannot silently decide a different N. The selection is frozen in storage
    before any state changes, then every member's version is bumped in the
    same transaction. `operation_id` is scoped to the job; a replay returns
    the recorded approval.
    """
    if decision not in ("approved", "rejected", "deferred"):
        raise ReviewError("BAD_DECISION", decision)
    if operation_id:
        prior = conn.execute("SELECT id FROM approvals WHERE job_id = ? AND operation_id = ?",
                             (job_id, operation_id)).fetchone()
        if prior is not None:
            return approval_view(conn, prior["id"], replayed=True)
    cs = ensure_change_set(conn, job_id)
    where, params = _where(job_id, flt)
    population = (flt["decision"],) if "decision" in flt else DEFAULT_POPULATION
    pop_sql = "decision IN (%s)" % ",".join("?" * len(population))
    aid = new_id("apr")
    with transaction(conn):
        n = conn.execute(f"SELECT COUNT(*) FROM observations WHERE {where} AND candidate IS NOT NULL AND {pop_sql}",
                         params + list(population)).fetchone()[0]
        if expected_count is not None and expected_count != n:
            raise ReviewError("SELECTION_DRIFT", f"filter matches {n} proposals, reviewer expected {expected_count}")
        if n == 0:
            raise ReviewError("EMPTY_SELECTION", "the filter matches no proposals in the decidable population")
        conn.execute("INSERT INTO approvals (id, job_id, change_set_id, change_set_fingerprint, approver, "
                     "authorization_scope, decision, state, selection_count, selection_digest, filter_json, operation_id, "
                     "decided_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (aid, job_id, cs["id"], cs["fingerprint"], approver, authorization_scope, decision, "recorded",
                      n, "", json.dumps(flt, sort_keys=True), operation_id, now_iso()))
        conn.execute(f"INSERT INTO selection_members (approval_id, ordinal, version) "
                     f"SELECT ?, ordinal, version + 1 FROM observations WHERE {where} AND candidate IS NOT NULL "
                     f"AND {pop_sql} ORDER BY ordinal", [aid] + params + list(population))
        conn.execute(f"UPDATE observations SET decision = ?, version = version + 1 WHERE {where} "
                     f"AND candidate IS NOT NULL AND {pop_sql}", [decision] + params + list(population))
        digest = _selection_digest(conn, aid)
        conn.execute("UPDATE approvals SET selection_digest = ? WHERE id = ?", (digest, aid))
        ws = conn.execute("SELECT workspace_id FROM jobs WHERE id = ?", (job_id,)).fetchone()["workspace_id"]
        audit(conn, ws, approver, f"proposal.{decision}", aid, f"job={job_id} count={n} digest={digest} op={operation_id or ''}")
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


def approvals_page(conn: sqlite3.Connection, job_id: str, after: str = "", limit: int = 100) -> List[Dict[str, Any]]:
    rows = conn.execute("SELECT * FROM approvals WHERE job_id = ? AND id > ? ORDER BY id LIMIT ?",
                        (job_id, after, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["filter"] = json.loads(d.pop("filter_json"))
        out.append(d)
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
    return cur.rowcount


def reanalyze(conn: sqlite3.Connection, job_id: str, reason: str, *, policy=None,
              batch: int = 5000) -> Dict[str, Any]:
    """Run the kernel over every observation again and reset the review state.

    Refused for a job whose generation is already published: a published
    generation is history, and a new analysis of the same file is a new job.
    """
    from . import ingest, jobs as jobq, reconcile
    import equipment_checkdigit as kernel
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise ReviewError("UNKNOWN_JOB")
    if job["state"] not in ("awaiting_review", "completed"):
        raise ReviewError("JOB_NOT_REVIEWABLE", f"job is {job['state']}")
    if job["generation_id"]:
        gstate = conn.execute("SELECT state FROM generations WHERE id = ?", (job["generation_id"],)).fetchone()["state"]
        if gstate in ("published", "superseded"):
            raise ReviewError("GENERATION_PUBLISHED", "re-analysis of a published generation is not allowed; submit a new job")
    options = json.loads(job["options_json"])
    ck = jobq.load_checkpoint(conn, job_id)
    fmt = (ck.get("inspect") or {}).get("format", "csv")
    context = ingest._context(options, fmt)
    owner_policy = options.get("owner_policy", "strict")
    changed = 0
    total = 0
    after = -1
    stale = mark_stale(conn, job_id, reason)
    with transaction(conn):
        conn.execute("UPDATE jobs SET analysis_version = analysis_version + 1, policy_fingerprint = ?, updated_at = ? "
                     "WHERE id = ?", (ingest.policy_fingerprint(policy), now_iso(), job_id))
    while True:
        rows = conn.execute("SELECT ordinal, raw, token, location, normalized, scheme, status, printed_check, "
                            "computed_check, candidate, reason, decision FROM observations WHERE job_id = ? "
                            "AND ordinal > ? ORDER BY ordinal LIMIT ?", (job_id, after, batch)).fetchall()
        if not rows:
            break
        updates = []
        for r in rows:
            total += 1
            token = r["token"] or r["raw"]
            if token == "":
                continue
            x12 = ingest.x12_parts(token) if r["location"].endswith(" N7") else None
            item = ingest.Item(token, r["raw"], 0, "", x12)
            res, candidate = ingest.evaluate_item(item, context, owner_policy, policy)
            if res is None:
                continue
            new = (res.normalized, res.id_type.value, res.status.value, res.printed_check, res.computed_check,
                   candidate, res.reason)
            old = (r["normalized"], r["scheme"], r["status"], r["printed_check"], r["computed_check"], r["candidate"],
                   r["reason"])
            if new != old or (candidate is not None and r["decision"] != "proposed"):
                changed += 1
                updates.append((*new, r["ordinal"]))
        if updates:
            with transaction(conn):
                conn.executemany("UPDATE observations SET normalized = ?, scheme = ?, status = ?, printed_check = ?, "
                                 "computed_check = ?, candidate = ?, reason = ?, decision = 'proposed', "
                                 "version = version + 1 WHERE job_id = ? AND ordinal = ?",
                                 [(u[0], u[1], u[2], u[3], u[4], u[5], u[6], job_id, u[7]) for u in updates])
        after = rows[-1]["ordinal"]
    with transaction(conn):
        for name in ("status_valid", "status_corrected", "status_flagged", "status_invalid_structure",
                     "status_not_a_target", "records_quarantined"):
            jobq.set_counter(conn, job_id, name, 0)
        for r in conn.execute("SELECT status, COUNT(*) AS n FROM observations WHERE job_id = ? AND (token <> '' OR raw <> '') "
                              "GROUP BY status", (job_id,)):
            jobq.set_counter(conn, job_id, f"status_{r['status']}", r["n"])
        missing = jobq.counters(conn, job_id).get("identifiers_missing", 0)
        invalid = conn.execute("SELECT COUNT(*) FROM observations WHERE job_id = ? AND status = 'invalid_structure' "
                               "AND (token <> '' OR raw <> '')", (job_id,)).fetchone()[0]
        jobq.set_counter(conn, job_id, "records_quarantined", missing + invalid)
        if job["generation_id"]:
            conn.execute("DELETE FROM memberships WHERE generation_id = ?", (job["generation_id"],))
            conn.execute("INSERT OR IGNORE INTO memberships (generation_id, key, attrs_hash) "
                         "SELECT ?, normalized, attrs_hash FROM observations WHERE job_id = ? "
                         "AND status IN ('valid','corrected','flagged') ORDER BY ordinal",
                         (job["generation_id"], job_id))
            conn.execute("UPDATE generations SET state = 'candidate' WHERE id = ? AND state IN ('publishable','candidate')",
                         (job["generation_id"],))
        ws = job["workspace_id"]
        audit(conn, ws, "system", "job.reanalyze", job_id, f"reason={reason} changed={changed} stale={stale}")
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    reconcile.finish_job(conn, job, "reanalysis")
    ensure_change_set(conn, job_id)
    return {"job_id": job_id, "analysis_version": job["analysis_version"], "observations": total,
            "changed": changed, "approvals_stale": stale}


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
