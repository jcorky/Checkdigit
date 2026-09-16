"""
snx_corrector.py
================
Pass 5 orchestration for SNX (terminal container XML): locate -> evaluate -> substitute
-> report. A corrected container number is replaced at EVERY attribute that
carries it (container/@eqid, equipment/@eqid, unit/@id, unit/@unique-key), so the
denormalized key stays in sync. Each such set is one Change with N Occurrences.

Wires existing layers only:
  * decisions -> equipment_checkdigit   * locating -> snx_locator
  * splicing  -> substitution           * shape    -> correction_report
The corrected text is produced by byte-splicing the validated source; the XML is
never re-serialized, so formatting/attribute-order/whitespace are preserved.
"""
from __future__ import annotations

from equipment_checkdigit import Status
from snx_locator import evaluate_text, size_type_codes
from substitution import Edit, apply_edits
from correction_report import Change, Occurrence, Flag, CorrectionReport, inventory_from_results


def correct_snx(text: str, *, owner_policy: str = "strict", policy=None) -> CorrectionReport:
    pairs = evaluate_text(text, owner_policy=owner_policy, policy=policy)   # owner_policy validated here

    edits: list[Edit] = []
    changes: list[Change] = []
    flags: list[Flag] = []
    valid = invalid = 0

    for ref, r in pairs:
        if r.status is Status.CORRECTED:
            occurrences = []
            for offset, label in ref.occurrences:
                edits.append(Edit(offset, ref.eqid, r.corrected))
                occurrences.append(Occurrence(
                    offset=offset, label=label,
                    before=f'{label.split("/@")[-1]}="{ref.eqid}"',
                    after=f'{label.split("/@")[-1]}="{r.corrected}"'))
            changes.append(Change(
                old=ref.eqid, new=r.corrected,
                printed_check=r.printed_check or "", computed_check=r.computed_check or "",
                id_type=r.id_type.value, occurrences=occurrences, reason=r.reason))
        elif r.status is Status.VALID:
            valid += 1
        elif r.status is Status.FLAGGED:
            off0 = ref.occurrences[0][0] if ref.occurrences else -1
            flags.append(Flag(offset=off0, eqid=ref.eqid,
                              suggested_check=r.computed_check or "", reason=r.reason,
                              occurrences=len(ref.occurrences)))
        else:                                              # INVALID_STRUCTURE
            invalid += 1

    corrected_text = apply_edits(text, edits)              # verifies every offset; fail-loud
    return CorrectionReport(
        owner_policy=owner_policy, total_containers=len(pairs),
        corrected=changes, flagged=flags,
        valid=valid, empty_id=0, invalid=invalid,
        corrected_text=corrected_text,
        containers=inventory_from_results([(r, r.status.value) for _, r in pairs if r is not None]),
    )
