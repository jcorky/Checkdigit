"""
substitution.py
===============
Generic, format-agnostic surgical substitution primitive used by every format's
corrector (EDIFACT / SNX / X12 / .txt). It applies a set of offset edits to the
source text, VERIFYING each edit against the exact characters it expects to
replace, so a stale or wrong offset fails loud (SubstitutionError) instead of
corrupting the file. Only the spans named by the edits change; every other
character is preserved verbatim.

This is the single place where source text is mutated. Correction *decisions*
live in the kernel; *locating* lives in each format's locator; this module only
splices.

Offset semantics
----------------
Every offset in this package is an index into the DECODED source text, counted
in Unicode code points (Python ``str`` indexing), zero-based. It is not a byte
offset and not a UTF-16 index:

* byte offset   -- depends on the encoding; use ``byte_offset()`` when a caller
                   needs to address the original bytes (it equals the code-point
                   index only for single-byte encodings or pure-ASCII prefixes);
* UTF-16 index  -- what JavaScript ``String`` indexing uses; differs from the
                   code-point index after any character outside the Basic
                   Multilingual Plane (emoji, some CJK); use ``utf16_index()``.

Locators compute offsets on the decoded text and the same text is spliced, so
edits stay consistent regardless of the encoding. Anything that serializes an
offset for another runtime must state which kind it is (see
``correction_report.OFFSET_KIND``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Edit:
    start: int      # code-point offset in the source text where `old` begins
    old: str        # exact substring expected at [start, start+len(old)) -- verified
    new: str        # replacement text


class SubstitutionError(ValueError):
    """Raised when an edit cannot be applied safely (overlap or verification miss)."""


def apply_edits(text: str, edits: List[Edit]) -> str:
    """
    Apply edits by splicing. Edits may be supplied in any order; they must not
    overlap. Each edit is verified: text[start:start+len(old)] must equal `old`,
    else SubstitutionError is raised -- never a blind write. `old` and `new` may
    differ in length; characters outside the edited spans are untouched.
    """
    ordered = sorted(edits, key=lambda e: e.start)
    parts: List[str] = []
    cursor = 0
    for e in ordered:
        if e.start < cursor:
            raise SubstitutionError(
                f"overlapping edit at offset {e.start} (previous edit ended at {cursor})")
        actual = text[e.start:e.start + len(e.old)]
        if actual != e.old:
            raise SubstitutionError(
                f"edit verification failed at offset {e.start}: "
                f"expected {e.old!r}, found {actual!r}")
        parts.append(text[cursor:e.start])
        parts.append(e.new)
        cursor = e.start + len(e.old)
    parts.append(text[cursor:])
    return "".join(parts)


def changed_indices(a: str, b: str) -> List[int]:
    """Indices where two equal-length strings differ. Used as a non-corruption proof."""
    if len(a) != len(b):
        raise ValueError("changed_indices requires equal-length strings")
    return [i for i, (x, y) in enumerate(zip(a, b)) if x != y]


def byte_offset(text: str, index: int, encoding: str) -> int:
    """Byte offset of code-point `index` of `text` once encoded with `encoding`."""
    if index < 0 or index > len(text):
        raise ValueError(f"index {index} outside text of length {len(text)}")
    return len(text[:index].encode(encoding))


def utf16_index(text: str, index: int) -> int:
    """UTF-16 code-unit index (JavaScript string index) of code-point `index`."""
    if index < 0 or index > len(text):
        raise ValueError(f"index {index} outside text of length {len(text)}")
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text[:index])
