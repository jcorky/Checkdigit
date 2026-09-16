#!/usr/bin/env python3
"""
run_pass17.py -- CSV/TSV and fixed-width parsers with column-scoped trust.

Proves the opt-in structured-text formats:

  column trust      a token in the DECLARED column is corrected; an identical
                    token in another column is left untouched (the whole point)
  byte fidelity     output differs ONLY at corrected check digits; quoting,
                    delimiters, CRLF/LF, header, other columns verbatim
  rfc4180           quoted fields, embedded delimiter, embedded newline, ""
  header names      columns selectable by header name, not just index
  tsv + sniff       tab delimiter auto-sniffed; explicit override honored
  fixed-width       column-range targeting; padding + line endings preserved
  range trust       token outside the declared range is ignored
  low-trust opt     trust=False keeps even declared-column tokens advisory
  no-hint safety    a .csv with no hint flows through safe free-text path
  service           process_upload(format_hint=...) routes + audits as csv/fixed
"""
import os
import tempfile

import db
import dispatcher
import equipment_checkdigit as k
import service
from csv_corrector import correct_csv
from fixedwidth_corrector import correct_fixedwidth
from csv_locator import parse_records, sniff_delimiter

BAD = "MSKU7351773"          # check should be 0
GOOD = "MSKU7351770"
BAD2 = "CBHU6818013"         # check should be 7
GOOD2 = "CBHU6818017"


def test_csv_column_trust():
    # Same bad token in col 2 (declared) and col 3 (remarks). Only col 2 fixed.
    text = ("ref,container,remarks\r\n"
            f"A1,{BAD},seen also {BAD}\r\n"
            f"A2,{GOOD2.replace(GOOD2[-1], str(int(GOOD2[-1])^1))[:0] or BAD2},ok\r\n")
    rep = correct_csv(text, ["container"], trust=True, owner_policy="strict")
    fixed = {c.old: c.new for c in rep.corrected}
    assert fixed == {BAD: GOOD, BAD2: GOOD2}, fixed
    out = rep.corrected_text
    # col-2 occurrences corrected; the col-3 "seen also MSKU7351773" preserved
    assert f"A1,{GOOD},seen also {BAD}" in out, out
    assert out.count("\r\n") == text.count("\r\n")           # CRLF preserved
    # exact-diff: only the two declared-column check digits changed
    assert out == text.replace(f"A1,{BAD},", f"A1,{GOOD},").replace(
        f"A2,{BAD2},", f"A2,{GOOD2},")
    print("  csv: declared column corrected; same token in remarks untouched; CRLF kept")


def test_csv_rfc4180():
    # Quoted field with embedded comma + newline + escaped quote, container after.
    text = ('id,note,box\n'
            f'1,"a, b\nc ""q""",{BAD}\n')
    rows = parse_records(text, ",")
    assert rows[1][1].value == 'a, b\nc "q"'                  # parsed through quoting
    rep = correct_csv(text, ["box"], trust=True)
    assert rep.corrected_text == text.replace(BAD, GOOD)
    assert '"a, b\nc ""q"""' in rep.corrected_text            # quoted field intact
    print("  csv: RFC4180 quoting (embedded comma/newline/escaped-quote) survives splice")


def test_tsv_and_index():
    text = f"ref\tbox\nX\t{BAD}\n"
    assert sniff_delimiter(text) == "\t"
    rep = correct_csv(text, [1], has_header=True, trust=True)   # column by index
    assert rep.corrected_text == text.replace(BAD, GOOD)
    # explicit delimiter override
    rep2 = correct_csv(text, [1], delimiter="\t", has_header=True, trust=True)
    assert rep2.corrected_text == rep.corrected_text
    print("  tsv: tab auto-sniffed; column-by-index; explicit delimiter honored")


def test_csv_low_trust():
    text = f"ref,box\nX,{BAD}\n"
    rep = correct_csv(text, ["box"], trust=False, owner_policy="strict")
    assert not rep.corrected and len(rep.flagged) == 1         # advisory only
    assert rep.corrected_text == text                          # nothing spliced
    # corroboration still corrects at low trust
    rep2 = correct_csv(text, ["box"], trust=False, known_owner_prefixes={"MSK"})
    assert rep2.corrected_text == text.replace(BAD, GOOD)
    print("  csv: trust=False advisory; owner corroboration still corrects")


def test_fixedwidth():
    # Layout: ref(1-3) container(5-15) status(17-19); pad with spaces.
    def row(ref, box, st):
        return f"{ref:<3} {box:<11} {st:<3}"
    text = row("A1", BAD, "OK") + "\n" + row("B2", GOOD, "NG") + "\r\n"
    rep = correct_fixedwidth(text, [(5, 15)], trust=True, owner_policy="strict")
    assert {c.old: c.new for c in rep.corrected} == {BAD: GOOD}
    out = rep.corrected_text
    assert out == text.replace(BAD, GOOD)                      # only the check digit
    assert out.endswith("\r\n") and "\n" in out               # mixed EOL preserved
    print("  fixed: column-range corrected; padding + mixed line endings verbatim")


def test_fixedwidth_range_trust():
    # A coincidental token OUTSIDE the declared range must be ignored.
    text = f"{BAD2}  {BAD}\n"          # cols 1-11 = BAD2, declared range is 14-24 (BAD)
    rep = correct_fixedwidth(text, [(14, 24)], trust=True)
    assert {c.old for c in rep.corrected} == {BAD}             # only the in-range one
    assert BAD2 in rep.corrected_text                          # out-of-range untouched
    print("  fixed: token outside declared range ignored (range trust holds)")


def test_no_hint_safety():
    # A CSV with NO hint must flow through the safe free-text path (flag, not fix).
    text = f"ref,box\nX,{BAD}\n"
    det, rep = dispatcher.correct(text, owner_policy="strict", trust=False)
    assert det.fmt == "txt" and not rep.corrected and rep.flagged
    print("  safety: unhinted CSV uses low-trust free-text path (no silent column trust)")


def test_service_routing():
    path = os.path.join(tempfile.mkdtemp(), "p17.db")
    conn = db.connect(path, create_schema=True)
    csv_bytes = f"ref,container\nA1,{BAD}\n".encode()
    res = service.process_upload(
        conn, csv_bytes, filename="loadlist.csv", content_type="text/csv",
        user_agent="p17", owner_policy="strict", trust=True,
        format_hint="csv", parse_options={"columns": ["container"]})
    assert res["status"] == "processed" and res["detected_format"] == "csv"
    assert res["report"].corrected and res["corrected_text"].endswith(f"A1,{GOOD}\n")
    ev = conn.execute("SELECT detected_format FROM ingestion_events ORDER BY id DESC").fetchone()
    assert ev["detected_format"] == "csv"

    fw = f"A1 {BAD} OK\n".encode()
    res2 = service.process_upload(
        conn, fw, filename="export.txt", content_type="", user_agent="p17",
        owner_policy="strict", trust=True, format_hint="fixed",
        parse_options={"ranges": [[4, 14]]})
    assert res2["detected_format"] == "fixed" and res2["report"].corrected
    # review-only (trust=False) must leave a hinted file unchanged
    ro = service.process_upload(
        conn, csv_bytes, filename="loadlist.csv", content_type="text/csv",
        user_agent="p17", owner_policy="strict", trust=False,
        format_hint="csv", parse_options={"columns": ["container"]})
    assert not ro["report"].corrected and ro["corrected_text"] == csv_bytes.decode()

    # missing spec -> loud rejection, audited
    bad = service.process_upload(
        conn, csv_bytes, filename="x.csv", content_type="", user_agent="p17",
        format_hint="csv", parse_options={})
    assert bad["status"] == "rejected" and "columns" in bad["reason"]
    conn.close()
    print("  service: csv + fixed hints route and audit; missing spec rejected loudly")


def main():
    print("Pass 17 -- CSV/TSV + fixed-width (column-scoped trust)")
    test_csv_column_trust()
    test_csv_rfc4180()
    test_tsv_and_index()
    test_csv_low_trust()
    test_fixedwidth()
    test_fixedwidth_range_trust()
    test_no_hint_safety()
    test_service_routing()
    print("\nPASS: structured-text parsers verified -- column trust, byte fidelity, safe defaults.")


if __name__ == "__main__":
    main()
