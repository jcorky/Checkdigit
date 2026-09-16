"""
fixedwidth_corrector.py
=======================
Pass 17 orchestration for fixed-width flat files: locate -> evaluate -> splice ->
report. Column-range trust (see fixedwidth_locator). Whole-token byte
substitution preserves padding, line endings, and every other column verbatim.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Optional, Sequence, Set, Tuple

from equipment_checkdigit import Status
from fixedwidth_locator import ColRange, evaluate_fixedwidth
from substitution import Edit, apply_edits
from correction_report import (Change, Occurrence, Flag, CorrectionReport,
                               inventory_from_results)


def correct_fixedwidth(text: str, ranges: Sequence[ColRange], *, trust: bool = True,
                       owner_policy: str = "strict", header_lines: int = 0,
                       whole_field: bool = True,
                       known_owner_prefixes: Optional[Set[str]] = None, policy=None) -> CorrectionReport:
    pairs = evaluate_fixedwidth(
        text, ranges, trust=trust, owner_policy=owner_policy,
        header_lines=header_lines, whole_field=whole_field,
        known_owner_prefixes=known_owner_prefixes, policy=policy)

    groups: "OrderedDict[str, list]" = OrderedDict()
    for ref, r in pairs:
        groups.setdefault(ref.eqid, []).append((ref, r))

    edits: list[Edit] = []
    changes: list[Change] = []
    flags: list[Flag] = []
    valid = invalid = 0

    for eqid, items in groups.items():
        r0 = items[0][1]
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
        occ = []
        for ref, r in items:
            edits.append(Edit(ref.offset, eqid, r.corrected))
            occ.append(Occurrence(offset=ref.offset,
                                  label=f"line {ref.line} col {ref.col_start}",
                                  before=eqid, after=r.corrected))
        changes.append(Change(
            old=eqid, new=r0.corrected, printed_check=r0.printed_check or "",
            computed_check=r0.computed_check or "", id_type=r0.id_type.value,
            occurrences=occ, reason=r0.reason))

    corrected_text = apply_edits(text, edits)
    return CorrectionReport(
        owner_policy=owner_policy, total_containers=len(groups),
        corrected=changes, flagged=flags, valid=valid, empty_id=0, invalid=invalid,
        corrected_text=corrected_text,
        containers=inventory_from_results(
            [(r, r.status.value) for _, r in pairs if r is not None]),
    )
