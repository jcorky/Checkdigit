"""
dispatcher.py
=============
Detects the format of an uploaded payload from its CONTENT (not its file
extension) and routes it to the matching corrector. Detection is ordered and
fail-loud: an XML document that is not recognized container XML, and any input
we cannot confidently place, is reported rather than silently mishandled. Plain
text is the catch-all -- anything with no structured markers is scanned for bare
container numbers.

Returns (Detection, CorrectionReport). For unsupported input the report is None
and Detection.reason explains why; the service records that as a rejected event.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Set, Tuple

import edifact_corrector
import snx_corrector
import x12_corrector
import txt_corrector
from correction_report import CorrectionReport


@dataclass
class Detection:
    fmt: str            # "edifact" | "snx" | "x12" | "txt" | "unsupported"
    reason: str = ""


def detect_format(text: str) -> Detection:
    s = text.lstrip("\ufeff \t\r\n")
    head = s[:1000]

    # 1) XML -> terminal container XML ("SNX" family), matched structurally
    #    (namespace/prefix-agnostic: <snx>, <ns:snx>, discharge-list, container/unit)
    if s.startswith("<?xml") or s.startswith("<"):
        if (re.search(r"<(?:[A-Za-z][\w.-]*:)?snx[\s/>]", head)
                or "<line-discharge-list" in head
                or ("<container" in s and "eqid=" in s)
                or ("<unit" in s and "unique-key=" in s)):
            return Detection("snx")
        return Detection("unsupported", "XML detected but not a recognized container XML schema")

    # 2) EDIFACT -- service-string-advice or interchange/message header
    if s.startswith("UNA") or s.startswith("UNB") or "UNB+" in head or "UNH+" in head:
        return Detection("edifact")

    # 3) X12 -- interchange/group/transaction header with segment terminator
    if "~" in s and (s.startswith("ISA") or "ISA*" in head or "GS*" in head or "ST*" in head):
        return Detection("x12")

    # 4) plain text catch-all
    return Detection("txt")


def correct(text: str, *, owner_policy: str = "strict", trust: bool = False,
            known_owner_prefixes: Optional[Set[str]] = None, policy=None
            ) -> Tuple[Detection, Optional[CorrectionReport]]:
    det = detect_format(text)
    if det.fmt == "edifact":
        return det, edifact_corrector.correct_edifact(text, owner_policy=owner_policy, policy=policy)
    if det.fmt == "snx":
        return det, snx_corrector.correct_snx(text, owner_policy=owner_policy, policy=policy)
    if det.fmt == "x12":
        return det, x12_corrector.correct_x12(text, owner_policy=owner_policy, policy=policy)
    if det.fmt == "txt":
        return det, txt_corrector.correct_txt(
            text, trust=trust, owner_policy=owner_policy,
            known_owner_prefixes=known_owner_prefixes, policy=policy)
    return det, None        # unsupported


# ── opt-in structured-text formats (CSV/TSV, fixed-width) ─────────────────────
# These are NEVER auto-detected: a CSV of container numbers is valid plain text,
# and silently treating a column as trusted would violate the low-trust default.
# The caller opts in with a format hint + the column spec; absent that, such a
# file flows through the safe free-text path. parse_options carries the spec.
def correct_with_hint(text: str, *, format_hint: str, parse_options: Optional[dict] = None,
                      owner_policy: str = "strict", trust: bool = True,
                      known_owner_prefixes: Optional[Set[str]] = None, policy=None
                      ) -> Tuple[Detection, Optional[CorrectionReport]]:
    """Route an explicitly-hinted structured-text format. Supported hints:
       'csv'  : parse_options = {columns:[...], delimiter?, has_header?, whole_cell?}
       'fixed': parse_options = {ranges:[[start,end],...], header_lines?, whole_field?}
    Unknown hints fall back to content-based detection (correct())."""
    import csv_corrector
    import fixedwidth_corrector
    from csv_locator import CsvError
    from fixedwidth_locator import FixedWidthError
    opts = dict(parse_options or {})

    if format_hint == "csv":
        cols = opts.get("columns")
        if not cols:
            return Detection("unsupported",
                             "CSV hint requires parse_options.columns (indices or header names)"), None
        try:
            rep = csv_corrector.correct_csv(
                text, cols, trust=trust, owner_policy=owner_policy,
                delimiter=opts.get("delimiter"), has_header=opts.get("has_header", True),
                whole_cell=opts.get("whole_cell", True),
                known_owner_prefixes=known_owner_prefixes, policy=policy)
        except CsvError as exc:
            return Detection("unsupported", f"CSV column spec error: {exc}"), None
        return Detection("csv"), rep

    if format_hint in ("fixed", "fixedwidth", "fixed_width"):
        ranges = opts.get("ranges")
        if not ranges:
            return Detection("unsupported",
                             "fixed-width hint requires parse_options.ranges [[start,end],...]"), None
        try:
            rep = fixedwidth_corrector.correct_fixedwidth(
                text, [tuple(r) for r in ranges], trust=trust, owner_policy=owner_policy,
                header_lines=opts.get("header_lines", 0),
                whole_field=opts.get("whole_field", True),
                known_owner_prefixes=known_owner_prefixes, policy=policy)
        except FixedWidthError as exc:
            return Detection("unsupported", f"fixed-width spec error: {exc}"), None
        return Detection("fixed"), rep

    # unknown hint -> safe content-based path
    return correct(text, owner_policy=owner_policy, trust=trust,
                   known_owner_prefixes=known_owner_prefixes, policy=policy)


# ── binary front door ─────────────────────────────────────────────────────────
_ZIP_MAGIC = b"PK\x03\x04"


def detect_bytes(data: bytes) -> "Detection | None":
    """
    Sniff binary containers BEFORE any text decode (a ZIP 'decodes' fine under
    latin-1 and would otherwise be garbage-scanned as txt). Returns a Detection
    for recognized binary formats ('xlsx') or explicit unsupported binaries;
    returns None when the payload should continue down the text path.
    """
    if data[:4] == _ZIP_MAGIC:
        import io
        import zipfile
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = set(zf.namelist())
        except zipfile.BadZipFile:
            return Detection(fmt="unsupported", reason="corrupt ZIP archive")
        if "xl/workbook.xml" in names:
            return Detection(fmt="xlsx")
        if "word/document.xml" in names:
            return Detection(fmt="unsupported",
                             reason="Word document (.docx) is not a supported format")
        if "ppt/presentation.xml" in names:
            return Detection(fmt="unsupported",
                             reason="PowerPoint (.pptx) is not a supported format")
        return Detection(fmt="unsupported",
                         reason="ZIP archive that is not an Excel workbook")
    if data[:5] == b"%PDF-":
        return Detection(fmt="unsupported", reason="PDF is not a supported format")
    return None
