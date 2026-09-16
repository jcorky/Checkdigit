"""
Tests for equipment_checkdigit (pass 2 correction kernel).

Runs under pytest *or* as a plain script:  python3 test_equipment_checkdigit.py
(No third-party dependency required.)
"""
from equipment_checkdigit import (
    iso6346_check_digit, uic_check_digit,
    correct_identifier, correct_x12_equipment,
    FieldContext, Status, IdentifierType,
)

# --------------------------------------------------------------------------- #
# Algorithm level
# --------------------------------------------------------------------------- #

def test_iso6346_canonical_example():
    assert iso6346_check_digit("CSQU305438") == 3            # CSQU3054383

def test_iso6346_remainder_10_maps_to_zero():
    assert iso6346_check_digit("APLU100000") == 0            # remainder 10 -> 0

def test_uic_worked_example():
    assert uic_check_digit("21812471217") == 3               # 21 81 2471 217-3

def test_iso6346_value_rejects_bad_char():
    try:
        iso6346_check_digit("@PLU123456")
    except ValueError:
        return
    raise AssertionError("expected ValueError for non-ISO6346 character")


# --------------------------------------------------------------------------- #
# SNX fixture: the four containers in 4Containers_snx_Example.xml
# Expectation: correct the three bad ones, leave CBHU2426018 untouched.
# --------------------------------------------------------------------------- #

def test_snx_fixture():
    cases = {
        "APLU9192812": ("APLU9192819", Status.CORRECTED),
        "APLU7979534": ("APLU7979533", Status.CORRECTED),
        "APLU2127329": ("APLU2127323", Status.CORRECTED),
        "CBHU2426018": (None,          Status.VALID),
    }
    for eqid, (corrected, status) in cases.items():
        r = correct_identifier(eqid, FieldContext.EQUIPMENT_ID)
        assert r.id_type is IdentifierType.ISO6346, (eqid, r.id_type)
        assert r.status is status, (eqid, r.status, r.reason)
        assert r.corrected == corrected, (eqid, r.corrected)

def test_snx_valid_one_is_not_changed():
    r = correct_identifier("CBHU2426018", FieldContext.EQUIPMENT_ID)
    assert not r.changed
    assert r.corrected is None


# --------------------------------------------------------------------------- #
# Fail-loud confidence gates
# --------------------------------------------------------------------------- #

def test_freetext_checkfail_is_flagged_not_corrected():
    r = correct_identifier("APLU9192812", FieldContext.FREE_TEXT)
    assert r.status is Status.FLAGGED
    assert r.corrected is None
    assert r.computed_check == "9"          # suggestion still surfaced

def test_freetext_corroborated_by_owner_registry_is_corrected():
    r = correct_identifier("APLU9192812", FieldContext.FREE_TEXT,
                           known_owner_prefixes={"APL"})
    assert r.status is Status.CORRECTED
    assert r.corrected == "APLU9192819"

def test_unusual_category_is_flagged():
    r = correct_identifier("APLX1234560", FieldContext.EQUIPMENT_ID)
    assert r.status is Status.FLAGGED
    assert r.id_type is IdentifierType.UNKNOWN

def test_ilu_category_routes_and_shares_math():
    body = "ABCD123456"                      # category 'D' -> ILU
    token = body + str(iso6346_check_digit(body))
    r = correct_identifier(token, FieldContext.EQUIPMENT_ID)
    assert r.id_type is IdentifierType.ILU
    assert r.status is Status.VALID

def test_size_type_passthrough():
    r = correct_identifier("L5G1", FieldContext.SIZE_TYPE)
    assert r.status is Status.NOT_A_TARGET
    assert r.id_type is IdentifierType.SIZE_TYPE

def test_uic_requires_rail_context_to_correct():
    valid_uic = "218124712173"
    assert correct_identifier(valid_uic, FieldContext.UNKNOWN).status is Status.FLAGGED
    assert correct_identifier(valid_uic, FieldContext.RAIL_VEHICLE).status is Status.VALID

def test_uic_correction_in_rail_context():
    r = correct_identifier("218124712170", FieldContext.RAIL_VEHICLE)   # bad check (3 expected)
    assert r.status is Status.CORRECTED
    assert r.corrected == "218124712173"

def test_garbage_is_invalid_structure():
    r = correct_identifier("HELLO WORLD", FieldContext.UNKNOWN)
    assert r.status is Status.INVALID_STRUCTURE

def test_painted_form_is_normalized():
    r = correct_identifier("APLU 919281 2", FieldContext.EQUIPMENT_ID)
    assert r.normalized == "APLU9192812"
    assert r.corrected == "APLU9192819"


# --------------------------------------------------------------------------- #
# X12 split-field adapter (N7-01 initial + N7-02 number + optional N7-18)
# --------------------------------------------------------------------------- #

def test_x12_populates_absent_check_digit():
    r = correct_x12_equipment("EMHU", "123456", "")          # N7-18 empty
    assert r.status is Status.CORRECTED
    assert r.printed_check is None
    assert r.corrected[:10] == "EMHU123456"

def test_x12_validates_correct_check_digit():
    body = "EMHU123456"
    good = str(iso6346_check_digit(body))
    r = correct_x12_equipment("EMHU", "123456", good)
    assert r.status is Status.VALID

def test_x12_corrects_wrong_check_digit():
    body = "EMHU123456"
    good = str(iso6346_check_digit(body))
    wrong = str((int(good) + 1) % 10)
    r = correct_x12_equipment("EMHU", "123456", wrong)
    assert r.status is Status.CORRECTED
    assert r.computed_check == good

def test_x12_rejects_bad_initial():
    r = correct_x12_equipment("EMH", "123456", "1")          # 3-char initial
    assert r.status is Status.INVALID_STRUCTURE


# --------------------------------------------------------------------------- #
# Non-standard owner (carrier pseudo-prefix, e.g. L01U... in synthetic data)
# --------------------------------------------------------------------------- #

def test_pseudo_prefix_strict_is_flagged():
    r = correct_identifier("L01U1150107", FieldContext.EQUIPMENT_ID)   # default strict
    assert r.status is Status.FLAGGED
    assert r.corrected is None
    assert r.computed_check == "5"        # suggestion under standard mod-11

def test_pseudo_prefix_lenient_is_corrected():
    r = correct_identifier("L01U1150107", FieldContext.EQUIPMENT_ID, owner_policy="lenient")
    assert r.status is Status.CORRECTED
    assert r.corrected == "L01U1150105"

def test_standard_owner_unaffected_by_policy():
    # a real-shaped BIC token corrects the same under either policy
    for policy in ("strict", "lenient"):
        r = correct_identifier("APLU9192812", FieldContext.EQUIPMENT_ID, owner_policy=policy)
        assert r.corrected == "APLU9192819", policy


# --------------------------------------------------------------------------- #
# Plain-script runner (works without pytest installed)
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import sys
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
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
