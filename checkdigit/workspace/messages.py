"""
messages.py
===========
Message envelopes, message-level validation and the message lifecycle
(contracts/DEFAULTS.md, "Message lifecycle").

While a job streams an EDIFACT interchange or an X12 interchange, a
collector receives every segment and assembles one transaction per message
(UNH..UNT, ST..SE): references, sender and receiver, document id and
function, the vessel and voyage context, event times, the raw hash and a
semantic digest of the identifiers it carries. It checks syntax (trailer
references and counts), schema (segments the profile requires, functions it
supports) and partner rules (expected sender, receiver, message version).
Container XML has no envelope; one transaction stands for the file.

Lifecycle resolution runs after streaming against the transactions already
applied in the workspace and follows the profile's keys and sequence rule:
same revision key and same digest is a duplicate (applied once); same
revision key and a different digest is a conflict; a change, replacement or
cancellation without an applied predecessor is held; an older revision after
a newer one is retained without replacing it; an unsupported function is
preserved and blocked. Effects are applied inside the generation's publish
transaction.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Callable, Dict, List, Optional, Tuple

from edifact_locator import Separators, parse_segment

from . import context as ctx_mod
from .profiles import DEFAULT_RULES, X12_FUNCTION_MAP, normalize_rules
from .store import new_id, now_iso, transaction

BLOCKING_RESOLUTIONS = ("held_predecessor_unresolved", "blocked_unsupported")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def key_from(fields: Dict[str, Any], names: List[str]) -> Tuple[Optional[str], List[str]]:
    """Join key fields; a missing field leaves the key unresolved."""
    parts = []
    missing = []
    for n in names:
        v = str(fields.get(n) or "").strip()
        if not v:
            missing.append(n)
        parts.append(v.upper())
    return (None if missing else "|".join(parts)), missing


class Collector:
    """Base: accumulates messages while segments stream; format subclasses fill facts."""

    def __init__(self, workspace_id: str, job_id: str, rules: Optional[Dict[str, Any]], receipt_time: str,
                 visit_resolver: Optional[Callable[..., Dict[str, Any]]] = None, source_sha256: str = "",
                 terminal_site: str = ""):
        self.ws = workspace_id
        self.job_id = job_id
        self.rules = rules or json.loads(json.dumps(DEFAULT_RULES))
        self.has_profile = rules is not None
        self.receipt_time = receipt_time
        self.visit_resolver = visit_resolver
        self.source_sha256 = source_sha256
        self.terminal_site = terminal_site
        self.envelope: Dict[str, Any] = {}
        self.current: Optional[Dict[str, Any]] = None
        self.finished: List[Dict[str, Any]] = []
        self.message_count = 0
        self.envelope_issues: List[str] = []
        self.identifier_total = 0

    # ---- helpers ----------------------------------------------------------- #

    def _new_message(self, start: int) -> Dict[str, Any]:
        self.message_count += 1
        self.current = {
            "message_no": self.message_count, "char_start": start, "char_end": start, "segment_count": 0,
            "message_ref": "", "message_type": "", "message_version": "", "document_id": "", "document_revision": "",
            "function_code": "", "sender": self.envelope.get("sender", ""), "receiver": self.envelope.get("receiver", ""),
            "interchange_ref": self.envelope.get("interchange_ref", ""), "vessel": "", "voyage": "", "pol": "", "pod": "",
            "terminal": self.terminal_site, "declared_segment_count": None, "declared_ref": "",
            "raw_parts": [], "semantic": [], "events": [], "syntax": [], "schema": [], "partner": [],
            "segments_seen": set(), "identifier_count": 0, "contexts": [], "visit": None, "pending_stow": "",
            "last_context": None,
        }
        return self.current

    def _ensure_visit(self) -> Optional[Dict[str, Any]]:
        m = self.current
        if m is None:
            return None
        if m["visit"] is None and self.visit_resolver is not None:
            m["visit"] = self.visit_resolver(m["message_no"], terminal=m["terminal"], vessel=m["vessel"], voyage=m["voyage"])
        return m["visit"]

    def eqd_context(self, eqid: str, size_type: str, full_empty: str, stow: str) -> Dict[str, Any]:
        m = self.current
        visit = self._ensure_visit()
        c = {"message_no": m["message_no"], "visit_id": visit["id"] if visit else None, "movement_id": None,
             "full_empty": full_empty or None, "stow_position": stow or None, "size_type": size_type or None,
             "defer": bool(self.rules.get("stow_follows_eqd"))}
        m["identifier_count"] += 1
        self.identifier_total += 1
        m["semantic"].append(f"{eqid}|{size_type}|{full_empty}")
        m["last_context"] = c
        if c["defer"]:
            m["contexts"].append(c)
        return c

    def _close_message(self, end: int) -> None:
        m = self.current
        if m is None:
            return
        m["char_end"] = end
        raw_hash = _sha("\n".join(m.pop("raw_parts")))
        m["raw_hash"] = raw_hash
        m["semantic_digest"] = _sha("\n".join(sorted(m.pop("semantic"))))
        self._checks(m)
        self._keys(m)
        m.pop("segments_seen", None)
        m.pop("pending_stow", None)
        m.pop("last_context", None)
        self._ensure_visit()
        self.finished.append(m)
        self.current = None

    def _checks(self, m: Dict[str, Any]) -> None:
        r = self.rules
        for seg in r.get("required_segments") or []:
            if seg not in m["segments_seen"]:
                m["schema"].append(f"required segment {seg} missing")
        if self.has_profile:
            for name, expected in (("sender", r.get("expected_sender")), ("receiver", r.get("expected_receiver"))):
                if expected and (m.get(name) or "").strip().upper() != str(expected).strip().upper():
                    m["partner"].append(f"{name} {m.get(name) or 'absent'} differs from expected {expected}")
            ev = r.get("expected_message_version")
            if ev and (m.get("message_version") or "") != ev:
                m["partner"].append(f"message version {m.get('message_version') or 'absent'} differs from expected {ev}")
        fn = self.function_of(m)
        m["function"] = fn
        if fn == "unsupported":
            m["schema"].append(f"message function code {m['function_code'] or 'absent'} is not supported by the profile")
        m["sender_validated"] = bool(self.has_profile and r.get("expected_sender") and not any(
            p.startswith("sender") for p in m["partner"]))

    def function_of(self, m: Dict[str, Any]) -> str:
        code = m.get("function_code") or ""
        fmap = self.rules.get("function_map") or {}
        if not code:
            return "original"
        return fmap.get(code, "unsupported")

    def _keys(self, m: Dict[str, Any]) -> None:
        fields = {"sender": m["sender"], "receiver": m["receiver"], "interchange_ref": m["interchange_ref"],
                  "message_ref": m["message_ref"], "message_type": m["message_type"], "message_version": m["message_version"],
                  "document_id": m["document_id"], "document_revision": m["document_revision"],
                  "function_code": m["function_code"], "vessel": m["vessel"], "voyage": m["voyage"],
                  "terminal": m["terminal"], "source_sha256": self.source_sha256}
        bk, missing_b = key_from(fields, self.rules["business_key"])
        rk, missing_r = key_from(fields, self.rules["revision_key"])
        if bk is None:
            m["schema"].append(f"business key unresolved: missing {', '.join(missing_b)}")
        if rk is None:
            m["schema"].append(f"revision key unresolved: missing {', '.join(missing_r)}")
        m["business_key"] = bk or f"unresolved:{self.job_id}:{m['message_no']}"
        m["revision_key"] = rk or f"unresolved:{self.job_id}:{m['message_no']}"
        seq_rule = self.rules.get("sequence", "none")
        m["sequence_no"] = None
        if seq_rule == "message_ref_numeric" and str(m["message_ref"]).isdigit():
            m["sequence_no"] = int(m["message_ref"])
        elif seq_rule == "document_revision" and str(m["document_revision"]).isdigit():
            m["sequence_no"] = int(m["document_revision"])

    def take_messages(self) -> List[Dict[str, Any]]:
        out, self.finished = self.finished, []
        return out

    def finish(self, end: int) -> Dict[str, Any]:
        if self.current is not None:
            self.current["syntax"].append("message has no trailer (truncated interchange)")
            self._close_message(end)
        return {"messages": self.message_count, "issues": list(self.envelope_issues),
                "envelope": {k: v for k, v in self.envelope.items() if k != "declared"}}


class EdifactCollector(Collector):
    def segment(self, seg_no: int, start: int, raw: str, sep: Separators) -> Optional[Dict[str, Any]]:
        seg = parse_segment(raw, start, sep)
        tag = seg.tag
        els = seg.elements
        m = self.current

        def el(i: int, j: int = 0) -> str:
            try:
                return els[i][j]
            except IndexError:
                return ""
        if tag == "UNB":
            self.envelope = {"sender": el(2), "receiver": el(3), "interchange_ref": el(5), "syntax": el(1)}
            return None
        if tag == "UNZ":
            declared = el(1)
            if declared.isdigit() and int(declared) != self.message_count:
                self.envelope_issues.append(f"UNZ declares {declared} message(s); {self.message_count} found")
            if el(2) and self.envelope.get("interchange_ref") and el(2) != self.envelope["interchange_ref"]:
                self.envelope_issues.append(f"UNZ reference {el(2)} differs from UNB reference {self.envelope['interchange_ref']}")
            self.envelope["closed"] = True
            return None
        if tag == "UNH":
            if m is not None:
                m["syntax"].append("UNH before the previous message's UNT")
                self._close_message(start)
            m = self._new_message(start)
            m["message_ref"] = el(1)
            m["message_type"] = el(2, 0)
            m["message_version"] = ":".join(c for c in (els[2][1:] if len(els) > 2 else []) if c)
        if m is None:
            return None
        m["segment_count"] += 1
        m["raw_parts"].append(raw)
        m["segments_seen"].add(tag)
        if tag == "BGM":
            m["document_id"] = el(2, 0)
            m["document_revision"] = el(2, 1) or el(2, 2)
            m["function_code"] = el(3)
        elif tag == "TDT":
            m["voyage"] = el(2) or m["voyage"]
            vessel_id, vessel_name = el(8, 0), el(8, 3)
            m["vessel"] = vessel_name or vessel_id or m["vessel"]
        elif tag == "LOC":
            q = el(1)
            if q == "9":
                m["pol"] = el(2, 0)
            elif q == "11":
                m["pod"] = el(2, 0)
            elif q == "147":
                if self.rules.get("stow_follows_eqd") and m["last_context"] is not None and m["last_context"]["stow_position"] is None:
                    m["last_context"]["stow_position"] = el(2, 0)
                else:
                    m["pending_stow"] = el(2, 0)
        elif tag == "DTM":
            q, value, fmt = el(1, 0), el(1, 1), el(1, 2)
            if q in ctx_mod.DTM_EVENTS and value:
                et, cls = ctx_mod.DTM_EVENTS[q]
                m["events"].append({"event_type": et, "classifier": cls, "time": ctx_mod.event_time(value, fmt),
                                    "source_ref": f"DTM+{q}", "provenance": f"{m['message_type']} segment {seg_no}"})
        elif tag == "EQD":
            if el(1) == "CN":
                full_empty = {"4": "empty", "5": "full"}.get(el(6), "")
                stow = m["pending_stow"]
                m["pending_stow"] = ""
                c = self.eqd_context(el(2), el(3), full_empty, stow)
                return c
        elif tag == "UNT":
            m["declared_segment_count"] = int(el(1)) if el(1).isdigit() else None
            m["declared_ref"] = el(2)
            if m["declared_segment_count"] is not None and m["declared_segment_count"] != m["segment_count"]:
                m["syntax"].append(f"UNT declares {m['declared_segment_count']} segments; {m['segment_count']} counted")
            if m["declared_ref"] and m["declared_ref"] != m["message_ref"]:
                m["syntax"].append(f"UNT reference {m['declared_ref']} differs from UNH reference {m['message_ref']}")
            self._close_message(start + len(raw))
        return None


class X12Collector(Collector):
    def __init__(self, *args, element: str = "*", **kwargs):
        super().__init__(*args, **kwargs)
        self.element = element
        if kwargs.get("rules") is None or "function_map" not in (kwargs.get("rules") or {}):
            self.rules["function_map"] = dict(X12_FUNCTION_MAP)

    def segment(self, seg_no: int, start: int, raw: str) -> Optional[Dict[str, Any]]:
        els = raw.split(self.element)
        tag = els[0]

        def el(i: int) -> str:
            return els[i].strip() if i < len(els) else ""
        m = self.current
        if tag == "ISA":
            self.envelope = {"sender": el(6), "receiver": el(8), "interchange_ref": el(13)}
            return None
        if tag == "IEA":
            declared = el(1)
            groups = self.envelope.get("groups", 0)
            if declared.isdigit() and groups and int(declared) != groups:
                self.envelope_issues.append(f"IEA declares {declared} group(s); {groups} found")
            self.envelope["closed"] = True
            return None
        if tag == "GS":
            self.envelope["groups"] = self.envelope.get("groups", 0) + 1
            self.envelope["group_sender"], self.envelope["group_receiver"] = el(2), el(3)
            return None
        if tag == "GE":
            return None
        if tag == "ST":
            if m is not None:
                m["syntax"].append("ST before the previous set's SE")
                self._close_message(start)
            m = self._new_message(start)
            m["message_type"] = el(1)
            m["message_ref"] = el(2)
            m["message_version"] = el(3)
        if m is None:
            return None
        m["segment_count"] += 1
        m["raw_parts"].append(raw)
        m["segments_seen"].add(tag)
        if tag == "BGN":
            m["function_code"] = el(1)
            m["document_id"] = el(2)
        elif tag == "B4" and not m["document_id"]:
            m["document_id"] = el(1) or m["message_ref"]
        elif tag == "V1":
            m["vessel"] = el(2) or el(1) or m["vessel"]
            m["voyage"] = el(4) or m["voyage"]
        elif tag == "G62":
            q, date, tcode, time = el(1), el(2), el(3), el(4)
            if q in ctx_mod.G62_EVENTS and date:
                et, cls = ctx_mod.G62_EVENTS[q]
                raw_time = date + (time or "")
                fmt = "203" if time else "102"
                m["events"].append({"event_type": et, "classifier": cls, "time": ctx_mod.event_time(raw_time, fmt),
                                    "source_ref": f"G62*{q}", "provenance": f"{m['message_type']} segment {seg_no}"})
        elif tag == "N7":
            initial, number = el(1), el(2)
            status = {"E": "empty", "L": "full"}.get(el(11), "")
            size = el(22) or el(15)
            c = self.eqd_context(f"{initial}{number}", size, status, "")
            return c
        elif tag == "SE":
            m["declared_segment_count"] = int(el(1)) if el(1).isdigit() else None
            m["declared_ref"] = el(2)
            if m["declared_segment_count"] is not None and m["declared_segment_count"] != m["segment_count"]:
                m["syntax"].append(f"SE declares {m['declared_segment_count']} segments; {m['segment_count']} counted")
            if m["declared_ref"] and m["declared_ref"] != m["message_ref"]:
                m["syntax"].append(f"SE control number {m['declared_ref']} differs from ST {m['message_ref']}")
            self._close_message(start + len(raw))
        return None


class XmlCollector(Collector):
    """One transaction per container XML file; no envelope to check."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        m = self._new_message(0)
        m["message_type"] = "SNX"
        m["message_ref"] = self.source_sha256[:16]
        m["document_id"] = self.source_sha256[:16]

    def element_context(self, eqid: str, size_type: str) -> Dict[str, Any]:
        return self.eqd_context(eqid, size_type, "", "")

    def finish(self, end: int) -> Dict[str, Any]:
        if self.current is not None:
            self._close_message(end)
        return {"messages": self.message_count, "issues": [], "envelope": {}}


# --------------------------------------------------------------------------- #
# Storage rows
# --------------------------------------------------------------------------- #

def message_row(ws: str, job_id: str, source_id: str, m: Dict[str, Any]) -> tuple:
    syntax = "failed" if m["syntax"] else "passed"
    schema = "failed" if m["schema"] else "passed"
    partner = "failed" if m["partner"] else "passed"
    detail = {"syntax": m["syntax"], "schema": m["schema"], "partner": m["partner"], "pol": m["pol"], "pod": m["pod"],
              "vessel": m["vessel"], "voyage": m["voyage"], "events": len(m["events"])}
    return (new_id("msg"), ws, job_id, source_id, m["message_no"], m["interchange_ref"] or None, m["message_ref"],
            m["message_type"], m["message_version"], m["document_id"], m["business_key"], m["revision_key"], m["function"],
            m["function_code"], m["sender"] or None, m["receiver"] or None, 1 if m.get("sender_validated") else 0,
            m["terminal"] or None, m["raw_hash"], m["semantic_digest"], m["sequence_no"], m["char_start"], m["char_end"],
            m["segment_count"], m["declared_segment_count"], m["identifier_count"], syntax, schema, partner,
            json.dumps(detail, sort_keys=True))


MESSAGE_INSERT = ("INSERT OR IGNORE INTO message_transactions (id, workspace_id, job_id, source_feed_id, message_no, "
                  "transport_envelope_ref, message_ref, message_type, message_version, document_id, business_key, "
                  "revision_key, function, function_code, sender, receiver, sender_validated, business_scope, raw_hash, "
                  "semantic_digest, sequence_no, char_start, char_end, segment_count, declared_segment_count, "
                  "identifier_count, syntax_state, schema_state, partner_state, detail_json) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
MOVEMENT_INSERT = ("INSERT OR IGNORE INTO movements (id, workspace_id, job_id, message_no, mode, vessel, voyage, origin, "
                   "destination, visit_id) VALUES (?,?,?,?,?,?,?,?,?,?)")
EVENT_INSERT = ("INSERT OR IGNORE INTO events (id, workspace_id, job_id, message_no, source, source_event_id, subject_json, "
                "event_type, classifier, raw, tz_offset, precision, parsed_utc, ambiguous, receipt_time, provenance) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
CONTEXT_INSERT = ("INSERT OR IGNORE INTO observation_context (job_id, ordinal, message_no, visit_id, movement_id, full_empty, "
                  "stow_position, size_type) VALUES (?,?,?,?,?,?,?,?)")


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #

def resolve_lifecycle(conn: sqlite3.Connection, job: sqlite3.Row, rules: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Set lifecycle_resolution per message and return findings to record.

    Returns {"findings": [(code, detail, location)], "blocking": bool, "counts": {...}}.
    """
    from .ingest import add_finding  # noqa: F401  (kept local to avoid an import cycle)
    ws = job["workspace_id"]
    job_id = job["id"]
    rules = rules or json.loads(json.dumps(DEFAULT_RULES))
    findings: List[Tuple[str, str, str]] = []
    counts: Dict[str, int] = {}
    blocking = False
    rows = conn.execute("SELECT * FROM message_transactions WHERE job_id = ? ORDER BY message_no", (job_id,)).fetchall()
    updates: List[tuple] = []
    for m in rows:
        loc = f"message {m['message_no']}"
        detail_json = json.loads(m["detail_json"] or "{}")
        for issue in detail_json.get("syntax", []):
            findings.append(("MESSAGE_SYNTAX_INVALID", issue, loc))
            blocking = True
        for issue in detail_json.get("schema", []):
            findings.append(("MESSAGE_SCHEMA_VIOLATION", issue, loc))
            blocking = True
        for issue in detail_json.get("partner", []):
            findings.append(("PARTNER_RULE_VIOLATION", issue, loc))
        resolution, detail = "staged", ""
        if m["function"] == "unsupported":
            resolution, detail = "blocked_unsupported", f"function code {m['function_code']!r} preserved, not applied"
        elif m["revision_key"].startswith("unresolved:"):
            resolution, detail = "blocked_unsupported", "keys unresolved; nothing applied"
        else:
            same_rev = conn.execute(
                "SELECT id, semantic_digest, lifecycle_resolution FROM message_transactions WHERE workspace_id = ? AND revision_key = ? "
                "AND job_id <> ? AND lifecycle_resolution IN ('applied_scoped','superseded','cancelled') ORDER BY applied_at DESC LIMIT 1",
                (ws, m["revision_key"], job_id)).fetchone()
            if same_rev is not None:
                if same_rev["semantic_digest"] == m["semantic_digest"]:
                    findings.append(("MESSAGE_DUPLICATE", f"same revision key and payload digest as {same_rev['id']}; no second effect", loc))
                    resolution, detail = "staged", f"duplicate of {same_rev['id']}"
                else:
                    findings.append(("MESSAGE_ID_CONFLICT", f"revision key {m['revision_key']} already applied as {same_rev['id']} "
                                     f"with a different payload digest", loc))
                    blocking = True
                    resolution, detail = "staged", f"conflicts with {same_rev['id']}"
            elif m["function"] in ("change", "replacement", "cancellation", "original", "addition"):
                current = conn.execute(
                    "SELECT id, sequence_no, function FROM message_transactions WHERE workspace_id = ? AND business_key = ? "
                    "AND job_id <> ? AND lifecycle_resolution = 'applied_scoped' ORDER BY applied_at DESC LIMIT 1",
                    (ws, m["business_key"], job_id)).fetchone()
                if m["function"] in ("change", "replacement", "cancellation"):
                    if current is None:
                        findings.append(("PREDECESSOR_UNRESOLVED", f"{m['function']} of business key {m['business_key']} "
                                         f"has no applied predecessor", loc))
                        blocking = True
                        resolution, detail = "held_predecessor_unresolved", "no applied predecessor"
                    elif (m["sequence_no"] is not None and current["sequence_no"] is not None
                          and m["sequence_no"] < current["sequence_no"]):
                        findings.append(("REVISION_OUT_OF_ORDER", f"sequence {m['sequence_no']} is older than the applied "
                                         f"{current['sequence_no']} ({current['id']}); retained without replacing", loc))
                        resolution, detail = "held_older_revision", f"older than {current['id']}"
                    else:
                        detail = f"{m['function']} of {current['id']}"
                elif current is not None:
                    if rules.get("original_on_existing") == "replace":
                        detail = f"original re-sent; replaces {current['id']}"
                    else:
                        findings.append(("MESSAGE_ID_CONFLICT", f"an {m['function']} for business key {m['business_key']} "
                                         f"is already applied as {current['id']}; nothing applied automatically", loc))
                        blocking = True
                        detail = f"conflicts with applied {current['id']}"
        counts[resolution] = counts.get(resolution, 0) + 1
        updates.append((resolution, detail, m["id"]))
    # The caller holds the transaction (feed_gates).
    conn.executemany("UPDATE message_transactions SET lifecycle_resolution = ?, resolution_detail = ? WHERE id = ?", updates)
    return {"findings": findings, "blocking": blocking, "counts": counts, "messages": len(rows)}


def apply_on_publish(conn: sqlite3.Connection, job_id: str) -> Dict[str, int]:
    """Inside the publish transaction: apply staged messages, supersede or cancel predecessors."""
    from .reconcile import PublishError
    job = conn.execute("SELECT workspace_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
    ws = job["workspace_id"]
    applied = {"applied": 0, "superseded": 0, "cancelled": 0, "duplicates": 0, "held": 0}
    ts = now_iso()
    for m in conn.execute("SELECT * FROM message_transactions WHERE job_id = ? ORDER BY message_no", (job_id,)).fetchall():
        if m["lifecycle_resolution"] != "staged":
            applied["held"] += 1
            continue
        clash = conn.execute(
            "SELECT id, semantic_digest FROM message_transactions WHERE workspace_id = ? AND revision_key = ? AND job_id <> ? "
            "AND lifecycle_resolution IN ('applied_scoped','superseded','cancelled') ORDER BY applied_at DESC LIMIT 1",
            (ws, m["revision_key"], job_id)).fetchone()
        if clash is not None and clash["semantic_digest"] != m["semantic_digest"]:
            raise PublishError("MESSAGE_ID_CONFLICT", f"message {m['message_no']} conflicts with {clash['id']} applied meanwhile")
        if clash is not None:
            conn.execute("UPDATE message_transactions SET lifecycle_resolution = 'superseded', resolution_detail = ?, applied_at = ?, "
                         "superseded_by = ? WHERE id = ?", (f"duplicate of {clash['id']}; no second effect", ts, clash["id"], m["id"]))
            applied["duplicates"] += 1
            continue
        current = conn.execute(
            "SELECT id FROM message_transactions WHERE workspace_id = ? AND business_key = ? AND id <> ? "
            "AND lifecycle_resolution = 'applied_scoped'", (ws, m["business_key"], m["id"])).fetchall()
        if m["function"] in ("change", "replacement", "original", "addition"):
            for c in current:
                conn.execute("UPDATE message_transactions SET lifecycle_resolution = 'superseded', superseded_by = ? WHERE id = ?",
                             (m["id"], c["id"]))
                applied["superseded"] += 1
            conn.execute("UPDATE message_transactions SET lifecycle_resolution = 'applied_scoped', applied_at = ? WHERE id = ?",
                         (ts, m["id"]))
            applied["applied"] += 1
        elif m["function"] == "cancellation":
            for c in current:
                conn.execute("UPDATE message_transactions SET lifecycle_resolution = 'cancelled', superseded_by = ? WHERE id = ?",
                             (m["id"], c["id"]))
                applied["cancelled"] += 1
            conn.execute("UPDATE message_transactions SET lifecycle_resolution = 'applied_scoped', applied_at = ? WHERE id = ?",
                         (ts, m["id"]))
            applied["applied"] += 1
    return applied


# --------------------------------------------------------------------------- #
# Whole-file validation for profile fixtures (no job, no storage)
# --------------------------------------------------------------------------- #

def validate_file(path: str, codec: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Run the collector over a file with the profile's rules; report findings without storing anything."""
    from .ingest import inspect_source
    from .stream import delimited_segments, text_chunks
    rules = normalize_rules(profile.get("rules"), profile.get("message_family") or "")
    info = inspect_source(path, {}, codec)
    fmt = info["format"]
    findings: List[Dict[str, Any]] = []
    messages = 0
    if fmt == "edifact":
        s = info["separators"]
        sep = Separators(component=s["component"], element=s["element"], decimal=s["decimal"], release=s["release"],
                         segment=s["segment"])
        col = EdifactCollector("fixture", "fixture", rules, now_iso(), terminal_site=profile.get("terminal_site", ""))
        chunks = (t for _c, t in text_chunks(path, codec))
        n = 0
        end = 0
        for start, raw in delimited_segments(chunks, sep.segment, sep.release, skip_una=True):
            n += 1
            col.segment(n, start, raw, sep)
            end = start + len(raw)
        summary = col.finish(end)
    elif fmt == "x12":
        d = info["delims"]
        col = X12Collector("fixture", "fixture", rules, now_iso(), element=d["element"],
                           terminal_site=profile.get("terminal_site", ""))
        chunks = (t for _c, t in text_chunks(path, codec))
        n = 0
        end = 0
        for start, raw in delimited_segments(chunks, d["segment"], "", skip_una=False):
            n += 1
            col.segment(n, start, raw)
            end = start + len(raw)
        summary = col.finish(end)
    elif fmt == "csv":
        from . import mapping
        from .stream import csv_records
        contract = profile.get("mapping_contract") or {}
        if not contract.get("fields"):
            return {"format": fmt, "messages": 0, "findings": [{"code": "MAPPING_DRIFT", "detail": "profile has no mapping contract"}],
                    "blocking": True}
        contract = mapping.normalize_contract(contract)
        chunks = (t for _c, t in text_chunks(path, codec))
        header: Optional[List[str]] = None
        width = 0
        validator: Optional[mapping.StreamValidator] = None
        check: Dict[str, Any] = {}
        for rec in csv_records(chunks, contract.get("delimiter") or info.get("delimiter", ",")):
            values = [f.value for f in rec.fields]
            if header is None and contract["has_header"]:
                header = [v.strip() for v in values]
                width = len(header)
                check = mapping.check_header(contract, mapping.fingerprint_of(header, width, info.get("delimiter", ",")))
                id_cols = [check["resolved"][f["name"]] for f in contract["fields"] if f["type"] == "identifier"
                           and check["resolved"].get(f["name"]) is not None]
                validator = mapping.StreamValidator(id_cols)
                continue
            if validator is None:
                check = mapping.check_header(contract, mapping.fingerprint_of(None, len(values), info.get("delimiter", ",")))
                id_cols = [int(f["identity"]) for f in contract["fields"] if f["type"] == "identifier"]
                validator = mapping.StreamValidator(id_cols)
            if check.get("blocked"):
                break
            validator.observe(rec.no, values)
        result = validator.finish() if validator else {"findings": [], "blocked": False}
        fl = [{"code": c, "detail": d, "location": l} for c, d, l in check.get("findings", []) + result["findings"]]
        return {"format": fmt, "messages": 0, "findings": fl, "blocking": bool(check.get("blocked") or result["blocked"])}
    else:
        return {"format": fmt, "messages": 0, "findings": [{"code": "UNSUPPORTED_INPUT", "detail": f"{fmt} has no profile checks"}],
                "blocking": fmt not in ("xml", "txt")}
    blocking = False
    for m in col.take_messages():
        messages += 1
        for issue in m["syntax"]:
            findings.append({"code": "MESSAGE_SYNTAX_INVALID", "detail": issue, "location": f"message {m['message_no']}"})
            blocking = True
        for issue in m["schema"]:
            findings.append({"code": "MESSAGE_SCHEMA_VIOLATION", "detail": issue, "location": f"message {m['message_no']}"})
            blocking = True
        for issue in m["partner"]:
            findings.append({"code": "PARTNER_RULE_VIOLATION", "detail": issue, "location": f"message {m['message_no']}"})
            blocking = True
    for issue in summary["issues"]:
        findings.append({"code": "MESSAGE_SYNTAX_INVALID", "detail": issue, "location": "interchange"})
        blocking = True
    return {"format": fmt, "messages": messages, "findings": findings, "blocking": blocking}
