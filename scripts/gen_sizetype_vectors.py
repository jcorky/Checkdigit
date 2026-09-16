"""
Emit size/type decode vectors from checkdigit/iso6346_sizetype.py so the
TypeScript port (src/lib/sizetype.ts) is proven against the reference tables,
including the undefined fallback, the deliberately unmapped length code 5 and
the VERIFY notes.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "checkdigit"))

import iso6346_sizetype as st   # noqa: E402

CODES = ["22G1", "45G1", "45R1", "42U1", "L5G1", "22T1", "42P3", "22B0", "45S1", "20G1",
         "4CG1", "2EG1", "22K2", "42H1", "25R1", "22V0", "22W1", "4NG1", "2MG1", "42A1",
         "22X1", "5555", "22G", "", "abcd", "42g1", " 22G1 ", "B2G1", "C2G1", "G2G1",
         "H2G1", "M2G1", "N2G1", "P2G1", "12G1", "32G1", "28G1", "29G1", "26G1", "24G1",
         "2DG1", "2FG1", "2LG1", "2PG1", "22N1", "22U1"]


def main():
    out = [{"code": c, "decoded": st.decode(c).as_dict()} for c in CODES]
    groups = dict(st.GROUP_COLOR)
    path = os.path.join(HERE, "..", "tests", "vectors", "sizetype_vectors.json")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"vectors": out, "group_color": groups}, fh, separators=(",", ":"), ensure_ascii=False)
        fh.write("\n")
    print(f"sizetype vectors: {len(out)} codes -> {os.path.relpath(path)}")


if __name__ == "__main__":
    main()
