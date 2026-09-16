"""
xlsx_locator.py
===============
Locate container identifiers inside an .xlsx workbook WITHOUT rewriting it.

An .xlsx is a ZIP of XML parts. The byte-splice discipline survives by moving
down one level: tokens are located inside <t> text nodes of exactly two part
families -- xl/sharedStrings.xml and xl/worksheets/sheet*.xml (inline strings)
-- and corrections are byte-spliced into THOSE MEMBERS ONLY. Every other member
(styles, charts, macros, pivots, media) is copied content-byte-identical on
rebuild. This is the SNX pattern (parse to validate, regex to locate offsets,
splice raw bytes) applied per ZIP member.

Cell text gets the same low-trust FREE_TEXT semantics as .txt scanning, with
the same token regex (imported, not copied, so they cannot drift).

Located but never auto-corrected (reported as flag refs instead):
  formula_cached -- <c t="str"><f>..</f><v>TOKEN</v>: the cached result of a
                    formula; editing the cache is futile (Excel recomputes) and
                    editing the formula is out of scope. Flag with the verdict.
  split_runs     -- a token split across rich-text runs in one <si>
                    (<r><t>MSKU735</t></r><r><t>1773</t></r>): the parser sees
                    it, the raw spans don't; splicing across runs is unsafe.
  unaddressable  -- the hardened parser finds a token that the raw-byte span
                    scan cannot address (encoding/namespace oddity). Loudly
                    flagged rather than silently skipped or unsafely edited.

XML security: every parsed member goes through the same DOCTYPE/ENTITY-rejecting
hardened parser as SNX (legitimate OOXML contains neither).
"""
from __future__ import annotations

import io
import re
import zipfile
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from snx_locator import XmlSecurityError, harden_and_parse
from txt_locator import _RE_BIC as _RE_TOKEN     # exact parity with .txt scanning


class XlsxError(ValueError):
    """Structural problem with the workbook (bad ZIP, missing parts, bad XML)."""


_ZIP_MAGIC = b"PK\x03\x04"
SHARED_PATH = "xl/sharedStrings.xml"
_SHEET_RE = re.compile(r"^xl/worksheets/sheet[^/]*\.xml$")
# Prefix-tolerant raw-XML helpers ("<t>", "<x:t>", attributes allowed).
_RE_T_SPAN = re.compile(r"<(?:[A-Za-z0-9]+:)?t(?:\s[^>]*)?>(.*?)</(?:[A-Za-z0-9]+:)?t>", re.S)
_RE_SI_OPEN = re.compile(r"<(?:[A-Za-z0-9]+:)?si[\s>]")
_RE_C_BLOCK = re.compile(r"<(?:[A-Za-z0-9]+:)?c\s[^>]*>.*?</(?:[A-Za-z0-9]+:)?c>", re.S)
_RE_ATTR_R = re.compile(r"""\br=["']([A-Z]+\d+)["']""")
_RE_INLINESTR = re.compile(r"""\bt=["']inlineStr["']""")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def is_zip(data: bytes) -> bool:
    return data[:4] == _ZIP_MAGIC


@dataclass
class WorkbookParts:
    names: List[str]                 # original member order, preserved on rebuild
    members: Dict[str, bytes]
    compress: Dict[str, int]         # per-member compression, preserved on rebuild
    sheet_paths: List[str]
    shared_path: Optional[str]


@dataclass
class XlsxRef:
    """One addressable token occurrence (safe to byte-splice)."""
    member: str
    offset: int                      # token offset within the member's text
    token: str
    kind: str                        # "shared" | "inline"
    label: str
    span_text: str                   # full text-node content (snippet source)
    span_start: int
    si_index: int = -1
    cells: List[str] = field(default_factory=list)


@dataclass
class XlsxFlagRef:
    """A located token that must never be auto-corrected in place."""
    member: str
    token: str
    label: str
    note: str                        # "formula_cached" | "split_runs" | "unaddressable"
    offset: int = -1


def load_workbook(data: bytes) -> WorkbookParts:
    if not is_zip(data):
        raise XlsxError("not a ZIP container")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        bad = zf.testzip()
    except zipfile.BadZipFile as exc:
        raise XlsxError(f"corrupt ZIP archive: {exc}") from exc
    if bad is not None:
        raise XlsxError(f"corrupt ZIP member: {bad!r}")
    names = zf.namelist()
    if "xl/workbook.xml" not in names:
        raise XlsxError("ZIP is not an Excel workbook (no xl/workbook.xml)")
    members = {n: zf.read(n) for n in names}
    compress = {i.filename: i.compress_type for i in zf.infolist()}
    sheets = sorted(n for n in names if _SHEET_RE.match(n))
    return WorkbookParts(names=names, members=members, compress=compress,
                         sheet_paths=sheets,
                         shared_path=SHARED_PATH if SHARED_PATH in names else None)


def _member_text(parts: WorkbookParts, name: str) -> str:
    try:
        return parts.members[name].decode("utf-8")
    except UnicodeDecodeError as exc:                 # OOXML is UTF-8; anything else is broken
        raise XlsxError(f"{name}: not valid UTF-8 ({exc})") from exc


def _t_spans(text: str) -> List[Tuple[int, str]]:
    """(content_start_offset, content) for every raw <t>...</t> span."""
    return [(m.start(1), m.group(1)) for m in _RE_T_SPAN.finditer(text)]


def _shared_cell_refs(parts: WorkbookParts) -> Dict[int, List[str]]:
    """shared-string index -> ['sheet1.xml!B2', ...] across all sheets."""
    refs: Dict[int, List[str]] = {}
    for sheet in parts.sheet_paths:
        root = harden_and_parse(_member_text(parts, sheet))
        short = sheet.rsplit("/", 1)[-1]
        for c in root.iter():
            if _local(c.tag) != "c" or c.get("t") != "s":
                continue
            v = next((ch for ch in c if _local(ch.tag) == "v"), None)
            if v is None or not (v.text or "").strip().isdigit():
                continue
            cell = c.get("r") or "?"
            refs.setdefault(int(v.text.strip()), []).append(f"{short}!{cell}")
    return refs


def _locate_shared(parts: WorkbookParts) -> Tuple[List[XlsxRef], List[XlsxFlagRef]]:
    if parts.shared_path is None:
        return [], []
    text = _member_text(parts, parts.shared_path)
    root = harden_and_parse(text)                     # structure + XXE gate
    refmap = _shared_cell_refs(parts)
    si_opens = [m.start() for m in _RE_SI_OPEN.finditer(text)]

    refs: List[XlsxRef] = []
    located_by_si: Dict[int, set] = {}
    for span_start, content in _t_spans(text):
        for m in _RE_TOKEN.finditer(content):
            idx = bisect_right(si_opens, span_start) - 1
            cells = refmap.get(idx, [])
            refs.append(XlsxRef(
                member=parts.shared_path, offset=span_start + m.start(),
                token=m.group(0), kind="shared",
                label=f"{parts.shared_path} si[{idx}] → "
                      f"{', '.join(cells) if cells else 'no referencing cells found'}",
                span_text=content, span_start=span_start, si_index=idx, cells=cells))
            located_by_si.setdefault(idx, set()).add(m.group(0))

    # Parse-vs-scan cross-check: tokens visible to the parser but not addressable
    # in raw spans (rich-text runs split them, or some oddity) are FLAGGED.
    flags: List[XlsxFlagRef] = []
    sis = [el for el in root.iter() if _local(el.tag) == "si"]
    for idx, si in enumerate(sis):
        full = "".join(si.itertext())
        for m in _RE_TOKEN.finditer(full):
            if m.group(0) in located_by_si.get(idx, set()):
                continue
            has_runs = any(_local(ch.tag) == "r" for ch in si)
            flags.append(XlsxFlagRef(
                member=parts.shared_path, token=m.group(0),
                label=f"{parts.shared_path} si[{idx}] → "
                      f"{', '.join(refmap.get(idx, [])) or 'no referencing cells found'}",
                note="split_runs" if has_runs else "unaddressable"))
    return refs, flags


def _locate_sheets(parts: WorkbookParts) -> Tuple[List[XlsxRef], List[XlsxFlagRef]]:
    refs: List[XlsxRef] = []
    flags: List[XlsxFlagRef] = []
    for sheet in parts.sheet_paths:
        text = _member_text(parts, sheet)
        root = harden_and_parse(text)                 # structure + XXE gate
        short = sheet.rsplit("/", 1)[-1]
        located: set = set()

        # Inline strings: addressable, spliceable.
        for blk in _RE_C_BLOCK.finditer(text):
            head = blk.group(0).split(">", 1)[0]
            if not _RE_INLINESTR.search(head):
                continue
            rm = _RE_ATTR_R.search(head)
            cell = rm.group(1) if rm else "?"
            for span_start, content in _t_spans(blk.group(0)):
                for m in _RE_TOKEN.finditer(content):
                    refs.append(XlsxRef(
                        member=sheet, offset=blk.start() + span_start + m.start(),
                        token=m.group(0), kind="inline",
                        label=f"{short} {cell} (inline)",
                        span_text=content, span_start=blk.start() + span_start,
                        cells=[f"{short}!{cell}"]))
                    located.add((cell, m.group(0)))

        # Formula-cached values: identified via parse, never spliced.
        for c in root.iter():
            if _local(c.tag) != "c":
                continue
            t = c.get("t")
            cell = c.get("r") or "?"
            has_f = any(_local(ch.tag) == "f" for ch in c)
            if t == "str" or (has_f and t not in ("s", "inlineStr")):
                v = next((ch for ch in c if _local(ch.tag) == "v"), None)
                for m in _RE_TOKEN.finditer((v.text or "") if v is not None else ""):
                    flags.append(XlsxFlagRef(
                        member=sheet, token=m.group(0),
                        label=f"{short} {cell} (formula-cached)", note="formula_cached"))
            elif t == "inlineStr":
                full = "".join(x.text or "" for x in c.iter() if _local(x.tag) == "t")
                for m in _RE_TOKEN.finditer(full):
                    if (cell, m.group(0)) not in located:
                        flags.append(XlsxFlagRef(
                            member=sheet, token=m.group(0),
                            label=f"{short} {cell} (inline)", note="unaddressable"))
    return refs, flags


def locate(parts: WorkbookParts) -> Tuple[List[XlsxRef], List[XlsxFlagRef]]:
    """All addressable token occurrences + all locate-only flag refs."""
    s_refs, s_flags = _locate_shared(parts)
    w_refs, w_flags = _locate_sheets(parts)
    return s_refs + w_refs, s_flags + w_flags
