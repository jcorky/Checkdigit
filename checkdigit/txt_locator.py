"""
txt_locator.py
==============
Pass 7, format #4: locate container identifiers in free / unstructured text
(plain .txt dumps, yard-report pastes, TSV/CSV exports, logs).

This is the LOW-TRUST path. Unlike EDIFACT/SNX/X12, plain text has no schema
declaring "this field is a container number", so a structurally-valid match
could be coincidental. The kernel's FREE_TEXT context handles that: a
check-failing token is FLAGGED rather than auto-corrected, UNLESS its owner
prefix is corroborated against a registry (supplied later by the enrichment
layer) or the caller explicitly opts into trusting the source.

Detection: the ISO 6346 shape -- 4 letters (owner + category) followed by 7
digits -- scanned GLOBALLY as fixed 11-char, non-overlapping matches so that:
  * back-to-back numbers with no delimiter are all found
    (APLU3280480MSKU4570605APLU8266451 -> three),
  * trailing punctuation / whitespace is excluded,
  * numbers inside tab/comma-delimited tables are found in place.
ISO 6346 size/type codes (42R1, 42T6, L5G1, ...) do not fit the shape and are
never matched. Category is left to the kernel: U/J/Z -> ISO6346, A/B/D/E/K ->
ILU, anything else -> flagged as a non-standard category.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

from equipment_checkdigit import (
    correct_identifier, CorrectionResult, FieldContext, _OWNER_POLICIES,
)

# 4 letters + 7 digits. Permissive on the category letter (kernel classifies);
# no look-behind, so concatenated numbers (preceded by a digit) are still found.
_RE_BIC = re.compile(r"[A-Z]{4}[0-9]{7}")


@dataclass
class TxtRef:
    eqid: str
    offset: int        # byte offset of the match in the source
    line: int          # 1-based line number
    col: int           # 1-based column


def _line_col(text: str, offset: int) -> Tuple[int, int]:
    line = text.count("\n", 0, offset) + 1
    last_nl = text.rfind("\n", 0, offset)
    return line, offset - last_nl            # last_nl == -1 -> col = offset + 1


def locate_equipment(text: str) -> List[TxtRef]:
    refs: List[TxtRef] = []
    for m in _RE_BIC.finditer(text):
        line, col = _line_col(text, m.start())
        refs.append(TxtRef(eqid=m.group(0), offset=m.start(), line=line, col=col))
    return refs


def evaluate_text(text: str, *, trust: bool = False, owner_policy: str = "strict",
                  known_owner_prefixes: Optional[Set[str]] = None, policy=None
                  ) -> List[Tuple[TxtRef, CorrectionResult]]:
    """
    Locate and evaluate each match. By default the source is LOW-TRUST
    (FieldContext.FREE_TEXT): a failing check digit is flagged, not corrected,
    unless `known_owner_prefixes` corroborates the owner. Set `trust=True` to
    treat matches as declared equipment ids (FieldContext.EQUIPMENT_ID), e.g.
    when the user confirms the file is a container list.
    """
    if owner_policy not in _OWNER_POLICIES:
        raise ValueError(
            f"owner_policy must be one of {sorted(_OWNER_POLICIES)}, got {owner_policy!r}")
    ctx = FieldContext.EQUIPMENT_ID if trust else FieldContext.FREE_TEXT
    out: List[Tuple[TxtRef, CorrectionResult]] = []
    for ref in locate_equipment(text):
        out.append((ref, correct_identifier(
            ref.eqid, ctx, owner_policy=owner_policy,
            known_owner_prefixes=known_owner_prefixes, policy=policy)))
    return out
