"""
views.py
========
Contract-shaped documents for the API. Each function turns storage rows into
the entity shape in contracts/entities.schema.json (validated by
test_workspace.py) and adds operational fields the schema leaves open.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from contracts import make_finding

from . import jobs as jobq


def workspace_doc(conn: sqlite3.Connection, row: sqlite3.Row) -> Dict[str, Any]:
    from . import reconcile
    out = {"id": row["id"], "name": row["name"],
           "retention_policy": {"source_retention_days": row["retention_days"],
                                "third_party_data_retention": row["third_party_data_retention"]},
           "current_generation_id": row["current_generation_id"], "created_at": row["created_at"]}
    out["jobs"] = {r["state"]: r["n"] for r in conn.execute(
        "SELECT state, COUNT(*) AS n FROM jobs WHERE workspace_id = ? GROUP BY state", (row["id"],))}
    out["fleet_count"] = reconcile.fleet_count(conn, row["id"])
    return out


def source_doc(row: sqlite3.Row) -> Dict[str, Any]:
    return {"id": row["id"], "workspace_id": row["workspace_id"], "filename": row["filename"],
            "size_bytes": row["size_bytes"], "sha256": row["sha256"], "encoding": row["encoding"],
            "declared_content_type": row["declared_content_type"], "received_at": row["received_at"],
            "immutable": True}


def intent_doc(conn: sqlite3.Connection, row: sqlite3.Row) -> Dict[str, Any]:
    job = conn.execute("SELECT id FROM jobs WHERE import_intent_id = ? ORDER BY created_at LIMIT 1", (row["id"],)).fetchone()
    return {"id": row["id"], "job_id": job["id"] if job else "pending", "workspace_id": row["workspace_id"],
            "mode": row["mode"], "source_file_id": row["source_file_id"],
            "scope": {"kind": row["scope_kind"], "value": row["scope_value"]},
            "effective_time": row["effective_time"], "baseline_generation_id": row["baseline_generation_id"],
            "field_update_policy": row["field_update_policy"], "change_volume_limit": row["change_volume_limit"],
            "empty_scope_decision": row["empty_scope_decision"], "intent_fingerprint": row["fingerprint"],
            "declared_record_count": row["declared_record_count"], "created_at": row["created_at"]}


def job_doc(conn: sqlite3.Connection, row: sqlite3.Row) -> Dict[str, Any]:
    counters = jobq.counters(conn, row["id"])
    ck = json.loads(row["checkpoint_json"] or "{}")
    member_count = None
    if row["generation_id"]:
        g = conn.execute("SELECT member_count FROM generations WHERE id = ?", (row["generation_id"],)).fetchone()
        member_count = g["member_count"] if g else None
    counts = {"logical_records": counters.get("records_total", 0),
              "identifier_occurrences": counters.get("identifiers_total", 0)}
    if member_count is not None:
        counts["unique_equipment"] = member_count
    out = {"id": row["id"], "workspace_id": row["workspace_id"], "source_file_id": row["source_file_id"],
           "profile_id": row["profile_id"], "profile_version": row["profile_version"],
           "import_intent_id": row["import_intent_id"], "state": row["state"], "created_at": row["created_at"],
           "updated_at": row["updated_at"], "counts": counts,
           "dependency_pins": {"parser_version": row["parser_version"], "ruleset_version": row["ruleset_version"],
                               "policy_fingerprint": row["policy_fingerprint"]},
           "retry_count": max(0, row["attempts"] - 1), "partial": not bool(ck.get("done")),
           "step": row["step"], "attempts": row["attempts"], "error": row["error"],
           "analysis_version": row["analysis_version"], "generation_id": row["generation_id"],
           "cancel_requested": bool(row["cancel_requested"]), "options": json.loads(row["options_json"] or "{}"),
           "checkpoint": ck, "counters": counters}
    out["findings"] = {r["code"]: r["n"] for r in conn.execute(
        "SELECT code, COUNT(*) AS n FROM findings WHERE job_id = ? GROUP BY code", (row["id"],))}
    out["decisions"] = {r["decision"]: r["n"] for r in conn.execute(
        "SELECT decision, COUNT(*) AS n FROM observations WHERE job_id = ? AND candidate IS NOT NULL GROUP BY decision",
        (row["id"],))}
    out["blocking"] = sorted({r["code"] for r in conn.execute(
        "SELECT code FROM findings WHERE job_id = ? AND severity = 'blocking'", (row["id"],))})
    return out


def generation_doc(row: sqlite3.Row) -> Dict[str, Any]:
    out = {"id": row["id"], "workspace_id": row["workspace_id"],
           "scope": {"kind": row["scope_kind"], "value": row["scope_value"]}, "state": row["state"],
           "baseline_generation_id": row["baseline_generation_id"], "import_intent_id": row["import_intent_id"] or "",
           "published_at": row["published_at"], "job_id": row["job_id"], "member_count": row["member_count"],
           "created_at": row["created_at"], "effects": json.loads(row["change_counts_json"] or "{}")}
    if row["change_manifest_sha256"]:
        out["change_manifest_sha256"] = row["change_manifest_sha256"]
    return out


def change_set_doc(conn: sqlite3.Connection, row: sqlite3.Row) -> Dict[str, Any]:
    job = conn.execute("SELECT generation_id, parser_version, ruleset_version, policy_fingerprint FROM jobs WHERE id = ?",
                       (row["job_id"],)).fetchone()
    baseline = None
    if job and job["generation_id"]:
        g = conn.execute("SELECT baseline_generation_id FROM generations WHERE id = ?", (job["generation_id"],)).fetchone()
        baseline = g["baseline_generation_id"] if g else None
    return {"id": row["id"], "job_id": row["job_id"], "analysis_version": row["analysis_version"],
            "edits": [], "edit_count": row["edit_count"],
            "edits_ref": f"jobs/{row['job_id']}/observations?decision=approved",
            "fingerprint": row["fingerprint"], "fingerprint_version": row["fingerprint_version"],
            "baseline_generation_id": baseline, "destination_profile_id": None,
            "rule_versions": {"parser": job["parser_version"], "ruleset": job["ruleset_version"],
                              "policy": job["policy_fingerprint"]} if job else {},
            "created_at": row["created_at"]}


DECISION_VERB = {"approved": "approve", "rejected": "reject", "deferred": "defer"}


def approval_doc(row: sqlite3.Row, check: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    state = "stale" if row["state"] == "stale" else row["decision"]
    out = {"id": row["id"], "job_id": row["job_id"], "change_set_id": row["change_set_id"],
           "change_set_fingerprint": row["change_set_fingerprint"], "approver": row["approver"],
           "authorization_scope": row["authorization_scope"], "decided_at": row["decided_at"],
           "decision": DECISION_VERB.get(row["decision"], row["decision"]), "state": state,
           "stale_reason": row["stale_reason"],
           "selection_manifest": {"count": row["selection_count"], "digest": row["selection_digest"],
                                  "storage_ref": f"selection_members/{row['id']}"},
           "previous_approval_id": None, "filter": json.loads(row["filter_json"]),
           "operation_id": row["operation_id"]}
    if check is not None:
        out["check"] = check
    return out


def artifact_doc(row: sqlite3.Row) -> Dict[str, Any]:
    manifest = json.loads(row["manifest_json"]) if row["manifest_json"] else None
    out = {"id": row["id"], "job_id": row["job_id"], "change_set_id": row["change_set_id"] or "",
           "approval_ids": [row["approval_id"]] if row["approval_id"] else [], "state": row["state"],
           "output_mode": row["output_mode"], "loss_report": None, "profile_id": None,
           "rule_versions": {"parser": manifest["parser_version"], "ruleset": manifest["ruleset_version"],
                             "policy": manifest.get("policy_fingerprint", "")} if manifest else {},
           "counts": {"edits_applied": row["edits_applied"], "exceptions": manifest["exceptions"] if manifest else None,
                      "size_bytes": row["size_bytes"]},
           "built_at": row["built_at"], "created_at": row["created_at"], "error": row["error"],
           "lineage": {"source_file_id": manifest["source"]["id"] if manifest else "",
                       "previous_artifact_id": row["previous_artifact_id"], "repair_of_feedback_id": None},
           "manifest": manifest, "idempotency_key": row["idempotency_key"]}
    if row["sha256"]:
        out["sha256"] = row["sha256"]
    return out


def finding_doc(row: sqlite3.Row) -> Dict[str, Any]:
    out = make_finding(row["code"], id=str(row["id"]), detail=row["detail"])
    out["job_id"] = row["job_id"]
    out["created_at"] = row["created_at"]
    if row["location"]:
        out["location_label"] = row["location"]
    return out


def role_docs(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    return [{"user_id": r["user_id"], "workspace_id": r["workspace_id"], "role": r["role"]} for r in rows]
