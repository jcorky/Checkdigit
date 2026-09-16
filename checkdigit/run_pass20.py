#!/usr/bin/env python3
"""
run_pass20.py -- operator rules/policy engine.

Proves the policy layer over the kernel:

  resolution order  deny > allow > per_prefix > default (first match wins)
  wildcards         'TES*' matches TESU/TEST...; exact keys win over wildcards
  validation        bad default / bad per-prefix value / allow∩deny -> loud
  json round-trip   to_dict/from_dict/from_json stable; load from string
  allow effect      an unregistered pseudo-prefix becomes corroborated ->
                    CORRECTED at low trust (was FLAGGED)
  per-prefix effect a prefix set to 'lenient' corrects its pseudo-prefix boxes
                    while the global default stays strict for others
  deny effect       a deny-listed prefix is FLAGGED even when its check digit is
                    structurally VALID (the strongest guarantee)
  cross-format      policy flows through txt, csv, edifact, and X12 paths
  service           process_upload(policy=...) honors deny end-to-end + audits
"""
import json
import os
import tempfile

import db
import dispatcher
import equipment_checkdigit as k
import service
from equipment_checkdigit import FieldContext, Status, correct_identifier
from policy import Policy, PolicyError, plan_for_tokens

GOOD = "MSKU7351770"      # valid (check 0)
BAD = "MSKU7351773"       # check should be 0
PSEUDO_OK = "L01U1150107"  # container-shaped, non-letter owner, check valid under mod-11
CBHU_GOOD = "CBHU6818017"


def test_resolution_and_wildcards():
    # Owner codes are 3 letters. deny 'TES' exactly; allow other wildcard 'TR*'.
    # per_prefix uses an exact key 'MSC' and a wildcard 'MS*' to show exact wins.
    p = Policy(default_policy="strict", allow={"LEA", "TR*"}, deny={"TES"},
               per_prefix={"MSC": "lenient", "MS*": "strict"})
    # exact deny fires (input truncated to 3-char owner)
    t = p.resolve("TESU")
    assert t.force_flag and not t.corroborated
    # wildcard allow catches TR* (e.g. TRHU)
    assert p.resolve("TRHU").corroborated
    # exact allow
    assert p.resolve("LEAU").corroborated
    # per-prefix exact 'MSC' beats wildcard 'MS*'
    assert p.resolve("MSCU").owner_policy == "lenient"
    assert p.resolve("MSKU").owner_policy == "strict"   # MS* wildcard
    # default for the rest
    d = p.resolve("ZZZU")
    assert not d.force_flag and not d.corroborated and d.owner_policy == "strict"
    print("  resolution: deny>allow>per_prefix>default; exact beats wildcard; 3-char owner")


def test_validation():
    for bad in (lambda: Policy(default_policy="loose"),
                lambda: Policy(per_prefix={"ABC": "nope"}),
                lambda: Policy(allow={"ABC"}, deny={"ABC"})):
        try:
            bad()
            raise AssertionError("should have raised PolicyError")
        except PolicyError:
            pass
    print("  validation: bad default / per-prefix / allow∩deny all rejected loudly")


def test_json_roundtrip():
    p = Policy(default_policy="lenient", allow={"LEA"}, deny={"TES*"},
               per_prefix={"MSC": "strict"})
    d = p.to_dict()
    assert Policy.from_dict(d).to_dict() == d
    assert Policy.from_json(json.dumps(d)).to_dict() == d
    assert Policy.from_dict(None).to_dict() == Policy().to_dict()
    try:
        Policy.from_json("{not json")
        raise AssertionError
    except PolicyError:
        pass
    print("  json: to_dict/from_dict/from_json stable; bad JSON rejected")


def test_allow_effect():
    # MSKU bad check, low trust, MSK NOT registered -> normally FLAGGED.
    base = correct_identifier(BAD, FieldContext.FREE_TEXT, owner_policy="strict")
    assert base.status is Status.FLAGGED
    # allow MSK via policy -> corroborated -> CORRECTED
    p = Policy(allow={"MSK"})
    out = correct_identifier(BAD, FieldContext.FREE_TEXT, owner_policy="strict", policy=p)
    assert out.status is Status.CORRECTED and out.corrected == GOOD
    print("  allow: unregistered prefix becomes corroborated -> corrected at low trust")


def test_per_prefix_effect():
    # Pseudo-prefix L01U: strict FLAGS it (non-conformant owner); lenient accepts
    # the standard mod-11. With a CORRECT check (L01U1150105) lenient => VALID.
    strict = correct_identifier("L01U1150105", FieldContext.EQUIPMENT_ID, owner_policy="strict")
    assert strict.status is Status.FLAGGED                # strict never trusts pseudo-prefix
    p = Policy(default_policy="strict", per_prefix={"L01": "lenient"})
    out = correct_identifier("L01U1150105", FieldContext.EQUIPMENT_ID, owner_policy="strict", policy=p)
    assert out.status is Status.VALID, out                # correct check under lenient
    # a WRONG check under the same lenient prefix is corrected, not flagged
    fix = correct_identifier("L01U1150107", FieldContext.EQUIPMENT_ID, owner_policy="strict", policy=p)
    assert fix.status is Status.CORRECTED and fix.corrected == "L01U1150105"
    # a different pseudo-prefix (not in per_prefix) stays strict-flagged
    other = correct_identifier("Z09U1150108", FieldContext.EQUIPMENT_ID, owner_policy="strict", policy=p)
    assert other.status is Status.FLAGGED
    print("  per-prefix: L01->lenient validates/corrects its boxes; others stay strict-flagged")


def test_deny_overrides_valid():
    # GOOD has a VALID check digit; deny must still FLAG it for human review.
    base = correct_identifier(GOOD, FieldContext.EQUIPMENT_ID)
    assert base.status is Status.VALID
    p = Policy(deny={"MSK"})
    out = correct_identifier(GOOD, FieldContext.EQUIPMENT_ID, policy=p)
    assert out.status is Status.FLAGGED and "deny list" in out.reason
    assert out.computed_check == "0"                    # still reports the math
    print("  deny: a structurally VALID box is flagged for review (strongest guarantee)")


def test_plan_for_tokens():
    p = Policy(default_policy="strict", allow={"LEA"}, deny={"TES*"},
               per_prefix={"MSC": "lenient"})
    pol_by, allow, deny = plan_for_tokens(p, ["LEAU", "TESU", "MSCU", "ZZZU"],
                                          base_known={"BIC"})
    assert "LEA" in allow and "BIC" in allow            # owner code, not 4-char token
    assert "TES" in deny
    assert pol_by["MSC"] == "lenient" and pol_by["ZZZ"] == "strict"
    print("  plan_for_tokens: batch resolves to known-set + deny-set + policy map (3-char)")


def test_cross_format():
    p = Policy(deny={"MSK"})
    # txt
    _, rep = dispatcher.correct(f"box {GOOD} ok", owner_policy="strict", trust=True, policy=p)
    assert rep.flagged and not rep.corrected            # valid box denied -> flagged
    # csv (hint path)
    _, rep2 = dispatcher.correct_with_hint(
        f"ref,box\nA,{GOOD}\n", format_hint="csv",
        parse_options={"columns": ["box"]}, owner_policy="strict", trust=True, policy=p)
    assert rep2.flagged and not rep2.corrected
    # edifact
    edi = ("UNB+UNOA:2+S+R+260609:1200+1'UNH+1+COPRAR:D:95B:UN'"
           f"EQD+CN+{GOOD}+22G1'UNT+3+1'UNZ+1+1'")
    _, rep3 = dispatcher.correct(edi, owner_policy="strict", policy=p)
    assert rep3.flagged and not rep3.corrected
    # X12 (N7 path) -- MAEU initial, deny MAE
    p2 = Policy(deny={"MAE"})
    x12 = "ISA*~GS*~ST*~N7*MAEU*733177*40000*N***2200*G*9G1**L~SE*~GE*~IEA*~"
    _, rep4 = dispatcher.correct(x12, owner_policy="strict", policy=p2)
    assert rep4.flagged and not rep4.corrected, rep4.summary()
    print("  cross-format: deny honored through txt, csv, edifact, and X12 N7 paths")


def test_service_integration():
    path = os.path.join(tempfile.mkdtemp(), "p20.db")
    conn = db.connect(path, create_schema=True)
    p = Policy(deny={"MSK"})
    res = service.process_upload(
        conn, f"manifest {GOOD}".encode(), filename="m.txt", content_type="text/plain",
        user_agent="p20", owner_policy="strict", trust=True, policy=p)
    assert res["status"] == "processed"
    assert res["report"].flagged and not res["report"].corrected
    # the flagged container is recorded as flagged in the warehouse
    row = conn.execute("SELECT times_flagged FROM containers WHERE eqid=?", (GOOD,)).fetchone()
    assert row and row["times_flagged"] == 1
    conn.close()
    print("  service: policy deny honored end-to-end; flagged disposition audited")


def main():
    print("Pass 20 -- operator rules/policy engine")
    test_resolution_and_wildcards()
    test_validation()
    test_json_roundtrip()
    test_allow_effect()
    test_per_prefix_effect()
    test_deny_overrides_valid()
    test_plan_for_tokens()
    test_cross_format()
    test_service_integration()
    print("\nPASS: policy engine verified -- resolution, effects, cross-format, audit.")


if __name__ == "__main__":
    main()
