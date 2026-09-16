"""
edifact_locator.py
==================
Pass 3, format #1: locate equipment identifiers in UN/EDIFACT terminal messages
(BAPLIE / COPRAR / COARRI / CODECO / COPARN / MOVINS ...).

Responsibility: LOCATE, do not correct. It walks segments, finds the EQD
(container) equipment-identification value -- composite C237, data element 8260 --
plus the ISO 6346 size/type code (C224 / 8155), and returns each with the byte
offset of the identifier in the source so the correction layer can substitute
surgically. It hands identifier strings to the equipment_checkdigit kernel; it
embeds no correction logic.

Scope decisions (informed by the SMDG/ITIGG COARRI guide review):
  * Only EQD is treated as a container source. EQA (Attached Equipment -- gensets,
    reefer generators, chassis, trailers) ALSO carries C237/8260, but those are
    NOT ISO 6346 containers and must not be "corrected" as such -- so EQA is
    deliberately excluded here.
  * EQD01 (8053) qualifies the equipment (CN=container, TE=trailer, CH=chassis,
    SW=swap body, BB=breakbulk). Only CN routes to the ISO 6346 algorithm.

Real-world EDIFACT mechanics handled:
  * UNA service-string-advice or default separators,
  * the release character (?: is a literal colon, ?' a literal apostrophe) so
    segment boundaries and values are not mis-parsed,
  * segments delimited only by the terminator (no newlines).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from equipment_checkdigit import (
    correct_identifier, CorrectionResult, FieldContext, _OWNER_POLICIES,
)


@dataclass
class Separators:
    component: str = ":"
    element: str = "+"
    decimal: str = "."
    release: str = "?"
    segment: str = "'"


def detect_separators(text: str) -> Separators:
    """Honor UNA service string advice if present; otherwise UN/EDIFACT defaults."""
    if text[:3] == "UNA" and len(text) >= 9:
        s = text[3:9]
        return Separators(component=s[0], element=s[1], decimal=s[2],
                          release=s[3], segment=s[5])
    return Separators()


@dataclass
class Segment:
    tag: str
    elements: List[List[str]]   # elements -> components (release chars removed)
    raw: str                    # verbatim segment text WITHOUT the terminator
    start: int                  # byte offset of the segment start in the source


def _split_segments(text: str, sep: Separators):
    """Yield (verbatim_segment_without_terminator, start_offset), honoring release char."""
    i = 9 if text[:3] == "UNA" else 0     # skip leading UNA service advice
    start = i
    buf: List[str] = []
    escaped = False
    n = len(text)
    while i < n:
        ch = text[i]
        if escaped:
            buf.append(ch)
            escaped = False
        elif ch == sep.release:
            buf.append(ch)               # keep release char in verbatim raw
            escaped = True
        elif ch == sep.segment:
            seg = "".join(buf)
            if seg.strip():
                yield seg, start
            i += 1
            while i < n and text[i] in "\r\n \t":   # skip inter-segment whitespace
                i += 1
            start = i
            buf = []
            continue
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf)
    if tail.strip():
        yield tail, start


def _split_on(s: str, delim: str, release: str) -> List[str]:
    """Split s on delim, honoring (and removing) the release char."""
    parts: List[str] = []
    buf: List[str] = []
    escaped = False
    for ch in s:
        if escaped:
            buf.append(ch); escaped = False
        elif ch == release:
            escaped = True               # drop release char from the value
        elif ch == delim:
            parts.append("".join(buf)); buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def parse_segment(raw: str, start: int, sep: Separators) -> Segment:
    elems_raw = _split_on(raw, sep.element, sep.release)
    elements = [_split_on(e, sep.component, sep.release) for e in elems_raw]
    return Segment(tag=elems_raw[0], elements=elements, raw=raw, start=start)


def iter_segments(text: str):
    sep = detect_separators(text)
    for raw, start in _split_segments(text, sep):
        yield parse_segment(raw, start, sep)


@dataclass
class EquipmentRef:
    eqid: str                       # C237/8260 (container number); "" if absent
    qualifier: str                  # EQD01 / 8053 (CN, TE, CH, SW, BB)
    size_type: str                  # C224/8155 (ISO 6346 size/type code); "" if absent
    raw_segment: str
    seg_start: int
    eqid_offset: Optional[int]      # absolute byte offset of eqid in source (substitution anchor)


def locate_equipment(text: str) -> List[EquipmentRef]:
    """Return EQD equipment references. EQA is intentionally excluded (see module docstring)."""
    refs: List[EquipmentRef] = []
    for seg in iter_segments(text):
        if seg.tag != "EQD":
            continue
        els = seg.elements
        qualifier = els[1][0] if len(els) > 1 and els[1] else ""
        eqid = els[2][0] if len(els) > 2 and els[2] else ""
        size_type = els[3][0] if len(els) > 3 and els[3] else ""
        eqid_offset = (seg.start + seg.raw.find(eqid)) if eqid else None
        refs.append(EquipmentRef(eqid, qualifier, size_type, seg.raw, seg.start, eqid_offset))
    return refs


def evaluate_text(text: str, *, owner_policy: str = "strict", policy=None
                  ) -> List[Tuple[EquipmentRef, Optional[CorrectionResult]]]:
    """
    Locate container identifiers (EQD + qualifier CN) and evaluate each via the
    kernel. Returns (ref, result) pairs; result is None for an EQD+CN with an
    empty equipment id (nothing to correct). Does NOT mutate text -- byte-offset
    substitution is pass 4.
    """
    if owner_policy not in _OWNER_POLICIES:
        raise ValueError(
            f"owner_policy must be one of {sorted(_OWNER_POLICIES)}, got {owner_policy!r}")
    out: List[Tuple[EquipmentRef, Optional[CorrectionResult]]] = []
    for ref in locate_equipment(text):
        if ref.qualifier != "CN":
            continue                                  # only containers -> ISO 6346
        if not ref.eqid:
            out.append((ref, None))                   # EQD+CN with empty 8260
            continue
        out.append((ref, correct_identifier(
            ref.eqid, FieldContext.EQUIPMENT_ID, owner_policy=owner_policy, policy=policy)))
    return out
