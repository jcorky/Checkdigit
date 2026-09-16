"""
run_pass4.py
============
Round-trips the uploaded EDIFACT files through pass 4 (correct + substitute),
PROVES the substitution was surgical, writes the corrected files, and prints the
before/after diff.

Verification performed on every file:
  1. length preserved (check-digit edits are 1-for-1);
  2. the ONLY bytes that differ are the check-digit positions named by the
     change record -- nothing else moved;
  3. re-parsing the corrected text yields zero further corrections (every fix
     now validates) and the same container count.
"""
import sys
from collections import Counter

from edifact_locator import evaluate_text
from edifact_corrector import correct_edifact
from substitution import changed_indices
import diff_report

import os
_HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(_HERE, "samples")
OUT_DIR = os.path.join(_HERE, "out")
os.makedirs(OUT_DIR, exist_ok=True)

JOBS = [
    ("BAPLIE (USER01)", os.path.join(SAMPLES, "USER01_baplie_edi.txt"),
     "strict",  f"{OUT_DIR}/USER01_baplie_edi.CORRECTED.txt"),
    ("COPRAR loadlist (L02)", os.path.join(SAMPLES, "L02LOADLIST.txt"),
     "lenient", f"{OUT_DIR}/L02LOADLIST.CORRECTED_lenient.txt"),
]


def verify(original: str, report, owner_policy: str):
    corrected = report.corrected_text

    # (1) length preserved
    assert len(corrected) == len(original), (
        f"length changed: {len(original)} -> {len(corrected)}")

    # (2) only the check-digit positions changed -- compute expected set from the
    #     change record and compare to the actual byte-level diff
    expected = set()
    for c in report.corrected:
        d = changed_indices(c.old, c.new)          # within the token
        assert len(d) == 1, f"correction altered >1 char: {c.old} -> {c.new}"
        for o in c.occurrences:
            expected.add(o.offset + d[0])
    actual = set(changed_indices(original, corrected))
    assert actual == expected, (
        f"byte diff != check-digit positions; "
        f"unexpected={sorted(actual - expected)[:8]} missing={sorted(expected - actual)[:8]}")

    # (3) re-parse: nothing left to correct, container count stable
    re_pairs = evaluate_text(corrected, owner_policy=owner_policy)
    re_tally = Counter((r.status.value if r else "empty_id") for _, r in re_pairs)
    assert re_tally.get("corrected", 0) == 0, f"still correctable after fix: {re_tally}"
    assert len(re_pairs) == report.total_containers, "container count changed"
    return actual, re_tally


def main():
    for name, src, policy, dst in JOBS:
        try:
            original = open(src, encoding="utf-8", errors="replace").read()
        except FileNotFoundError:
            print(f"!! not found: {src}\n"); continue

        report = correct_edifact(original, owner_policy=policy)
        actual, re_tally = verify(original, report, policy)

        print("=" * 72)
        print(f"FILE: {name}   [policy={policy}]")
        print(diff_report.render(report, max_items=3, show_segments=True))
        print(f"  VERIFY: bytes_changed={len(actual)}  re-parse={dict(re_tally)}  "
              f"length={len(original)} (unchanged)  -> PASS")

        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(report.corrected_text)
        print(f"  wrote: {dst.split('/')[-1]}")
        print()


if __name__ == "__main__":
    main()
