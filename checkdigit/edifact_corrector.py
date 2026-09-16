"""
edifact_corrector.py
====================
Pass 4 orchestration for EDIFACT: locate -> evaluate -> substitute -> report.

Wires existing layers only -- no check-digit math, no parsing of its own:
  * correction decisions  -> equipment_checkdigit (kernel)
  * locating identifiers  -> edifact_locator
  * byte splicing         -> substitution
  * result shape          -> correction_report (shared across formats)

An EDIFACT container number occupies a single byte span (C237/8260), so each
Change here has exactly one Occurrence.
"""
from __future__ import annotations

from equipment_checkdigit import Status
from edifact_locator import evaluate_text
from substitution import Edit, apply_edits
from correction_report import Change, Occurrence, Flag, CorrectionReport, inventory_from_results


def correct_edifact(text: str, *, owner_policy: str = "strict", policy=None) -> CorrectionReport:
    pairs = evaluate_text(text, owner_policy=owner_policy, policy=policy)   # owner_policy validated here

    edits: list[Edit] = []
    changes: list[Change] = []
    flags: list[Flag] = []
    valid = empty = invalid = 0

    for ref, r in pairs:
        if r is None:                                  # EQD+CN with empty 8260
            empty += 1
            continue
        if r.status is Status.CORRECTED:
            segment_after = ref.raw_segment.replace(ref.eqid, r.corrected, 1)
            edits.append(Edit(ref.eqid_offset, ref.eqid, r.corrected))
            changes.append(Change(
                old=ref.eqid, new=r.corrected,
                printed_check=r.printed_check or "", computed_check=r.computed_check or "",
                id_type=r.id_type.value,
                occurrences=[Occurrence(
                    offset=ref.eqid_offset, label="EQD/C237/8260",
                    before=ref.raw_segment, after=segment_after)],
                reason=r.reason,
            ))
        elif r.status is Status.VALID:
            valid += 1
        elif r.status is Status.FLAGGED:
            flags.append(Flag(
                offset=ref.eqid_offset if ref.eqid_offset is not None else -1,
                eqid=ref.eqid, suggested_check=r.computed_check or "", reason=r.reason))
        else:                                          # INVALID_STRUCTURE
            invalid += 1

    corrected_text = apply_edits(text, edits)          # verifies every offset; fail-loud
    return CorrectionReport(
        owner_policy=owner_policy, total_containers=len(pairs),
        corrected=changes, flagged=flags,
        valid=valid, empty_id=empty, invalid=invalid,
        corrected_text=corrected_text,
        containers=inventory_from_results([(r, r.status.value) for _, r in pairs if r is not None]),
    )
