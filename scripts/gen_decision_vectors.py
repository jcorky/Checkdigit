"""
Emit decision-table vectors from the Python kernel for the TypeScript parity test.

Runs correct_identifier and correct_x12_equipment over a grid of tokens, field
contexts, owner policies, corroboration sets and operator policies, and writes
every field of every CorrectionResult (reason text included) to
tests/vectors/decision_vectors.json. The TypeScript port must reproduce each
record exactly. Also records the kernel's ValueError messages for bad input.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "checkdigit"))

import equipment_checkdigit as k   # noqa: E402
from policy import Policy          # noqa: E402

TOKENS = [
    "CSQU3054383", "APLU9192812", "CBHU2426018", "APLX1234560", "ABCD1234566",
    "L01U1150107", "L01U1150105", "218124712173", "218124712170", "HELLO WORLD",
    "APLU 919281 2", "L5G1", "MSKU1234567", "msku-123456-7", "", "12345",
    "CONT1234565", "MSCK1234560", "2181247121734", "22G1", "aplu9192819",
    "TESU1234560", "TSTU1234560",
    # non-ASCII digits and letters: invalid structure on both sides (ASCII-only by decision)
    "MSKU١٢٣٤٥٦٧", "21812471217٣", "MSKÜ1234567",
]
CONTEXTS = list(k.FieldContext)
POLICIES = {
    "none": None,
    "deny_APL": Policy(deny={"APL"}),
    "allow_APL": Policy(allow={"APL"}),
    "per_prefix_L01_lenient": Policy(per_prefix={"L01": "lenient"}),
    "default_lenient": Policy(default_policy="lenient"),
    "deny_wild_TE": Policy(deny={"TE*"}),
    "allow_wild_TS_deny_TST": Policy(allow={"TS*"}, deny={"TST"}),
}


def record(r):
    return {
        "raw": r.raw, "normalized": r.normalized, "id_type": r.id_type.value,
        "status": r.status.value, "confidence": r.confidence, "reason": r.reason,
        "printed_check": r.printed_check, "computed_check": r.computed_check,
        "corrected": r.corrected, "changed": r.changed,
    }


def main():
    cases = []
    for tok in TOKENS:
        for ctx in CONTEXTS:
            for op in ("strict", "lenient"):
                # known_owner_prefixes only matters in the FREE_TEXT branch of _decide_bic
                for known in ((None, ["APL"]) if ctx is k.FieldContext.FREE_TEXT else (None,)):
                    r = k.correct_identifier(tok, ctx, owner_policy=op,
                                             known_owner_prefixes=set(known) if known else None)
                    cases.append({"fn": "correct_identifier", "token": tok, "context": ctx.value,
                                  "owner_policy": op, "known_owner_prefixes": known,
                                  "policy": "none", "result": record(r)})
        # operator policies: the deny/allow/per-prefix hooks only bite in the
        # EQUIPMENT_ID and FREE_TEXT branches
        for ctx in (k.FieldContext.EQUIPMENT_ID, k.FieldContext.FREE_TEXT):
            for name, pol in POLICIES.items():
                if pol is None:
                    continue
                r = k.correct_identifier(tok, ctx, policy=pol)
                cases.append({"fn": "correct_identifier", "token": tok, "context": ctx.value,
                              "owner_policy": "strict", "known_owner_prefixes": None,
                              "policy": name, "result": record(r)})

    x12 = [
        ("EMHU", "123456", ""), ("EMHU", "123456", None), ("EMHU", "123456", "1"),
        ("EMHU", "123456", "0"), ("EMH", "123456", "1"), ("EMHU", "1234567", "1"),
        ("EMHX", "123456", "1"), ("EMHU", "12", ""), ("emhu", "12-34-56", "2"),
        ("APLU", "919281", "2"), ("APLU", "919281", "9"), ("MSCU", "123456", "0"),
        ("EMHÜ", "123456", "1"), ("EMHU", "12٣456", ""),
        ("TCLU", "456789", ""), ("HLBU", "112233", None), ("TSTU", "000001", ""),
    ]
    for init, num, chk in x12:
        for name, pol in POLICIES.items():
            r = k.correct_x12_equipment(init, num, chk, policy=pol)
            cases.append({"fn": "correct_x12_equipment", "initial": init, "number": num,
                          "printed_check": chk, "policy": name, "result": record(r)})

    errors = []
    for fn, arg in (("iso6346_value", "@"), ("iso6346_value", "a"), ("iso6346_check_digit", "CSQU30543"),
                    ("iso6346_check_digit", "CSQU3054383"), ("uic_check_digit", "2181247121"),
                    ("uic_check_digit", "2181247121A")):
        try:
            getattr(k, fn)(arg)
            raise SystemExit(f"expected ValueError from {fn}({arg!r})")
        except ValueError as exc:
            errors.append({"fn": fn, "arg": arg, "message": str(exc)})
    try:
        k.correct_identifier("CSQU3054383", owner_policy="loose")
        raise SystemExit("expected ValueError for bad owner_policy")
    except ValueError as exc:
        errors.append({"fn": "correct_identifier", "arg": "owner_policy=loose", "message": str(exc)})

    policies = {name: (pol.to_dict() if pol else None) for name, pol in POLICIES.items()}
    out = {"cases": cases, "errors": errors, "policies": policies}
    path = os.path.join(HERE, "..", "tests", "vectors", "decision_vectors.json")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, separators=(",", ":"), ensure_ascii=False)
        fh.write("\n")
    print(f"decision vectors: {len(cases)} cases, {len(errors)} error messages -> {os.path.relpath(path)}")


if __name__ == "__main__":
    main()
