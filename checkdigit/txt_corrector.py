"""
txt_corrector.py
================
Pass 7 orchestration for plain text: locate -> evaluate -> substitute -> report.

The same container number can appear many times in a dump; each distinct number
becomes ONE Change whose Occurrences are every match offset, so a correction is
applied consistently to all of them. By default the source is low-trust, so
check-failing numbers are flagged (not corrected) unless `trust=True` or
`known_owner_prefixes` corroborates the owner. Whole-token byte substitution
preserves all surrounding text (delimiters, table layout, punctuation).
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Optional, Set

from equipment_checkdigit import Status
from txt_locator import evaluate_text
from substitution import Edit, apply_edits
from correction_report import Change, Occurrence, Flag, CorrectionReport, inventory_from_results


def correct_txt(text: str, *, trust: bool = False, owner_policy: str = "strict",
                known_owner_prefixes: Optional[Set[str]] = None, policy=None) -> CorrectionReport:
    pairs = evaluate_text(text, trust=trust, owner_policy=owner_policy,
                          known_owner_prefixes=known_owner_prefixes, policy=policy)

    groups: "OrderedDict[str, list]" = OrderedDict()
    for ref, r in pairs:
        groups.setdefault(ref.eqid, []).append((ref, r))

    edits: list[Edit] = []
    changes: list[Change] = []
    flags: list[Flag] = []
    valid = invalid = 0

    for eqid, items in groups.items():
        r0 = items[0][1]                               # same token -> same verdict
        if r0.status is Status.VALID:
            valid += 1
            continue
        if r0.status is Status.INVALID_STRUCTURE:
            invalid += 1
            continue
        if r0.status is Status.FLAGGED:
            flags.append(Flag(offset=items[0][0].offset, eqid=eqid,
                              suggested_check=r0.computed_check or "", reason=r0.reason,
                              occurrences=len(items)))
            continue
        # CORRECTED -> fix every occurrence
        occ = []
        for ref, r in items:
            edits.append(Edit(ref.offset, eqid, r.corrected))
            occ.append(Occurrence(offset=ref.offset, label=f"line {ref.line}:{ref.col}",
                                  before=eqid, after=r.corrected))
        changes.append(Change(
            old=eqid, new=r0.corrected, printed_check=r0.printed_check or "",
            computed_check=r0.computed_check or "", id_type=r0.id_type.value,
            occurrences=occ, reason=r0.reason))

    corrected_text = apply_edits(text, edits)          # verifies every offset; fail-loud
    return CorrectionReport(
        owner_policy=owner_policy, total_containers=len(groups),
        corrected=changes, flagged=flags, valid=valid, empty_id=0, invalid=invalid,
        corrected_text=corrected_text,
        containers=inventory_from_results([(r, r.status.value) for _, r in pairs if r is not None]),
    )
