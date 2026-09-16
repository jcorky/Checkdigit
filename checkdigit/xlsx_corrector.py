"""
xlsx_corrector.py
=================
Correct container check digits inside an .xlsx workbook.

Model (see xlsx_locator for the rationale): only the text of matched tokens
inside xl/sharedStrings.xml and inline strings in xl/worksheets/sheet*.xml is
byte-spliced; every other ZIP member's CONTENT is written back verbatim. The
ZIP container itself is necessarily re-assembled (member offsets shift), so
member metadata (timestamps) is not preserved -- member bytes are.

Semantics are .txt parity: cell text is low-trust FREE_TEXT unless trust=True
(then EQUIPMENT_ID), same owner_policy and known_owner_prefixes corroboration,
same token regex. A shared string referenced by N cells is ONE physical string:
correcting it corrects every referencing cell, reported as kept-in-sync
occurrences (exactly the SNX synced-attribute concept).

Never auto-corrected, always flagged with the computed check: formula-cached
values (Excel recomputes them), tokens split across rich-text runs, and any
parser-visible-but-unaddressable token.

Output: CorrectionReport with corrected_b64 (base64 of the rebuilt .xlsx) and
corrected_text=None.
"""
from __future__ import annotations

import base64
import io
import zipfile
from collections import OrderedDict
from typing import Dict, List, Optional, Set, Tuple

from equipment_checkdigit import FieldContext, Status, correct_identifier
from substitution import Edit, apply_edits
from correction_report import (Change, CorrectionReport, Flag, Occurrence,
                               inventory_from_results)
from xlsx_locator import WorkbookParts, XlsxFlagRef, XlsxRef, load_workbook, locate


def _snippet(span_text: str, token: str, replacement: str) -> Tuple[str, str]:
    """before/after context limited to the text node, trimmed around the token."""
    i = span_text.find(token)
    lo, hi = max(0, i - 18), min(len(span_text), i + len(token) + 18)
    pre = ("…" if lo > 0 else "") + span_text[lo:i]
    post = span_text[i + len(token):hi] + ("…" if hi < len(span_text) else "")
    return pre + token + post, pre + replacement + post


def correct_workbook(data: bytes, *, trust: bool = False,
                     owner_policy: str = "strict",
                     known_owner_prefixes: Optional[Set[str]] = None, policy=None
                     ) -> CorrectionReport:
    parts = load_workbook(data)
    refs, flag_refs = locate(parts)
    ctx = FieldContext.EQUIPMENT_ID if trust else FieldContext.FREE_TEXT

    # One verdict per distinct token (txt parity), applied to every occurrence.
    groups: "OrderedDict[str, List[XlsxRef]]" = OrderedDict()
    for ref in refs:
        groups.setdefault(ref.token, []).append(ref)

    changes: List[Change] = []
    flags: List[Flag] = []
    dispositions: List[Tuple[object, str]] = []
    edits_by_member: Dict[str, List[Edit]] = {}
    valid = invalid = 0

    for token, items in groups.items():
        r = correct_identifier(token, ctx, owner_policy=owner_policy,
                               known_owner_prefixes=known_owner_prefixes, policy=policy)
        if r.status is Status.VALID:
            valid += 1
            dispositions.append((r, r.status.value))
            continue
        if r.status is Status.INVALID_STRUCTURE:
            invalid += 1
            dispositions.append((r, r.status.value))
            continue
        if r.status is Status.FLAGGED:
            flags.append(Flag(offset=items[0].offset, eqid=token,
                              suggested_check=r.computed_check or "",
                              reason=f"{items[0].label}: {r.reason}",
                              occurrences=sum(max(1, len(i.cells)) for i in items)))
            dispositions.append((r, r.status.value))
            continue
        # CORRECTED: splice every addressable occurrence; a shared-string splice
        # corrects every referencing cell at once (synced occurrences).
        occ: List[Occurrence] = []
        for ref in items:
            edits_by_member.setdefault(ref.member, []).append(
                Edit(ref.offset, token, r.corrected))
            before, after = _snippet(ref.span_text, token, r.corrected)
            occ.append(Occurrence(offset=ref.offset, label=ref.label,
                                  before=before, after=after))
        changes.append(Change(old=token, new=r.corrected,
                              printed_check=r.printed_check or "",
                              computed_check=r.computed_check or "",
                              id_type=r.id_type.value, occurrences=occ,
                              reason=r.reason))
        dispositions.append((r, r.status.value))

    # Locate-only refs: evaluated for the verdict, NEVER spliced.
    _NOTE = {"formula_cached": "formula-cached value; Excel recomputes it -- fix the "
                               "source data/formula instead",
             "split_runs": "token is split across rich-text runs; splicing across "
                           "runs is unsafe",
             "unaddressable": "located by the XML parser but not addressable in the "
                              "raw bytes"}
    for fref in flag_refs:
        r = correct_identifier(fref.token, ctx, owner_policy=owner_policy,
                               known_owner_prefixes=known_owner_prefixes, policy=policy)
        if r.status is Status.VALID:
            valid += 1
            dispositions.append((r, r.status.value))
            continue
        suggested = r.computed_check or ""
        flags.append(Flag(offset=fref.offset, eqid=fref.token,
                          suggested_check=suggested,
                          reason=f"{fref.label}: {_NOTE[fref.note]}; "
                                 f"not auto-corrected"
                                 + (f" (computed check {suggested})" if suggested else ""),
                          occurrences=1))
        dispositions.append((r, "flagged"))           # final disposition overrides

    # Splice edited members; copy every other member's content verbatim.
    edited: Dict[str, bytes] = {}
    for member, edits in edits_by_member.items():
        text = parts.members[member].decode("utf-8")
        edited[member] = apply_edits(text, edits).encode("utf-8")  # fail-loud verify

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zo:
        for name in parts.names:
            zi = zipfile.ZipInfo(name)                # fixed epoch; bytes matter, not mtimes
            zi.compress_type = parts.compress.get(name, zipfile.ZIP_DEFLATED)
            zo.writestr(zi, edited.get(name, parts.members[name]))

    return CorrectionReport(
        owner_policy=owner_policy,
        total_containers=len(groups) + len({f.token for f in flag_refs} - set(groups)),
        corrected=changes, flagged=flags, valid=valid, empty_id=0, invalid=invalid,
        corrected_text=None,
        corrected_b64=base64.b64encode(out.getvalue()).decode("ascii"),
        containers=inventory_from_results(dispositions),
    )
