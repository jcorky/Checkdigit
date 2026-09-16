"""
csv_locator.py
==============
Pass 17, format #6: locate container identifiers in delimited tables (CSV/TSV)
with COLUMN-SCOPED TRUST.

Why this exists when the free-text scanner already finds tokens inside CSVs:
the txt path is blanket low-trust -- every match is FLAGGED unless an owner
registry corroborates it, because plain text has no schema. A CSV *does* carry
structure: if the user says "column 3 is the container number", those cells are
declared equipment ids and a wrong check digit there should be CORRECTED, while
a stray 11-char token in a free-text remarks column should NOT be. This parser
provides that distinction.

It is therefore OPT-IN, never auto-detected. The dispatcher only routes here
when the caller supplies a format hint + a column spec; absent that, a .csv
falls through to the safe free-text path unchanged.

Parsing is RFC 4180-aware (quoted fields, escaped quotes "", embedded
delimiters and newlines inside quotes) but records BYTE OFFSETS into the
original text so corrections are spliced in place -- the file is never
re-serialized, exactly as with every other format. Only the *value bytes* of a
matched token inside a targeted cell are replaced; quoting, delimiters, line
endings (LF or CRLF), and every other cell are preserved verbatim.

Column targeting accepts either 0-based indices or header names (when the first
row is a header). A token must occupy the whole cell value (after optional
surrounding whitespace) OR be found within it -- see `whole_cell`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Set, Tuple, Union

from equipment_checkdigit import (
    correct_identifier, CorrectionResult, FieldContext, _OWNER_POLICIES)
from txt_locator import _RE_BIC

ColumnSel = Union[int, str]


class CsvError(ValueError):
    """Structural problem with the column spec (e.g. named column, no header)."""


@dataclass
class CsvCell:
    """One parsed field with the byte span of its VALUE in the source text."""
    row: int                 # 0-based record index (header counts as row 0 if present)
    col: int                 # 0-based field index
    value: str               # decoded field value (quotes removed, "" unescaped)
    val_start: int           # byte offset where the *raw* field text begins
    val_end: int             # byte offset just past the raw field text
    quoted: bool             # whether the raw field was quoted


@dataclass
class CsvRef:
    eqid: str
    offset: int              # absolute byte offset of the token in the source
    row: int
    col: int
    cell_quoted: bool


def sniff_delimiter(text: str, override: Optional[str] = None) -> str:
    """Pick a delimiter. Honors an explicit override; else compares comma vs tab
    vs semicolon vs pipe counts on the first non-empty line (most common wins)."""
    if override:
        return override
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    counts = {d: first.count(d) for d in (",", "\t", ";", "|")}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def parse_records(text: str, delimiter: str) -> List[List[CsvCell]]:
    """RFC 4180-aware parse that retains byte offsets for every field value.

    State machine over the raw characters; quoted fields may contain the
    delimiter, CR/LF, and escaped quotes ("" -> "). val_start/val_end bound the
    RAW field (including surrounding quotes if quoted) so the corrector can
    splice inside it precisely.
    """
    rows: List[List[CsvCell]] = []
    row: List[CsvCell] = []
    i, n = 0, len(text)
    field_start = 0
    buf: List[str] = []
    in_quotes = False
    quoted_field = False
    r = 0

    def end_field(end_pos: int) -> None:
        nonlocal buf, quoted_field
        row.append(CsvCell(row=r, col=len(row), value="".join(buf),
                           val_start=field_start, val_end=end_pos,
                           quoted=quoted_field))
        buf = []
        quoted_field = False

    while i < n:
        ch = text[i]
        if in_quotes:
            if ch == '"':
                if i + 1 < n and text[i + 1] == '"':   # escaped quote
                    buf.append('"')
                    i += 2
                    continue
                in_quotes = False
                i += 1
                continue
            buf.append(ch)
            i += 1
            continue
        # not in quotes
        if ch == '"' and i == field_start:
            in_quotes = True
            quoted_field = True
            i += 1
            continue
        if ch == delimiter:
            end_field(i)
            i += 1
            field_start = i
            continue
        if ch == "\r":
            end_field(i)
            rows.append(row)
            row = []
            r += 1
            i += 2 if (i + 1 < n and text[i + 1] == "\n") else 1
            field_start = i
            continue
        if ch == "\n":
            end_field(i)
            rows.append(row)
            row = []
            r += 1
            i += 1
            field_start = i
            continue
        buf.append(ch)
        i += 1

    # trailing field / row (file not ending in newline)
    if buf or row or field_start <= n:
        if i > field_start or buf or row:
            end_field(n)
            rows.append(row)
    # drop a spurious empty trailing row from a final newline
    if rows and len(rows[-1]) == 1 and rows[-1][0].value == "" \
            and rows[-1][0].val_start >= n:
        rows.pop()
    return rows


def resolve_columns(rows: Sequence[Sequence[CsvCell]], columns: Sequence[ColumnSel],
                    *, has_header: bool) -> List[int]:
    """Map a mix of indices and header names to 0-based column indices."""
    header = {}
    if has_header and rows:
        header = {c.value.strip().lower(): c.col for c in rows[0]}
    out: List[int] = []
    for sel in columns:
        if isinstance(sel, int):
            out.append(sel)
        else:
            key = sel.strip().lower()
            if key not in header:
                raise CsvError(
                    f"column {sel!r} not found in header row "
                    f"(have: {sorted(header) if header else 'no header parsed'})")
            out.append(header[key])
    return out


def locate_csv(text: str, columns: Sequence[ColumnSel], *, delimiter: Optional[str] = None,
               has_header: bool = True, whole_cell: bool = True
               ) -> Tuple[List[CsvRef], str]:
    """Find container tokens in the targeted columns. Returns (refs, delimiter).

    whole_cell=True (default): only consider a cell whose entire trimmed value is
    a single token -- the conservative reading of "this column holds container
    numbers". whole_cell=False: scan the targeted cells for embedded tokens too.
    Header row (row 0 when has_header) is never matched.
    """
    delim = sniff_delimiter(text, delimiter)
    rows = parse_records(text, delim)
    targets = set(resolve_columns(rows, columns, has_header=has_header))
    refs: List[CsvRef] = []
    for ri, rec in enumerate(rows):
        if has_header and ri == 0:
            continue
        for cell in rec:
            if cell.col not in targets:
                continue
            if whole_cell:
                stripped = cell.value.strip()
                if not _RE_BIC.fullmatch(stripped):
                    continue
                # locate the token's raw offset inside the field span
                raw = text[cell.val_start:cell.val_end]
                pos = raw.find(stripped)
                if pos < 0:
                    continue
                refs.append(CsvRef(eqid=stripped, offset=cell.val_start + pos,
                                   row=ri, col=cell.col, cell_quoted=cell.quoted))
            else:
                raw = text[cell.val_start:cell.val_end]
                for m in _RE_BIC.finditer(raw):
                    refs.append(CsvRef(eqid=m.group(0), offset=cell.val_start + m.start(),
                                       row=ri, col=cell.col, cell_quoted=cell.quoted))
    return refs, delim


def evaluate_csv(text: str, columns: Sequence[ColumnSel], *, trust: bool = True,
                 owner_policy: str = "strict", delimiter: Optional[str] = None,
                 has_header: bool = True, whole_cell: bool = True,
                 known_owner_prefixes: Optional[Set[str]] = None, policy=None
                 ) -> Tuple[List[Tuple[CsvRef, CorrectionResult]], str]:
    """Locate + evaluate targeted-column tokens. Default trust=True: a declared
    container column is authoritative, so failing checks are CORRECTED. Set
    trust=False to keep them advisory (flagged) like the free-text path."""
    if owner_policy not in _OWNER_POLICIES:
        raise ValueError(
            f"owner_policy must be one of {sorted(_OWNER_POLICIES)}, got {owner_policy!r}")
    refs, delim = locate_csv(text, columns, delimiter=delimiter,
                             has_header=has_header, whole_cell=whole_cell)
    ctx = FieldContext.EQUIPMENT_ID if trust else FieldContext.FREE_TEXT
    out = [(ref, correct_identifier(ref.eqid, ctx, owner_policy=owner_policy,
                                    known_owner_prefixes=known_owner_prefixes, policy=policy))
           for ref in refs]
    return out, delim
