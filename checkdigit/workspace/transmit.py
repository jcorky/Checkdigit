"""
transmit.py
===========
Transmission of artifacts over enabled connections, with the outcome kept
separate from the artifact, the acknowledgment and the business processing.

Preconditions for a send: the artifact is ready, the connection is enabled
and outbound, the job ran under the connection's profile (which must be
production_enabled), and the approval behind the artifact carries the
`transmission` authorization scope recorded by an administrator. The attempt
row moves queued -> sending -> delivery_confirmed | failed | outcome_unknown.
A transport timeout is `outcome_unknown` and raises
DELIVERY_OUTCOME_UNKNOWN; a worker that dies mid-send leaves `sending`, and
recovery marks it `outcome_unknown` once its lease expires. A resend after an
unknown outcome happens automatically only when the receiver is known to
reject duplicates; otherwise it needs an administrator's note.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

from . import connections as conn_mod
from .store import audit, new_id, now_iso, transaction

SEND_LEASE_SECONDS = 300.0


class TransmitError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def authorize_transmission(conn: sqlite3.Connection, approval_id: str, actor: str, note: str) -> Dict[str, Any]:
    """Separate from approving the draft: an administrator authorizes transmission of this approval's edits."""
    apr = conn.execute("SELECT a.*, j.workspace_id FROM approvals a JOIN jobs j ON j.id = a.job_id WHERE a.id = ?", (approval_id,)).fetchone()
    if apr is None:
        raise TransmitError("UNKNOWN_APPROVAL")
    if apr["decision"] != "approved" or apr["state"] != "recorded":
        raise TransmitError("NOT_APPROVED", f"approval is {apr['decision']} / {apr['state']}")
    if len(note.strip()) < 8:
        raise TransmitError("EVIDENCE_REQUIRED", "name the customer's transmission authorization in the note")
    with transaction(conn):
        conn.execute("UPDATE approvals SET authorization_scope = 'transmission' WHERE id = ?", (approval_id,))
        audit(conn, apr["workspace_id"], actor, "approval.authorize_transmission", approval_id, note)
    from .views import approval_doc
    return approval_doc(conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone())


def _control_reference(conn: sqlite3.Connection, job_id: str, artifact_id: str) -> str:
    row = conn.execute("SELECT transport_envelope_ref, message_ref FROM message_transactions WHERE job_id = ? "
                       "ORDER BY message_no LIMIT 1", (job_id,)).fetchone()
    if row and row["transport_envelope_ref"]:
        return row["transport_envelope_ref"]
    if row and row["message_ref"]:
        return row["message_ref"]
    return artifact_id


def send_artifact(conn: sqlite3.Connection, root: str, workspace_id: str, artifact_id: str, connection_id: str, actor: str, *,
                  idempotency_key: str, resend_of: Optional[str] = None, resend_note: Optional[str] = None,
                  transport=None) -> Dict[str, Any]:
    prior = conn.execute("SELECT * FROM delivery_attempts WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
    if prior is not None:
        out = attempt_doc(conn, prior["id"])
        out["replayed"] = True
        return out
    art = conn.execute("SELECT a.*, j.profile_id AS job_profile_id, j.id AS job_id, j.workspace_id FROM export_artifacts a "
                       "JOIN jobs j ON j.id = a.job_id WHERE a.id = ? AND j.workspace_id = ?", (artifact_id, workspace_id)).fetchone()
    if art is None:
        raise TransmitError("UNKNOWN_ARTIFACT")
    if art["state"] != "ready" or not art["path"]:
        raise TransmitError("ARTIFACT_NOT_READY", f"artifact is {art['state']}")
    con = conn_mod.connection_row(conn, connection_id)
    if con["workspace_id"] != workspace_id:
        raise TransmitError("UNKNOWN_CONNECTION")
    if con["state"] != "enabled" or con["direction"] != "outbound":
        raise TransmitError("CONNECTION_NOT_ENABLED", f"connection is {con['state']} {con['direction']}")
    if not con["profile_id"] or con["profile_id"] != art["job_profile_id"]:
        raise TransmitError("PROFILE_MISMATCH", "the artifact's job did not run under the connection's receiver profile")
    prof = conn.execute("SELECT verification_state FROM feed_profiles WHERE id = ?", (con["profile_id"],)).fetchone()
    if prof is None or prof["verification_state"] != "production_enabled":
        raise TransmitError("PROFILE_UNVERIFIED", "the receiver profile is not production_enabled")
    if not art["approval_id"]:
        raise TransmitError("TRANSMISSION_NOT_AUTHORIZED", "an unchanged copy carries no transmission authorization")
    apr = conn.execute("SELECT authorization_scope, state FROM approvals WHERE id = ?", (art["approval_id"],)).fetchone()
    if apr is None or apr["authorization_scope"] != "transmission" or apr["state"] != "recorded":
        raise TransmitError("TRANSMISSION_NOT_AUTHORIZED", "the approval behind this artifact is not authorized for transmission")
    manifest = json.loads(art["manifest_json"] or "{}")
    local_path = os.path.join(art["path"], manifest["files"]["corrected"])
    control = _control_reference(conn, art["job_id"], artifact_id)
    did = new_id("dlv")
    base_name, ext = os.path.splitext(manifest["files"]["corrected"])
    remote_name = manifest["files"]["corrected"] if resend_of is None else f"{base_name}.resend-{did[-8:]}{ext}"
    ts = now_iso()
    with transaction(conn):
        conn.execute("INSERT INTO delivery_attempts (id, workspace_id, artifact_id, destination, state, idempotency_key, started_at, "
                     "control_reference, recorded_by, connection_id, origin, lease_until, resend_of_id, resend_note, updated_at, created_at) "
                     "VALUES (?,?,?,?,'queued',?,?,?,?,?,'connector',?,?,?,?,?)",
                     (did, workspace_id, artifact_id, f"{con['kind']}:{con['name']}", idempotency_key, ts, control, actor,
                      connection_id, time.time() + SEND_LEASE_SECONDS, resend_of, resend_note, ts, ts))
        conn.execute("UPDATE delivery_attempts SET state = 'sending', updated_at = ? WHERE id = ?", (now_iso(), did))
        audit(conn, workspace_id, actor, "delivery.send", did, f"artifact={artifact_id} connection={connection_id} control={control}")
    transport = transport or conn_mod.transport_for(con)
    try:
        receipt = transport.send(local_path, remote_name)
        state, evidence, error = "delivery_confirmed", json.dumps(receipt, sort_keys=True), None
    except TimeoutError as exc:
        state, evidence, error = "outcome_unknown", None, f"timeout: {exc}"
    except conn_mod.TransportUnavailable as exc:
        state, evidence, error = "failed", None, f"transport unavailable: {exc}"
    except Exception as exc:  # noqa: BLE001
        state, evidence, error = "failed", None, f"{type(exc).__name__}: {exc}"
    with transaction(conn):
        conn.execute("UPDATE delivery_attempts SET state = ?, outcome_evidence = ?, receipt_json = ?, error = ?, lease_until = NULL, "
                     "updated_at = ? WHERE id = ?", (state, evidence, evidence, error, now_iso(), did))
        if state == "outcome_unknown":
            from .ingest import add_finding
            add_finding(conn, art["job_id"], "DELIVERY_OUTCOME_UNKNOWN", f"delivery {did} via {con['name']}: {error}", f"delivery {did}")
        audit(conn, workspace_id, actor, "delivery.outcome", did, f"state={state} {error or ''}".strip())
    out = attempt_doc(conn, did)
    out["replayed"] = False
    return out


def recover_stale(conn: sqlite3.Connection, workspace_id: Optional[str] = None) -> List[str]:
    """Attempts left in `sending` past their lease are outcome_unknown, never retried on their own."""
    now = time.time()
    rows = conn.execute("SELECT id, workspace_id, artifact_id, connection_id FROM delivery_attempts WHERE state = 'sending' "
                        "AND lease_until IS NOT NULL AND lease_until < ?" + (" AND workspace_id = ?" if workspace_id else ""),
                        (now, workspace_id) if workspace_id else (now,)).fetchall()
    out = []
    for r in rows:
        with transaction(conn):
            conn.execute("UPDATE delivery_attempts SET state = 'outcome_unknown', error = 'send lease expired without an outcome', "
                         "lease_until = NULL, updated_at = ? WHERE id = ?", (now_iso(), r["id"]))
            job = conn.execute("SELECT job_id FROM export_artifacts WHERE id = ?", (r["artifact_id"],)).fetchone()
            if job:
                from .ingest import add_finding
                add_finding(conn, job["job_id"], "DELIVERY_OUTCOME_UNKNOWN", f"delivery {r['id']}: send lease expired", f"delivery {r['id']}")
            audit(conn, r["workspace_id"], "automation", "delivery.recover", r["id"], "outcome_unknown after lease expiry")
        out.append(r["id"])
    return out


def resend(conn: sqlite3.Connection, root: str, workspace_id: str, attempt_id: str, actor: str, *,
           idempotency_key: str, note: Optional[str] = None, transport=None) -> Dict[str, Any]:
    prior = conn.execute("SELECT * FROM delivery_attempts WHERE id = ? AND workspace_id = ?", (attempt_id, workspace_id)).fetchone()
    if prior is None:
        raise TransmitError("UNKNOWN_DELIVERY")
    if prior["state"] not in ("failed", "outcome_unknown"):
        raise TransmitError("NOT_RESENDABLE", f"attempt is {prior['state']}")
    con = conn_mod.connection_row(conn, prior["connection_id"]) if prior["connection_id"] else None
    if con is None:
        raise TransmitError("NOT_RESENDABLE", "manual deliveries are re-recorded, not resent")
    if prior["state"] == "outcome_unknown" and con["duplicate_handling"] != "rejects_duplicates":
        if not note or len(note.strip()) < 8:
            raise TransmitError("RESEND_NOT_SAFE", f"the receiver's duplicate handling is {con['duplicate_handling']}; "
                                f"an administrator's note is required to resend after an unknown outcome")
    return send_artifact(conn, root, workspace_id, prior["artifact_id"], prior["connection_id"], actor,
                         idempotency_key=idempotency_key, resend_of=attempt_id, resend_note=note, transport=transport)


def attempt_doc(conn: sqlite3.Connection, attempt_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM delivery_attempts WHERE id = ?", (attempt_id,)).fetchone()
    if row is None:
        raise TransmitError("UNKNOWN_DELIVERY")
    out = {"id": row["id"], "artifact_id": row["artifact_id"], "destination": row["destination"], "state": row["state"],
           "idempotency_key": row["idempotency_key"], "started_at": row["started_at"], "control_reference": row["control_reference"],
           "outcome_evidence": row["outcome_evidence"], "recorded_by": row["recorded_by"], "connection_id": row["connection_id"],
           "origin": row["origin"], "receipt": json.loads(row["receipt_json"]) if row["receipt_json"] else None,
           "error": row["error"], "resend_of_id": row["resend_of_id"], "resend_note": row["resend_note"],
           "created_at": row["created_at"], "updated_at": row["updated_at"]}
    return out


def list_attempts(conn: sqlite3.Connection, workspace_id: str, after: str = "", limit: int = 100) -> List[Dict[str, Any]]:
    rows = conn.execute("SELECT id FROM delivery_attempts WHERE workspace_id = ? AND id > ? ORDER BY id LIMIT ?",
                        (workspace_id, after, limit)).fetchall()
    return [attempt_doc(conn, r["id"]) for r in rows]
