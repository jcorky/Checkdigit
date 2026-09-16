"""
substitution.py
===============
Generic, format-agnostic surgical substitution primitive used by every format's
corrector (EDIFACT / SNX / X12 / .txt). It applies a set of byte-offset edits to
the source text, VERIFYING each edit against the exact bytes it expects to
replace, so a stale or wrong offset fails loud (SubstitutionError) instead of
corrupting the file. Only the spans named by the edits change; every other byte
is preserved verbatim.

This is the single place where source text is mutated. Correction *decisions*
live in the kernel; *locating* lives in each format's locator; this module only
splices.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Edit:
    start: int      # byte offset in the source where `old` begins
    old: str        # exact substring expected at [start, start+len(old)) -- verified
    new: str        # replacement text


class SubstitutionError(ValueError):
    """Raised when an edit cannot be applied safely (overlap or verification miss)."""


def apply_edits(text: str, edits: List[Edit]) -> str:
    """
    Apply edits by splicing. Edits may be supplied in any order; they must not
    overlap. Each edit is verified: text[start:start+len(old)] must equal `old`,
    else SubstitutionError is raised -- never a blind write. `old` and `new` may
    differ in length; bytes outside the edited spans are untouched.
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
