#!/usr/bin/env python3
"""
run_pass14.py -- xlsx in/out tests.

Builds a real minimal OOXML workbook in-memory (stdlib zipfile; no openpyxl --
deliberately, the whole design avoids third-party rewrite risk), then proves:

  shared-string SYNC      one <si> splice corrects every referencing cell
  inline-string splice    member-local offset substitution
  formula-cached          flagged with computed check, member bytes UNTOUCHED
  rich-run split          flagged, never spliced
  verbatim guarantee      every untouched member content-byte-identical
  re-validation           corrected output relocates as fully valid
  low-trust parity        same gates as .txt (flag, corroborate, trust)
  detection & rejection   xlsx vs docx vs generic zip vs corrupt vs PDF
  service e2e             detect -> correct -> audit(fmt=xlsx) -> near-miss
                          on a flagged cell token (cross-feature) -> b64 output
"""
import base64
import io
import os
import tempfile
import zipfile

import db
import dispatcher
import equipment_checkdigit as k
import service
import xlsx_corrector
import xlsx_locator

SST_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'

CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

WB = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook {SST_NS}><sheets><sheet name="Load" sheetId="1" r:id="rId1" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></sheets></workbook>"""

WB_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>"""

# si[0] valid; si[1] BAD check (2->9), referenced by B1 AND B3 (sync test);
# si[2] non-container text (must remain untouched); si[3] rich-run SPLIT token.
SST = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst {SST_NS} count="5" uniqueCount="4">
<si><t>CBHU6818017</t></si>
<si><t>unit APLU9192812 hold</t></si>
<si><t>HELLO WORLD 1234567</t></si>
<si><r><t>MSKU735</t></r><r><t>1773 split</t></r></si>
</sst>"""

# A1/B1/B3 shared refs; C1 inline BAD (MSKU7351793 -> 0, body 1-sub from MSKU7351770);
# D1 formula-cached BAD (APLU7979534 -> 3); E1 number.
SHEET1 = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet {SST_NS}><sheetData>
<row r="1">
<c r="A1" t="s"><v>0</v></c>
<c r="B1" t="s"><v>1</v></c>
<c r="C1" t="inlineStr"><is><t>box MSKU7351793 dam</t></is></c>
<c r="D1" t="str"><f>CONCATENATE(F1,G1)</f><v>APLU7979534</v></c>
<c r="E1"><v>42</v></c>
</row>
<row r="3"><c r="B3" t="s"><v>1</v></c><c r="C3" t="s"><v>3</v></c><c r="D3" t="s"><v>2</v></c></row>
</sheetData></worksheet>"""

STYLES = """<?xml version="1.0"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="1"><font/></fonts></styleSheet>"""


def build_xlsx(sst=SST, sheet=SHEET1) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CT)
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("xl/workbook.xml", WB)
        z.writestr("xl/_rels/workbook.xml.rels", WB_RELS)
        z.writestr("xl/styles.xml", STYLES)           # untouched-member witness
        z.writestr("xl/sharedStrings.xml", sst)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def members_of(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.namelist(), {n: z.read(n) for n in z.namelist()}


def test_detection():
    assert dispatcher.detect_bytes(build_xlsx()).fmt == "xlsx"
    docx = io.BytesIO()
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml", "<d/>")
    d = dispatcher.detect_bytes(docx.getvalue())
    assert d.fmt == "unsupported" and "Word" in d.reason
    plain = io.BytesIO()
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("a.txt", "x")
    assert "not an Excel" in dispatcher.detect_bytes(plain.getvalue()).reason
    assert "corrupt" in dispatcher.detect_bytes(b"PK\x03\x04nope").reason
    assert "PDF" in dispatcher.detect_bytes(b"%PDF-1.7 etc").reason
    assert dispatcher.detect_bytes(b"UNB+UNOA:2+X") is None      # text falls through
    print("  detect: xlsx / docx / generic zip / corrupt / pdf / text routing")


def test_correct_trusted():
    src = build_xlsx()
    rep = xlsx_corrector.correct_workbook(src, trust=True, owner_policy="strict")

    ch = {c.old: c for c in rep.corrected}
    assert set(ch) == {"APLU9192812", "MSKU7351793"}, set(ch)
    sync = ch["APLU9192812"]
    assert sync.new == "APLU9192819" and sync.computed_check == "9"
    assert len(sync.occurrences) == 1                  # ONE physical splice
    assert "B1" in sync.occurrences[0].label and "B3" in sync.occurrences[0].label
    inline = ch["MSKU7351793"]
    assert inline.new == "MSKU735179" + str(k.iso6346_check_digit("MSKU735179"))
    assert "C1 (inline)" in inline.occurrences[0].label
    assert "APLU9192819 hold" in sync.occurrences[0].after   # snippet shows context

    fl = {f.eqid: f for f in rep.flagged}
    assert set(fl) == {"APLU7979534", "MSKU7351773"}, set(fl)
    assert "formula-cached" in fl["APLU7979534"].reason
    assert fl["APLU7979534"].suggested_check == "3"
    assert "rich-text runs" in fl["MSKU7351773"].reason
    assert rep.valid == 1 and rep.total_containers == 5
    assert rep.corrected_text is None and rep.corrected_b64

    out = base64.b64decode(rep.corrected_b64)
    names0, m0 = members_of(src)
    names1, m1 = members_of(out)
    assert names0 == names1                            # order preserved
    with zipfile.ZipFile(io.BytesIO(out)) as z:
        assert z.testzip() is None                     # structurally sound ZIP
    for n in names0:                                   # verbatim guarantee
        if n in ("xl/sharedStrings.xml", "xl/worksheets/sheet1.xml"):
            continue
        assert m0[n] == m1[n], f"member {n} should be byte-identical"
    # Edited members differ ONLY by the spliced tokens:
    assert m1["xl/sharedStrings.xml"].decode() == m0["xl/sharedStrings.xml"].decode().replace(
        "APLU9192812", "APLU9192819")
    assert m1["xl/worksheets/sheet1.xml"].decode() == m0["xl/worksheets/sheet1.xml"].decode().replace(
        "MSKU7351793", inline.new)
    assert b"APLU7979534" in m1["xl/worksheets/sheet1.xml"]    # cached value untouched
    print("  trusted: sync + inline corrected, cached/split flagged, verbatim members")

    # Re-validate the corrected workbook: previously-corrected now valid.
    rep2 = xlsx_corrector.correct_workbook(out, trust=True)
    assert not rep2.corrected and rep2.valid == 3      # CBHU + the two fixes
    assert {f.eqid for f in rep2.flagged} == {"APLU7979534", "MSKU7351773"}
    print("  re-validate: corrected output is clean; locate-only flags persist")


def test_low_trust_parity():
    rep = xlsx_corrector.correct_workbook(build_xlsx(), trust=False)
    assert not rep.corrected                           # FREE_TEXT gates hold
    assert {f.eqid for f in rep.flagged} >= {"APLU9192812", "MSKU7351793"}
    out = base64.b64decode(rep.corrected_b64)
    _, m0 = members_of(build_xlsx())
    _, m1 = members_of(out)
    assert all(m0[n] == m1[n] for n in m0)             # zero edits -> all verbatim
    # corroboration: APL registered -> corrected even at low trust
    rep2 = xlsx_corrector.correct_workbook(build_xlsx(), trust=False,
                                           known_owner_prefixes={"APL"})
    assert {c.old for c in rep2.corrected} == {"APLU9192812"}
    print("  low-trust: flags only; owner corroboration corrects APL; bytes verbatim")


def test_service_e2e():
    path = os.path.join(tempfile.mkdtemp(), "p14.db")
    conn = db.connect(path, create_schema=True)
    # Seed the registry so the flagged inline token gets a near-miss candidate.
    service.process_upload(conn, b"seed MSKU7351770 ok", filename="seed.txt",
                           content_type="text/plain", user_agent="p14",
                           owner_policy="strict", trust=False)
    res = service.process_upload(conn, build_xlsx(), filename="loadlist.xlsx",
                                 content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 user_agent="p14", owner_policy="strict", trust=False)
    assert res["status"] == "processed" and res["detected_format"] == "xlsx"
    ev = conn.execute("SELECT detected_format, status, flagged FROM ingestion_events "
                      "ORDER BY id DESC").fetchone()
    assert ev["detected_format"] == "xlsx" and ev["status"] == "processed"
    by = {c.as_found: c for c in res["report"].containers}
    nm = by["MSKU7351793"].near_misses                 # cross-feature: suggester fires
    assert nm and nm[0]["eqid"] == "MSKU7351770" and nm[0]["distance"] == 1, nm
    d = res["report"].to_dict()
    assert d["corrected_text"] is None and base64.b64decode(d["corrected_b64"])[:4] == b"PK\x03\x04"
    # docx rejection through the full service path
    docx = io.BytesIO()
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml", "<d/>")
    rj = service.process_upload(conn, docx.getvalue(), filename="x.docx",
                                content_type="", user_agent="p14",
                                owner_policy="strict", trust=False)
    assert rj["status"] == "rejected" and "Word" in rj["reason"]
    conn.close()
    print("  service: detect->audit(xlsx)->near-miss on cell token->b64; docx rejected")


def test_regex_parity():
    import txt_locator
    assert xlsx_locator._RE_TOKEN.pattern == txt_locator._RE_BIC.pattern
    print("  parity: xlsx uses the same token regex object as .txt scanning")


def main():
    print("Pass 14 -- xlsx in/out")
    test_detection()
    test_correct_trusted()
    test_low_trust_parity()
    test_service_e2e()
    test_regex_parity()
    print("\nPASS: xlsx located, spliced, rebuilt, gated, audited -- verified end-to-end.")


if __name__ == "__main__":
    main()
