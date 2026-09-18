"""
Generates tests/vectors/stream_vectors.json from checkdigit/workspace/stream.py:
records, fields and offsets for a set of fixtures that exercise chunk
boundaries (quoted newlines, escaped quotes, CRLF split, multibyte text, a
UNA header, release characters, XML comments and CDATA). The TypeScript
readers (src/lib/stream.ts) must produce the same structure; offsets are
code-point indices here and the test translates them to UTF-16 units.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "checkdigit"))

from workspace import stream  # noqa: E402

CSV = 'a,b,c\r\n1,"x\r\ny",é\n2,"say ""hi""",\U0001F600\r\n3,,\n"4","",last\r\n\r\n5,北京,"q,r"'
EDI = "UNA:+.? 'UNB+UNOA:2+A+B'EQD+CN+MSKU9070323+22G1'FTX+AAA+++it?'s escaped ?+plus'\nEQD+CN+CSQU3054383'UNZ+1'"
EDI_NOUNA = "UNB+UNOA:2+A+B'EQD+CN+MSKU9070323'UNT+2+1'"
X12 = "ISA*00*          *00*          *ZZ*TERM           *ZZ*RAIL           *260916*1200*U*00401*000000001*0*P*:~\nGS*IO*TERM*RAIL~ST*322*0001~N7*MSCU*123456*******L*******0****22G1~SE*3*0001~IEA*1*000000001~"
XML = ('<?xml version="1.0"?><r><!-- <container eqid="MSKU0000001"/> --><![CDATA[<container eqid="MSKU0000002"/>]]>'
       '<?pi <container eqid="MSKU0000003"/> ?><container eqid="MSKU9070320" note="a > b" x=\'y"z\'/>'
       '<unit id="CSQU3054383" unique-key="CSQU3054383"/><élément eqid="X"/></r>')
LINES = "one\r\ntwo\nthree\r\n\r\nfour é\U0001F600\nlast"

def cp_records(text):
    out = []
    for rec in stream.csv_records(iter([text]), ","):
        out.append({"no": rec.no, "start": rec.start, "end": rec.end,
                    "fields": [{"col": f.col, "value": f.value, "start": f.start, "end": f.end, "quoted": f.quoted} for f in rec.fields]})
    return out

vectors = {
    "csv": {"text": CSV, "delimiter": ",", "records": cp_records(CSV)},
    "edifact": {"text": EDI, "release": "?", "terminator": "'", "skip_una": True,
                "segments": [{"start": s, "raw": r} for s, r in stream.delimited_segments(iter([EDI]), "'", "?", skip_una=True)]},
    "edifact_no_una": {"text": EDI_NOUNA, "release": "?", "terminator": "'", "skip_una": True,
                       "segments": [{"start": s, "raw": r} for s, r in stream.delimited_segments(iter([EDI_NOUNA]), "'", "?", skip_una=True)]},
    "x12": {"text": X12, "release": "", "terminator": "~", "skip_una": False,
            "segments": [{"start": s, "raw": r} for s, r in stream.delimited_segments(iter([X12]), "~", "", skip_una=False)]},
    "xml": {"text": XML, "tags": [{"start": s, "tag": t} for s, t in stream.xml_start_tags(iter([XML]))]},
    "lines": {"text": LINES, "lines": [{"no": n, "start": s, "text": t} for n, s, t in stream.line_records(iter([LINES]))]},
    "offset_kind": "text_codepoint",
}
out = os.path.join(HERE, "..", "tests", "vectors", "stream_vectors.json")
with open(out, "w", encoding="utf-8", newline="\n") as fh:
    json.dump(vectors, fh, ensure_ascii=False, indent=1)
print(out, {k: len(v.get("records", v.get("segments", v.get("tags", v.get("lines", []))))) for k, v in vectors.items() if isinstance(v, dict)})
