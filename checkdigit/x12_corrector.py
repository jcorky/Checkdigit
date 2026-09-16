"""
x12_corrector.py
================
Pass 6 orchestration for X12: locate -> evaluate -> substitute -> report.

X12 has two correction shapes:
  * N7  -- the check digit is a dedicated element (N7-18, element 761). We write
           that element only. Three sub-cases, fail-loud:
             present & wrong  -> replace the element value in place
             present & empty  -> insert the digit into the empty slot
             ABSENT (segment too short) -> FLAG, do NOT restructure the segment
           (N7-01/N7-02 -- the body -- are the source of truth and are never
            altered; only N7-18 is.)
  * N9*EQ -- a full 11-char token; replace it whole (only the check digit differs).

Wires existing layers only. Byte-splicing preserves everything else verbatim.
"""
from __future__ import annotations

from equipment_checkdigit import Status
from x12_locator import evaluate_text, detect_delims
from substitution import Edit, apply_edits
from correction_report import Change, Occurrence, Flag, CorrectionReport, inventory_from_results


def _body10(initial: str, number: str) -> str:
    init = (initial or "").upper()
    num = "".join(c for c in (number or "") if c.isdigit()).zfill(6)[-6:]
    return init + num


def correct_x12(text: str, *, owner_policy: str = "strict", policy=None) -> CorrectionReport:
    ele = detect_delims(text).element
    pairs = evaluate_text(text, owner_policy=owner_policy, policy=policy)

    edits: list[Edit] = []
    changes: list[Change] = []
    flags: list[Flag] = []
    disp: list = []                                    # (result, final_status) for the inventory
    valid = invalid = 0

    for ref, r in pairs:
        if r.status is Status.VALID:
            valid += 1
            disp.append((r, "valid"))
            continue
        if r.status is Status.INVALID_STRUCTURE:
            invalid += 1
            disp.append((r, "invalid_structure"))
            continue
        if r.status is Status.FLAGGED:
            off = (ref.check_offset if ref.kind == "N7" else ref.token_offset)
            eqid = _body10(ref.initial, ref.number) if ref.kind == "N7" else (ref.token or "")
            flags.append(Flag(offset=off if off is not None else ref.seg_start,
                              eqid=eqid, suggested_check=r.computed_check or "", reason=r.reason))
            disp.append((r, "flagged"))
            continue

        # ---- CORRECTED ----
        if ref.kind == "N7":
            if ref.check_offset is None:
                # N7-18 element absent -> writing it would mean inserting elements.
                flags.append(Flag(
                    offset=ref.seg_start, eqid=_body10(ref.initial, ref.number),
                    suggested_check=r.computed_check or "",
                    reason=(f"N7-18 (element 761) is absent; populating it would require "
                            f"inserting elements. Suggested check {r.computed_check}; "
                            f"not auto-applied.")))
                disp.append((r, "flagged"))            # corrector's final decision
                continue
            old = ref.check_value or ""               # "" => insert into empty slot
            new = r.computed_check or ""
            els = ref.raw_segment.split(ele)
            after_seg = ele.join(els[:18] + [new] + els[19:])
            body = _body10(ref.initial, ref.number)
            edits.append(Edit(ref.check_offset, old, new))
            changes.append(Change(
                old=body + (r.printed_check or "_"), new=body + new,
                printed_check=r.printed_check or "", computed_check=new,
                id_type=r.id_type.value,
                occurrences=[Occurrence(offset=ref.check_offset, label=ref.label,
                                        before=ref.raw_segment, after=after_seg)],
                reason=r.reason))
            disp.append((r, "corrected"))
        else:  # N9EQ whole-token
            old, new = ref.token or "", r.corrected or ""
            after_seg = ref.raw_segment.replace(old, new, 1)
            edits.append(Edit(ref.token_offset, old, new))
            changes.append(Change(
                old=old, new=new,
                printed_check=r.printed_check or "", computed_check=r.computed_check or "",
                id_type=r.id_type.value,
                occurrences=[Occurrence(offset=ref.token_offset, label=ref.label,
                                        before=ref.raw_segment, after=after_seg)],
                reason=r.reason))
            disp.append((r, "corrected"))

    corrected_text = apply_edits(text, edits)         # verifies every offset; fail-loud
    return CorrectionReport(
        owner_policy=owner_policy, total_containers=len(pairs),
        corrected=changes, flagged=flags, valid=valid, empty_id=0, invalid=invalid,
        corrected_text=corrected_text,
        containers=inventory_from_results(disp),
    )
