"""
Contract data tests: the Python side agrees with contracts/*.json exactly as
the TypeScript side does (tests/contracts.test.ts). Runs under pytest or as a
plain script: python3 test_contracts.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contracts                        # noqa: E402
import correction_report as cr          # noqa: E402
import equipment_checkdigit as k        # noqa: E402


def test_enums_match_kernel():
    assert sorted(contracts.ENUMS["identifier_scheme"]) == sorted(t.value for t in k.IdentifierType)
    assert sorted(contracts.ENUMS["field_context"]) == sorted(c.value for c in k.FieldContext)
    assert sorted(contracts.ENUMS["kernel_status"]) == sorted(s.value for s in k.Status)
    assert cr.OFFSET_KIND in contracts.ENUMS["offset_kind"]
    for basis in (cr.IDENTITY_PRINTED_VALID, cr.IDENTITY_MATH_CANDIDATE, cr.IDENTITY_UNVERIFIED):
        assert basis in contracts.ENUMS["identity_basis"], basis


def test_required_finding_codes_exist():
    for code in ("IMPORT_INTENT_UNRESOLVED", "SNAPSHOT_INCOMPLETE", "BASELINE_CONFLICT", "MAPPING_DRIFT",
                 "MESSAGE_ID_CONFLICT", "PREDECESSOR_UNRESOLVED", "CONTEXT_AMBIGUOUS", "APPROVAL_STALE",
                 "FEEDBACK_UNMATCHED", "FEEDBACK_AMBIGUOUS", "DELIVERY_OUTCOME_UNKNOWN",
                 "RESOURCE_BUDGET_EXCEEDED", "UNSUPPORTED_INPUT"):
        f = contracts.finding(code)
        assert f["severity"] in ("info", "warning", "blocking") and f["recovery"]
    codes = [f["code"] for f in contracts.FINDINGS]
    assert len(codes) == len(set(codes))
    try:
        contracts.finding("NOT_A_CODE")
        raise AssertionError("unknown code must raise")
    except KeyError:
        pass


def test_make_finding_validates_against_schema():
    doc = contracts.make_finding("MAPPING_DRIFT", id="f_9", detail="column 'container' renamed",
                                 location={"line": 4, "offset_kind": "text_codepoint"},
                                 rule_id="mapping/required-columns", rule_version="1")
    assert contracts.validate_entity("finding", doc) == []
    bad = dict(doc, severity="critical")
    assert contracts.validate_entity("finding", bad)


def test_examples_validate():
    helpers = {"id", "sha256", "timestamp", "version", "mapping_contract", "coverage_scope",
               "source_location", "identifier_assertion", "layer_result", "event_time", "edit",
               "selection_manifest"}
    for name, doc in contracts.EXAMPLES["valid"].items():
        errors = contracts.validate_entity(name, doc)
        assert errors == [], (name, errors)
    for name in contracts.SCHEMA["$defs"]:
        if name not in helpers:
            assert name in contracts.EXAMPLES["valid"], f"no example for {name}"
    for label, case in contracts.EXAMPLES["invalid"].items():
        assert contracts.validate_entity(case["entity"], case["document"]), label


def test_every_x_enum_is_defined():
    import json
    import re
    text = json.dumps(contracts.SCHEMA)
    names = set(re.findall(r'"x-enum": "([a-z_]+)"', text))
    assert len(names) > 10
    for name in names:
        assert isinstance(contracts.ENUMS.get(name), list), name


if __name__ == "__main__":
    tests = [v for kname, v in sorted(globals().items()) if kname.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
