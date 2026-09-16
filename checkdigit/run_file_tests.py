"""
run_file_tests.py
=================
Exercises the EDIFACT locator + correction kernel against the uploaded terminal
files and prints a per-file report. Reusable: pass file paths as arguments, or
run with no arguments to use the defaults below.

  python3 run_file_tests.py [file1 file2 ...]
"""
import sys
from collections import Counter
from equipment_checkdigit import Status
from edifact_locator import iter_segments, locate_equipment, evaluate_text

import os
_SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")
DEFAULTS = [
    ("COPRAR discharge (XML)", os.path.join(_SAMPLES, "COPRAR_Discharge.xml")),
    ("BAPLIE (Bigship)",       os.path.join(_SAMPLES, "Bigship_BAPLIE.edi")),
    ("COPARN booking",         os.path.join(_SAMPLES, "Booking_no1.txt")),
    ("COPARN booking (amend)", os.path.join(_SAMPLES, "Booking_no2_amend.txt")),
    ("COPRAR loadlist (L02)",  os.path.join(_SAMPLES, "L02LOADLIST.txt")),
]


def first_unh(text: str) -> str:
    for seg in iter_segments(text):
        if seg.tag == "UNH" and len(seg.elements) > 2:
            return ":".join(seg.elements[2])
    return "?"


def service_values(text: str):
    """Collect CNT and UNT element values across the file (may be several)."""
    cnt, unt = [], []
    for seg in iter_segments(text):
        if seg.tag == "CNT":
            cnt.append("+".join(":".join(c) for c in seg.elements[1:]))
        elif seg.tag == "UNT":
            unt.append(seg.elements[1][0] if len(seg.elements) > 1 and seg.elements[1] else "")
    return cnt, unt


def sample_rows(results, limit=None):
    rows = []
    for ref, r in results:
        if r is None:
            rows.append((ref.eqid or "(empty)", "-", "empty_id", "", ""))
            continue
        change = f"{r.printed_check}->{r.computed_check}" if r.printed_check is not None else f"(+{r.computed_check})"
        rows.append((ref.eqid, r.id_type.value, r.status.value, change, r.corrected or ""))
        if limit and len(rows) >= limit:
            break
    return rows


def print_table(rows):
    for eqid, idt, status, change, corrected in rows:
        print(f"     {eqid:<14} {idt:<8} {status:<17} {change:<8} {corrected}")


def run(name: str, path: str, policies):
    if not os.path.exists(path):
        print(f"\n=== {name} ===\n  SKIP: sample not present ({os.path.basename(path)})")
        return
    text = open(path, encoding="utf-8", errors="replace").read()
    import dispatcher
    fmt = dispatcher.detect_format(text).fmt
    if fmt != "edifact":          # e.g. COPRAR discharge in XML -> SNX path
        _, rep = dispatcher.correct(text, owner_policy="lenient", trust=False)
        s = rep.summary()
        print(f"\n=== {name} ===")
        print(f"  format={fmt}  containers={s['containers']}  corrected={s['corrected']}  "
              f"flagged={s['flagged']}  valid={s['valid']}")
        return
    refs = locate_equipment(text)
    by_qual = Counter(r.qualifier or "(none)" for r in refs)
    cn = [r for r in refs if r.qualifier == "CN"]
    empty = [r for r in cn if not r.eqid]
    cnt, unt = service_values(text)

    print("=" * 72)
    print(f"FILE: {name}   [{path.split('/')[-1]}]")
    print(f"  first UNH02 : {first_unh(text)}")
    print(f"  CNT seg»   : {cnt}")
    print(f"  UNT count   : {unt}")
    print(f"  EQD total   : {len(refs)}  (by qualifier: {dict(by_qual)})  <- EQA excluded by design")
    print(f"  EQD+CN      : {len(cn)}   (empty 8260: {len(empty)})")

    for pol in policies:
        results = evaluate_text(text, owner_policy=pol)
        tally = Counter((r.status.value if r else "empty_id") for _, r in results)
        print(f"  -- {pol} owner policy --   {dict(tally)}")
        nonempty = [(ref, r) for ref, r in results if r is not None]
        if not nonempty:
            print(f"     (all {len(results)} EQD+CN carry an empty 8260 -- nothing to correct)")
        elif len(nonempty) <= 12:
            print_table(sample_rows(nonempty))
        else:
            print_table(sample_rows(nonempty, limit=4))
            print(f"     ... ({len(nonempty) - 4} more)")
    print()


if __name__ == "__main__":
    args = sys.argv[1:]
    policy = "strict"                          # engine default
    if "--policy" in args:
        i = args.index("--policy")
        policy = args[i + 1]
        del args[i:i + 2]
    policies = ["strict", "lenient"] if policy == "both" else [policy]
    targets = [("file", a) for a in args] if args else DEFAULTS
    for name, path in targets:
        try:
            run(name, path, policies)
        except FileNotFoundError:
            print(f"!! not found: {path}\n")
