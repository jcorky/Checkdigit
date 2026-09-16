"""
iso6346_sizetype.py
===================
Pass 18: decode the ISO 6346 four-character size/type code (the value in the
EDIFACT EQD C224/8155 element, e.g. "22G1", "45R1", "42U0", "L0G1") into:
  * physical length / height / width (millimetres),
  * a coarse equipment GROUP used to pick an icon + colour, and
  * human-readable descriptions of each character.

This is decode-and-ANNOTATE, never "correct": a size/type code is descriptive,
not a check-digit target (the kernel already returns NOT_A_TARGET for it). The
visualizer uses the decoded dimensions to scale each drawn unit and the group to
choose its rendering (dry box / reefer / open-top / flat-rack / tank / bulk /
undefined).

Grounding: character tables reproduce BIC's published ISO 6346:1995 (Amd 3:2012)
/ 2022 size and type tables. Values that ISO marks unassigned, or codes not in
the table, decode with `defined=False` so callers render an explicit "undefined"
unit with its raw code shown rather than guessing. External dimensions are for
PROPORTIONAL DRAWING ONLY -- not stability or stowage math.

Anything uncertain is flagged in comments as VERIFY rather than invented.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

# --- char 1: length -> external length in mm (ISO 6346 Table 1 via BIC) -------
# ISO marks code "5" UNASSIGNED for length; many senders nonetheless use 45G1/45R1
# where the FIRST "4" is the 40-grid length and the SECOND char "5" is HC height.
# We therefore do NOT map a length code "5"; 45ft is length code "L".
_LENGTH_MM: Dict[str, int] = {
    "1": 2991,   # 10 ft
    "2": 6068,   # 20 ft  (ISO Table 1 nominal; carrier datasheets often cite 6058)
    "3": 9125,   # 30 ft
    "4": 12192,  # 40 ft
    "B": 7315,   # 24 ft
    "C": 7430,   # 24 ft 6 in
    "G": 12500,  # 41 ft
    "H": 13106,  # 43 ft
    "L": 13716,  # 45 ft
    "M": 14630,  # 48 ft
    "N": 14935,  # 49 ft
    "P": 16154,  # 53 ft
}
_LENGTH_LABEL: Dict[str, str] = {
    "1": "10ft", "2": "20ft", "3": "30ft", "4": "40ft", "B": "24ft",
    "C": "24ft6in", "G": "41ft", "H": "43ft", "L": "45ft", "M": "48ft",
    "N": "49ft", "P": "53ft",
}

# --- char 2: height (mm) + whether over-width (ISO 6346 Table 2 via BIC) -------
# Digits 0/2/4/5/6/8/9 are standard-width heights. Letters denote >2438mm width
# (pallet-wide / over-width) at various heights; we decode the common ones and
# fall back to standard height + over_width flag for the rest.
_HEIGHT_MM: Dict[str, int] = {
    "0": 2438,   # 8 ft
    "2": 2591,   # 8 ft 6 in  (standard)
    "4": 2743,   # 9 ft
    "5": 2896,   # 9 ft 6 in  (High Cube)
    "6": 2926,   # > 9 ft 6 in (VERIFY exact; ISO says ">2896")
    "8": 1295,   # 4 ft 3 in  (half height)
    "9": 1219,   # <= 4 ft
}
# Over-width letter codes: standard heights but width > 2438mm. Heights per BIC
# Table 2 letter rows (C/D = 8'6"/9'6" pallet-wide etc.). We map height; width is
# flagged as over-width for the callout.
_HEIGHT_LETTER_MM: Dict[str, int] = {
    "C": 2591, "D": 2896, "E": 2591, "F": 2896, "L": 2591, "M": 2896,
    "N": 2896, "P": 2896,
}
_STD_WIDTH_MM = 2438

# --- chars 3-4: type group -> coarse render group (ISO 6346 Table 3 via BIC) --
# First letter of the type code is the family; we map to a small set of render
# groups the bay-plan understands. Second char (detail) is kept for the label.
_GROUP_BY_TYPE_LETTER: Dict[str, str] = {
    "G": "dry", "V": "dry", "W": "dry",          # general purpose / ventilated / foldable
    "R": "reefer", "H": "reefer",                # mechanically refrigerated / thermal
    "U": "open_top",
    "P": "flat_rack",                            # platform / flat rack
    "K": "tank", "N": "tank",                    # tank / bulk-tank
    "T": "tank",                                 # tank container family (TN/TG/TD/TC)
    "B": "bulk",
    "S": "named",                                # named cargo (livestock/auto/genset)
    "A": "dry",                                  # air/surface -> draw as box
}
_TYPE_LETTER_LABEL: Dict[str, str] = {
    "G": "General purpose", "V": "Ventilated", "W": "Foldable GP",
    "R": "Refrigerated", "H": "Thermal/insulated", "U": "Open top",
    "P": "Platform / flat rack", "K": "Tank", "N": "Bulk (tank/hopper)",
    "B": "Dry bulk", "S": "Named cargo", "A": "Air/surface", "T": "Tank container",
}


@dataclass
class SizeType:
    code: str                       # the raw 4-char code as given
    defined: bool                   # False if any char is unknown/unassigned
    length_mm: int
    width_mm: int
    height_mm: int
    high_cube: bool
    over_width: bool
    group: str                      # dry|reefer|open_top|flat_rack|tank|bulk|named|undefined
    teu: float                      # 1.0 for 20ft-class, 2.0 for 40/45ft-class (drawing span)
    length_label: str               # "20ft", "40ft", ...
    height_label: str               # "8ft6in", "9ft6in (HC)", ...
    type_label: str                 # "General purpose", "Refrigerated", ...
    notes: list = field(default_factory=list)   # human flags (VERIFY/undefined reasons)

    def as_dict(self) -> dict:
        return {
            "code": self.code, "defined": self.defined,
            "length_mm": self.length_mm, "width_mm": self.width_mm,
            "height_mm": self.height_mm, "high_cube": self.high_cube,
            "over_width": self.over_width, "group": self.group, "teu": self.teu,
            "length_label": self.length_label, "height_label": self.height_label,
            "type_label": self.type_label, "notes": list(self.notes),
        }


# Fallback geometry for an undefined/blank code: a 20ft standard dry box, but the
# unit is explicitly flagged undefined so the renderer shows the raw code.
_FALLBACK = dict(length_mm=6068, width_mm=2438, height_mm=2591, teu=1.0)


def decode(code: Optional[str]) -> SizeType:
    raw = (code or "").strip().upper()
    notes: list = []
    if len(raw) != 4:
        notes.append(f"size/type code {raw!r} is not 4 characters; treated as undefined")
        return SizeType(raw, False, _FALLBACK["length_mm"], _FALLBACK["width_mm"],
                        _FALLBACK["height_mm"], False, False, "undefined",
                        _FALLBACK["teu"], "?", "?", "?", notes)

    c_len, c_hgt, c_t1, c_t2 = raw[0], raw[1], raw[2], raw[3]
    defined = True

    # length
    length_mm = _LENGTH_MM.get(c_len)
    if length_mm is None:
        defined = False
        notes.append(f"length code {c_len!r} unknown/unassigned (ISO marks '5' unassigned; "
                     f"45ft is 'L')")
        length_mm = _FALLBACK["length_mm"]
        length_label = "?"
    else:
        length_label = _LENGTH_LABEL.get(c_len, "?")
    # drawing span in grid cells: 20ft-class = 1, 40ft-and-longer = 2
    teu = 2.0 if length_mm >= 12000 else 1.0

    # height / width
    over_width = False
    high_cube = False
    if c_hgt in _HEIGHT_MM:
        height_mm = _HEIGHT_MM[c_hgt]
        high_cube = c_hgt in ("5", "6")
        width_mm = _STD_WIDTH_MM
        height_label = {"0": "8ft", "2": "8ft6in", "4": "9ft", "5": "9ft6in (HC)",
                        "6": ">9ft6in", "8": "4ft3in", "9": "<=4ft"}.get(c_hgt, "?")
    elif c_hgt in _HEIGHT_LETTER_MM:
        height_mm = _HEIGHT_LETTER_MM[c_hgt]
        over_width = True
        high_cube = height_mm >= 2896
        width_mm = 2500   # pallet-wide nominal (VERIFY exact per letter; over-width flagged)
        height_label = ("9ft6in (HC)" if high_cube else "8ft6in") + ", pallet-wide"
    else:
        defined = False
        height_mm = _FALLBACK["height_mm"]
        width_mm = _STD_WIDTH_MM
        height_label = "?"
        notes.append(f"height/width code {c_hgt!r} unknown")

    # type group (first type char)
    group = _GROUP_BY_TYPE_LETTER.get(c_t1)
    if group is None:
        defined = False
        group = "undefined"
        type_label = f"type {c_t1}{c_t2} (unrecognised)"
        notes.append(f"type code {c_t1!r} unrecognised")
    else:
        type_label = _TYPE_LETTER_LABEL.get(c_t1, "?") + f" ({c_t1}{c_t2})"

    if not defined and group != "undefined":
        notes.append("partially decoded; raw code shown on the unit")

    return SizeType(raw, defined, length_mm, width_mm, height_mm, high_cube,
                    over_width, group, teu, length_label, height_label,
                    type_label, notes)


# Render palette per group (hex; chosen for contrast, not a maritime standard).
GROUP_COLOR: Dict[str, str] = {
    "dry": "#5b8def",        # blue
    "reefer": "#22a3b8",     # teal (reefers commonly cyan/teal)
    "open_top": "#e0a13a",   # amber
    "flat_rack": "#9b6dd6",  # purple
    "tank": "#c65d5d",       # red-brown
    "bulk": "#7a8a5a",       # olive
    "named": "#d98ab2",      # pink
    "undefined": "#9aa0a6",  # gray (always for undefined)
}
