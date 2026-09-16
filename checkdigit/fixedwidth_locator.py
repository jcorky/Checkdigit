"""
fixedwidth_locator.py
=====================
Pass 17, format #7: locate container identifiers in FIXED-WIDTH flat files with
COLUMN-RANGE TRUST.

Legacy terminal/mainframe exports often have no delimiters at all: each field
occupies a fixed character range on every line (e.g. container number in
columns 10-20). The free-text scanner finds the tokens, but blanket low-trust;
declaring the range makes those cells authoritative (corrected, not just
flagged), and avoids matching a coincidental 11-char run elsewhere on the line.

Opt-in, never auto-detected: the dispatcher routes here only when the caller
supplies a (start, end) column range. Ranges are specified as 1-based, inclusive
START and END columns -- the human-readable convention used in record layouts --
and converted internally to 0-based slice bounds. Byte offsets are computed per
line so corrections splice in place; line endings, padding, and every other
column are preserved verbatim. The file is never re-serialized.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Set, Tuple

from equipment_checkdigit import (
    correct_identifier, CorrectionResult, FieldContext, _OWNER_POLICIES)
from txt_locator import _RE_BIC


class FixedWidthError(ValueError):
    """Bad column range (start > end, non-positive, etc.)."""


# (start, end) 1-based inclusive columns.
ColRange = Tuple[int, int]


@dataclass
class FwRef:
    eqid: str
    offset: int          # absolute byte offset in the source
    line: int            # 1-based line number
    col_start: int       # 1-based column where the token begins


def _validate_ranges(ranges: Sequence[ColRange]) -> List[ColRange]:
    out = []
    for (a, b) in ranges:
        if a < 1 or b < 1 or a > b:
            raise FixedWidthError(
                f"invalid column range ({a},{b}); expected 1-based start<=end")
        out.append((a, b))
    return out


def _line_spans(text: str) -> List[Tuple[int, int, int]]:
    """(line_no_1based, abs_start_offset, content_len_excluding_newline)."""
    spans = []
    pos = 0
    for i, line in enumerate(text.splitlines(keepends=True), start=1):
        content = line.rstrip("\r\n")
        spans.append((i, pos, len(content)))
        pos += len(line)
    return spans


def locate_fixedwidth(text: str, ranges: Sequence[ColRange], *, header_lines: int = 0,
                      whole_field: bool = True) -> List[FwRef]:
    """Find container tokens within the given column ranges on each data line.

    whole_field=True: the trimmed slice must be exactly one token (the strict
    reading of "the container number lives in columns a-b"). whole_field=False:
    scan within the slice for embedded tokens. The first `header_lines` lines are
    skipped.
    """
    rngs = _validate_ranges(ranges)
    refs: List[FwRef] = []
    for line_no, abs_start, content_len in _line_spans(text):
        if line_no <= header_lines:
            continue
        for (a, b) in rngs:
            s0 = a - 1                       # 0-based slice start
            e0 = min(b, content_len)         # clamp to line content
            if s0 >= content_len or s0 >= e0:
                continue
            raw = text[abs_start + s0: abs_start + e0]
            if whole_field:
                stripped = raw.strip()
                if not _RE_BIC.fullmatch(stripped):
                    continue
                pos = raw.find(stripped)
                refs.append(FwRef(eqid=stripped, offset=abs_start + s0 + pos,
                                  line=line_no, col_start=s0 + pos + 1))
            else:
                for m in _RE_BIC.finditer(raw):
                    refs.append(FwRef(eqid=m.group(0),
                                      offset=abs_start + s0 + m.start(),
                                      line=line_no, col_start=s0 + m.start() + 1))
    return refs


def evaluate_fixedwidth(text: str, ranges: Sequence[ColRange], *, trust: bool = True,
                        owner_policy: str = "strict", header_lines: int = 0,
                        whole_field: bool = True,
                        known_owner_prefixes: Optional[Set[str]] = None, policy=None
                        ) -> List[Tuple[FwRef, CorrectionResult]]:
    if owner_policy not in _OWNER_POLICIES:
        raise ValueError(
            f"owner_policy must be one of {sorted(_OWNER_POLICIES)}, got {owner_policy!r}")
    ctx = FieldContext.EQUIPMENT_ID if trust else FieldContext.FREE_TEXT
    refs = locate_fixedwidth(text, ranges, header_lines=header_lines,
                             whole_field=whole_field)
    return [(ref, correct_identifier(ref.eqid, ctx, owner_policy=owner_policy,
                                     known_owner_prefixes=known_owner_prefixes, policy=policy))
            for ref in refs]
