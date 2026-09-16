"""
correction_report.py
=====================
Shared, format-agnostic result types for the correction layer. EDIFACT / SNX /
X12 / .txt correctors all populate these, so no single format owns the report
shape and the web API has one stable JSON contract (CorrectionReport.to_dict()).

A `Change` carries one or more `Occurrence`s: a single identifier may be
denormalized across several byte positions that must stay in sync (e.g. SNX,
where one container number lives in four attributes). EDIFACT changes have a
single occurrence; SNX changes have N.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Occurrence:
    offset: int      # byte offset of the identifier VALUE in the source
    label: str       # where it lives: "EQD/C237/8260", "container/@eqid", "unit/@unique-key", ...
    before: str      # short context snippet, before
    after: str       # short context snippet, after


@dataclass
class Change:
    old: str
    new: str
    printed_check: str
    computed_check: str
    id_type: str
    occurrences: List[Occurrence]    # 1 for EDIFACT; N kept-in-sync for SNX
    reason: str

    @property
    def offsets(self) -> List[int]:
        return [o.offset for o in self.occurrences]


@dataclass
class Flag:
    offset: int                      # representative offset (-1 if unknown)
    eqid: str
    suggested_check: str
    reason: str
    occurrences: int = 1             # how many in-sync positions this id has


@dataclass
class ContainerRecord:
    """One distinct container as seen in a file (the registry / audit unit)."""
    as_found: str          # the token as it appeared (may carry a wrong check digit)
    canonical: str         # corrected form when known, else as-found (physical identity)
    owner: str             # 3-letter owner prefix (ISO 6346 / ILU only)
    category: str          # category letter (ISO 6346 / ILU only)
    id_type: str           # iso6346 | ilu | uic | unknown
    status: str            # valid | corrected | flagged | invalid_structure
    printed_check: str
    computed_check: str
    occurrences: int
    # Near-miss candidates from previously-seen, check-valid containers; attached
    # by the service layer for flagged/invalid items only. Distance 0 = exact body
    # match (only the check digit differed). Proposals, never applied.
    near_misses: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class CorrectionReport:
    owner_policy: str
    total_containers: int
    corrected: List[Change]
    flagged: List[Flag]
    valid: int
    empty_id: int
    invalid: int
    # Exactly one of the two output fields is set: corrected_text for text
    # formats (EDIFACT/X12/SNX/txt), corrected_b64 (base64 of the rebuilt
    # binary, e.g. an .xlsx ZIP) for binary formats.
    corrected_text: Optional[str]
    corrected_b64: Optional[str] = None
    containers: List[ContainerRecord] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.corrected)

    def summary(self) -> Dict[str, int]:
        return {
            "containers": self.total_containers,
            "corrected": len(self.corrected),
            "flagged": len(self.flagged),
            "valid": self.valid,
            "empty_id": self.empty_id,
            "invalid": self.invalid,
        }

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serializable form (the web API will return this shape)."""
        return asdict(self)


def inventory_from_results(dispositions) -> List[ContainerRecord]:
    """
    Build the distinct-container inventory from (CorrectionResult, final_status)
    pairs, where `final_status` is the corrector's FINAL decision for that
    identifier (which can differ from the kernel verdict -- e.g. an X12 number
    the kernel would correct but the corrector flags because there is no N7-18
    slot to write). Groups by (canonical, as-found, final_status) and counts
    occurrences. `canonical` is the corrected form when known, else the
    normalized as-found token; owner/category are filled for ISO 6346 / ILU only.
    """
    groups: "OrderedDict[tuple, ContainerRecord]" = OrderedDict()
    for r, final_status in dispositions:
        canonical = r.corrected or r.normalized
        is_bic = r.id_type.value in ("iso6346", "ilu") and len(canonical) >= 4
        owner = canonical[:3] if is_bic else ""
        category = canonical[3] if is_bic else ""
        key = (canonical, r.normalized, final_status)
        rec = groups.get(key)
        if rec is None:
            groups[key] = ContainerRecord(
                as_found=r.normalized, canonical=canonical, owner=owner, category=category,
                id_type=r.id_type.value, status=final_status,
                printed_check=r.printed_check or "", computed_check=r.computed_check or "",
                occurrences=1)
        else:
            rec.occurrences += 1
    return list(groups.values())
