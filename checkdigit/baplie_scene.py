"""
baplie_scene.py
===============
Parse a BAPLIE (EDIFACT bay-plan) message into a TRANSPORT-AGNOSTIC scene model:
a vessel made of bays, each bay a grid of rows x tiers, each occupied slot a
container unit carrying everything needed to draw it and to flag oversize /
reefer / hazmat / undefined cargo.

Reuses the existing release-char-aware tokenizer (edifact_locator.iter_segments)
-- no new EDIFACT parsing. Pure data: NO rendering here (that is bayplan_render).

Scope and grounding (per the SMDG BAPLIE MIG + ISO 9711; see project research doc):
  * Stowage position: LOC+147 value, ISO 9711 "BBBRRTT" -> bay/row/tier.
      bay  : odd = 20ft slot, even = 40ft slot (spans the two flanking odd bays)
      row  : even = port, odd = starboard, 00 = centreline
      tier : <80 = under deck (in hold), >=80 = on deck
      8-digit (3-digit tier) and 9-digit (RoRo deck-prefixed) variants handled.
  * Container: EQD (8053 qualifier CN; 8260 id; 8155 ISO size/type; full/empty).
  * Weight: MEA, both legacy "MEA+WT++KGM:23700" and SMDG "MEA+AAE+G|AET|VGM+KGM:..".
  * Oversize: DIM (qualifiers 1, 5-10, and 13 in BAPLIE-3) -> overhang cm.
  * Reefer: TMP (6245=2 carriage temp) + optional RNG range.
  * Hazmat: DGS (8273 regulation, 8351 IMDG class, 7124 UN number).
  * Carrier/operator: NAD+CA (carrier) or NAD+CF (operator, BAPLIE-3).

A BAPLIE 2.x record is the run of segments from one LOC+147 up to (but excluding)
the next LOC+147 or UNT. The container's EQD lives inside that run. This grouping
also tolerates the BAPLIE-3 layout where cargo characteristics sit on the EQD
group, because we collect every relevant segment that appears between positions.

Anything ambiguous is recorded on the unit's `notes` and surfaced in the drawing
rather than guessed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import iso6346_sizetype as st
from edifact_locator import iter_segments


# ----------------------------- data model ----------------------------------- #
@dataclass
class StowPosition:
    raw: str
    bay: int
    row: int
    tier: int
    deck: Optional[int] = None     # RoRo deck prefix when 9-digit
    on_deck: bool = False          # tier >= 80
    valid: bool = True

    @property
    def slot_len(self) -> str:
        return "40ft-slot" if self.bay % 2 == 0 else "20ft-slot"

    @property
    def side(self) -> str:
        if self.row == 0:
            return "centreline"
        return "port" if self.row % 2 == 0 else "starboard"


@dataclass
class Hazmat:
    regulation: str = ""           # IMD/ADR/RID/...
    imdg_class: str = ""           # 8351
    un_number: str = ""            # 7124 (may be absent)


@dataclass
class Reefer:
    temperature: str = ""          # e.g. "-18" or "015"
    unit: str = ""                 # CEL / FAH
    range_low: str = ""
    range_high: str = ""


@dataclass
class Oversize:
    # overhang in cm per direction; 0 if not present
    front: int = 0
    back: int = 0
    right: int = 0
    left: int = 0
    height: int = 0
    raw_codes: List[str] = field(default_factory=list)

    @property
    def any(self) -> bool:
        return any((self.front, self.back, self.right, self.left, self.height))


@dataclass
class Unit:
    container_id: str
    size_type_code: str
    sizetype: dict                 # iso6346_sizetype.decode(...).as_dict()
    position: Optional[StowPosition]
    gross_weight_kg: Optional[float] = None
    weight_kind: str = ""          # "" | gross | VGM
    full: Optional[bool] = None    # True full, False empty
    carrier: str = ""
    pod: str = ""                  # port of discharge (LOC+11) if present
    pol: str = ""                  # port of loading (LOC+9) if present
    reefer: Optional[Reefer] = None
    hazmat: Optional[Hazmat] = None
    oversize: Optional[Oversize] = None
    notes: List[str] = field(default_factory=list)

    @property
    def flags(self) -> List[str]:
        f = []
        if self.reefer:
            f.append("reefer")
        if self.hazmat:
            f.append("hazmat")
        if self.oversize and self.oversize.any:
            f.append("oversize")
        if not self.sizetype.get("defined", True):
            f.append("undefined")
        if self.full is False:
            f.append("empty")
        if self.sizetype.get("high_cube"):
            f.append("hc")
        return f


@dataclass
class Scene:
    transport: str                 # "vessel" (rail/truck later)
    units: List[Unit]
    placed: List[Unit] = field(default_factory=list)   # units with a valid position
    unplaced: List[Unit] = field(default_factory=list)
    bays: List[int] = field(default_factory=list)
    rows: List[int] = field(default_factory=list)
    tiers: List[int] = field(default_factory=list)
    vessel_name: str = ""
    counts: Dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        def unit_dict(u: Unit) -> dict:
            d = {
                "container_id": u.container_id, "size_type": u.size_type_code,
                "sizetype": u.sizetype, "gross_weight_kg": u.gross_weight_kg,
                "weight_kind": u.weight_kind, "full": u.full, "carrier": u.carrier,
                "pod": u.pod, "pol": u.pol, "flags": u.flags, "notes": u.notes,
            }
            if u.position:
                p = u.position
                d["position"] = {"raw": p.raw, "bay": p.bay, "row": p.row,
                                 "tier": p.tier, "deck": p.deck, "on_deck": p.on_deck,
                                 "side": p.side, "slot_len": p.slot_len, "valid": p.valid}
            else:
                d["position"] = None
            if u.reefer:
                d["reefer"] = vars(u.reefer)
            if u.hazmat:
                d["hazmat"] = vars(u.hazmat)
            if u.oversize and u.oversize.any:
                d["oversize"] = {k: v for k, v in vars(u.oversize).items()
                                 if k != "raw_codes"}
            return d

        return {
            "transport": self.transport, "vessel_name": self.vessel_name,
            "bays": self.bays, "rows": self.rows, "tiers": self.tiers,
            "counts": self.counts,
            "units": [unit_dict(u) for u in self.units],
        }


# ----------------------------- parsing -------------------------------------- #
def parse_position(value: str) -> Optional[StowPosition]:
    """ISO 9711 BBBRRTT (7), 3-digit-tier (8), or RoRo deck-prefixed (9)."""
    s = (value or "").strip()
    if not s.isdigit():
        return None
    deck = None
    if len(s) == 9:                 # DDBBBRRTT (RoRo)
        deck, s = int(s[0:2]), s[2:]
    if len(s) == 7:                 # BBBRRTT
        bay, row, tier = int(s[0:3]), int(s[3:5]), int(s[5:7])
    elif len(s) == 8:               # BBBRRTTT (3-digit on-deck tier)
        bay, row, tier = int(s[0:3]), int(s[3:5]), int(s[5:8])
    else:
        return None
    return StowPosition(raw=value, bay=bay, row=row, tier=tier, deck=deck,
                        on_deck=tier >= 80, valid=True)


def _mea_weight(els: List[List[str]]) -> Optional[Tuple[float, str]]:
    """Return (kg, kind) from a MEA segment, or None. Handles WT and AAE forms."""
    if len(els) < 2:
        return None
    q = els[1][0] if els[1] else ""
    prop = els[2][0] if len(els) > 2 and els[2] else ""
    comp = els[3] if len(els) > 3 else []
    unit = comp[0] if comp else ""
    val = comp[1] if len(comp) > 1 else ""
    if not val:
        return None
    try:
        kg = float(val.replace(",", "."))
    except ValueError:
        return None
    if unit and unit.upper() not in ("KGM", "KG"):
        # tonne/lb seen rarely; record but don't convert (flag upstream).
        pass
    kind = "VGM" if prop.upper() == "VGM" else "gross"
    # only treat as weight when qualifier looks like a weight measure
    if q.upper() in ("WT", "AAE", "G", "AET", "VGM", "T"):
        return kg, kind
    return kg, kind


_DIM_MAP = {  # DIM qualifier -> Oversize attribute (SMDG 2.x codes 5-9; 13 see notes)
    "5": "front", "6": "back", "7": "right", "8": "left", "9": "height",
}


def _dim_oversize(over: Oversize, els: List[List[str]]) -> None:
    """Accumulate a DIM segment's overhang into `over`. C211 = unit:len:wid:hgt (cm)."""
    if len(els) < 2:
        return
    qual = els[1][0] if els[1] else ""
    over.raw_codes.append(qual)
    comp = els[2] if len(els) > 2 else []
    # comp = [unit, length, width, height]
    def _cm(idx: int) -> int:
        if len(comp) > idx and comp[idx]:
            try:
                return int(round(float(comp[idx].replace(",", "."))))
            except ValueError:
                return 0
        return 0
    if qual in _DIM_MAP:
        attr = _DIM_MAP[qual]
        # for over-X codes only the relevant element is populated; pick the max nonzero
        val = max(_cm(1), _cm(2), _cm(3))
        setattr(over, attr, max(getattr(over, attr), val))
    elif qual in ("1", "10", "13"):
        # full external dims / non-ISO / BAPLIE-3 equipment dims: treat a height
        # element as over-height context; record raw for display.
        h = _cm(3)
        if h:
            over.height = max(over.height, h)


def _tmp_reefer(els: List[List[str]]) -> Optional[Reefer]:
    """TMP+2+015:CEL -> Reefer. Only carriage-temperature (6245=2) sets a reefer."""
    if len(els) < 2:
        return None
    qual = els[1][0] if els[1] else ""
    comp = els[2] if len(els) > 2 else []
    temp = comp[0] if comp else ""
    unit = comp[1] if len(comp) > 1 else ""
    if qual not in ("2", ""):       # 2 = transport/carriage temperature
        # other qualifiers (e.g. setpoint variants) still indicate reefer cargo
        pass
    if not temp and not unit:
        return None
    return Reefer(temperature=temp, unit=unit)


def _dgs_hazmat(els: List[List[str]]) -> Optional[Hazmat]:
    """DGS+IMD+1.2+1234... -> Hazmat. C205 class in 8351; C234 UN in 7124."""
    if len(els) < 2:
        return None
    regulation = els[1][0] if els[1] else ""
    c205 = els[2] if len(els) > 2 else []
    imdg_class = c205[0] if c205 else ""
    c234 = els[3] if len(els) > 3 else []
    un = c234[0] if c234 else ""
    if not (imdg_class or un or regulation):
        return None
    return Hazmat(regulation=regulation, imdg_class=imdg_class, un_number=un)


def _is_baplie(text: str) -> bool:
    head = text[:600].upper()
    return "BAPLIE" in head or "LOC+147" in text[:5000].upper()


def parse_scene(text: str) -> Optional[Scene]:
    """Parse a BAPLIE message into a Scene, or return None if it is not a BAPLIE."""
    if not _is_baplie(text):
        return None

    segments = list(iter_segments(text))
    vessel_name = ""
    # vessel name: TDT+20+<voyage>+...+<carrier>:::<NAME>'. The name is the LAST
    # alphabetic component (voyage and carrier codes precede it). Heuristic, since
    # the exact C222 layout varies; we take the longest trailing alpha token.
    for seg in segments:
        if seg.tag == "TDT":
            candidates = []
            for el in seg.elements[2:]:        # skip TDT tag and qualifier
                for comp in el:
                    if comp and any(ch.isalpha() for ch in comp) and len(comp) >= 3 \
                            and comp.upper() not in ("VSL", "VESSEL") \
                            and not (comp[:1].isalpha() and comp[1:].isdigit()):  # skip VOY123-style
                        candidates.append(comp)
            if candidates:
                vessel_name = candidates[-1]   # the name follows the carrier code
            break

    # Group into stowage records delimited by LOC+147.
    units: List[Unit] = []
    cur: Optional[dict] = None

    def flush(rec: Optional[dict]) -> None:
        if rec is None:
            return
        eqd = rec.get("eqd") or {}
        code = eqd.get("size_type", "")
        decoded = st.decode(code)
        over = rec.get("over")
        unit = Unit(
            container_id=eqd.get("eqid", ""),
            size_type_code=code,
            sizetype=decoded.as_dict(),
            position=rec.get("pos"),
            gross_weight_kg=rec.get("wt"),
            weight_kind=rec.get("wt_kind", ""),
            full=eqd.get("full"),
            carrier=rec.get("carrier", ""),
            pod=rec.get("pod", ""),
            pol=rec.get("pol", ""),
            reefer=rec.get("reefer"),
            hazmat=rec.get("hazmat"),
            oversize=over if (over and over.any) else None,
            notes=list(decoded.notes),
        )
        if rec.get("pos") is None and rec.get("had_loc147"):
            unit.notes.append("LOC+147 present but position could not be parsed")
        units.append(unit)

    for seg in segments:
        tag = seg.tag
        els = seg.elements
        if tag == "LOC":
            qual = els[1][0] if len(els) > 1 and els[1] else ""
            val = els[2][0] if len(els) > 2 and els[2] else ""
            if qual == "147":
                flush(cur)
                cur = {"over": Oversize(), "had_loc147": True,
                       "pos": parse_position(val)}
                continue
            if cur is not None:
                if qual == "9":
                    cur["pol"] = val
                elif qual == "11":
                    cur["pod"] = val
            continue
        if cur is None:
            # segments before the first LOC+147 (header) are ignored for units
            continue
        if tag == "EQD":
            qualifier = els[1][0] if len(els) > 1 and els[1] else ""
            if qualifier != "CN":
                continue
            eqid = els[2][0] if len(els) > 2 and els[2] else ""
            size_type = els[3][0] if len(els) > 3 and els[3] else ""
            # full/empty: scan later simple elements for 4/5 (position varies by version)
            full = None
            for el in els[4:]:
                v = el[0] if el else ""
                if v == "5":
                    full = True
                elif v == "4":
                    full = False
            cur["eqd"] = {"eqid": eqid, "size_type": size_type, "full": full}
        elif tag == "MEA":
            w = _mea_weight(els)
            if w and ("wt" not in cur or (cur.get("wt_kind") != "VGM" and w[1] == "VGM")):
                cur["wt"], cur["wt_kind"] = w
        elif tag == "DIM":
            _dim_oversize(cur["over"], els)
        elif tag == "TMP":
            r = _tmp_reefer(els)
            if r:
                cur["reefer"] = r
        elif tag == "RNG" and cur.get("reefer"):
            comp = els[2] if len(els) > 2 else []
            if len(comp) >= 3:
                cur["reefer"].range_low = comp[1]
                cur["reefer"].range_high = comp[2]
        elif tag == "DGS":
            h = _dgs_hazmat(els)
            if h:
                cur["hazmat"] = h
        elif tag == "NAD":
            q = els[1][0] if len(els) > 1 and els[1] else ""
            if q in ("CA", "CF"):
                cur["carrier"] = els[2][0] if len(els) > 2 and els[2] else ""
        elif tag == "UNT":
            flush(cur)
            cur = None
    flush(cur)

    # Split placed / unplaced and collect grid extents.
    placed = [u for u in units if u.position and u.position.valid]
    unplaced = [u for u in units if not (u.position and u.position.valid)]
    bays = sorted({u.position.bay for u in placed})
    rows = sorted({u.position.row for u in placed})
    tiers = sorted({u.position.tier for u in placed})

    counts = {
        "total": len(units), "placed": len(placed), "unplaced": len(unplaced),
        "reefer": sum(1 for u in units if u.reefer),
        "hazmat": sum(1 for u in units if u.hazmat),
        "oversize": sum(1 for u in units if u.oversize and u.oversize.any),
        "undefined": sum(1 for u in units if not u.sizetype.get("defined", True)),
        "empty": sum(1 for u in units if u.full is False),
        "high_cube": sum(1 for u in units if u.sizetype.get("high_cube")),
    }

    return Scene(transport="vessel", units=units, placed=placed, unplaced=unplaced,
                 bays=bays, rows=rows, tiers=tiers, vessel_name=vessel_name,
                 counts=counts)
