"""
intermodal_scene.py
===================
Parse RAIL and TRUCK intermodal EDI into the same transport-agnostic unit
vocabulary used by the vessel viewer (baplie_scene.Unit-shaped dicts), for the
isometric renderer.

Covers (per the project research doc, with primary-source grounding):
  * X12 404  (Rail Carrier Shipment Information) -- N7 equipment loop
  * X12 418  (Rail Advance Consist)              -- W1 blocks / W2 car loop
  * X12 322  (Terminal Operations / Ramp)        -- N7 + W2 + NA cross-ref
  * EDIFACT COPINO / CODECO / COPRAR / IFTMIN    -- EQD groups, TDT mode

THE CRITICAL HONESTY RULE (grounded): well number, platform (A/B), tier
(top/bottom) for rail, and front/rear chassis position for truck, are NOT
carried as standardized EDI elements. They are INFERRED from equipment type +
container count/size + loading rules. Every inferred position is marked
`inferred=True` so the renderer can draw it distinctly. We never present derived
geometry as if it were read from the file.

Equipment classification (research recommendation #1) is done first, for every
record, via X12 element 40 (N7-11 / W2-04) or EDIFACT 8053 (EQD/EQA-01).

Reuses the existing tokenizers (x12_locator.iter_segments,
edifact_locator.iter_segments) -- no new low-level EDI parsing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import iso6346_sizetype as st
from x12_locator import detect_delims, iter_segments as x12_iter
from edifact_locator import iter_segments as edi_iter


# X12 element 40 (equipment description) -> our class. Grounded in the research
# table; container-size codes (2B/4B/5B...) also classify as container.
_X12_DESC_CLASS = {
    "RR": "railcar", "FC": "railcar", "RE": "railcar", "ID": "idler",
    "CN": "container", "CC": "container",          # CC = container-on-chassis
    "CH": "chassis", "IC": "chassis",
    "TL": "trailer", "TV": "trailer",
    "LO": "power", "2E": "power", "2H": "power", "2C": "power",
    "HT": "device", "ET": "device",
}
# IL container size codes in element 40 -> (class, nominal feet)
_X12_DESC_CONTAINER_FT = {
    "2B": 20, "20": 20, "4B": 40, "40": 40, "4H": 40, "5B": 53, "53": 53, "45": 45,
}
# EDIFACT 8053 equipment qualifier -> our class
_EDI_EQ_CLASS = {
    "CN": "container", "RR": "railcar", "RF": "railcar", "BX": "railcar",
    "CH": "chassis", "TE": "trailer", "SW": "trailer", "BB": "breakbulk",
    "RG": "genset",
}
# UN/EDIFACT 8067 transport-mode-name code (C220) -> label
_EDI_MODE = {"1": "maritime", "2": "rail", "3": "road", "4": "air",
             "6": "multimodal", "8": "inland-water"}


@dataclass
class IUnit:
    """One intermodal equipment unit (container/trailer) to be drawn."""
    container_id: str
    size_type_code: str
    sizetype: dict                          # iso6346_sizetype.decode().as_dict()
    eq_class: str                           # container|trailer|chassis|railcar|...
    full: Optional[bool] = None
    length_ft: Optional[int] = None         # from N7-15 / size code when no ISO code
    carrier: str = ""
    reefer: bool = False
    hazmat: str = ""                        # IMDG class or hazmat id when present
    # placement (mode-specific; inferred flags say what is derived vs read)
    car_id: str = ""                        # parent rail car (reporting mark+number)
    car_type: str = ""                      # X12 element 301 car type code
    well: Optional[int] = None              # rail: well index in the car (inferred)
    platform: str = ""                      # rail: A/B/... articulated unit (inferred)
    tier: str = ""                          # rail: "bottom"/"top" (inferred)
    chassis_pos: str = ""                   # truck: "front"/"rear"/"single" (inferred)
    consist_seq: Optional[int] = None       # rail consist: ordinal car position (READ from order)
    inferred: List[str] = field(default_factory=list)   # which placements are derived
    notes: List[str] = field(default_factory=list)

    @property
    def flags(self) -> List[str]:
        f = []
        if self.reefer:
            f.append("reefer")
        if self.hazmat:
            f.append("hazmat")
        if not self.sizetype.get("defined", True):
            f.append("undefined")
        if self.full is False:
            f.append("empty")
        if self.sizetype.get("high_cube"):
            f.append("hc")
        if self.inferred:
            f.append("inferred-position")
        return f

    def as_dict(self) -> dict:
        return {
            "container_id": self.container_id, "size_type": self.size_type_code,
            "sizetype": self.sizetype, "eq_class": self.eq_class, "full": self.full,
            "length_ft": self.length_ft, "carrier": self.carrier,
            "reefer": self.reefer, "hazmat": self.hazmat,
            "car_id": self.car_id, "car_type": self.car_type, "well": self.well,
            "platform": self.platform, "tier": self.tier,
            "chassis_pos": self.chassis_pos, "consist_seq": self.consist_seq,
            "inferred": list(self.inferred), "flags": self.flags, "notes": self.notes,
        }


@dataclass
class RailCar:
    car_id: str
    car_type: str = ""
    status: str = ""                        # L loaded / W empty (W2-05)
    seq: int = 0                            # ordinal position in consist (READ from order)
    block: str = ""                         # W1 block id
    units: List[IUnit] = field(default_factory=list)
    wells: int = 1                          # inferred well count


@dataclass
class IntermodalScene:
    transport: str                          # "rail" | "truck"
    view: str                               # "consist" | "doublestack" | "chassis"
    units: List[IUnit]
    cars: List[RailCar] = field(default_factory=list)
    truck_id: str = ""
    mode: str = ""
    counts: Dict[str, int] = field(default_factory=dict)
    inference_note: str = ""

    def as_dict(self) -> dict:
        return {
            "transport": self.transport, "view": self.view, "mode": self.mode,
            "truck_id": self.truck_id, "counts": self.counts,
            "inference_note": self.inference_note,
            "cars": [{"car_id": c.car_id, "car_type": c.car_type, "status": c.status,
                      "seq": c.seq, "block": c.block, "wells": c.wells,
                      "units": [u.as_dict() for u in c.units]} for c in self.cars],
            "units": [u.as_dict() for u in self.units],
        }


# --------------------------- helpers ---------------------------------------- #
def _is_container_id(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{4}\d{6,7}", (s or "").upper()))


def _decode_size(code: str, length_ft: Optional[int]) -> dict:
    """Decode an ISO 6346 size/type; if absent, synthesize a minimal dict from ft."""
    if code and len(code) == 4:
        return st.decode(code).as_dict()
    d = st.decode("").as_dict()      # undefined fallback
    if length_ft:
        d["length_label"] = f"{length_ft}ft"
        d["teu"] = 2.0 if length_ft >= 40 else 1.0
        d["length_mm"] = int(length_ft * 304.8)
        d["notes"] = d.get("notes", []) + [f"length {length_ft}ft from EDI; no ISO size/type code"]
    return d


def _x12_length_ft(n7_15: str) -> Optional[int]:
    """N7-15 equipment length FFFII (e.g. '4000'=40'00) -> whole feet."""
    s = re.sub(r"\D", "", n7_15 or "")
    if len(s) >= 3:
        try:
            return int(s[:-2])
        except ValueError:
            return None
    return None


# --------------------------- X12 rail --------------------------------------- #
def _classify_x12(desc: str) -> Tuple[str, Optional[int]]:
    d = (desc or "").upper()
    if d in _X12_DESC_CONTAINER_FT:
        return "container", _X12_DESC_CONTAINER_FT[d]
    return _X12_DESC_CLASS.get(d, "container" if d == "" else "other"), None


def _parse_x12(text: str) -> Optional[IntermodalScene]:
    delims = detect_delims(text)
    segs = list(x12_iter(text, delims))
    tags = [s[0] for s in segs]
    if "N7" not in tags and "W2" not in tags:
        return None
    is_consist = "BAX" in tags or "W2" in tags

    if is_consist:
        return _parse_x12_consist(segs)
    return _parse_x12_404_322(segs)


def _parse_x12_consist(segs) -> IntermodalScene:
    """X12 418: W1 blocks -> ordered W2 car records. Sequence is IMPLICIT in order."""
    cars: List[RailCar] = []
    block = ""
    seq = 0
    for tag, els, offs, start, raw in segs:
        if tag == "W1":
            block = els[1] if len(els) > 1 else ""
        elif tag == "W2":
            seq += 1
            init = els[1] if len(els) > 1 else ""
            num = els[2] if len(els) > 2 else ""
            desc = els[4] if len(els) > 4 else ""
            status = els[5] if len(els) > 5 else ""
            car_type = els[15] if len(els) > 15 else ""
            cls, _ = _classify_x12(desc)
            car = RailCar(car_id=f"{init}{num}", car_type=car_type, status=status,
                          seq=seq, block=block)
            car.wells = _infer_wells(car_type)
            cars.append(car)
    units: List[IUnit] = []
    for c in cars:
        # a consist W2 is the CAR itself; we draw cars in sequence (no child containers here)
        u = IUnit(container_id=c.car_id, size_type_code="", sizetype=_decode_size("", None),
                  eq_class="railcar", full=(c.status == "L"),
                  car_id=c.car_id, car_type=c.car_type, consist_seq=c.seq)
        c.units.append(u)
        units.append(u)
    counts = {"total": len(cars), "cars": len(cars),
              "loaded": sum(1 for c in cars if c.status == "L"),
              "empty": sum(1 for c in cars if c.status == "W")}
    return IntermodalScene(transport="rail", view="consist", units=units, cars=cars,
                           counts=counts,
                           inference_note="Car order is the segment order of W2 records "
                                          "(X12 418 carries no explicit sequence element).")


def _parse_x12_404_322(segs) -> IntermodalScene:
    """X12 404/322: N7 loop. RR/idler records start a car; CN/CC records are its
    containers (grouped by order). Well/tier/platform are INFERRED."""
    cars: List[RailCar] = []
    loose: List[IUnit] = []
    cur: Optional[RailCar] = None
    seq = 0
    for tag, els, offs, start, raw in segs:
        if tag != "N7":
            continue
        init = els[1] if len(els) > 1 else ""
        num = els[2] if len(els) > 2 else ""
        desc = els[11] if len(els) > 11 else ""
        length_ft = _x12_length_ft(els[15] if len(els) > 15 else "")
        size_type = els[22] if len(els) > 22 else ""    # N7-22 may hold a 4-char type
        car_type = els[24] if len(els) > 24 else ""
        cls, ft2 = _classify_x12(desc)
        length_ft = length_ft or ft2
        ident = f"{init}{num}"
        if cls in ("railcar", "idler"):
            seq += 1
            cur = RailCar(car_id=ident, car_type=car_type, seq=seq)
            cur.wells = _infer_wells(car_type)
            cars.append(cur)
        elif cls in ("container", "trailer"):
            iso = size_type if re.fullmatch(r"[0-9A-Z]{4}", size_type or "") else ""
            u = IUnit(container_id=ident, size_type_code=iso,
                      sizetype=_decode_size(iso, length_ft), eq_class=cls,
                      length_ft=length_ft, car_id=cur.car_id if cur else "",
                      car_type=cur.car_type if cur else "")
            if not _is_container_id(ident):
                u.notes.append(f"equipment id {ident!r} is not a standard container number")
            if cur:
                cur.units.append(u)
            else:
                loose.append(u)
    _infer_doublestack(cars)
    all_units = [u for c in cars for u in c.units] + loose
    counts = {
        "total": len(all_units), "cars": len(cars),
        "containers": sum(1 for u in all_units if u.eq_class == "container"),
        "reefer": sum(1 for u in all_units if u.reefer),
        "undefined": sum(1 for u in all_units if not u.sizetype.get("defined", True)),
    }
    return IntermodalScene(transport="rail", view="doublestack", units=all_units,
                           cars=cars, counts=counts,
                           inference_note="Well, platform and tier are DERIVED from car "
                                          "type and container count/size (AAR loading rules); "
                                          "they are not present in the EDI.")


def _infer_wells(car_type: str) -> int:
    """Best-effort well count from an AAR/Umler car type code. Heuristic -> VERIFY."""
    t = (car_type or "").upper()
    # common doublestack families: 3-pack / 5-pack. Without Umler we guess by hints.
    if t.startswith(("S", "DTTX")) or "5" in t:
        return 5
    if "3" in t:
        return 3
    return 1


def _infer_doublestack(cars: List[RailCar]) -> None:
    """Assign well/platform/tier to a car's containers by loading rules (DERIVED).
    Rule (AAR Intermodal Loading Guide, operational): per well, the longer/53'
    domestic box rides TOP, the 40'/international box rides BOTTOM; a single
    container in a well is BOTTOM only. Platform = ordinal well (A,B,...)."""
    for car in cars:
        n = len(car.units)
        if n == 0:
            continue
        # distribute containers across wells, up to 2 per well (bottom+top)
        per_well: List[List[IUnit]] = []
        i = 0
        wells = max(car.wells, (n + 1) // 2)
        for w in range(wells):
            chunk = car.units[i:i + 2]
            i += 2
            if chunk:
                per_well.append(chunk)
        for w, chunk in enumerate(per_well):
            platform = chr(ord("A") + w)
            if len(chunk) == 1:
                chunk[0].well, chunk[0].platform, chunk[0].tier = w + 1, platform, "bottom"
                chunk[0].inferred += ["well", "platform", "tier"]
            else:
                # shorter on bottom, longer on top (domestic 53 over intl 40)
                a, b = chunk
                la = a.length_ft or (40 if a.sizetype.get("teu") == 1.0 else 40)
                lb = b.length_ft or 40
                bottom, top = (a, b) if la <= lb else (b, a)
                bottom.well, bottom.platform, bottom.tier = w + 1, platform, "bottom"
                top.well, top.platform, top.tier = w + 1, platform, "top"
                for u in (bottom, top):
                    u.inferred += ["well", "platform", "tier"]


# --------------------------- EDIFACT rail/truck ----------------------------- #
def _parse_edifact(text: str) -> Optional[IntermodalScene]:
    msg_type = ""
    mode = ""
    truck_id = ""
    eqd_groups: List[dict] = []
    cur: Optional[dict] = None

    for seg in edi_iter(text):
        tag = seg.tag
        els = seg.elements
        if tag == "UNH":
            # UNH+ref+COPINO:D:00B:UN:SMDG20 -> message type in C002 first comp
            comp = els[2] if len(els) > 2 else []
            msg_type = comp[0] if comp else ""
        elif tag == "TDT":
            # C220 mode = els[? ] : in TDT the mode-of-transport is element 3 (8067)
            mode_code = els[3][0] if len(els) > 3 and els[3] else ""
            mode = _EDI_MODE.get(mode_code, mode)
            # truck plate often a later simple element
            for el in els[8:]:
                v = el[0] if el else ""
                if v and any(ch.isalnum() for ch in v):
                    truck_id = truck_id or v
        elif tag == "EQD":
            qual = els[1][0] if len(els) > 1 and els[1] else ""
            eqid = els[2][0] if len(els) > 2 and els[2] else ""
            size_type = els[3][0] if len(els) > 3 and els[3] else ""
            full = None
            for el in els[4:]:
                v = el[0] if el else ""
                if v == "5":
                    full = True
                elif v == "4":
                    full = False
            cur = {"qual": qual, "eqid": eqid, "size_type": size_type, "full": full,
                   "reefer": False, "hazmat": "", "attached": []}
            eqd_groups.append(cur)
        elif cur is not None and tag == "EQA":
            q = els[1][0] if len(els) > 1 and els[1] else ""
            aid = els[2][0] if len(els) > 2 and els[2] else ""
            cur["attached"].append((q, aid))
        elif cur is not None and tag == "TMP":
            cur["reefer"] = True
        elif cur is not None and tag == "DGS":
            c205 = els[2] if len(els) > 2 else []
            cur["hazmat"] = c205[0] if c205 else cur["hazmat"]

    if not eqd_groups:
        return None

    # classify; containers are the drawn units, chassis become attachments
    units: List[IUnit] = []
    chassis_ids: List[str] = []
    for g in eqd_groups:
        cls = _EDI_EQ_CLASS.get(g["qual"], "container")
        if cls in ("chassis",):
            chassis_ids.append(g["eqid"])
            continue
        u = IUnit(container_id=g["eqid"], size_type_code=g["size_type"],
                  sizetype=_decode_size(g["size_type"], None), eq_class=cls,
                  full=g["full"], reefer=g["reefer"], hazmat=g["hazmat"])
        # attached chassis from EQA
        for q, aid in g["attached"]:
            if _EDI_EQ_CLASS.get(q) == "chassis":
                chassis_ids.append(aid)
        units.append(u)

    # truck view: infer front/rear when 2x20 share a 40 chassis
    _infer_chassis(units)

    view = "chassis"
    transport = "truck"
    if mode == "rail":
        transport, view = "rail", "doublestack"
    counts = {
        "total": len(units),
        "containers": sum(1 for u in units if u.eq_class == "container"),
        "reefer": sum(1 for u in units if u.reefer),
        "hazmat": sum(1 for u in units if u.hazmat),
        "undefined": sum(1 for u in units if not u.sizetype.get("defined", True)),
        "empty": sum(1 for u in units if u.full is False),
        "chassis": len(set(chassis_ids)),
    }
    note = ("Front/rear chassis position is DERIVED from container count and size "
            "(2x20ft on a 40ft chassis = front+rear); it is not present in the EDI."
            if transport == "truck" else
            "Inland mode read from TDT; rail position is derived, not in EDI.")
    return IntermodalScene(transport=transport, view=view, units=units, mode=mode or "road",
                           truck_id=truck_id, counts=counts, inference_note=note)


def _infer_chassis(units: List[IUnit]) -> None:
    """Two 20ft containers in one truck visit -> front + rear (DERIVED)."""
    containers = [u for u in units if u.eq_class == "container"]
    twenties = [u for u in containers if (u.sizetype.get("teu") == 1.0
                                          or (u.length_ft and u.length_ft <= 20))]
    if len(containers) == 2 and len(twenties) == 2:
        twenties[0].chassis_pos = "front"
        twenties[1].chassis_pos = "rear"
        for u in twenties:
            u.inferred.append("chassis_pos")
    elif len(containers) == 1:
        containers[0].chassis_pos = "single"


# --------------------------- entry point ------------------------------------ #
def parse_intermodal(text: str) -> Optional[IntermodalScene]:
    """Detect and parse a rail/truck intermodal message. Returns None if neither."""
    head = text[:400].upper()
    if head[:3] == "ISA" or "\nST*" in text[:2000] or text[:3] == "ST*" or "~ST*" in text[:2000]:
        return _parse_x12(text)
    if "UNH" in head or "UNB" in head:
        # Only treat as rail/truck intermodal if it is a genuine inland-movement
        # message. COPINO/CODECO are truck/barge gate messages; IFTMIN/IFTSTA are
        # multimodal movement instructions/status. COPRAR is deliberately EXCLUDED:
        # it is a vessel discharge/loading order whose LOC positions are vessel
        # stow cells, not chassis/rail positions -- it belongs to the BAPLIE-family
        # vessel view, not here.
        if any(m in text[:600].upper() for m in ("COPINO", "CODECO", "IFTMIN", "IFTSTA")):
            return _parse_edifact(text)
    return None
