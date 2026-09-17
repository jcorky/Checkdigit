"""
context.py
==========
Equipment, visit, movement and event context (the rules in
contracts/DEFAULTS.md, "Equipment, visits, movements and events").

A container number is an observed identifier. Visits are never merged on the
container number alone: a visit is keyed by the profile's terminal site plus
the vessel and voyage references, and a message whose references are
missing gets its own unresolved visit and a CONTEXT_AMBIGUOUS finding. Event
times keep their raw text, precision and offset; a time without an offset
stays unresolved for comparison. Full/empty state, stowage position and the
size/type code observed in a message live in the observation's context, not
on the equipment.

The seven validation layers are computed on read for one observation from
stored facts (kernel status, fleet membership, context, message states) so
the layer model stays consistent at any scale without a per-layer table.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from typing import Any, Dict, List, Optional

from .store import new_id, now_iso

# EDIFACT DTM 2005 qualifiers the context understands: (event type, classifier)
DTM_EVENTS = {
    "132": ("arrival", "estimated"), "133": ("departure", "estimated"),
    "178": ("arrival", "actual"), "186": ("departure", "actual"),
    "63": ("delivery_latest", "planned"), "64": ("delivery_earliest", "planned"),
    "200": ("pickup", "planned"), "137": ("message_issued", "actual"), "7": ("effective", "actual"),
}
# EDIFACT 2379 date/time format codes: (precision per the contract's event_time,
# two-digit year, carries zone). An unknown format keeps the raw text, is marked
# ambiguous and is never parsed.
DTM_FORMATS = {
    "101": ("date", True, False), "102": ("date", False, False), "201": ("minute", True, False),
    "203": ("minute", False, False), "204": ("second", False, False), "303": ("minute", False, True),
    "304": ("second", False, True), "301": ("minute", True, True),
}
# X12 G62 date qualifiers used by the intermodal sets.
G62_EVENTS = {"86": ("pickup", "actual"), "17": ("delivery", "estimated"), "10": ("delivery", "requested"),
              "70": ("arrival", "actual"), "68": ("departure", "actual"), "69": ("arrival", "estimated"),
              "11": ("shipped", "actual")}

RE_ZONE = re.compile(r"([+-]\d{2}(?::?\d{2})?|Z|UTC)$")


def event_time(raw: str, fmt: str) -> Dict[str, Any]:
    """Parse an EDIFACT DTM value into the contract's event_time shape."""
    known_format = fmt in DTM_FORMATS
    precision, two_digit_year, has_zone = DTM_FORMATS.get(fmt, ("date", False, False))
    value = raw
    tz: Optional[str] = None
    if has_zone:
        m = RE_ZONE.search(raw)
        if m:
            zone = m.group(1)
            tz = "+00:00" if zone in ("Z", "UTC") else (zone if ":" in zone else zone[:3] + ":" + (zone[3:] or "00"))
            value = raw[:m.start()]
    ambiguous = two_digit_year or not known_format
    parsed: Optional[str] = None
    if tz is not None and not ambiguous:
        try:
            digits = re.sub(r"\D", "", value)
            pattern, width = {"date": ("%Y%m%d", 8), "minute": ("%Y%m%d%H%M", 12), "second": ("%Y%m%d%H%M%S", 14)}[precision]
            if len(digits) < width:
                raise ValueError("short date")
            local = dt.datetime.strptime(digits[:width], pattern)
            sign = 1 if tz[0] == "+" else -1
            hh, mm = int(tz[1:3]), int(tz[4:6])
            utc = local - sign * dt.timedelta(hours=hh, minutes=mm)
            parsed = utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, KeyError):
            parsed = None
            ambiguous = True
    return {"raw": raw, "tz_offset": tz, "precision": precision, "parsed_utc": parsed, "ambiguous": ambiguous}


def composite_visit_key(terminal: str, vessel: str, voyage: str) -> Optional[str]:
    """Profile-defined visit key; never built from empty references."""
    if not terminal or not vessel or not voyage:
        return None
    return f"{terminal}|{vessel.strip().upper()}|{voyage.strip().upper()}"


def resolve_visit(conn: sqlite3.Connection, workspace_id: str, job_id: str, message_no: int, *, terminal: str,
                  vessel: str, voyage: str, scope: str = "vessel_visit") -> Dict[str, Any]:
    """Find or create the visit a message belongs to. Returns {id, composite_key, ambiguity}."""
    key = composite_visit_key(terminal, vessel, voyage)
    if key is not None:
        row = conn.execute("SELECT id FROM visits WHERE workspace_id = ? AND composite_key = ?", (workspace_id, key)).fetchone()
        if row:
            return {"id": row["id"], "composite_key": key, "ambiguity": None}
        vid = new_id("vis")
        conn.execute("INSERT INTO visits (id, workspace_id, terminal_site, visit_scope, source_visit_ref, composite_key, "
                     "vessel, voyage, first_job_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (vid, workspace_id, terminal, scope, f"{vessel}/{voyage}", key, vessel, voyage, job_id, now_iso()))
        return {"id": vid, "composite_key": key, "ambiguity": None}
    # missing references: an unresolved visit of its own, keyed by job and message
    ref = f"{job_id}#{message_no}"
    row = conn.execute("SELECT id, unresolved_association FROM visits WHERE workspace_id = ? AND source_visit_ref = ?",
                       (workspace_id, ref)).fetchone()
    missing = [n for n, v in (("terminal site", terminal), ("vessel", vessel), ("voyage", voyage)) if not v]
    candidates = 0
    if vessel and not voyage:
        candidates = conn.execute("SELECT COUNT(*) FROM visits WHERE workspace_id = ? AND vessel = ? AND composite_key IS NOT NULL",
                                  (workspace_id, vessel.strip().upper())).fetchone()[0]
    reason = f"missing {', '.join(missing)}" + (f"; {candidates} candidate visit(s) share the vessel" if candidates else "")
    if row:
        return {"id": row["id"], "composite_key": None, "ambiguity": row["unresolved_association"]}
    vid = new_id("vis")
    conn.execute("INSERT INTO visits (id, workspace_id, terminal_site, visit_scope, source_visit_ref, composite_key, vessel, "
                 "voyage, unresolved_association, first_job_id, created_at) VALUES (?,?,?,?,?,NULL,?,?,?,?,?)",
                 (vid, workspace_id, terminal, scope, ref, vessel or None, voyage or None, reason, job_id, now_iso()))
    return {"id": vid, "composite_key": None, "ambiguity": reason}


def movement_row(workspace_id: str, job_id: str, message_no: int, *, mode: str, vessel: str, voyage: str,
                 origin: str, destination: str, visit_id: Optional[str]) -> tuple:
    return (new_id("mov"), workspace_id, job_id, message_no, mode, vessel or None, voyage or None, origin or None,
            destination or None, visit_id)


def event_rows(workspace_id: str, job_id: str, message_no: int, events: List[Dict[str, Any]], receipt_time: str,
               subject: Dict[str, Any], source: str) -> List[tuple]:
    rows = []
    for i, ev in enumerate(events):
        t = ev["time"]
        rows.append((new_id("evt"), workspace_id, job_id, message_no, source, f"{message_no}:{ev['source_ref']}:{i}",
                     json.dumps(subject, sort_keys=True), ev["event_type"], ev["classifier"], t["raw"], t["tz_offset"],
                     t["precision"], t["parsed_utc"], 1 if t["ambiguous"] else 0, receipt_time, ev.get("provenance", "")))
    return rows


# --------------------------------------------------------------------------- #
# Validation layers for one observation, computed on read
# --------------------------------------------------------------------------- #

def _layer(name: str, state: str, source: str, rule_version: str, detail: str, checked: bool = True) -> Dict[str, Any]:
    return {"layer": name, "state": state, "source": source, "rule_version": rule_version,
            "checked_at": now_iso() if checked else None, "detail": detail}


def layers_for(conn: sqlite3.Connection, job: sqlite3.Row, obs: sqlite3.Row, profile: Optional[Dict[str, Any]]
               ) -> List[Dict[str, Any]]:
    import iso6346_sizetype as st
    from . import reconcile
    ruleset = job["ruleset_version"]
    ws = job["workspace_id"]
    out: List[Dict[str, Any]] = []
    status = obs["status"]
    if obs["token"] == "" and obs["raw"] == "":
        out.append(_layer("structure", "failed", "kernel", ruleset, "identifier field is empty"))
    elif status == "invalid_structure":
        out.append(_layer("structure", "failed", "kernel", ruleset, obs["reason"]))
    else:
        out.append(_layer("structure", "passed", "kernel", ruleset, f"{obs['scheme']} shape"))
    if status == "valid":
        out.append(_layer("check_digit", "passed", "kernel", ruleset, f"printed {obs['printed_check']} agrees"))
    elif status == "corrected":
        out.append(_layer("check_digit", "failed", "kernel", ruleset,
                          f"printed {obs['printed_check'] or 'absent'}, computed {obs['computed_check']}; proposal {obs['decision']}"))
    elif status == "flagged":
        out.append(_layer("check_digit", "warning", "kernel", ruleset, obs["reason"]))
    elif status == "not_a_target":
        out.append(_layer("check_digit", "unsupported", "kernel", ruleset, obs["reason"]))
    else:
        out.append(_layer("check_digit", "not_checked", "kernel", ruleset, "no identifier shape to check", False))
    from .references import prefix_registration
    reg = prefix_registration(conn, ws, obs["normalized"]) if obs["normalized"] else None
    if reg is None:
        out.append(_layer("prefix_registration", "not_checked", "workspace", "register/none",
                          "no owner-code register is bound to this workspace", False))
    elif obs["scheme"] not in ("iso6346", "ilu"):
        out.append(_layer("prefix_registration", "unsupported", reg["source"], reg["version"],
                          f"{obs['scheme']} identifiers carry no owner code", False))
    elif reg["registered"]:
        out.append(_layer("prefix_registration", "passed", reg["source"], reg["version"],
                          f"{reg['prefix']} registered to {reg['owner']}" + (f" ({reg['country']})" if reg["country"] else "")
                          + "; registration does not prove the serial number"))
    else:
        out.append(_layer("prefix_registration", "failed", reg["source"], reg["version"],
                          f"{reg['prefix']} is not in the register version {reg['version']}"))
    # The fleet is keyed by the observed identifier; a check-digit candidate is a
    # proposal and never an identity, so the lookup uses the normalized value.
    key = obs["normalized"]
    member = reconcile.fleet_member(conn, ws, key) if key else None
    fleet_size = reconcile.fleet_count(conn, ws)
    if member:
        same = member["attrs_hash"] == obs["attrs_hash"]
        out.append(_layer("equipment_record", "passed", "fleet", member["generation_id"],
                          f"published member of scope {member['scope_kind']}/{member['scope_value']}"))
        out.append(_layer("attribute_consistency", "passed" if same else "warning", "fleet", member["generation_id"],
                          "attributes match the published record" if same else "attributes differ from the published record"))
    elif fleet_size == 0:
        out.append(_layer("equipment_record", "insufficient_information", "fleet", "none", "the workspace has no published fleet", False))
        out.append(_layer("attribute_consistency", "not_checked", "fleet", "none", "no published record to compare with", False))
    else:
        out.append(_layer("equipment_record", "warning", "fleet", "current", "not in the published fleet"))
        out.append(_layer("attribute_consistency", "not_checked", "fleet", "current", "no published record to compare with", False))
    ctx = conn.execute("SELECT * FROM observation_context WHERE job_id = ? AND ordinal = ?", (job["id"], obs["ordinal"])).fetchone()
    if ctx is not None:
        details = []
        state = "passed"
        if ctx["size_type"]:
            decoded = st.decode(ctx["size_type"]).as_dict()
            if not decoded.get("defined", True):
                details.append(f"size/type {ctx['size_type']} undefined")
                out[-1] = _layer("attribute_consistency", "warning", "message", ruleset,
                                 (out[-1]["detail"] + "; " if out[-1]["state"] != "not_checked" else "") + details[-1])
        if ctx["full_empty"]:
            details.append(f"{ctx['full_empty']}")
            conflict = conn.execute(
                "SELECT COUNT(DISTINCT c.full_empty) FROM observation_context c JOIN observations o ON o.job_id = c.job_id "
                "AND o.ordinal = c.ordinal WHERE c.job_id = ? AND c.visit_id IS ? AND o.normalized = ? AND c.full_empty IS NOT NULL",
                (job["id"], ctx["visit_id"], obs["normalized"])).fetchone()[0]
            if conflict > 1:
                state = "warning"
                details.append("reported both full and empty within the same visit")
        else:
            state = "insufficient_information"
            details.append("full/empty state not stated")
        if ctx["stow_position"]:
            details.append(f"stow {ctx['stow_position']}")
        visit = conn.execute("SELECT composite_key, unresolved_association FROM visits WHERE id = ?", (ctx["visit_id"],)).fetchone() \
            if ctx["visit_id"] else None
        if visit is not None and visit["unresolved_association"]:
            state = "warning" if state == "passed" else state
            details.append(f"visit unresolved: {visit['unresolved_association']}")
        out.append(_layer("operational_state", state, "message", ruleset, "; ".join(details)))
        msg = conn.execute("SELECT syntax_state, schema_state, partner_state, lifecycle_resolution, resolution_detail "
                           "FROM message_transactions WHERE job_id = ? AND message_no = ?", (job["id"], ctx["message_no"])).fetchone()
        if msg is None:
            out.append(_layer("message_profile_acceptance", "not_checked", "profile", "none", "no message envelope", False))
        else:
            states = (msg["syntax_state"], msg["schema_state"], msg["partner_state"])
            if profile is None:
                mstate = "insufficient_information" if "failed" not in states else "failed"
                detail = "no profile bound; syntax " + msg["syntax_state"]
            elif "failed" in states:
                mstate = "failed"
                detail = f"syntax {states[0]}, schema {states[1]}, partner {states[2]}"
            elif profile["verification_state"] == "unverified":
                mstate = "warning"
                detail = f"profile {profile['name']} v{profile['version']} unverified; checks passed"
            else:
                mstate = "passed"
                detail = f"profile {profile['name']} v{profile['version']} ({profile['verification_state']})"
            detail += f"; lifecycle {msg['lifecycle_resolution']}" + (f" ({msg['resolution_detail']})" if msg["resolution_detail"] else "")
            out.append(_layer("message_profile_acceptance", mstate, "profile",
                              f"{profile['name']}/{profile['version']}" if profile else "none", detail))
    else:
        out.append(_layer("operational_state", "not_checked", "message", ruleset,
                          "no message context (delimited or text source)", False))
        out.append(_layer("message_profile_acceptance", "not_checked", "profile", "none",
                          "not a message format" if job["profile_id"] is None else "mapping contract governs this feed", False))
    return out
