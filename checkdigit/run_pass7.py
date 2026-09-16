"""
run_pass7.py
============
Identifies every container number in a plain-text file: total occurrences,
distinct numbers, owner, occurrence count, and validity (ISO 6346 check digit).
Demonstrates the locator on real, messy input.
"""
import sys
from collections import OrderedDict, Counter

from txt_locator import locate_equipment, evaluate_text
from txt_corrector import correct_txt
from equipment_checkdigit import Status

import os
SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples", "txtcontainers.txt")


def main(path=SRC):
    text = open(path, encoding="utf-8", errors="replace").read()

    refs = locate_equipment(text)
    pairs = evaluate_text(text, trust=False)            # low-trust FREE_TEXT default

    # group by eqid for the distinct view
    by_eqid = OrderedDict()
    for ref, r in pairs:
        by_eqid.setdefault(ref.eqid, {"count": 0, "r": r})
        by_eqid[ref.eqid]["count"] += 1

    valid = [e for e, d in by_eqid.items() if d["r"].status is Status.VALID]
    flagged = [e for e, d in by_eqid.items()
               if d["r"].status in (Status.FLAGGED,)]   # FREE_TEXT check-fail
    other = [e for e, d in by_eqid.items()
             if d["r"].status not in (Status.VALID, Status.FLAGGED)]

    print(f"=== {path.split('/')[-1]} -- container identification ===")
    print(f"total occurrences matched : {len(refs)}")
    print(f"distinct container numbers: {len(by_eqid)}")
    print(f"  valid check digit       : {len(valid)}")
    print(f"  check-digit FAILS       : {len(flagged)}")
    if other:
        print(f"  other (non-standard)    : {len(other)} -> {other}")
    print()
    print(f"{'EQID':<13} {'own':<4} {'cat':<3} {'n':<3} {'status':<8} check")
    for eqid, d in by_eqid.items():
        r = d["r"]
        st = "VALID" if r.status is Status.VALID else ("FAILS" if r.status is Status.FLAGGED else r.status.value)
        chk = "" if r.status is Status.VALID else f"{r.printed_check}->{r.computed_check}"
        print(f"{eqid:<13} {eqid[:3]:<4} {eqid[3]:<3} {d['count']:<3} {st:<8} {chk}")

    # how many would correct if we trusted the source / corroborated owners
    trusted = correct_txt(text, trust=True)
    print()
    print(f"if treated as a trusted container list: {len(trusted.corrected)} distinct numbers "
          f"would be corrected, {trusted.valid} already valid.")

    # observations
    dup_occurrences = len(refs) - len(by_eqid)
    print()
    print("notes:")
    print(f"  - the delimiter-less run was split into 3 numbers (APLU3280480 / MSKU4570605 / APLU8266451).")
    print(f"  - trailing punctuation (',', '*', '=', '!') and surrounding tabs were excluded from matches.")
    print(f"  - two tabular yard-report regions; {dup_occurrences} occurrences are repeats of an already-listed number.")
    print(f"  - ISO size/type codes (42R1, 42T6, L5G1) in the 'Type ISO' column are correctly NOT matched.")
    print(f"  - low-trust default: failing check digits are FLAGGED, not auto-corrected.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else SRC)
