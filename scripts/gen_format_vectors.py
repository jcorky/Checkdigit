"""
Emit format-level parity vectors from the Python correctors for the TypeScript
ports: for each fixture (a small in-memory file plus routing options), the
detection result, the report summary, every change and flag, the inventory,
and the corrected text. The TypeScript port must reproduce each record.

Fixtures deliberately cover positive, negative, malformed and no-edit cases
per format, plus quoting, CRLF, release characters, custom separators and
namespace prefixes.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "checkdigit"))

import dispatcher                       # noqa: E402
from policy import Policy               # noqa: E402

GOOD = "CSQU3054383"
BAD = "CSQU3054384"        # printed 4, expected 3
BAD2 = "APLU9192812"       # printed 2, expected 9
VALID2 = "CBHU6818017"

FIXTURES = [
    # ---- plain text -------------------------------------------------------
    {"name": "txt_free_text_flags", "text": f"note {BAD} and {GOOD} and {BAD2} end\n", "trust": False},
    {"name": "txt_trusted_corrects", "text": f"note {BAD} and {GOOD} and {BAD2} end\n", "trust": True},
    {"name": "txt_concatenated_and_repeated", "text": f"{BAD}{VALID2}{BAD} tail {BAD}\n", "trust": True},
    {"name": "txt_lowercase_invisible", "text": "msku1234567 MSKU 123456 7\n", "trust": True},
    {"name": "txt_no_edit_all_valid", "text": f"{GOOD}\n{VALID2}\n", "trust": True},
    {"name": "txt_empty", "text": "", "trust": True},
    {"name": "txt_multibyte_prefix", "text": f"Ünïcödé ✓ 🚢 {BAD} end\n", "trust": True},
    # ---- EDIFACT ----------------------------------------------------------
    {"name": "edifact_default_separators",
     "text": f"UNB+UNOA:2+A+B+260101:1200+1'UNH+1+COARRI:D:95B:UN'EQD+CN+{BAD}+22G1+++5'EQD+CN+{GOOD}+45G1'EQA+CN+{BAD2}'UNT+4+1'UNZ+1+1'"},
    {"name": "edifact_una_custom_and_release",
     "text": f"UNA|~.^ #UNB|UNOA~2|A|B|260101~1200|1#UNH|1|COARRI~D~95B~UN#FTX|AAA|||text with ^# escaped#EQD|CN|{BAD}|22G1#EQD|TE|{BAD2}#EQD|CN||22G1#UNT|5|1#UNZ|1|1#"},
    {"name": "edifact_newlines_between_segments",
     "text": f"UNB+UNOA:2+A+B+260101:1200+1'\r\nUNH+1+BAPLIE:D:95B:UN:SMDG20'\r\nEQD+CN+{BAD}+22G1+++5'\r\nEQD+CN+{VALID2}+45R1+++4'\r\nUNT+4+1'\r\nUNZ+1+1'\r\n"},
    {"name": "edifact_pseudo_prefix_lenient",
     "text": "UNB+UNOA:2+A+B+260101:1200+1'EQD+CN+L01U1150107+22G1'EQD+CN+L01U1150105+22G1'UNZ+1+1'", "owner_policy": "lenient"},
    {"name": "edifact_pseudo_prefix_strict",
     "text": "UNB+UNOA:2+A+B+260101:1200+1'EQD+CN+L01U1150107+22G1'UNZ+1+1'"},
    {"name": "edifact_no_containers", "text": "UNB+UNOA:2+A+B+260101:1200+1'UNH+1+COARRI:D:95B:UN'UNT+2+1'UNZ+1+1'"},
    # ---- X12 --------------------------------------------------------------
    {"name": "x12_synthetic_322",
     "text": ("ST*322*0001~\nN7*MSCU*123456****************0****22G1~\nN7*TCLU*456789********************45G1~\n"
              "N7*HLBU*112233~\nN9*EQ*MSCU1234560~\nN9*BM*SSLMSCU1234560001~\nSE*6*0001~\n")},
    {"name": "x12_isa_declared_delimiters",
     "text": ("ISA*00*          *00*          *ZZ*SENDER         *ZZ*RECEIVER       *260101*1200*U*00401*000000001*0*P*>\n"
              "GS*SO*SENDER*RECEIVER*20260101*1200*1*X*004010~ST*322*0001~N7*MSCU*123456****************0~SE*3*0001~GE*1*1~IEA*1*000000001~")},
    {"name": "x12_malformed_isa_padding",
     "text": "ISA*00*short*00*x~ST*322*0001~N7*CSQU*305438****************4~N9*EQ*CSQU3054383~SE*4*0001~"},
    {"name": "x12_bad_initial_and_long_serial",
     "text": "ST*322*0001~N7*EMH*123456****************1~N7*EMHU*1234567****************1~N7*EMHX*123456****************1~SE*4*0001~"},
    {"name": "x12_no_edit", "text": "ST*322*0001~N7*CSQU*305438****************3~N9*EQ*CSQU3054383~SE*4*0001~"},
    # ---- SNX / container XML -------------------------------------------------
    {"name": "snx_synced_attributes",
     "text": (f'<?xml version="1.0"?>\n<snx><container eqid="{BAD}" type="22G1"><unit id="{BAD}" unique-key="{BAD}"/>'
              f'<equipment eqid="{BAD}"/></container><container eqid="{GOOD}" type="45G1"/></snx>')},
    {"name": "snx_namespace_prefix",
     "text": (f'<n4:snx xmlns:n4="http://example.test/snx"><n4:container eqid="{BAD2}" type="22G1"/>'
              f'<n4:unit id="{BAD2}" unique-key="{BAD2}"/></n4:snx>')},
    {"name": "snx_line_discharge_list",
     "text": f'<discharge><line-discharge-list unit-id="{BAD}"/><line-discharge-list unit-id="{VALID2}"/></discharge>'},
    {"name": "snx_no_edit", "text": f'<snx><container eqid="{GOOD}" type="22G1"/></snx>'},
    {"name": "snx_carrier_id_untouched",
     "text": f'<snx><carrier id="{BAD}"/><container eqid="{BAD}"/></snx>'},
    {"name": "snx_doctype_refused", "text": f'<!DOCTYPE x [<!ENTITY e "x">]><snx><container eqid="{BAD}"/></snx>'},
    {"name": "snx_malformed", "text": f'<snx><container eqid="{BAD}"></snx>'},
    {"name": "xml_unrecognized", "text": "<root><item>x</item></root>"},
    # ---- CSV (hinted) ------------------------------------------------------
    {"name": "csv_header_names_quotes_crlf",
     "text": f'ref,container,remark\r\n1,"{BAD}","says {BAD2} in remark"\r\n2,{GOOD},"multi\r\nline"\r\n3,"{VALID2}",x\r\n',
     "format_hint": "csv", "parse_options": {"columns": ["container"]}, "trust": True},
    {"name": "csv_review_only", "text": f"container\n{BAD}\n", "format_hint": "csv",
     "parse_options": {"columns": ["container"]}, "trust": False},
    {"name": "csv_index_semicolon_no_header",
     "text": f"1;{BAD};x\n2;{BAD2};y\n", "format_hint": "csv",
     "parse_options": {"columns": [1], "has_header": False}, "trust": True},
    {"name": "csv_missing_column", "text": "a,b\n1,2\n", "format_hint": "csv",
     "parse_options": {"columns": ["container"]}, "trust": True},
    {"name": "csv_no_columns", "text": "a,b\n1,2\n", "format_hint": "csv", "parse_options": {}, "trust": True},
    {"name": "csv_embedded_scan",
     "text": f"remark\nsee {BAD} here\n", "format_hint": "csv",
     "parse_options": {"columns": ["remark"], "whole_cell": False}, "trust": True},
    # ---- fixed width (hinted) ---------------------------------------------
    {"name": "fixed_ranges",
     "text": f"HDR line\nA1 {BAD} OK\r\nA2 {GOOD} OK\nA3 {BAD2}\n", "format_hint": "fixed",
     "parse_options": {"ranges": [[4, 14]], "header_lines": 1}, "trust": True},
    {"name": "fixed_bad_range", "text": "x\n", "format_hint": "fixed",
     "parse_options": {"ranges": [[9, 3]]}, "trust": True},
    {"name": "fixed_short_lines", "text": f"{BAD}\nAB\n", "format_hint": "fixed",
     "parse_options": {"ranges": [[1, 11]]}, "trust": True},
    # ---- operator policy ---------------------------------------------------
    {"name": "policy_deny_and_allow",
     "text": f"UNB+UNOA:2+A+B+260101:1200+1'EQD+CN+{BAD}'EQD+CN+{BAD2}'UNZ+1+1'",
     "policy": {"deny": ["CSQ"], "allow": ["APL"]}},
]


def run(fx):
    text = fx["text"]
    policy = Policy.from_dict(fx["policy"]) if fx.get("policy") else None
    kwargs = dict(owner_policy=fx.get("owner_policy", "strict"), trust=fx.get("trust", False), policy=policy)
    try:
        if fx.get("format_hint"):
            det, rep = dispatcher.correct_with_hint(text, format_hint=fx["format_hint"],
                                                    parse_options=fx.get("parse_options"), **kwargs)
        else:
            det, rep = dispatcher.correct(text, **kwargs)
    except Exception as exc:                       # noqa: BLE001 -- recorded, not hidden
        return {"error": type(exc).__name__, "message": str(exc)}
    out = {"detected": {"fmt": det.fmt, "reason": det.reason}}
    if rep is None:
        out["report"] = None
        return out
    d = rep.to_dict()
    d.pop("offset_kind", None)
    for c in d["containers"]:
        c.pop("near_misses", None)
    out["report"] = d
    out["summary"] = rep.summary()
    return out


def main():
    vectors = []
    for fx in FIXTURES:
        rec = {k: v for k, v in fx.items()}
        rec["expected"] = run(fx)
        vectors.append(rec)
    path = os.path.join(HERE, "..", "tests", "vectors", "format_vectors.json")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(vectors, fh, separators=(",", ":"), ensure_ascii=False)
        fh.write("\n")
    print(f"format vectors: {len(vectors)} fixtures -> {os.path.relpath(path)}")


if __name__ == "__main__":
    main()
