"""
reconcile.py
============
Staged snapshot and delta reconciliation.

A job with an import intent stages a candidate generation while it streams.
When streaming completes, the candidate is compared with the workspace's
published fleet as pinned by the intent's baseline generation, and the
effects are recorded as counts plus a change manifest hash. Publication is a
single transaction that checks the baseline is still current, applies the
generation's effects to the fleet table, marks the candidate published,
supersedes the baseline and moves the workspace pointer. Two publishers
racing on the same baseline cannot both succeed; the second sees
BASELINE_CONFLICT. A replay with the same operation id returns the recorded
outcome without acting again.

Coverage scope: every fleet member carries the scope it was last published
under (terminal, source fleet, location, voyage or profile population). A
full snapshot compares against, and retires within, its own scope only; a
member that appears in a snapshot for another scope moves to that scope.

Retirement (a fleet key in scope but absent from the candidate) is only
applied for a complete full snapshot: parsing finished, the declared record
count matched, and nothing was quarantined. A partial or quarantined
snapshot retires nothing. An empty snapshot needs an explicit decision.
Incremental intents never retire. explicit_removal intents retire only what
the file lists.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Dict, List, Optional

from . import jobs as jobq
from .store import audit, iter_rows, new_id, now_iso, transaction


class PublishError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def new_generation(conn: sqlite3.Connection, workspace_id: str, intent_id: str, job_id: str,
                   scope_kind: str, scope_value: str, baseline_id: Optional[str]) -> str:
    gid = new_id("gen")
    conn.execute("""INSERT INTO generations (id, workspace_id, scope_kind, scope_value, state, baseline_generation_id,
                    import_intent_id, job_id, created_at) VALUES (?,?,?,?,'candidate',?,?,?,?)""",
                 (gid, workspace_id, scope_kind, scope_value, baseline_id, intent_id, job_id, now_iso()))
    return gid


def current_generation(conn: sqlite3.Connection, workspace_id: str) -> Optional[str]:
    row = conn.execute("SELECT current_generation_id FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
    return row["current_generation_id"] if row else None


def fleet_count(conn: sqlite3.Connection, workspace_id: str, scope_kind: Optional[str] = None,
                scope_value: Optional[str] = None) -> int:
    if scope_kind is None:
        return conn.execute("SELECT COUNT(*) FROM fleet WHERE workspace_id = ?", (workspace_id,)).fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM fleet WHERE workspace_id = ? AND scope_kind = ? AND scope_value = ?",
                        (workspace_id, scope_kind, scope_value)).fetchone()[0]


def completeness(conn: sqlite3.Connection, job: sqlite3.Row, intent: sqlite3.Row) -> Dict[str, Any]:
    c = jobq.counters(conn, job["id"])
    ck = jobq.load_checkpoint(conn, job["id"])
    total = c.get("records_total", 0)
    quarantined = c.get("records_quarantined", 0)
    declared = intent["declared_record_count"]
    reasons = []
    if not ck.get("done"):
        reasons.append("parsing did not complete")
    if declared is not None and declared != total:
        reasons.append(f"declared {declared} records, parsed {total}")
    if quarantined:
        reasons.append(f"{quarantined} record(s) quarantined")
    return {"complete": not reasons, "reasons": reasons, "records_total": total,
            "records_quarantined": quarantined, "declared": declared}


def compare(conn: sqlite3.Connection, workspace_id: str, candidate_id: str, scope_kind: str,
            scope_value: str) -> Dict[str, int]:
    """Set comparison of candidate memberships against the published fleet by key.

    added     candidate keys not in the fleet at all
    changed   keys in the fleet whose attributes or scope differ
    unchanged keys in the fleet with the same attributes and scope
    removed   fleet keys in this scope that the candidate does not list
    """
    q = """
        SELECT
          (SELECT COUNT(*) FROM memberships c WHERE c.generation_id = :cand
             AND NOT EXISTS (SELECT 1 FROM fleet f WHERE f.workspace_id = :ws AND f.key = c.key)) AS added,
          (SELECT COUNT(*) FROM fleet f WHERE f.workspace_id = :ws AND f.scope_kind = :sk AND f.scope_value = :sv
             AND NOT EXISTS (SELECT 1 FROM memberships c WHERE c.generation_id = :cand AND c.key = f.key)) AS removed,
          (SELECT COUNT(*) FROM memberships c JOIN fleet f ON f.workspace_id = :ws AND f.key = c.key
             WHERE c.generation_id = :cand AND (c.attrs_hash <> f.attrs_hash OR f.scope_kind <> :sk OR f.scope_value <> :sv)) AS changed,
          (SELECT COUNT(*) FROM memberships c JOIN fleet f ON f.workspace_id = :ws AND f.key = c.key
             WHERE c.generation_id = :cand AND c.attrs_hash = f.attrs_hash AND f.scope_kind = :sk AND f.scope_value = :sv) AS unchanged,
          (SELECT COUNT(*) FROM memberships WHERE generation_id = :cand) AS candidate,
          (SELECT COUNT(*) FROM fleet WHERE workspace_id = :ws) AS baseline,
          (SELECT COUNT(*) FROM fleet WHERE workspace_id = :ws AND scope_kind = :sk AND scope_value = :sv) AS baseline_in_scope
    """
    r = conn.execute(q, {"cand": candidate_id, "ws": workspace_id, "sk": scope_kind, "sv": scope_value}).fetchone()
    return {k: r[k] for k in r.keys()}


def change_manifest_sha256(conn: sqlite3.Connection, workspace_id: str, candidate_id: str, scope_kind: str,
                           scope_value: str, retire: bool) -> str:
    """Hash of the ordered effect list (key, effect, attrs) so a replay can be recognised."""
    h = hashlib.sha256()
    sql = """
        SELECT key, effect, attrs_hash FROM (
          SELECT c.key AS key, 'added' AS effect, c.attrs_hash AS attrs_hash FROM memberships c
            WHERE c.generation_id = :cand AND NOT EXISTS
              (SELECT 1 FROM fleet f WHERE f.workspace_id = :ws AND f.key = c.key)
          UNION ALL
          SELECT c.key, 'changed', c.attrs_hash FROM memberships c JOIN fleet f
            ON f.workspace_id = :ws AND f.key = c.key
            WHERE c.generation_id = :cand AND (c.attrs_hash <> f.attrs_hash OR f.scope_kind <> :sk OR f.scope_value <> :sv)
          UNION ALL
          SELECT f.key, 'removed', f.attrs_hash FROM fleet f
            WHERE :retire AND f.workspace_id = :ws AND f.scope_kind = :sk AND f.scope_value = :sv AND NOT EXISTS
              (SELECT 1 FROM memberships c WHERE c.generation_id = :cand AND c.key = f.key)
        ) ORDER BY key, effect"""
    params = {"cand": candidate_id, "ws": workspace_id, "sk": scope_kind, "sv": scope_value,
              "retire": 1 if retire else 0}
    for row in iter_rows(conn, sql, params):
        h.update(f"{row[0]}\t{row[1]}\t{row[2]}\n".encode("utf-8"))
    return h.hexdigest()


GATE_CODES = ("IMPORT_INTENT_UNRESOLVED", "SNAPSHOT_INCOMPLETE", "EMPTY_SNAPSHOT_DECISION_REQUIRED",
              "BASELINE_CONFLICT", "IDENTITY_COLLISION")


def finish_job(conn: sqlite3.Connection, job: sqlite3.Row, worker_id: str) -> None:
    """After streaming: intent gates, comparison, manifest, findings, generation state."""
    job_id = job["id"]
    ws = job["workspace_id"]
    from .ingest import add_finding
    if not job["import_intent_id"]:
        with transaction(conn):
            conn.execute("DELETE FROM findings WHERE job_id = ? AND code = 'IMPORT_INTENT_UNRESOLVED'", (job_id,))
            add_finding(conn, job_id, "IMPORT_INTENT_UNRESOLVED", "no import intent declared; comparison only")
        return
    intent = conn.execute("SELECT * FROM import_intents WHERE id = ?", (job["import_intent_id"],)).fetchone()
    gen_id = job["generation_id"]
    with transaction(conn):
        conn.execute("DELETE FROM findings WHERE job_id = ? AND code IN (%s)" % ",".join("?" * len(GATE_CODES)),
                     (job_id, *GATE_CODES))
        if intent["mode"] == "comparison_only" or gen_id is None:
            add_finding(conn, job_id, "IMPORT_INTENT_UNRESOLVED", "comparison only: nothing will be applied")
            return
        sk, sv = intent["scope_kind"], intent["scope_value"]
        blocked: List[str] = []
        current = current_generation(conn, ws)
        if (intent["baseline_generation_id"] or None) != (current or None):
            add_finding(conn, job_id, "BASELINE_CONFLICT",
                        f"pinned baseline {intent['baseline_generation_id']} is not the current generation {current}")
            blocked.append("BASELINE_CONFLICT")
        comp = completeness(conn, job, intent)
        counts = compare(conn, ws, gen_id, sk, sv)
        # Identity collisions are computed from storage after streaming, so a
        # job that resumed after a crash still sees pairs on both sides of it.
        collisions = conn.execute(
            "SELECT COUNT(*) FROM (SELECT normalized FROM observations WHERE job_id = ? "
            "AND status IN ('valid','corrected','flagged') GROUP BY normalized "
            "HAVING COUNT(DISTINCT attrs_hash) > 1)", (job_id,)).fetchone()[0]
        jobq.set_counter(conn, job_id, "identity_collisions", collisions)
        if collisions:
            add_finding(conn, job_id, "IDENTITY_COLLISION",
                        f"{collisions} identifier(s) appear more than once with different attributes")
        retire = 0
        mode = intent["mode"]
        if mode == "full_snapshot":
            if counts["candidate"] == 0 and not intent["empty_scope_decision"]:
                add_finding(conn, job_id, "EMPTY_SNAPSHOT_DECISION_REQUIRED",
                            "the snapshot lists no identifiers; state whether the scope is really empty")
                blocked.append("EMPTY_SNAPSHOT_DECISION_REQUIRED")
            if not comp["complete"]:
                add_finding(conn, job_id, "SNAPSHOT_INCOMPLETE", "; ".join(comp["reasons"]))
                blocked.append("SNAPSHOT_INCOMPLETE")
            if not blocked:
                retire = counts["removed"]
        elif mode == "explicit_removal":
            retire = counts["unchanged"] + counts["changed"]
        if intent["change_volume_limit"] is not None:
            volume = retire if mode == "explicit_removal" else counts["added"] + counts["changed"] + retire
            if volume > intent["change_volume_limit"]:
                add_finding(conn, job_id, "SNAPSHOT_INCOMPLETE",
                            f"change volume {volume} exceeds the declared limit {intent['change_volume_limit']}")
                blocked.append("CHANGE_VOLUME")
        effects = dict(counts)
        effects["mode"] = mode
        effects["scope"] = {"kind": sk, "value": sv}
        effects["retire"] = retire
        effects["retire_withheld"] = counts["removed"] - retire if mode == "full_snapshot" else 0
        effects["complete"] = comp["complete"]
        effects["blocked"] = blocked
        manifest = change_manifest_sha256(conn, ws, gen_id, sk, sv, retire > 0 and mode == "full_snapshot")
        state = "publishable" if not blocked else "candidate"
        conn.execute("UPDATE generations SET state = ?, member_count = ?, change_manifest_sha256 = ?, "
                     "change_counts_json = ? WHERE id = ?",
                     (state, counts["candidate"], manifest, json.dumps(effects, sort_keys=True), gen_id))


def effects(conn: sqlite3.Connection, generation_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM generations WHERE id = ?", (generation_id,)).fetchone()
    if row is None:
        raise PublishError("UNKNOWN_GENERATION")
    out = dict(row)
    out["effects"] = json.loads(row["change_counts_json"] or "{}")
    return out


def publish(conn: sqlite3.Connection, generation_id: str, actor: str, *,
            operation_id: Optional[str] = None) -> Dict[str, Any]:
    """Atomically apply a publishable generation to the fleet and make it current."""
    with transaction(conn):
        gen = conn.execute("SELECT * FROM generations WHERE id = ?", (generation_id,)).fetchone()
        if gen is None:
            raise PublishError("UNKNOWN_GENERATION")
        ws = gen["workspace_id"]
        if operation_id:
            prior = conn.execute("SELECT detail FROM audit_events WHERE action = 'generation.publish' AND subject = ? "
                                 "AND detail LIKE ?", (generation_id, f"op={operation_id} %")).fetchone()
            if prior is not None:
                return {"generation_id": generation_id, "state": gen["state"], "replayed": True}
        if gen["state"] == "published":
            return {"generation_id": generation_id, "state": "published", "replayed": True}
        if gen["state"] != "publishable":
            fx = json.loads(gen["change_counts_json"] or "{}")
            raise PublishError("PUBLICATION_BLOCKED", ", ".join(fx.get("blocked") or [gen["state"]]))
        current = current_generation(conn, ws)
        if (gen["baseline_generation_id"] or None) != (current or None):
            raise PublishError("BASELINE_CONFLICT",
                               f"pinned baseline {gen['baseline_generation_id']} is not current ({current})")
        fx = json.loads(gen["change_counts_json"] or "{}")
        mode = fx.get("mode", "incremental")
        sk, sv = gen["scope_kind"], gen["scope_value"]
        applied = {"upserted": 0, "retired": 0}
        if mode == "explicit_removal":
            cur = conn.execute("DELETE FROM fleet WHERE workspace_id = ? AND key IN "
                               "(SELECT key FROM memberships WHERE generation_id = ?)", (ws, generation_id))
            applied["retired"] = cur.rowcount
        else:
            cur = conn.execute(
                "INSERT INTO fleet (workspace_id, key, attrs_hash, generation_id, scope_kind, scope_value) "
                "SELECT ?, key, attrs_hash, ?, ?, ? FROM memberships WHERE generation_id = ? "
                "ON CONFLICT(workspace_id, key) DO UPDATE SET attrs_hash = excluded.attrs_hash, "
                "generation_id = excluded.generation_id, scope_kind = excluded.scope_kind, scope_value = excluded.scope_value "
                "WHERE fleet.attrs_hash <> excluded.attrs_hash OR fleet.scope_kind <> excluded.scope_kind "
                "OR fleet.scope_value <> excluded.scope_value",
                (ws, generation_id, sk, sv, generation_id))
            applied["upserted"] = cur.rowcount
            if mode == "full_snapshot" and fx.get("retire", 0) > 0:
                cur = conn.execute("DELETE FROM fleet WHERE workspace_id = ? AND scope_kind = ? AND scope_value = ? "
                                   "AND key NOT IN (SELECT key FROM memberships WHERE generation_id = ?)",
                                   (ws, sk, sv, generation_id))
                applied["retired"] = cur.rowcount
        ts = now_iso()
        cur = conn.execute("UPDATE generations SET state = 'published', published_at = ? WHERE id = ? AND state = 'publishable'",
                           (ts, generation_id))
        if cur.rowcount != 1:
            raise PublishError("BASELINE_CONFLICT", "generation changed state during publication")
        if current:
            conn.execute("UPDATE generations SET state = 'superseded' WHERE id = ? AND state = 'published'", (current,))
        cur = conn.execute("UPDATE workspaces SET current_generation_id = ? WHERE id = ? AND current_generation_id IS ?",
                           (generation_id, ws, current))
        if cur.rowcount != 1:
            raise PublishError("BASELINE_CONFLICT", "workspace pointer moved during publication")
        audit(conn, ws, actor, "generation.publish", generation_id,
              f"op={operation_id or ''} baseline={current or ''} manifest={gen['change_manifest_sha256']} "
              f"upserted={applied['upserted']} retired={applied['retired']}")
        return {"generation_id": generation_id, "state": "published", "replayed": False, "superseded": current,
                "published_at": ts, "applied": applied, "fleet_count": fleet_count(conn, ws),
                "fleet_in_scope": fleet_count(conn, ws, sk, sv)}


def abandon(conn: sqlite3.Connection, generation_id: str, actor: str) -> Dict[str, Any]:
    with transaction(conn):
        gen = conn.execute("SELECT workspace_id, state FROM generations WHERE id = ?", (generation_id,)).fetchone()
        if gen is None:
            raise PublishError("UNKNOWN_GENERATION")
        if gen["state"] in ("published", "superseded"):
            raise PublishError("CANNOT_ABANDON", "only unpublished generations can be abandoned")
        if gen["state"] == "abandoned":
            return {"generation_id": generation_id, "state": "abandoned", "replayed": True}
        conn.execute("UPDATE generations SET state = 'abandoned' WHERE id = ?", (generation_id,))
        audit(conn, gen["workspace_id"], actor, "generation.abandon", generation_id)
        return {"generation_id": generation_id, "state": "abandoned", "replayed": False}


def members_page(conn: sqlite3.Connection, generation_id: str, after: str = "", limit: int = 500):
    rows = conn.execute("SELECT key, attrs_hash FROM memberships WHERE generation_id = ? AND key > ? ORDER BY key LIMIT ?",
                        (generation_id, after, limit)).fetchall()
    return [dict(r) for r in rows]


def fleet_page(conn: sqlite3.Connection, workspace_id: str, after: str = "", limit: int = 500,
               scope_kind: Optional[str] = None, scope_value: Optional[str] = None):
    if scope_kind:
        rows = conn.execute("SELECT key, attrs_hash, generation_id, scope_kind, scope_value FROM fleet "
                            "WHERE workspace_id = ? AND scope_kind = ? AND scope_value = ? AND key > ? ORDER BY key LIMIT ?",
                            (workspace_id, scope_kind, scope_value or "", after, limit)).fetchall()
    else:
        rows = conn.execute("SELECT key, attrs_hash, generation_id, scope_kind, scope_value FROM fleet "
                            "WHERE workspace_id = ? AND key > ? ORDER BY key LIMIT ?",
                            (workspace_id, after, limit)).fetchall()
    return [dict(r) for r in rows]


def fleet_member(conn: sqlite3.Connection, workspace_id: str, key: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT key, attrs_hash, generation_id, scope_kind, scope_value FROM fleet "
                       "WHERE workspace_id = ? AND key = ?", (workspace_id, key)).fetchone()
    return dict(row) if row else None


def generations_page(conn: sqlite3.Connection, workspace_id: str, after: str = "", limit: int = 100):
    rows = conn.execute("SELECT * FROM generations WHERE workspace_id = ? AND id > ? ORDER BY id LIMIT ?",
                        (workspace_id, after, limit)).fetchall()
    return [dict(r) for r in rows]
