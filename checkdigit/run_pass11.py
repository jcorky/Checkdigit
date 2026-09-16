#!/usr/bin/env python3
"""
run_pass11.py -- calculator worked-math + CSV export tests.

Also writes calc_vectors.json under the OS temp dir (tempfile.gettempdir()) and
returns its path: kernel-generated truth vectors that the SPA's JavaScript mirror
is then executed against under node (separate step), so the client-side calculator
is cross-validated against the tested Python kernel rather than trusted on inspection.
"""
import json
import os
import tempfile

import db
import dispatcher
import equipment_checkdigit as k


def test_explain_iso():
    r = k.explain("CSQU3054383")                       # ISO 6346 documented example
    assert r["ok"] and r["kind"] == "iso6346_ilu"
    assert r["verdict"] == "valid" and r["computed"] == 3
    assert len(r["chars"]) == 10 and r["chars"][0] == {"char": "C", "value": 13, "weight": 1, "product": 13}
    assert r["chars"][3]["value"] == 32                # U = 32 (skips multiples of 11)
    print("  explain: CSQU3054383 valid, per-char steps correct")

    r = k.explain("csqu 3054 38")                      # 10 chars, messy input
    assert r["verdict"] == "computed" and r["full"] == "CSQU3054383"
    print("  explain: 10-char compute mode + normalization")

    r = k.explain("APLU1000000")                       # kernel-documented remainder-10
    assert r["remainder_ten"] and r["mod"] == 10 and r["computed"] == 0 and r["verdict"] == "valid"
    print("  explain: remainder 10 -> check digit 0")

    r = k.explain("APLU9192812")                       # known SNX fixture mismatch
    assert r["verdict"] == "mismatch" and r["computed"] == 9 and r["full"] == "APLU9192819"
    assert r["computed"] == k.iso6346_check_digit("APLU919281")
    print("  explain: mismatch reports printed 2 vs computed 9")

    assert k.explain("MSCK1234560")["category_set"] == "ilu"        # K = ILU category
    assert k.explain("CONT1234565")["category_set"] == "unknown"    # T = neither set
    assert k.explain("MSKU1234561")["category_set"] == "iso6346"
    print("  explain: category routing iso6346/ilu/unknown")


def test_explain_uic_and_errors():
    r = k.explain("218124712173")                      # kernel docstring example
    assert r["ok"] and r["kind"] == "uic" and r["verdict"] == "valid" and r["computed"] == 3
    assert sum(s["product"] for s in r["steps"]) == r["sum"]
    r2 = k.explain("21 81 2471 217")                   # 11 digits -> compute
    assert r2["verdict"] == "computed" and r2["full"] == "218124712173"
    print("  explain: UIC verify + compute, Luhn steps sum")

    for bad in ("", "MSKU12345", "ABCDE123456", "12345", "MSK U123456789"):
        assert k.explain(bad)["ok"] is False, bad
    print("  explain: malformed shapes return structured errors")


def _find_remainder_ten(prefix="MSKU"):
    for n in range(1000000):
        body = f"{prefix}{n:06d}"
        if sum(k.iso6346_value(c) * (2 ** i) for i, c in enumerate(body)) % 11 == 10:
            return body
    raise AssertionError("no remainder-10 serial found")


def test_csv_export():
    path = os.path.join(tempfile.mkdtemp(), "p11.db")
    conn = db.connect(path, create_schema=True)
    try:
        text = "note CBHU6818017 ok, APLU9192812 bad, MSKU7351770 ok"
        det, report = dispatcher.correct(text, owner_policy="strict", trust=True)
        event_id = db.record_event(
            conn, filename="pass11.txt", content_type="text/plain",
            detected_format=det.fmt, file_size=len(text), user_agent="t",
            owner_policy="strict", status="processed", reason="", report=report)
        out = db.export_event_csv(conn, event_id)
        lines = out.strip().split("\r\n")
        assert lines[0].startswith("container,as_found,status,")
        assert len(lines) == 1 + 3, lines                 # header + 3 containers
        assert any(l.startswith("APLU9192819,APLU9192812,corrected,APL,") for l in lines), lines
        assert any("CBHU6818017" in l and ",valid," in l for l in lines)
        assert db.export_event_csv(conn, 99999) is None   # missing event -> None (-> API 404)
        print("  csv: header + per-container rows, corrected/valid verdicts, 404 path")
    finally:
        conn.close()


def write_js_vectors():
    cases = ["CSQU3054383", "csqu 305438", "APLU1000000", _find_remainder_ten(),
             "APLU9192812", "CBHU6818017", "MSCK1234560", "CONT1234565",
             "TCLU456789", "218124712173", "21812471217"]
    vectors = []
    for c in cases:
        r = k.explain(c)
        v = {"input": c, "ok": r["ok"]}
        if r["ok"]:
            v.update(kind=r["kind"], computed=r["computed"], verdict=r["verdict"],
                     sum=r["sum"], full=r["full"])
            if r["kind"] == "iso6346_ilu":
                v.update(mod=r["mod"], category_set=r["category_set"])
        vectors.append(v)
    path = os.path.join(tempfile.gettempdir(), "calc_vectors.json")
    with open(path, "w") as fh:
        json.dump(vectors, fh, indent=1)
    print(f"  vectors: wrote {len(vectors)} kernel-truth cases to {path}")
    return path


def main():
    print("Pass 11 -- calculator worked math + CSV export")
    test_explain_iso()
    test_explain_uic_and_errors()
    test_csv_export()
    write_js_vectors()
    print("\nPASS: explain() and CSV export verified; JS vectors generated.")


if __name__ == "__main__":
    main()
