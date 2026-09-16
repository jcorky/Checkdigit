"""
run_pass6.py
============
Exercises the X12 locator + corrector on (a) the real x12.org 310 example, whose
dummy "CONT" container has an invalid ISO 6346 category letter and must FLAG, and
(b) a synthetic sample with real prefixes that exercises every N7 path plus
N9*EQ.

Verification:
  * idempotence -- re-running the corrector on the output yields zero further
    corrections (everything auto-correctable is now valid);
  * the only length change is inserted check digits (empty-slot N7-18 fills);
  * equal-length corrections each move exactly one byte;
  * a container number embedded in an N9*BM Bill-of-Lading reference is never
    touched (offset-based substitution, not string replace).
"""
import sys
from collections import Counter

from x12_locator import locate_equipment, evaluate_text
from x12_corrector import correct_x12
from substitution import changed_indices
import diff_report

import os
_HERE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(_HERE, "out"), exist_ok=True)
REAL = os.path.join(_HERE, "samples", "Example_1_X12.edi")
SYNTH_OUT = os.path.join(_HERE, "out", "X12_synthetic.CORRECTED.edi")

# Synthetic 322 exercising: N7 wrong-check (in place), N7 empty N7-18 (insert),
# N7 absent N7-18 (flag, no restructure), N9*EQ wrong-check, N9*BM (must ignore).
SYNTH = (
    "ST*322*0001~\n"
    + "*".join(["N7", "MSCU", "123456"] + [""] * 15 + ["0"] + [""] * 3 + ["22G1"]) + "~\n"
    + "*".join(["N7", "TCLU", "456789"] + [""] * 15 + [""] + [""] * 3 + ["45G1"]) + "~\n"
    + "N7*HLBU*112233~\n"
    + "N9*EQ*MSCU1234560~\n"
    + "N9*BM*SSLMSCU1234560001~\n"
    + "SE*6*0001~\n"
)


def verify(original: str, report, policy: str):
    corrected = report.corrected_text
    re_report = correct_x12(corrected, owner_policy=policy)
    assert re_report.summary()["corrected"] == 0, f"not idempotent: {re_report.summary()}"
    assert len(re_report.flagged) == len(report.flagged), "flag set changed after correction"
    inserts = sum(1 for c in report.corrected for o in c.occurrences
                  if len(o.after) - len(o.before) == 1)
    inplace = sum(1 for c in report.corrected for o in c.occurrences
                  if len(o.after) == len(o.before))
    assert len(corrected) - len(original) == inserts, "unexpected length delta"
    if inserts == 0:
        assert len(changed_indices(original, corrected)) == inplace, "non-localized change"
    return re_report, inserts, inplace


def run_real():
    text = open(REAL, encoding="utf-8").read()
    report = correct_x12(text, owner_policy="strict")

    # the dummy CONT container (category 'T') must flag in both N7 and N9*EQ; 0 corrected
    assert len(report.corrected) == 0, "expected no corrections for the CONT placeholder"
    assert len(report.flagged) == 2, f"expected 2 flags (N7 + N9*EQ), got {len(report.flagged)}"
    # BOL reference and the EQ token left exactly as-is
    assert "N9*BM*SSLACONT1234567001~" in report.corrected_text
    assert "N9*EQ*CONT1234567~" in report.corrected_text
    re_report, ins, inp = verify(text, report, "strict")

    print("=" * 72)
    print("FILE: x12.org 310 example-1  [policy=strict]")
    print(diff_report.render(report, show_segments=True))
    print(f"  VERIFY: idempotent (re-run corrected={re_report.summary()['corrected']}), "
          f"length unchanged, BOL & EQ untouched  -> PASS")
    print()


def run_synth():
    report = correct_x12(SYNTH, owner_policy="strict")

    got = {c.old: c.new for c in report.corrected}
    assert got.get("MSCU1234560") == "MSCU1234566", "N7 in-place correction wrong"      # N7 0->6
    assert got.get("TCLU456789_") == "TCLU4567897", "N7 empty-slot insert wrong"         # insert 7
    assert "MSCU1234560" in got and got["MSCU1234560"] == "MSCU1234566"                   # appears twice (N7 + N9*EQ)
    assert len(report.corrected) == 3 and len(report.flagged) == 1, report.summary()
    # the no-slot HLBU container is flagged, not restructured
    assert "N7*HLBU*112233~" in report.corrected_text
    # N9*EQ corrected, but the N9*BM that CONTAINS the same digits is untouched
    assert "N9*EQ*MSCU1234566~" in report.corrected_text
    assert "N9*EQ*MSCU1234560~" not in report.corrected_text
    assert "N9*BM*SSLMSCU1234560001~" in report.corrected_text, "BOL substring was corrupted!"
    re_report, ins, inp = verify(SYNTH, report, "strict")

    print("=" * 72)
    print("FILE: synthetic X12 (real prefixes)  [policy=strict]")
    print(diff_report.render(report, show_segments=True))
    print(f"  VERIFY: idempotent, inserts={ins} (empty N7-18 filled), in-place={inp}, "
          f"length delta={len(report.corrected_text) - len(SYNTH)}, BOL untouched  -> PASS")
    with open(SYNTH_OUT, "w", encoding="utf-8") as fh:
        fh.write(report.corrected_text)
    print(f"  wrote: {SYNTH_OUT.split('/')[-1]}")
    print()


if __name__ == "__main__":
    run_real()
    run_synth()
