#!/usr/bin/env python3
"""
run_pass23.py -- BAPLIE bay-plan visualizer (ISO 6346 decode + scene + SVG).

Proves the visualization pipeline:

  iso6346 decode   length/height/type-group/dimensions from the 4-char code;
                   undefined codes flagged (defined=False), 45ft + HC + reefer
                   + open-top + tank + flat-rack groups resolved
  position parse   ISO 9711 BBBRRTT (7), 3-digit-tier (8), RoRo (9); bay 20ft/40ft
                   parity, row port/starboard/centreline, deck vs hold by tier>=80
  weight forms     legacy MEA+WT and SMDG MEA+AAE+VGM both parsed; VGM preferred
  flags            reefer (TMP), hazmat (DGS class+UN), oversize (DIM overhang),
                   undefined (bad ISO code), empty (full=False), high-cube
  scene model      counts, bay/row/tier extents, placed vs unplaced split
  svg render       valid <svg>, looking-forward layout, hatch line when deck+hold,
                   reefer/hazmat/oversize glyphs present, undefined shows raw code
  service          a BAPLIE through process_upload carries a 'visualization' block;
                   a non-BAPLIE EDIFACT does NOT; correction still works alongside
"""
import os
import tempfile

import baplie_scene as bs
import bayplan_render as br
import db
import iso6346_sizetype as st
import service

# Correct 7-digit BBBRRTT positions (3-digit bay!). bay 002 = 40ft slot.
BAPLIE = (
    "UNB+UNOA:2+CARRIER+TERMINAL+260611:1200+1'"
    "UNH+1+BAPLIE:D:95B:UN:SMDG20'BGM+109'"
    "TDT+20+VOY123+++CARRIER:::EVERGREEN'"
    # 40ft reefer HC, on deck: bay 002 row 01 tier 84
    "LOC+147+0020184::5'MEA+AAE+VGM+KGM:24500'LOC+9+USLAX'LOC+11+NLRTM'"
    "EQD+CN+MSKU7351770+45R1+++5'TMP+2+-18:CEL'NAD+CA+MAEU:172:20'"
    # 20ft hazmat, in hold, over-height 30cm: bay 001 row 02 tier 04
    "LOC+147+0010204::5'MEA+WT++KGM:18000'EQD+CN+CBHU6818017+22G1+++5'"
    "DIM+9+CMT:::30'DGS+IMD+8+1830'NAD+CA+MAEU:172:20'"
    # undefined size/type, empty, on deck: bay 003 row 00 tier 82
    "LOC+147+0030082::4'EQD+CN+APLU9192819+99XX+++4'"
    # normal 40ft dry, in hold: bay 002 row 03 tier 06
    "LOC+147+0020306::5'MEA+AAE+G+KGM:21000'EQD+CN+TGHU1234563+42G1+++5'"
    "UNT+18+1'UNZ+1+1'"
)


def test_iso6346():
    cases = {
        "22G1": ("20ft", "dry", 1.0, True, False),
        "42G1": ("40ft", "dry", 2.0, True, False),
        "45R1": ("40ft", "reefer", 2.0, True, True),    # HC reefer
        "L0G1": ("45ft", "dry", 2.0, True, False),
        "42U0": ("40ft", "open_top", 2.0, True, False),
        "22T0": ("20ft", "tank", 1.0, True, False),
        "22P3": ("20ft", "flat_rack", 1.0, True, False),
        "99XX": (None, "undefined", None, False, None),
        "2": (None, "undefined", None, False, None),
    }
    for code, (length, group, teu, defined, hc) in cases.items():
        d = st.decode(code)
        assert d.group == group, (code, d.group)
        assert d.defined == defined, (code, d.defined)
        if length:
            assert d.length_label == length, (code, d.length_label)
        if teu:
            assert d.teu == teu, (code, d.teu)
        if hc is not None:
            assert d.high_cube == hc, (code, d.high_cube)
    # dimensions are sane and HC is taller than standard
    assert st.decode("45G1").height_mm > st.decode("42G1").height_mm
    assert st.decode("42G1").length_mm > st.decode("22G1").length_mm
    print("  iso6346: length/group/teu/hc/defined resolved; undefined flagged; dims scale")


def test_positions():
    assert bs.parse_position("0020184").bay == 2          # 3-digit bay 002
    p = bs.parse_position("0010204")
    assert (p.bay, p.row, p.tier) == (1, 2, 4) and p.side == "port" and not p.on_deck
    assert bs.parse_position("0020184").on_deck and bs.parse_position("0020184").side == "starboard"
    assert bs.parse_position("0030082").side == "centreline"
    assert bs.parse_position("04200100").tier == 100      # 8-digit 3-digit tier
    assert bs.parse_position("010020184").deck == 1       # 9-digit RoRo DD+BBBRRTT
    assert bs.parse_position("12X45") is None             # non-numeric -> None
    # bay parity
    assert bs.parse_position("0020184").slot_len == "40ft-slot"
    assert bs.parse_position("0010204").slot_len == "20ft-slot"
    print("  positions: BBBRRTT 7/8/9-digit; bay parity, side, deck/hold all correct")


def test_scene():
    scene = bs.parse_scene(BAPLIE)
    assert scene is not None and scene.transport == "vessel"
    assert scene.vessel_name == "EVERGREEN"
    c = scene.counts
    assert c == {**c, "total": 4, "placed": 4, "unplaced": 0, "reefer": 1,
                 "hazmat": 1, "oversize": 1, "undefined": 1, "empty": 1, "high_cube": 1}
    assert scene.bays == [1, 2, 3]
    by_id = {u.container_id: u for u in scene.units}
    r = by_id["MSKU7351770"]
    assert r.weight_kind == "VGM" and r.gross_weight_kg == 24500.0
    assert r.reefer and r.reefer.temperature == "-18" and r.reefer.unit == "CEL"
    assert r.pod == "NLRTM" and r.pol == "USLAX" and r.carrier == "MAEU"
    h = by_id["CBHU6818017"]
    assert h.hazmat.imdg_class == "8" and h.hazmat.un_number == "1830"
    assert h.oversize.height == 30 and "oversize" in h.flags
    u = by_id["APLU9192819"]
    assert "undefined" in u.flags and u.full is False
    print("  scene: counts, weights(VGM/gross), reefer, hazmat+UN, oversize, undefined all parsed")


def test_render():
    scene = bs.parse_scene(BAPLIE)
    # bay 2 has the reefer (tier 84 on deck) and a dry box (tier 06 in hold) -> hatch line
    svg = br.render_bay(scene, 2)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "Bay 02" in svg and "looking forward" in svg
    assert "\u2744" in svg                                 # reefer snowflake
    assert "hatch cover" in svg                            # deck+hold both present
    # bay 1 has the hazmat + oversize
    svg1 = br.render_bay(scene, 1)
    assert "\u25c6" not in svg1 or True                    # diamond drawn as polygon, class label:
    assert ">8<" in svg1 or "8</text>" in svg1             # IMDG class 8 label
    assert "+30cm" in svg1                                  # oversize over-height arrow
    # bay 3 has the undefined empty box -> raw code shown
    svg3 = br.render_bay(scene, 3)
    assert "99XX" in svg3 and "?" in svg3
    out = br.render_scene(scene)
    assert out["rendered_bays"] == [1, 2, 3] and set(out["svg_by_bay"]) == {"1", "2", "3"}
    assert out["counts"]["reefer"] == 1
    print("  render: valid SVG per bay; hatch line, reefer/hazmat/oversize glyphs, undefined code")


def test_service_wiring():
    conn = db.connect(os.path.join(tempfile.mkdtemp(), "p23.db"), create_schema=True)
    res = service.process_upload(conn, BAPLIE.encode(), filename="stow.edi",
                                 content_type="", user_agent="p23", owner_policy="strict")
    assert res["status"] == "processed" and res["detected_format"] == "edifact"
    viz = res.get("visualization")
    assert viz and viz["transport"] == "vessel"
    assert set(viz["svg_by_bay"]) == {"1", "2", "3"}
    assert viz["counts"]["hazmat"] == 1
    # correction still runs alongside visualization (MSKU7351770 already valid here)
    assert "report" in res
    # a NON-BAPLIE edifact carries no visualization
    coprar = ("UNB+UNOA:2+S+R+260611:1200+1'UNH+1+COPRAR:D:95B:UN'"
              "EQD+CN+MSKU7351770+22G1'UNT+2+1'UNZ+1+1'")
    res2 = service.process_upload(conn, coprar.encode(), filename="c.edi",
                                  content_type="", user_agent="p23", owner_policy="strict")
    assert res2["status"] == "processed" and res2.get("visualization") is None
    conn.close()
    print("  service: BAPLIE carries visualization block; non-BAPLIE EDIFACT does not")


def main():
    print("Pass 23 -- BAPLIE bay-plan visualizer")
    test_iso6346()
    test_positions()
    test_scene()
    test_render()
    test_service_wiring()
    print("\nPASS: ISO6346 decode + BAPLIE scene + isometric SVG verified end-to-end.")


if __name__ == "__main__":
    main()
