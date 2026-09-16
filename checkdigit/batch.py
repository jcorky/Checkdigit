"""
batch.py
========
Pass 21: bulk correction. Submit many files at once (a list, or a .zip of them),
get back a single .zip containing every corrected file plus a consolidated
report (CSV + JSON manifest). This is packaging over the EXISTING per-file
pipeline -- each member goes through service.process_upload unchanged, so it
inherits identical detection, correction, audit, policy, and enrichment.

Distinct from watch_folder.py: that is a long-running daemon watching an SFTP
drop directory; this is a synchronous one-shot for an interactive "correct these
40 files now" request (API endpoint or CLI). They share the pipeline, nothing
else.

Failure isolation: a rejected or crashing member is recorded in the manifest
with its reason and SKIPPED in the output zip; one bad file never sinks the
batch. Every member is still audited (its ingestion_event row is written by
process_upload, exactly as a single upload would be).

Output zip layout:
    corrected/<original-name>     # only for members that produced output
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
from typing import Callable, Dict, List, Optional, Tuple

import service


# Members of an input zip that are never treated as data files.
_SKIP_NAMES = ("__MACOSX/",)


@dataclass
class MemberResult:
    name: str
    status: str                  # processed | rejected | error
    detected_format: str = ""
    reason: str = ""
    summary: Dict[str, int] = field(default_factory=dict)
    output_name: str = ""        # name under corrected/ if output was produced
    event_id: Optional[int] = None


@dataclass
class BatchResult:
    members: List[MemberResult]
    zip_bytes: bytes
    totals: Dict[str, int]

    def manifest(self) -> dict:
        return {"totals": self.totals,
                "members": [vars(m) for m in self.members]}


def _iter_zip(data: bytes) -> List[Tuple[str, bytes]]:
    """Flatten a .zip into (basename, bytes), skipping dirs and junk members."""
    out: List[Tuple[str, bytes]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        if zf.testzip() is not None:
            raise ValueError("input zip has a corrupt member")
        for info in zf.infolist():
            if info.is_dir():
                continue
            if any(info.filename.startswith(s) for s in _SKIP_NAMES):
                continue
            out.append((os.path.basename(info.filename), zf.read(info.filename)))
    return out


def _dedupe_name(name: str, used: set) -> str:
    """Ensure unique names under corrected/ (two inputs can share a basename)."""
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
                  user_agent: str = "checkdigit-batch/1") -> BatchResult:
    """Run a list of (filename, bytes) through the pipeline and assemble the zip.

    A single format_hint/parse_options applies to every member (use when the
    whole batch is e.g. the same CSV layout). Members that fail are recorded and
    skipped, not fatal.
    """
    members: List[MemberResult] = []
    out_buf = io.BytesIO()
    used_names: set = set()
    totals = {"files": 0, "processed": 0, "rejected": 0, "errored": 0,
              "containers": 0, "corrected": 0, "flagged": 0, "valid": 0, "invalid": 0}

    with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as zo:
        csv_rows: List[List[str]] = [[
            "file", "status", "detected_format", "containers", "corrected",
            "flagged", "valid", "invalid", "output", "reason", "event_id"]]

        for name, content in files:
            totals["files"] += 1
            mr = MemberResult(name=name, status="error")
            try:
                res = service.process_upload(
                    conn, content, filename=name, content_type="",
                    user_agent=user_agent, owner_policy=owner_policy, trust=trust,
                    enrichment=enrichment, policy=policy,
                    format_hint=format_hint, parse_options=parse_options)
            except Exception as exc:                       # never sink the batch
                mr.reason = f"worker exception: {exc}"
                totals["errored"] += 1
                members.append(mr)
                csv_rows.append([name, "error", "", "", "", "", "", "", "", mr.reason, ""])
                continue

            mr.status = res["status"]
            mr.detected_format = res.get("detected_format", "")
            mr.event_id = res.get("event_id")
            if res["status"] == "rejected":
                mr.reason = res.get("reason", "")
                totals["rejected"] += 1
                csv_rows.append([name, "rejected", mr.detected_format, "", "", "",
                                 "", "", "", mr.reason, ""])
                members.append(mr)
                continue

            # processed -> write the corrected artifact + accumulate
            report = res["report"]
            s = report.summary()
            mr.summary = s
            for k in ("containers", "corrected", "flagged", "valid", "invalid"):
                totals[k] += s.get(k, 0)
            totals["processed"] += 1

            stem, ext = os.path.splitext(name)
            if report.corrected_b64:                       # binary (xlsx)
                out_name = _dedupe_name(f"{stem}.corrected.xlsx", used_names)
                zo.writestr(f"corrected/{out_name}", base64.b64decode(report.corrected_b64))
            else:
                out_name = _dedupe_name(f"{stem}.corrected{ext or '.txt'}", used_names)
                data = report.corrected_text.encode(res.get("encoding") or "utf-8")
                zo.writestr(f"corrected/{out_name}", data)
            mr.output_name = out_name

            csv_rows.append([name, "processed", mr.detected_format,
                             str(s.get("containers", 0)), str(s.get("corrected", 0)),
                             str(s.get("flagged", 0)), str(s.get("valid", 0)),
                             str(s.get("invalid", 0)), out_name, "",
                             str(mr.event_id or "")])
            members.append(mr)

        # consolidated report.csv
        sio = io.StringIO()
        csv.writer(sio).writerows(csv_rows)
        zo.writestr("report.csv", sio.getvalue())
        # manifest.json (written last; totals are final here)
        manifest = {"totals": totals, "members": [vars(m) for m in members]}
        zo.writestr("manifest.json", json.dumps(manifest, indent=2))

    return BatchResult(members=members, zip_bytes=out_buf.getvalue(), totals=totals)


def process_batch_zip(conn, zip_bytes: bytes, **kwargs) -> BatchResult:
    """Convenience: accept a .zip of inputs and process its members."""
    return process_batch(conn, _iter_zip(zip_bytes), **kwargs)
