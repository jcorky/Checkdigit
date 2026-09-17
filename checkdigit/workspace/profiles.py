"""
profiles.py
===========
Versioned feed profiles. A profile names the terminal system, partner,
message family and version a feed comes from, carries the approved mapping
contract for delimited feeds and the message rules for EDI and XML feeds,
and records how far it has been verified: unverified, fixture_tested,
partner_tested, production_enabled. Editing a profile creates a new version;
earlier jobs keep the version they were run with.

Verification never advances on its own: fixture_tested is reached by running
the profile's acceptance fixtures cleanly; partner_tested and
production_enabled are recorded by an administrator with a note that names
the evidence.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from . import mapping
from .store import audit, new_id, now_iso, transaction

VERIFICATION_STATES = ("unverified", "fixture_tested", "partner_tested", "production_enabled")
FAMILIES = ("BAPLIE", "COARRI", "CODECO", "COPARN", "COPRAR", "IFTMIN", "IFTSTA", "X12_322", "X12_301", "X12_404",
            "SNX", "CSV", "FIXED", "TXT", "GENERIC")

# EDIFACT BGM 1225 message function codes the lifecycle understands.
DEFAULT_FUNCTION_MAP = {"9": "original", "22": "original", "2": "addition", "4": "change", "5": "replacement",
                        "1": "cancellation", "3": "cancellation"}
# X12 BGN01 / transaction set purpose codes.
X12_FUNCTION_MAP = {"00": "original", "05": "replacement", "04": "change", "01": "cancellation", "06": "addition"}

DEFAULT_RULES: Dict[str, Any] = {
    "family": "GENERIC",
    "business_key": ["sender", "receiver", "message_type", "document_id"],
    "revision_key": ["sender", "receiver", "message_type", "document_id", "message_ref"],
    "sequence": "none",                  # none | message_ref_numeric | document_revision
    "function_map": DEFAULT_FUNCTION_MAP,
    "required_segments": [],
    "expected_sender": None,
    "expected_receiver": None,
    "expected_message_version": None,
    "stow_follows_eqd": False,
    "feedback_codes": {},
    "original_on_existing": "conflict",  # conflict | replace
}
KEY_FIELDS = {"sender", "receiver", "interchange_ref", "message_ref", "message_type", "message_version", "document_id",
              "document_revision", "function_code", "vessel", "voyage", "terminal", "source_sha256"}


class ProfileError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def normalize_rules(rules: Optional[Dict[str, Any]], family: str = "") -> Dict[str, Any]:
    out = json.loads(json.dumps(DEFAULT_RULES))
    if family:
        out["family"] = family
    for k, v in (rules or {}).items():
        if k not in out:
            raise ProfileError("BAD_RULES", f"unknown rule {k!r}")
        out[k] = v
    if out["family"] not in FAMILIES:
        raise ProfileError("BAD_RULES", f"unknown family {out['family']!r}; one of {FAMILIES}")
    for key in ("business_key", "revision_key"):
        if not isinstance(out[key], list) or not out[key]:
            raise ProfileError("BAD_RULES", f"{key} must list at least one field")
        bad = [f for f in out[key] if f not in KEY_FIELDS]
        if bad:
            raise ProfileError("BAD_RULES", f"{key}: unknown fields {bad}; allowed {sorted(KEY_FIELDS)}")
    if out["sequence"] not in ("none", "message_ref_numeric", "document_revision"):
        raise ProfileError("BAD_RULES", f"sequence must be none, message_ref_numeric or document_revision")
    if not isinstance(out["function_map"], dict):
        raise ProfileError("BAD_RULES", "function_map must be an object of code -> message function")
    for code, fn in out["function_map"].items():
        if fn not in ("original", "addition", "change", "replacement", "cancellation"):
            raise ProfileError("BAD_RULES", f"function_map[{code!r}] = {fn!r} is not a message function")
    if out["original_on_existing"] not in ("conflict", "replace"):
        raise ProfileError("BAD_RULES", "original_on_existing must be conflict or replace")
    if out["family"].startswith("X12") and rules is not None and "function_map" not in rules:
        out["function_map"] = dict(X12_FUNCTION_MAP)
    out["stow_follows_eqd"] = bool(out["stow_follows_eqd"]) or (out["family"] in ("COARRI", "CODECO", "COPARN") and
                                                               not (rules or {}).get("stow_follows_eqd") is False)
    return out


def create_profile(conn: sqlite3.Connection, workspace_id: str, actor: str, *, name: str,
                   mapping_contract: Optional[Dict[str, Any]] = None, rules: Optional[Dict[str, Any]] = None,
                   system: str = "", system_version: str = "", terminal_site: str = "", partner: str = "",
                   message_family: str = "", message_version: str = "", correction_policy: Optional[Dict[str, Any]] = None,
                   output_encoding: str = "", acceptance_fixtures: Optional[List[str]] = None) -> Dict[str, Any]:
    """Create the next version of `name`. Returns the profile document."""
    if not name.strip():
        raise ProfileError("BAD_PROFILE", "name is required")
    contract = mapping.normalize_contract(mapping_contract) if mapping_contract else None
    norm_rules = normalize_rules(rules, message_family or (rules or {}).get("family", ""))
    if message_family and norm_rules["family"] != message_family:
        norm_rules["family"] = message_family if message_family in FAMILIES else norm_rules["family"]
    for sid in acceptance_fixtures or []:
        if conn.execute("SELECT 1 FROM source_files WHERE id = ? AND workspace_id = ?", (sid, workspace_id)).fetchone() is None:
            raise ProfileError("BAD_PROFILE", f"acceptance fixture {sid} is not a source file of this workspace")
    with transaction(conn):
        prev = conn.execute("SELECT id, version FROM feed_profiles WHERE workspace_id = ? AND name = ? "
                            "ORDER BY version DESC LIMIT 1", (workspace_id, name)).fetchone()
        version = (prev["version"] + 1) if prev else 1
        pid = new_id("prof")
        conn.execute("""INSERT INTO feed_profiles (id, workspace_id, name, version, contract_json, rules_json, system,
                        system_version, terminal_site, partner, message_family, message_version, correction_policy_json,
                        output_encoding, acceptance_fixtures_json, verification_state, previous_version_id, created_by,
                        created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'unverified',?,?,?)""",
                     (pid, workspace_id, name, version, json.dumps(contract) if contract else "null",
                      json.dumps(norm_rules, sort_keys=True), system, system_version, terminal_site, partner,
                      norm_rules["family"], message_version, json.dumps(correction_policy or {}, sort_keys=True),
                      output_encoding, json.dumps(acceptance_fixtures or []), prev["id"] if prev else None, actor, now_iso()))
        audit(conn, workspace_id, actor, "profile.create", pid, f"{name} v{version}")
    return get_profile(conn, pid)


def get_profile(conn: sqlite3.Connection, profile_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM feed_profiles WHERE id = ?", (profile_id,)).fetchone()
    if row is None:
        raise ProfileError("UNKNOWN_PROFILE", profile_id)
    return profile_doc(row)


def profile_doc(row: sqlite3.Row) -> Dict[str, Any]:
    contract = json.loads(row["contract_json"]) if row["contract_json"] not in (None, "null") else None
    rules = json.loads(row["rules_json"] or "{}")
    fixtures = json.loads(row["acceptance_fixtures_json"] or "[]")
    doc = {"id": row["id"], "workspace_id": row["workspace_id"], "name": row["name"], "version": row["version"],
           "system": row["system"], "system_version": row["system_version"], "terminal_site": row["terminal_site"],
           "partner": row["partner"], "message_family": row["message_family"], "message_version": row["message_version"],
           "mapping_contract": _contract_for_contract_schema(contract),
           "structural_fingerprint": row["structural_fingerprint"] or "",
           "correction_policy": json.loads(row["correction_policy_json"] or "{}"),
           "output_encoding": row["output_encoding"], "acceptance_fixtures": fixtures,
           "verification_state": row["verification_state"], "verification_note": row["verification_note"],
           "verified_at": row["verified_at"], "verification": json.loads(row["verification_json"] or "{}"),
           "previous_version_id": row["previous_version_id"], "rules": rules, "created_by": row["created_by"],
           "created_at": row["created_at"]}
    if doc["previous_version_id"] is None:
        del doc["previous_version_id"]
    return doc


def _contract_for_contract_schema(contract: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The contracts' mapping_contract shape (fields, required_fields, extension_policy) plus ours."""
    if not contract:
        return {"fields": [], "required_fields": [], "extension_policy": "ignore"}
    out = dict(contract)
    out.setdefault("required_fields", [f["name"] for f in contract["fields"] if f.get("required")])
    out.setdefault("extension_policy", "preserve")
    return out


def latest(conn: sqlite3.Connection, workspace_id: str, name: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM feed_profiles WHERE workspace_id = ? AND name = ? ORDER BY version DESC LIMIT 1",
                       (workspace_id, name)).fetchone()
    return profile_doc(row) if row else None


def list_profiles(conn: sqlite3.Connection, workspace_id: str, after: str = "", limit: int = 100) -> List[Dict[str, Any]]:
    rows = conn.execute("SELECT * FROM feed_profiles WHERE workspace_id = ? AND id > ? ORDER BY id LIMIT ?",
                        (workspace_id, after, limit)).fetchall()
    return [profile_doc(r) for r in rows]


def set_verification(conn: sqlite3.Connection, profile_id: str, state: str, note: str, actor: str) -> Dict[str, Any]:
    """Administrator-recorded verification with evidence. fixture_tested comes from verify_fixtures."""
    if state not in VERIFICATION_STATES:
        raise ProfileError("BAD_STATE", f"one of {VERIFICATION_STATES}")
    if state == "fixture_tested":
        raise ProfileError("BAD_STATE", "fixture_tested is reached by running the acceptance fixtures")
    if state != "unverified" and len(note.strip()) < 8:
        raise ProfileError("EVIDENCE_REQUIRED", "name the partner test or production evidence in the note")
    row = conn.execute("SELECT workspace_id, verification_state FROM feed_profiles WHERE id = ?", (profile_id,)).fetchone()
    if row is None:
        raise ProfileError("UNKNOWN_PROFILE", profile_id)
    with transaction(conn):
        conn.execute("UPDATE feed_profiles SET verification_state = ?, verification_note = ?, verified_at = ? WHERE id = ?",
                     (state, note, now_iso(), profile_id))
        audit(conn, row["workspace_id"], actor, "profile.verify", profile_id, f"{row['verification_state']} -> {state}: {note}")
    return get_profile(conn, profile_id)


def verify_fixtures(conn: sqlite3.Connection, root: str, profile_id: str, actor: str) -> Dict[str, Any]:
    """Run every acceptance fixture through the profile's checks. All clean -> fixture_tested."""
    from . import messages
    doc = get_profile(conn, profile_id)
    fixtures = doc["acceptance_fixtures"]
    if not fixtures:
        raise ProfileError("NO_FIXTURES", "the profile lists no acceptance fixtures")
    results = []
    ok = True
    for sid in fixtures:
        src = conn.execute("SELECT * FROM source_files WHERE id = ?", (sid,)).fetchone()
        if src is None:
            results.append({"source_file_id": sid, "ok": False, "detail": "fixture source missing"})
            ok = False
            continue
        report = messages.validate_file(src["path"], src["encoding"] or "utf-8", doc)
        clean = not report["blocking"]
        ok = ok and clean
        results.append({"source_file_id": sid, "filename": src["filename"], "ok": clean, "format": report["format"],
                        "messages": report["messages"], "findings": report["findings"][:20],
                        "blocking": report["blocking"]})
    state = "fixture_tested" if ok else "unverified"
    with transaction(conn):
        conn.execute("UPDATE feed_profiles SET verification_state = CASE WHEN verification_state IN "
                     "('partner_tested','production_enabled') AND ? = 'fixture_tested' THEN verification_state ELSE ? END, "
                     "verification_json = ?, verified_at = ? WHERE id = ?",
                     (state, state, json.dumps({"fixtures": results, "ran_at": now_iso()}), now_iso(), profile_id))
        audit(conn, doc["workspace_id"], actor, "profile.fixtures", profile_id, f"ok={ok} fixtures={len(fixtures)}")
    return {"profile_id": profile_id, "ok": ok, "state": get_profile(conn, profile_id)["verification_state"],
            "results": results}


def profile_for_job(conn: sqlite3.Connection, job: sqlite3.Row) -> Optional[Dict[str, Any]]:
    if not job["profile_id"]:
        return None
    try:
        return get_profile(conn, job["profile_id"])
    except ProfileError:
        return None
