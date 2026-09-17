"""
mapping.py
==========
Mapping contracts and structural fingerprints for delimited feeds, the
service-side counterpart of src/lib/mapping.ts.

The approved contract says which fields exist and how they are identified
(by header name or by position). The observed fingerprint is computed from
the file's header row and column widths. They are compared, never confused:
raw file hashes play no part. Sampling never decides anything; every row of
the stream is validated against the identifier field contract while the job
runs, and a late violation prevents a completeness claim.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

RE_BIC_FULL = re.compile(r"^[A-Z]{4}[0-9]{7}$")
MAX_ISOLATED_VIOLATIONS = 3
BLOCK_RATIO = 0.2


def normalize_contract(contract: Dict[str, Any]) -> Dict[str, Any]:
    """Fill defaults and validate the shape used by the workspace."""
    if not isinstance(contract, dict):
        raise ValueError("mapping contract must be an object")
    fields = contract.get("fields") or []
    if not isinstance(fields, list) or not fields:
        raise ValueError("mapping contract needs at least one field")
    out = {
        "version": int(contract.get("version", 1)),
        "positional": bool(contract.get("positional", False)),
        "delimiter": contract.get("delimiter"),
        "has_header": bool(contract.get("has_header", not contract.get("positional", False))),
        "column_count": contract.get("column_count"),
        "extension_policy": contract.get("extension_policy", "preserve"),
        "required_fields": list(contract.get("required_fields") or []),
        "fields": [],
    }
    for f in fields:
        if not isinstance(f, dict) or "name" not in f:
            raise ValueError("each field needs a name")
        identity = f.get("identity", f["name"])
        ftype = f.get("type", "text")
        if ftype not in ("identifier", "text"):
            raise ValueError(f"field {f['name']!r}: type must be identifier or text")
        required = bool(f.get("required", f["name"] in out["required_fields"]))
        out["fields"].append({"name": f["name"], "identity": identity, "required": required, "type": ftype})
        if required and f["name"] not in out["required_fields"]:
            out["required_fields"].append(f["name"])
    if out["positional"]:
        for f in out["fields"]:
            if not isinstance(f["identity"], int):
                raise ValueError(f"positional contract: field {f['name']!r} needs an integer identity")
    return out


def fingerprint_of(header: Optional[List[str]], column_count: int, delimiter: str) -> Dict[str, Any]:
    canon = json.dumps({"delimiter": delimiter, "header": header, "column_count": column_count}, sort_keys=True)
    return {"delimiter": delimiter, "header": header, "column_count": column_count,
            "sha256": hashlib.sha256(canon.encode("utf-8")).hexdigest()}


def check_header(contract: Dict[str, Any], observed: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the contract against the observed header before streaming.

    Returns {"resolved": {field: column|None}, "findings": [(code, detail, location)],
    "blocked": bool, "treatment": mapping_change_treatment}.
    """
    findings: List[tuple] = []
    resolved: Dict[str, Optional[int]] = {}
    blocked = False
    treatment = "continue_identity_mapping"
    header = observed.get("header") or []
    if contract["positional"]:
        if contract.get("column_count") is not None and observed["column_count"] != contract["column_count"]:
            blocked = True
            treatment = "block_mapping_review"
            findings.append(("MAPPING_DRIFT",
                             f"Positional contract expects {contract['column_count']} columns; the file has "
                             f"{observed['column_count']}. A column index cannot be reused once the field it "
                             f"represented may have moved.", ""))
        for f in contract["fields"]:
            resolved[f["name"]] = None if blocked else int(f["identity"])
    else:
        lower = [h.lower() for h in header]
        dupes = sorted({h for i, h in enumerate(lower) if h and lower.index(h) != i})
        if dupes:
            blocked = True
            treatment = "block_mapping_review"
            findings.append(("MAPPING_DRIFT", "Duplicate header name%s %s: a name that appears twice cannot identify a column."
                             % ("" if len(dupes) == 1 else "s", ", ".join(f'"{d}"' for d in dupes)), "header row"))
        for f in contract["fields"]:
            key = str(f["identity"]).lower()
            idx = lower.index(key) if key in lower else -1
            if idx == -1:
                resolved[f["name"]] = None
                if f["required"]:
                    blocked = True
                    treatment = "block_mapping_review"
                    findings.append(("MAPPING_DRIFT", f"Required column \"{f['identity']}\" is missing from the header (have: "
                                     f"{', '.join(chr(34) + h + chr(34) for h in header) or 'no header'}).", "header row"))
            else:
                resolved[f["name"]] = idx
        if not blocked:
            order = [lower.index(str(f["identity"]).lower()) if str(f["identity"]).lower() in lower else -1
                     for f in contract["fields"]]
            if order != sorted(order):
                findings.append(("MAPPING_REORDER_HARMLESS",
                                 "Columns are in a different order than the contract lists them; identity-based mapping continues.", ""))
            known = {str(f["identity"]).lower() for f in contract["fields"]}
            extra = [h for h in header if h and h.lower() not in known]
            if extra:
                findings.append(("MAPPING_EXTENSION_PRESERVED", "Additional column%s %s preserved untouched."
                                 % ("" if len(extra) == 1 else "s", ", ".join(f'"{h}"' for h in extra)), ""))
    return {"resolved": resolved, "findings": findings, "blocked": blocked, "treatment": treatment}


class StreamValidator:
    """Full-stream validation of identifier fields: empty or identifier-shaped.

    Counts violations while records stream; `finish()` decides between
    isolated bad records (reported one by one) and a layout change (blocked).
    """

    def __init__(self, id_columns: List[int]):
        self.id_columns = id_columns
        self.rows = 0
        self.violations: List[Dict[str, Any]] = []
        self.violation_count = 0

    def observe(self, row_no: int, values: List[str]) -> None:
        self.rows += 1
        for col in self.id_columns:
            value = values[col].strip() if col < len(values) else ""
            if value != "" and not RE_BIC_FULL.match(re.sub(r"[\s-]", "", value.upper())):
                self.violation_count += 1
                if len(self.violations) < 50:
                    self.violations.append({"row": row_no, "column": col, "value": value[:64]})

    def finish(self) -> Dict[str, Any]:
        data_rows = max(1, self.rows)
        blocked = self.violation_count > MAX_ISOLATED_VIOLATIONS and self.violation_count / data_rows > BLOCK_RATIO
        findings: List[tuple] = []
        if blocked:
            first = self.violations[0]
            findings.append(("MAPPING_VIOLATION_LATE",
                             f"{self.violation_count} of {data_rows} rows carry a value in the identifier column that is not "
                             f"identifier-shaped (first: row {first['row']}, \"{first['value']}\"). This looks like a layout "
                             f"change, not isolated bad records; the transformation is blocked until the mapping is reviewed.",
                             f"row {first['row']}"))
        else:
            for v in self.violations:
                findings.append(("MAPPING_VIOLATION_LATE",
                                 f"Row {v['row']}, column {v['column']}: \"{v['value']}\" is not identifier-shaped; the row is "
                                 f"reported, not transformed.", f"row {v['row']}"))
        return {"rows": self.rows, "violations": self.violation_count, "blocked": blocked, "findings": findings,
                "treatment": "block_mapping_review" if blocked else "continue_identity_mapping"}


def propose_contract(header: Optional[List[str]], sample_rows: List[List[str]], delimiter: str) -> Dict[str, Any]:
    """A contract proposed from a header and a sample (a starting point the user reviews)."""
    header_like = bool(header) and all(h.strip() != "" and not RE_BIC_FULL.match(h.strip()) for h in header)
    first = header if header_like else (sample_rows[0] if sample_rows else [])
    width = len(first)
    fields = []
    for i in range(width):
        hits = sum(1 for r in sample_rows if i < len(r) and RE_BIC_FULL.match(r[i].strip().upper()))
        is_id = bool(sample_rows) and hits / len(sample_rows) >= 0.5
        name = header[i].strip() if header_like else f"column {i}"
        fields.append({"name": name, "identity": name if header_like else i, "required": is_id,
                       "type": "identifier" if is_id else "text"})
    return normalize_contract({"version": 1, "positional": not header_like, "delimiter": delimiter,
                               "has_header": header_like, "fields": fields,
                               "column_count": None if header_like else width, "extension_policy": "preserve"})
