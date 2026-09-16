"""
Tests for substitution.py. Runs under pytest or as a plain script:
  python3 test_substitution.py
"""
from substitution import Edit, apply_edits, SubstitutionError, changed_indices


def test_single_edit():
    s = "EQD+CN+ASCU2643846+2200"
    out = apply_edits(s, [Edit(7, "ASCU2643846", "ASCU2643843")])
    assert out == "EQD+CN+ASCU2643843+2200"


def test_multi_edit_any_order():
    s = "a=AAAU1111111;b=BBBU2222222"
    edits = [Edit(16, "BBBU2222222", "BBBU2222229"),   # given out of order on purpose
             Edit(2,  "AAAU1111111", "AAAU1111119")]
    assert apply_edits(s, edits) == "a=AAAU1111119;b=BBBU2222229"


def test_verification_failure_fails_loud():
    s = "EQD+CN+ASCU2643846"
    try:
        apply_edits(s, [Edit(7, "WRONGTOKEN00", "x")])
    except SubstitutionError:
        return
    raise AssertionError("expected SubstitutionError on verification miss")


def test_overlap_fails_loud():
    s = "ABCDEFGHIJ"
    try:
        apply_edits(s, [Edit(0, "ABCDE", "x"), Edit(2, "CDEFG", "y")])
    except SubstitutionError:
        return
    raise AssertionError("expected SubstitutionError on overlap")


def test_unequal_length_ok():
    assert apply_edits("[X]", [Edit(1, "X", "LONGER")]) == "[LONGER]"


def test_no_edits_is_identity():
    s = "unchanged text"
    assert apply_edits(s, []) == s


def test_changed_indices_single_char():
    assert changed_indices("ASCU2643846", "ASCU2643843") == [10]


if __name__ == "__main__":
    import sys
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn(); print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1; print(f"FAIL  {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1; print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
