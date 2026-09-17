"""
feedback.py
===========
Deliveries and receiver feedback (contracts/DEFAULTS.md, "Export and
receiver feedback").

Artifact, delivery, technical acknowledgment and business processing are
independent dimensions. Nothing here talks to a partner: deliveries are
recorded manually with their evidence, and feedback arrives as an uploaded
CONTRL or APERAK (EDIFACT), 997 or 824 (X12), or as a manual outcome. Each
feedback item is correlated to the message transactions it references:
exactly one match is `exact`; several is `ambiguous`; none is `unmatched`.
Codes the response profile does not define stay visible as unknown. A
rejection can start a linked repair job whose lineage names the feedback.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

from edifact_locator import Separators, detect_separators, parse_segment
from x12_locator import detect_delims

from .store import audit, new_id, now_iso, transaction

MANUAL_DELIVERY_STATES = ("delivery_confirmed", "failed", "outcome_unknown")
# UN/EDIFACT CONTRL action codes (0083) and X12 997 AK5/AK9 codes with their meaning.
CONTRL_ACTIONS = {"7": "accepted", "4": "rejected", "8": "rejected", "1": "accepted", "2": "accepted", "3": "rejected"}
X12_ACK = {"A": "accepted", "E": "accepted", "R": "rejected", "M": "rejected", "W": "rejected", "X": "rejected", "P": "partial"}
APERAK_FUNCTIONS = {"27": "rejected", "29": "accepted", "34": "accepted", "6": "accepted", "7": "rejected"}  # BGM 1225 / 4343


class FeedbackError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def record_delivery(conn: sqlite3.Connection, workspace_id: str, artifact_id: str, *, destination: str, state: str,
                    idempotency_key: str, actor: str, control_reference: Optional[str] = None,
                    outcome_evidence: Optional[str] = None, started_at: Optional[str] = None) -> Dict[str, Any]:
    if state not in MANUAL_DELIVERY_STATES:
        raise FeedbackError("BAD_STATE", f"manual deliveries record one of {MANUAL_DELIVERY_STATES}")
    if state == "delivery_confirmed" and not (outcome_evidence or "").strip():
        raise FeedbackError("EVIDENCE_REQUIRED", "a confirmed delivery needs its evidence (transfer log, receipt reference)")
    prior = conn.execute("SELECT * FROM delivery_attempts WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
    if prior is not None:
        out = dict(prior)
        out["replayed"] = True
        return out
    art = conn.execute("SELECT a.id, a.state, j.id AS job_id FROM export_artifacts a JOIN jobs j ON j.id = a.job_id "
                       "WHERE a.id = ? AND j.workspace_id = ?", (artifact_id, workspace_id)).fetchone()
    if art is None:
        raise FeedbackError("UNKNOWN_ARTIFACT")
    if art["state"] not in ("ready", "superseded"):
        raise FeedbackError("ARTIFACT_NOT_READY", "only a built artifact can have been delivered")
    did = new_id("dlv")
    with transaction(conn):
        conn.execute("INSERT INTO delivery_attempts (id, workspace_id, artifact_id, destination, state, idempotency_key, "
                     "started_at, control_reference, outcome_evidence, recorded_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (did, workspace_id, artifact_id, destination, state, idempotency_key, started_at, control_reference,
                      outcome_evidence, actor, now_iso()))
        if state == "outcome_unknown":
            from .ingest import add_finding
            add_finding(conn, art["job_id"], "DELIVERY_OUTCOME_UNKNOWN",
                        f"delivery {did} to {destination}: outcome unknown; no automatic resend", f"delivery {did}")
        audit(conn, workspace_id, actor, "delivery.record", did, f"artifact={artifact_id} state={state} dest={destination}")
    out = dict(conn.execute("SELECT * FROM delivery_attempts WHERE id = ?", (did,)).fetchone())
    out["replayed"] = False
    return out


def deliveries_for_artifact(conn: sqlite3.Connection, artifact_id: str) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM delivery_attempts WHERE artifact_id = ? ORDER BY id", (artifact_id,))]


# --------------------------------------------------------------------------- #
# Parsing acknowledgments
# --------------------------------------------------------------------------- #

def parse_feedback_text(text: str) -> Dict[str, Any]:
    """Extract references and codes from CONTRL, APERAK, 997 or 824 text."""
    stripped = text.lstrip("﻿ \r\n\t")
    items: List[Dict[str, Any]] = []
    refs: List[str] = []
    interchange_refs: List[str] = []
    kind = ""
    technical = "not_expected"
    business = "unknown"
    if stripped[:3] in ("UNA", "UNB", "UNH"):
        sep = detect_separators(stripped)
        from .stream import delimited_segments
        segs = list(delimited_segments(iter([stripped]), sep.segment, sep.release, skip_una=True))
        msg_type = ""
        for start, raw in segs:
            seg = parse_segment(raw, start, sep)
            els = seg.elements

            def el(i, j=0):
                try:
                    return els[i][j]
                except IndexError:
                    return ""
            if seg.tag == "UNH":
                msg_type = el(2, 0)
                kind = msg_type
            elif seg.tag == "UCI":
                interchange_refs.append(el(1))
                code = el(4)
                meaning = CONTRL_ACTIONS.get(code)
                items.append({"ref": el(1), "level": "interchange", "code": code, "meaning": meaning, "text": el(5)})
            elif seg.tag == "UCM":
                ref, code = el(1), el(3)
                refs.append(ref)
                items.append({"ref": ref, "level": "message", "code": code, "meaning": CONTRL_ACTIONS.get(code), "text": el(4)})
            elif seg.tag == "UCS" or seg.tag == "UCD":
                if items:
                    items[-1]["text"] = (items[-1].get("text") or "") + f" {seg.tag} {el(1)} {el(2)}".rstrip()
            elif seg.tag == "BGM" and msg_type == "APERAK":
                code = el(3)
                items.append({"ref": "", "level": "message", "code": code, "meaning": APERAK_FUNCTIONS.get(code), "text": el(2, 0)})
            elif seg.tag == "RFF" and msg_type == "APERAK":
                q, val = el(1, 0), el(1, 1)
                if q in ("ACW", "AAK", "AAM", "ACD", "MS", "ZZZ") and val:
                    refs.append(val)
                    if items:
                        items[-1]["ref"] = items[-1]["ref"] or val
            elif seg.tag == "ERC" and msg_type == "APERAK":
                code = el(1, 0)
                items.append({"ref": refs[-1] if refs else "", "level": "item", "code": code, "meaning": None, "text": ""})
            elif seg.tag == "FTX" and msg_type == "APERAK" and items:
                items[-1]["text"] = " ".join(c for c in (els[4] if len(els) > 4 else []) if c) or items[-1]["text"]
        if kind == "CONTRL":
            technical = "rejected" if any(i["meaning"] == "rejected" for i in items) else (
                "accepted" if items and all(i["meaning"] == "accepted" for i in items) else "unrecognized")
        elif kind == "APERAK":
            technical = "accepted"
            meanings = [i["meaning"] for i in items if i["level"] == "message"]
            business = "rejected" if "rejected" in meanings else ("accepted" if meanings and all(m == "accepted" for m in meanings) else "unknown")
    elif stripped[:3] in ("ISA", "GS*", "ST*") or stripped.startswith("ST"):
        d = detect_delims(stripped)
        from .stream import delimited_segments
        segs = list(delimited_segments(iter([stripped]), d.segment, "", skip_una=False))
        for start, raw in segs:
            els = raw.split(d.element)
            tag = els[0]

            def el(i):
                return els[i].strip() if i < len(els) else ""
            if tag == "ST":
                kind = el(1)
            elif tag == "AK1":
                interchange_refs.append(el(2))
            elif tag == "AK2":
                refs.append(el(2))
            elif tag == "AK5":
                code = el(1)
                items.append({"ref": refs[-1] if refs else "", "level": "message", "code": code, "meaning": X12_ACK.get(code),
                              "text": " ".join(e for e in els[2:] if e)})
            elif tag == "AK9":
                code = el(1)
                items.append({"ref": interchange_refs[-1] if interchange_refs else "", "level": "group", "code": code,
                              "meaning": X12_ACK.get(code), "text": ""})
            elif tag == "OTI":
                code, ref = el(1), el(3) or el(2)
                if ref:
                    refs.append(ref)
                meaning = {"TA": "accepted", "TR": "rejected", "TE": "accepted", "TP": "partial", "IA": "accepted",
                           "IR": "rejected"}.get(code)
                items.append({"ref": ref, "level": "message", "code": code, "meaning": meaning, "text": el(4)})
            elif tag == "TED":
                items.append({"ref": refs[-1] if refs else "", "level": "item", "code": el(1), "meaning": None, "text": el(2)})
        if kind == "997":
            meanings = [i["meaning"] for i in items if i["level"] == "message"]
            technical = "rejected" if "rejected" in meanings else ("accepted" if meanings and all(m == "accepted" for m in meanings) else "unrecognized")
        elif kind == "824":
            technical = "accepted"
            meanings = [i["meaning"] for i in items if i["level"] == "message"]
            business = ("rejected" if meanings and all(m == "rejected" for m in meanings) else "partially_accepted"
                        if "rejected" in meanings else "accepted" if meanings and all(m == "accepted" for m in meanings) else "unknown")
    else:
        raise FeedbackError("UNSUPPORTED_INPUT", "feedback must be an EDIFACT CONTRL/APERAK, an X12 997/824, or a manual outcome")
    return {"kind": kind, "refs": [r for r in refs if r], "interchange_refs": [r for r in interchange_refs if r],
            "items": items, "technical_ack_state": technical, "business_processing_state": business}


# --------------------------------------------------------------------------- #
# Correlation and recording
# --------------------------------------------------------------------------- #

def _correlate(conn: sqlite3.Connection, workspace_id: str, refs: List[str], interchange_refs: List[str]) -> Dict[str, Any]:
    matched: List[Dict[str, Any]] = []
    states: List[str] = []
    for ref in refs:
        rows = conn.execute("SELECT id, job_id, message_no, revision_key FROM message_transactions WHERE workspace_id = ? "
                            "AND (message_ref = ? OR document_id = ? OR transport_envelope_ref = ?) "
                            "AND lifecycle_resolution IN ('applied_scoped','superseded','cancelled','staged')",
                            (workspace_id, ref, ref, ref)).fetchall()
        if len(rows) > 1:
            # Feedback answers what was sent: prefer transactions of jobs with a recorded delivery.
            delivered = [r for r in rows if conn.execute(
                "SELECT 1 FROM delivery_attempts d JOIN export_artifacts a ON a.id = d.artifact_id WHERE a.job_id = ? LIMIT 1",
                (r["job_id"],)).fetchone()]
            if delivered:
                rows = delivered
        if len(rows) == 1:
            states.append("exact")
            matched.append({"ref": ref, "transaction_id": rows[0]["id"], "job_id": rows[0]["job_id"],
                            "message_no": rows[0]["message_no"]})
        elif len(rows) > 1:
            states.append("ambiguous")
            matched.append({"ref": ref, "candidates": [r["id"] for r in rows]})
        else:
            states.append("unmatched")
    attempt_id = None
    if interchange_refs:
        attempts = conn.execute("SELECT id FROM delivery_attempts WHERE workspace_id = ? AND control_reference IN (%s)"
                                % ",".join("?" * len(interchange_refs)), (workspace_id, *interchange_refs)).fetchall()
        if len(attempts) == 1:
            attempt_id = attempts[0]["id"]
        elif len(attempts) > 1:
            states.append("ambiguous")
    if not states:
        state = "unmatched"
    elif "ambiguous" in states:
        state = "ambiguous"
    elif all(s == "exact" for s in states):
        state = "exact"
    elif "exact" in states:
        state = "ambiguous"
    else:
        state = "unmatched"
    return {"state": state, "attempt_id": attempt_id, "matched": matched}


def ingest_feedback(conn: sqlite3.Connection, root: str, workspace_id: str, actor: str, *, origin: str,
                    text: Optional[str] = None, manual: Optional[Dict[str, Any]] = None,
                    response_profile: Optional[Dict[str, Any]] = None, decision_by: Optional[str] = None) -> Dict[str, Any]:
    """Record receiver feedback and correlate it. Returns the feedback document."""
    if origin not in ("manual_upload", "manual_outcome", "connector"):
        raise FeedbackError("BAD_ORIGIN", "origin is manual_upload, manual_outcome or connector")
    if origin == "connector" and text is None:
        raise FeedbackError("BAD_UPLOAD", "connector feedback is the acknowledgment text fetched from the inbox")
    if origin == "manual_outcome":
        if not manual or not manual.get("message_refs"):
            raise FeedbackError("BAD_OUTCOME", "a manual outcome names the message references it concerns")
        parsed = {"kind": "manual", "refs": list(manual["message_refs"]), "interchange_refs": list(manual.get("interchange_refs") or []),
                  "items": [{"ref": i.get("ref", ""), "level": "item", "code": str(i.get("code", "")), "meaning": None,
                             "text": i.get("text", "")} for i in manual.get("items") or []],
                  "technical_ack_state": manual.get("technical_ack_state", "not_expected"),
                  "business_processing_state": manual.get("business_processing_state", "unknown")}
        raw = json.dumps(manual, sort_keys=True)
        if not decision_by:
            raise FeedbackError("DECISION_BY_REQUIRED", "a manual outcome records who decided it")
    else:
        if not text:
            raise FeedbackError("BAD_UPLOAD", "no feedback text")
        parsed = parse_feedback_text(text)
        raw = text
    raw_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    prior = conn.execute("SELECT id FROM receiver_feedback WHERE workspace_id = ? AND raw_hash = ?", (workspace_id, raw_hash)).fetchone()
    if prior is not None:
        out = feedback_doc(conn, prior["id"])
        out["replayed"] = True
        return out
    codes = (response_profile or {}).get("rules", {}).get("feedback_codes") or {}
    known_default = {**CONTRL_ACTIONS, **X12_ACK, **APERAK_FUNCTIONS}
    for item in parsed["items"]:
        code = item["code"]
        if code in codes:
            item["known"] = True
            item["meaning"] = item.get("meaning") or codes[code]
        elif item.get("meaning") is not None or code in known_default:
            item["known"] = True
            item["meaning"] = item.get("meaning") or known_default.get(code)
        else:
            item["known"] = False
        # contract shape (receiver_feedback.items): export_item_ref + outcome
        item["export_item_ref"] = item.get("ref") or item.get("level") or "message"
        item["outcome"] = {"accepted": "accepted", "rejected": "rejected", "partial": "pending"}.get(item.get("meaning") or "", "unknown")
        item["source_record_id"] = None
    corr = _correlate(conn, workspace_id, parsed["refs"], parsed["interchange_refs"])
    fid = new_id("fb")
    with transaction(conn):
        conn.execute("INSERT INTO receiver_feedback (id, workspace_id, origin, raw_hash, received_at, response_profile_version, "
                     "response_kind, correlation_state, attempt_id, message_refs_json, matched_transactions_json, decision_by, "
                     "technical_ack_state, business_processing_state, items_json, detail, created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (fid, workspace_id, origin, raw_hash, now_iso(),
                      f"{response_profile['name']}/{response_profile['version']}" if response_profile else None,
                      parsed["kind"], corr["state"], corr["attempt_id"], json.dumps(parsed["refs"]),
                      json.dumps(corr["matched"]), decision_by, parsed["technical_ack_state"], parsed["business_processing_state"],
                      json.dumps(parsed["items"]), "", now_iso()))
        from .ingest import add_finding
        jobs = {m["job_id"] for m in corr["matched"] if m.get("job_id")}
        if corr["state"] == "unmatched":
            audit(conn, workspace_id, actor, "feedback.unmatched", fid, f"refs={parsed['refs']}")
        for job_id in jobs:
            loc = f"feedback {fid}"
            if corr["state"] == "ambiguous":
                add_finding(conn, job_id, "FEEDBACK_AMBIGUOUS", f"{parsed['kind']} matches more than one transaction or attempt", loc)
            for item in parsed["items"]:
                if not item["known"]:
                    add_finding(conn, job_id, "FEEDBACK_CODE_UNKNOWN", f"code {item['code']!r} ({item['level']}) is not defined by the response profile", loc)
        if not jobs and corr["state"] == "unmatched":
            pass
        audit(conn, workspace_id, actor, "feedback.record", fid,
              f"origin={origin} kind={parsed['kind']} correlation={corr['state']} technical={parsed['technical_ack_state']} "
              f"business={parsed['business_processing_state']}")
    out = feedback_doc(conn, fid)
    out["replayed"] = False
    if corr["state"] == "unmatched":
        out["finding"] = "FEEDBACK_UNMATCHED"
    return out


def feedback_doc(conn: sqlite3.Connection, feedback_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM receiver_feedback WHERE id = ?", (feedback_id,)).fetchone()
    if row is None:
        raise FeedbackError("UNKNOWN_FEEDBACK")
    matched = json.loads(row["matched_transactions_json"] or "[]")
    return {"id": row["id"], "workspace_id": row["workspace_id"], "origin": row["origin"], "raw_hash": row["raw_hash"],
            "received_at": row["received_at"], "response_profile_version": row["response_profile_version"],
            "response_kind": row["response_kind"],
            "correlation": {"state": row["correlation_state"], "attempt_id": row["attempt_id"],
                            "message_refs": json.loads(row["message_refs_json"] or "[]"), "decision_by": row["decision_by"],
                            "matched": matched},
            "technical_ack_state": row["technical_ack_state"], "business_processing_state": row["business_processing_state"],
            "items": json.loads(row["items_json"] or "[]"), "repair_job_id": row["repair_job_id"], "created_at": row["created_at"]}


def start_repair(conn: sqlite3.Connection, root: str, workspace_id: str, feedback_id: str, actor: str,
                 policy=None) -> Dict[str, Any]:
    """A rejection starts a linked repair job on the delivered artifact's bytes, with lineage to the feedback."""
    from . import export as export_mod, ingest, runner
    doc = feedback_doc(conn, feedback_id)
    if doc["repair_job_id"]:
        return {"feedback_id": feedback_id, "job_id": doc["repair_job_id"], "replayed": True}
    rejected = doc["technical_ack_state"] == "rejected" or doc["business_processing_state"] in ("rejected", "partially_accepted")
    if not rejected:
        raise FeedbackError("NOT_A_REJECTION", "only a rejection starts a repair draft")
    if doc["correlation"]["state"] != "exact":
        raise FeedbackError("CORRELATION_NOT_EXACT", f"correlation is {doc['correlation']['state']}; investigate before repairing")
    jobs = {m["job_id"] for m in doc["correlation"]["matched"] if m.get("job_id")}
    if len(jobs) != 1:
        raise FeedbackError("CORRELATION_NOT_EXACT", "the feedback spans several jobs")
    job_id = next(iter(jobs))
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    art = conn.execute("SELECT * FROM export_artifacts WHERE job_id = ? AND state IN ('ready','superseded') AND path IS NOT NULL "
                       "ORDER BY built_at DESC LIMIT 1", (job_id,)).fetchone()
    if art is None:
        raise FeedbackError("NO_ARTIFACT", "the rejected job has no built artifact to repair from")
    path = export_mod.artifact_file(conn, art["id"], "corrected")
    sid = ingest.register_source(conn, root, workspace_id, os.path.basename(path), path)
    options = json.loads(job["options_json"] or "{}")
    options["repair"] = {"feedback_id": feedback_id, "message_refs": doc["correlation"]["message_refs"],
                         "previous_artifact_id": art["id"]}
    sub = runner.submit(conn, workspace_id=workspace_id, source_file_id=sid, intent_id=None, options=options, actor=actor)
    with transaction(conn):
        conn.execute("UPDATE jobs SET repair_of_feedback_id = ?, profile_id = ?, profile_version = ? WHERE id = ?",
                     (feedback_id, job["profile_id"], job["profile_version"], sub["job_id"]))
        conn.execute("UPDATE receiver_feedback SET repair_job_id = ? WHERE id = ?", (sub["job_id"], feedback_id))
        audit(conn, workspace_id, actor, "feedback.repair", feedback_id, f"job={sub['job_id']} from artifact {art['id']}")
    return {"feedback_id": feedback_id, "job_id": sub["job_id"], "source_file_id": sid, "previous_artifact_id": art["id"],
            "replayed": False}
