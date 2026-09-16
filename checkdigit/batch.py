"""
batch.py
========
Bulk correction. Submit many files at once (a list, or a .zip of them), get
back a single .zip containing every corrected file plus a consolidated report
(CSV + JSON manifest). This is packaging over the EXISTING per-file pipeline:
each member goes through service.process_upload unchanged, so it inherits
identical detection, correction, audit, policy, and enrichment.

Distinct from watch_folder.py: that is a long-running daemon watching an SFTP
drop directory; this is a synchronous one-shot for an interactive "correct these
40 files now" request (API endpoint or CLI). They share the pipeline, nothing
else.

Failure isolation: a rejected or crashing member is recorded in the manifest
with its reason and SKIPPED in the output zip; one bad file never sinks the
batch. Every processed member is still audited (its ingestion_event row is
written by process_upload, exactly as a single upload would be).

Expansion budgets (limits.ZIP_*) are applied BEFORE any member is expanded:
member count, per-member declared size, total declared size and compression
ratio. Members past a budget are recorded as rejected, never read. Each member
is then read in bounded chunks and refused if it produces more bytes than it
declared. A member that is itself a ZIP is never expanded here; the per-file
pipeline recognizes an .xlsx workbook (with its own budgets) and rejects any
other archive, so nesting stops at depth one.

Member identity: a ZIP may contain the same path twice, or two paths with the
same basename. Every member is read through its own ZipInfo (never by name) and
reported under a display name that is unique within the batch; `source_name`
keeps the member's original path.

Output zip layout:
    corrected/<name>              # only for members that produced output
    report.csv                    # one row per member (consolidated verdicts)
    manifest.json                 # structured per-member detail + totals
"""
from __future__ import annotations

import base64
import csv
import io
import json
import os
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import limits
import service


# Members of an input zip that are never treated as data files.
_SKIP_NAMES = ("__MACOSX/",)


@dataclass
class MemberResult:
    name: str                    # unique display name within the batch
    status: str                  # processed | rejected | error
    detected_format: str = ""
    reason: str = ""
    summary: Dict[str, int] = field(default_factory=dict)
    output_name: str = ""        # name under corrected/ if output was produced
    event_id: Optional[int] = None
    source_name: str = ""        # original path/basename as submitted
    size: int = 0


@dataclass
class BatchResult:
    members: List[MemberResult]
    zip_bytes: bytes
    totals: Dict[str, int]

    def manifest(self) -> dict:
        return {"totals": self.totals,
                "members": [vars(m) for m in self.members]}


@dataclass
class ZipMember:
    """One archive member: either its bytes or the reason it was not expanded."""
    name: str
    content: Optional[bytes]
    reason: str = ""


def _read_member_bounded(zf: zipfile.ZipFile, info: zipfile.ZipInfo, cap: int) -> bytes:
    declared = info.file_size
    chunks: List[bytes] = []
    total = 0
    with zf.open(info) as fh:
        while True:
            chunk = fh.read(min(limits.READ_CHUNK, cap - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > declared or total > cap:
                raise limits.BudgetExceeded(
                    f"member {info.filename!r} expands beyond its declared {declared} bytes "
                    f"or the {cap}-byte member limit")
            chunks.append(chunk)
    return b"".join(chunks)


def _iter_zip(data: bytes, *, deadline: Optional[limits.Deadline] = None) -> List[ZipMember]:
    """Expand a .zip into members, applying limits.ZIP_* before and during reads.
    Directory and junk entries are skipped. A member past a budget is returned
    with `content=None` and a reason instead of being read."""
    if len(data) > limits.MAX_ARCHIVE_BYTES:
        raise ValueError(f"archive is {len(data)} bytes; limit is {limits.MAX_ARCHIVE_BYTES}")
    out: List[ZipMember] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infos = [i for i in zf.infolist()
                 if not i.is_dir() and not any(i.filename.startswith(s) for s in _SKIP_NAMES)]
        expanded_total = 0
        for index, info in enumerate(infos):
            if deadline is not None:
                deadline.check("archive expansion")
            if index >= limits.ZIP_MAX_MEMBERS:
                out.append(ZipMember(info.filename, None,
                                     f"not processed: archive member limit of {limits.ZIP_MAX_MEMBERS} exceeded"))
                continue
            if info.file_size > limits.ZIP_MAX_MEMBER_BYTES:
                out.append(ZipMember(info.filename, None,
                                     f"member declares {info.file_size} bytes; limit is {limits.ZIP_MAX_MEMBER_BYTES}"))
                continue
            if info.compress_size and info.file_size / info.compress_size > limits.ZIP_MAX_COMPRESSION_RATIO:
                out.append(ZipMember(info.filename, None,
                                     f"compression ratio above {limits.ZIP_MAX_COMPRESSION_RATIO}:1; not expanded"))
                continue
            if expanded_total + info.file_size > limits.ZIP_MAX_EXPANDED_BYTES:
                out.append(ZipMember(info.filename, None,
                                     f"not processed: archive would expand beyond {limits.ZIP_MAX_EXPANDED_BYTES} bytes"))
                continue
            try:
                content = _read_member_bounded(zf, info, limits.ZIP_MAX_MEMBER_BYTES)
            except limits.BudgetExceeded as exc:
                out.append(ZipMember(info.filename, None, str(exc)))
                continue
            except zipfile.BadZipFile as exc:
                out.append(ZipMember(info.filename, None, f"corrupt member: {exc}"))
                continue
            expanded_total += len(content)
            out.append(ZipMember(info.filename, content))
    return out


def _dedupe_name(name: str, used: set) -> str:
    """Ensure unique names (two inputs can share a basename)."""
    if name not in used:
        used.add(name)
        return name
    stem, ext = os.path.splitext(name)
    i = 2
    while f"{stem}({i}){ext}" in used:
        i += 1
    final = f"{stem}({i}){ext}"
    used.add(final)
    return final


def process_batch(conn, files: List[Tuple[str, bytes]], *, owner_policy: str = "strict",
                  trust: bool = False, enrichment=None, policy=None,
                  format_hint: Optional[str] = None, parse_options: Optional[dict] = None,
                  user_agent: str = "checkdigit-batch/1",
                  deadline: Optional[limits.Deadline] = None,
                  skipped: Optional[List[ZipMember]] = None) -> BatchResult:
    """Run a list of (filename, bytes) through the pipeline and assemble the zip.

    A single format_hint/parse_options applies to every member (use when the
    whole batch is e.g. the same CSV layout). Members that fail are recorded and
    skipped, not fatal. `skipped` lists archive members that were never expanded
    (budget violations); they appear in the manifest as rejected.
    """
    members: List[MemberResult] = []
    out_buf = io.BytesIO()
    used_output: set = set()
    used_display: set = set()
    deadline = deadline or limits.Deadline()
    totals = {"files": 0, "processed": 0, "rejected": 0, "errored": 0,
              "containers": 0, "corrected": 0, "flagged": 0, "valid": 0, "invalid": 0}

    with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as zo:
        csv_rows: List[List[str]] = [[
            "file", "status", "detected_format", "containers", "corrected",
            "flagged", "valid", "invalid", "output", "reason", "event_id", "source_name"]]

        def record(mr: MemberResult, row_status: str, s: Dict[str, int]) -> None:
            members.append(mr)
            csv_rows.append([mr.name, row_status, mr.detected_format,
                             str(s.get("containers", "")), str(s.get("corrected", "")),
                             str(s.get("flagged", "")), str(s.get("valid", "")),
                             str(s.get("invalid", "")), mr.output_name, mr.reason,
                             str(mr.event_id or ""), mr.source_name])

        for zm in skipped or []:
            totals["files"] += 1
            totals["rejected"] += 1
            mr = MemberResult(name=_dedupe_name(os.path.basename(zm.name) or zm.name, used_display),
                              status="rejected", reason=zm.reason, source_name=zm.name)
            record(mr, "rejected", {})

        for source_name, content in files:
            totals["files"] += 1
            display = _dedupe_name(os.path.basename(source_name) or "file", used_display)
            mr = MemberResult(name=display, status="error", source_name=source_name,
                              size=len(content))
            if len(content) > limits.MAX_UPLOAD_BYTES:
                mr.status = "rejected"
                mr.reason = f"member is {len(content)} bytes; limit is {limits.MAX_UPLOAD_BYTES}"
                totals["rejected"] += 1
                record(mr, "rejected", {})
                continue
            try:
                deadline.check("batch processing")
                res = service.process_upload(
                    conn, content, filename=display, content_type="",
                    user_agent=user_agent, owner_policy=owner_policy, trust=trust,
                    enrichment=enrichment, policy=policy,
                    format_hint=format_hint, parse_options=parse_options)
            except limits.BudgetExceeded as exc:
                mr.reason = str(exc)
                totals["errored"] += 1
                record(mr, "error", {})
                continue
            except Exception as exc:                       # never sink the batch
                mr.reason = f"worker exception: {exc}"
                totals["errored"] += 1
                record(mr, "error", {})
                continue

            mr.status = res["status"]
            mr.detected_format = res.get("detected_format", "")
            mr.event_id = res.get("event_id")
            if res["status"] == "rejected":
                mr.reason = res.get("reason", "")
                totals["rejected"] += 1
                record(mr, "rejected", {})
                continue

            # processed -> write the corrected artifact + accumulate
            report = res["report"]
            s = report.summary()
            mr.summary = s
            for k in ("containers", "corrected", "flagged", "valid", "invalid"):
                totals[k] += s.get(k, 0)
            totals["processed"] += 1

            stem, ext = os.path.splitext(os.path.basename(source_name) or "file")
            if report.corrected_b64:                       # binary (xlsx)
                out_name = _dedupe_name(f"{stem}.corrected.xlsx", used_output)
                zo.writestr(f"corrected/{out_name}", base64.b64decode(report.corrected_b64))
            else:
                out_name = _dedupe_name(f"{stem}.corrected{ext or '.txt'}", used_output)
                data = report.corrected_text.encode(res.get("encoding") or "utf-8")
                zo.writestr(f"corrected/{out_name}", data)
            mr.output_name = out_name
            record(mr, "processed", s)

        # consolidated report.csv
        sio = io.StringIO()
        csv.writer(sio).writerows(csv_rows)
        zo.writestr("report.csv", sio.getvalue())
        # manifest.json (written last; totals are final here)
        manifest = {"totals": totals, "members": [vars(m) for m in members],
                    "limits": {"member_bytes": limits.MAX_UPLOAD_BYTES,
                               "archive_bytes": limits.MAX_ARCHIVE_BYTES,
                               "members": limits.ZIP_MAX_MEMBERS,
                               "expanded_bytes": limits.ZIP_MAX_EXPANDED_BYTES}}
        zo.writestr("manifest.json", json.dumps(manifest, indent=2))

    return BatchResult(members=members, zip_bytes=out_buf.getvalue(), totals=totals)


def process_batch_zip(conn, zip_bytes: bytes, **kwargs) -> BatchResult:
    """Accept a .zip of inputs and process its members under the expansion budgets."""
    deadline = kwargs.pop("deadline", None) or limits.Deadline()
    expanded = _iter_zip(zip_bytes, deadline=deadline)
    files = [(m.name, m.content) for m in expanded if m.content is not None]
    skipped = [m for m in expanded if m.content is None]
    return process_batch(conn, files, deadline=deadline, skipped=skipped, **kwargs)
