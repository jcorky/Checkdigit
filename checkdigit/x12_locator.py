"""
x12_locator.py
==============
Pass 6, format #3: locate container identifiers in ANSI ASC X12 (e.g. 310/322/
304/309 ocean & intermodal sets).

Unlike EDIFACT/SNX, the X12 container number is SPLIT and its check digit lives
in a dedicated element:
    N7-01  Equipment Initial   (alpha/alnum prefix, 4 chars for a container)
    N7-02  Equipment Number    (serial)
    N7-18  Equipment Number Check Digit   (data element 761)   <-- the target
    N7-22  Equipment Description / ISO size-type (e.g. 40HQ)    <-- NOT a target
The same number may also appear as a FULL 11-char token in a reference segment
qualified EQ:
    N9-01 = "EQ"  ->  N9-02 = full equipment number (e.g. CONT1234567)
A container number embedded in a Bill-of-Lading reference (N9-01="BM", value like
"SSLACONT1234567001") is deliberately NOT touched.

Responsibility: LOCATE only. It records, per identifier occurrence, the byte
offset to be edited (the N7-18 element, or the N9*EQ token) so the substitution
layer can act surgically. Correction decisions stay in the kernel. X12 has NO
release/escape character (delimiters must not occur in data), so tokenizing is a
straight split with offset bookkeeping.

Delimiters: read from the ISA header when present (element sep = ISA[3],
sub-element sep = ISA[104], segment terminator = the char after ISA16); otherwise
the de-facto defaults (* : ~).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from equipment_checkdigit import (
    correct_identifier, correct_x12_equipment, CorrectionResult,
    FieldContext, _OWNER_POLICIES,
)


@dataclass
class Delims:
    element: str = "*"
    subelement: str = ":"
    segment: str = "~"


def detect_delims(text: str) -> Delims:
    """ISA-declared delimiters if an ISA header is present; else X12 defaults.

    A conformant ISA is fixed-width: element sep at index 3, component sep at
    104, segment terminator at 105. Some real-world senders pad the ISA slightly
    wrong; rather than tokenize with a garbage terminator (which silently yields
    no segments), we validate index 105 and, if it looks wrong, recover the real
    terminator structurally from the element separator known at index 3.
    """
    if text[:3] == "ISA":
        ele = text[3] if len(text) > 3 else "*"
        if len(text) >= 106:
            comp, seg = text[104], text[105]
            # A plausible terminator is a non-alphanumeric, non-space control/punct
            # char that differs from the element separator. If index 105 fails that
            # (mis-padded ISA), fall back to the default '~' or the first newline.
            if not seg.isalnum() and seg not in (" ", ele):
                return Delims(element=ele, subelement=comp, segment=seg)
        # malformed ISA: keep the (reliable) element sep, recover a terminator.
        nl = text.find("\n")
        # default X12 terminator is '~'; prefer it if present, else newline.
        seg = "~" if "~" in text[:200] else ("\n" if nl != -1 else "~")
        comp = ":" if ":" in text[:200] else ":"
        return Delims(element=ele, subelement=comp, segment=seg)
    return Delims()


def iter_segments(text: str, delims: Delims):
    """
    Yield (tag, elements, offsets, seg_start, raw) for each segment.
    `offsets[i]` is the absolute byte offset of element i's VALUE in `text`
    (offsets[0] is the tag). Inter-segment whitespace/newlines are skipped.
    """
    ele, seg = delims.element, delims.segment
    ws = "\r\n \t"
    pos, n = 0, len(text)
    while pos < n:
        while pos < n and text[pos] in ws:
            pos += 1
        if pos >= n:
            break
        end = text.find(seg, pos)
        if end == -1:
            end = n                       # trailing segment without terminator
        raw = text[pos:end]
        elements, offsets = [], []
        off = pos
        for fld in raw.split(ele):
            elements.append(fld)
            offsets.append(off)
            off += len(fld) + 1           # + the element separator
        yield (elements[0] if elements else ""), elements, offsets, pos, raw
        pos = end + len(seg)


@dataclass
class X12Ref:
    kind: str                       # "N7" | "N9EQ"
    label: str                      # "N7/N7-18(761)" | "N9*EQ"
    seg_start: int
    raw_segment: str
    # N7 split form:
    initial: Optional[str] = None
    number: Optional[str] = None
    check_value: Optional[str] = None     # N7-18 value; "" = empty slot present; None = slot absent
    check_offset: Optional[int] = None    # byte offset of N7-18 value/slot; None if slot absent
    description: str = ""                 # N7-22 (size/type) -- context only
    # N9*EQ full token:
    token: Optional[str] = None
    token_offset: Optional[int] = None


def locate_equipment(text: str) -> List[X12Ref]:
    delims = detect_delims(text)
    refs: List[X12Ref] = []
    for tag, els, offs, seg_start, raw in iter_segments(text, delims):
        if tag == "N7":
            has18 = len(els) > 18
            refs.append(X12Ref(
                kind="N7", label="N7/N7-18(761)", seg_start=seg_start, raw_segment=raw,
                initial=els[1] if len(els) > 1 else "",
                number=els[2] if len(els) > 2 else "",
                check_value=(els[18] if has18 else None),
                check_offset=(offs[18] if has18 else None),
                description=els[22] if len(els) > 22 else "",
            ))
        elif tag == "N9" and len(els) > 1 and els[1] == "EQ":
            refs.append(X12Ref(
                kind="N9EQ", label="N9*EQ", seg_start=seg_start, raw_segment=raw,
                token=els[2] if len(els) > 2 else "",
                token_offset=offs[2] if len(els) > 2 else None,
            ))
    return refs


def evaluate_text(text: str, *, owner_policy: str = "strict", policy=None
                  ) -> List[Tuple[X12Ref, CorrectionResult]]:
    """
    Locate container identifiers and evaluate each via the kernel:
      * N7   -> correct_x12_equipment(N7-01, N7-02, N7-18)
      * N9EQ -> correct_identifier(token, EQUIPMENT_ID)
    """
    if owner_policy not in _OWNER_POLICIES:
        raise ValueError(
            f"owner_policy must be one of {sorted(_OWNER_POLICIES)}, got {owner_policy!r}")
    out: List[Tuple[X12Ref, CorrectionResult]] = []
    for ref in locate_equipment(text):
        if ref.kind == "N7":
            # check_value None (slot absent) and "" (empty slot) both mean "not transmitted"
            printed = ref.check_value if ref.check_value else None
            r = correct_x12_equipment(ref.initial or "", ref.number or "", printed, policy=policy)
        else:  # N9EQ
            r = correct_identifier(ref.token or "", FieldContext.EQUIPMENT_ID,
                                   owner_policy=owner_policy, policy=policy)
        out.append((ref, r))
    return out
