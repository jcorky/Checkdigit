"""
run_pass5.py
============
Round-trips the SNX sample through pass 5 (correct + synced substitution),
PROVES the edit was surgical and kept all attribute occurrences in sync, checks
the four known fixtures, exercises the XXE guard, writes the corrected file, and
prints the before/after diff.

Sync/surgical verification on the file:
  1. length preserved;
  2. the ONLY bytes that differ are the check-digit positions named in the
     change record -- across ALL occurrences of every corrected container;
  3. for each correction the OLD number no longer appears in any id-bearing
     attribute and the NEW number appears exactly as many times as the old did
     (the denormalized key stayed in sync);
  4. the corrected text still parses (hardened) and yields zero further
     corrections.
"""
import re
import sys
from collections import Counter

from snx_locator import (
    harden_and_parse, evaluate_text, size_type_codes, XmlSecurityError,
)
from snx_corrector import correct_snx
from substitution import changed_indices
import diff_report

import os
_HERE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(_HERE, "out"), exist_ok=True)
SRC = os.path.join(_HERE, "samples", "4Containers_snx_Example.xml")
DST = os.path.join(_HERE, "out", "4Containers_snx_Example.CORRECTED.xml")

# Known fixtures (3 invalid -> correction, 1 already valid -> untouched).
EXPECTED = {
    "APLU9192812": "APLU9192819",
    "APLU7979534": "APLU7979533",
    "APLU2127329": "APLU2127323",
    "CBHU2426018": None,          # valid; must NOT appear in changes
}

_ATTR_VAL = re.compile(r'\b(?:eqid|id|unique-key)="%s"')


def count_attr_value(text: str, value: str) -> int:
    return len(re.findall(r'\b(?:eqid|id|unique-key)="' + re.escape(value) + r'"', text))


def verify(original: str, report, owner_policy: str):
    corrected = report.corrected_text

    # (1) length preserved
    assert len(corrected) == len(original), f"length changed {len(original)}->{len(corrected)}"

    # (2) only check-digit positions changed, across every occurrence
    expected = set()
    for c in report.corrected:
        d = changed_indices(c.old, c.new)
        assert len(d) == 1, f"correction altered >1 char: {c.old}->{c.new}"
        for o in c.occurrences:
            expected.add(o.offset + d[0])
    actual = set(changed_indices(original, corrected))
    assert actual == expected, (
        f"byte diff != check-digit positions; "
        f"unexpected={sorted(actual - expected)[:8]} missing={sorted(expected - actual)[:8]}")

    # (3) per-correction sync: old gone everywhere, new present the same number of times
    for c in report.corrected:
        n = len(c.occurrences)
        assert count_attr_value(original, c.old) == n, f"{c.old} pre-count != occurrences"
        assert count_attr_value(corrected, c.old) == 0, f"{c.old} still present after fix"
        assert count_attr_value(corrected, c.new) == n, f"{c.new} not synced to {n} positions"

    # (4) re-parse: still valid XML, nothing left to correct, count stable
    harden_and_parse(corrected)
    re_pairs = evaluate_text(corrected, owner_policy=owner_policy)
    re_tally = Counter(r.status.value for _, r in re_pairs)
    assert re_tally.get("corrected", 0) == 0, f"still correctable after fix: {re_tally}"
    assert len(re_pairs) == report.total_containers, "container count changed"
    return actual, re_tally


def check_fixtures(report):
    got = {c.old: c.new for c in report.corrected}
    for old, new in EXPECTED.items():
        if new is None:
            assert old not in got, f"{old} should be valid/untouched but was changed"
        else:
            assert got.get(old) == new, f"{old}: expected -> {new}, got -> {got.get(old)}"


def test_xxe_guard():
    hostile = (
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE snx [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
        '<tos:snx xmlns:tos="urn:tos:container-xml">'
        '<container eqid="&xxe;" type="L5G1"/></tos:snx>'
    )
    try:
        harden_and_parse(hostile)
    except XmlSecurityError:
        return True
    raise AssertionError("XXE guard did not fire on DOCTYPE/ENTITY payload")


def main():
    original = open(SRC, encoding="utf-8").read()

    print("=== XXE guard ===")
    test_xxe_guard()
    print("  DOCTYPE/ENTITY payload rejected -> PASS\n")

    report = correct_snx(original, owner_policy="strict")
    check_fixtures(report)
    actual, re_tally = verify(original, report, "strict")

    print("=== SNX round-trip (policy=strict) ===")
    print(diff_report.render(report, show_segments=True))
    print(f"  size/type codes present (untouched): {size_type_codes(original)}")
    print(f"  VERIFY: bytes_changed={len(actual)}  re-parse={dict(re_tally)}  "
          f"length={len(original)} (unchanged)  fixtures OK  -> PASS")

    with open(DST, "w", encoding="utf-8") as fh:
        fh.write(report.corrected_text)
    print(f"  wrote: {DST.split('/')[-1]}")


if __name__ == "__main__":
    main()
