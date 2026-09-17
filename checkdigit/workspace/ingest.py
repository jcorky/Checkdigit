"""
ingest.py
=========
Source registration, import intents and the streaming job runner.

A job streams its source once, emits one observation per identifier found,
stages generation memberships when the intent asks for reconciliation, and
checkpoints after every committed batch. All work units are idempotent:
observation and membership rows carry natural keys and a replayed batch is a
no-op, so a job interrupted at any point resumes from its checkpoint without
double counting.

Formats on the streaming path: CSV (mapped columns), plain text lines and
EDIFACT (EQD equipment segments). Other formats are refused here with
UNSUPPORTED_INPUT and remain on the whole-file service path, which has its
own size limit.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
from typing import Any, Dict, Iterator, List, Optional, Tuple

import equipment_checkdigit as kernel
from edifact_locator import Separators, parse_segment

from . import jobs as jobq
from .store import audit, new_id, now_iso, transaction
from .stream import csv_records, edifact_segments, line_records, text_chunks

PARSER_VERSION = "workspace-py/1"
RULESET_VERSION = "kernel/2"
BATCH_RECORDS = 5000
INSPECT_BYTES = 256 * 1024

RE_TOKEN = re.compile(r"(?<![A-Z0-9])[A-Z]{4} ?[0-9]{6} ?[0-9](?![A-Z0-9])")


class IngestError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Sources and intents
# --------------------------------------------------------------------------- #

def hash_file(path: str) -> Tuple[str, int]:
    sha, size, _codec = hash_and_sniff(path)
    return sha, size


def hash_and_sniff(path: str) -> Tuple[str, int, str]:
    """One sequential read: SHA-256, size and the codec (strict UTF-8 or ISO-8859-1)."""
    import codecs
    h = hashlib.sha256()
    size = 0
    dec = codecs.getincrementaldecoder("utf-8")(errors="strict")
    utf8 = True
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
            if utf8:
                try:
                    dec.decode(chunk, final=False)
                except UnicodeDecodeError:
                    utf8 = False
    if utf8:
        try:
            dec.decode(b"", final=True)
        except UnicodeDecodeError:
            utf8 = False
    return h.hexdigest(), size, "utf-8" if utf8 else "latin-1"


def register_source(conn: sqlite3.Connection, root: str, workspace_id: str, filename: str,
                    src_path: str, declared_content_type: str = "", *, move: bool = False) -> str:
    """Copy (or move) a file into the immutable source store and record it once per content."""
    sha, size, codec = hash_and_sniff(src_path)
    existing = conn.execute("SELECT id FROM source_files WHERE workspace_id = ? AND sha256 = ? AND size_bytes = ?",
                            (workspace_id, sha, size)).fetchone()
    if existing:
        if move:
            os.remove(src_path)
        return existing["id"]
    store_dir = os.path.join(root, "sources", workspace_id)
    os.makedirs(store_dir, exist_ok=True)
    dest = os.path.join(store_dir, f"{sha}.bin")
    if not os.path.exists(dest):
        tmp = dest + ".part"
        if move:
            shutil.move(src_path, tmp)
        else:
            shutil.copyfile(src_path, tmp)
        os.replace(tmp, dest)
    elif move:
        os.remove(src_path)
    sid = new_id("src")
    conn.execute("INSERT INTO source_files (id, workspace_id, filename, size_bytes, sha256, encoding, "
                 "declared_content_type, path, received_at) VALUES (?,?,?,?,?,?,?,?,?)",
                 (sid, workspace_id, os.path.basename(filename), size, sha, codec,
                  declared_content_type, dest, now_iso()))
    audit(conn, workspace_id, "system", "source.register", sid, f"{filename} {size} bytes sha256 {sha}")
    return sid


def intent_fingerprint(fields: Dict[str, Any]) -> str:
    canon = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def create_intent(conn: sqlite3.Connection, workspace_id: str, source_file_id: str, *,
                  mode: str, scope_kind: str = "fleet", scope_value: str = "all",
                  effective_time: str = "", baseline_generation_id: Optional[str] = None,
                  field_update_policy: str = "unknown_blocks", change_volume_limit: Optional[int] = None,
                  empty_scope_decision: Optional[str] = None, declared_record_count: Optional[int] = None
                  ) -> Tuple[str, bool]:
    """Record an import intent. Returns (intent_id, duplicate).

    The fingerprint covers everything that decides the intent's effect, so a
    replayed request maps onto the existing intent instead of a second one.
    """
    if mode not in ("comparison_only", "full_snapshot", "incremental", "explicit_removal"):
        raise IngestError(f"unknown import mode {mode!r}")
    if field_update_policy not in ("absent_means_no_update", "explicit_null_clears",
                                   "empty_string_declared", "unknown_blocks"):
        raise IngestError(f"unknown field update policy {field_update_policy!r}")
    src = conn.execute("SELECT sha256 FROM source_files WHERE id = ?", (source_file_id,)).fetchone()
    if src is None:
        raise IngestError("unknown source file")
    fields = {"workspace": workspace_id, "source_sha256": src["sha256"], "mode": mode,
              "scope_kind": scope_kind, "scope_value": scope_value, "effective_time": effective_time,
              "baseline": baseline_generation_id or "", "field_update_policy": field_update_policy,
              "change_volume_limit": change_volume_limit, "empty_scope_decision": empty_scope_decision,
              "declared_record_count": declared_record_count}
    fp = intent_fingerprint(fields)
    existing = conn.execute("SELECT id FROM import_intents WHERE fingerprint = ?", (fp,)).fetchone()
    if existing:
        return existing["id"], True
    iid = new_id("int")
    conn.execute("""INSERT INTO import_intents (id, workspace_id, source_file_id, mode, scope_kind, scope_value,
                    effective_time, baseline_generation_id, field_update_policy, change_volume_limit,
                    empty_scope_decision, declared_record_count, fingerprint, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (iid, workspace_id, source_file_id, mode, scope_kind, scope_value, effective_time or now_iso(),
                  baseline_generation_id, field_update_policy, change_volume_limit, empty_scope_decision,
                  declared_record_count, fp, now_iso()))
    return iid, False


# --------------------------------------------------------------------------- #
# Format inspection
# --------------------------------------------------------------------------- #

def inspect_source(path: str, options: Dict[str, Any], codec: Optional[str] = None) -> Dict[str, Any]:
    """Decide the streaming format from the first bytes and the job options."""
    with open(path, "rb") as fh:
        head = fh.read(INSPECT_BYTES)
    from .stream import sniff_codec
    codec = codec or sniff_codec(path)
    text = head.decode(codec, errors="replace")
    fmt = options.get("format")
    if not fmt:
        stripped = text.lstrip("﻿ \r\n\t")
        if stripped.startswith("UNA") or stripped.startswith("UNB"):
            fmt = "edifact"
        elif stripped.startswith("ISA"):
            fmt = "x12"
        elif stripped.startswith("<"):
            fmt = "xml"
        elif head[:2] == b"PK":
            fmt = "archive"
        elif options.get("columns"):
            fmt = "csv"
        else:
            first = stripped.split("\n", 1)[0]
            fmt = "csv" if ("," in first or ";" in first or "\t" in first) else "txt"
    out: Dict[str, Any] = {"format": fmt, "codec": codec}
    if fmt == "csv":
        first = text.lstrip("﻿").split("\n", 1)[0]
        delim = options.get("delimiter")
        if not delim:
            counts = {d: first.count(d) for d in (",", ";", "\t", "|")}
            delim = max(counts, key=lambda d: counts[d]) if any(counts.values()) else ","
        out["delimiter"] = delim
        out["has_header"] = bool(options.get("has_header", True))
    if fmt == "edifact":
        sep = Separators()
        if text[:3] == "UNA" and len(text) >= 9:
            s = text[3:9]
            sep = Separators(component=s[0], element=s[1], decimal=s[2], release=s[3], segment=s[5])
        out["separators"] = {"component": sep.component, "element": sep.element, "decimal": sep.decimal,
                             "release": sep.release, "segment": sep.segment}
    return out


# --------------------------------------------------------------------------- #
# Record streams: yield (record_no, location, [(raw, char_offset, attrs_text)])
# --------------------------------------------------------------------------- #

def _csv_stream(path: str, info: Dict[str, Any], options: Dict[str, Any]) -> Iterator[Tuple[int, str, List[Tuple[str, int, str]]]]:
    from csv_locator import CsvError
    delim = info["delimiter"]
    has_header = info["has_header"]
    columns = options.get("columns") or []
    if not columns:
        raise IngestError("csv jobs need a 'columns' option naming the identifier column(s)")
    chunks = (t for _c, t in text_chunks(path, info["codec"]))
    indices: Optional[List[int]] = None
    for rec in csv_records(chunks, delim):
        if indices is None:
            if has_header:
                header = {f.value.strip().lstrip("﻿").lower(): f.col for f in rec.fields}
                indices = []
                for sel in columns:
                    if isinstance(sel, int):
                        indices.append(sel)
                    else:
                        key = sel.strip().lower()
                        if key not in header:
                            raise CsvError(f"column {sel!r} not found in header row (have: {sorted(header)})")
                        indices.append(header[key])
                continue
            indices = [int(c) for c in columns]
        if len(rec.fields) == 1 and rec.fields[0].value == "":
            continue
        items: List[Tuple[str, int, str]] = []
        others = [f.value for k, f in enumerate(rec.fields) if k not in indices]
        attrs = "\x1f".join(others)
        for idx in indices:
            if idx >= len(rec.fields):
                items.append(("", rec.end, attrs))
                continue
            f = rec.fields[idx]
            offset = f.start + (1 if f.quoted else 0)
            raw = f.value.strip()
            if raw == "" or f.quoted and '"' in raw:
                # empty, or a quoted value containing escaped quotes: no single splice span
                items.append(("", offset, attrs))
                continue
            offset += len(f.value) - len(f.value.lstrip())
            items.append((raw, offset, attrs))
        yield rec.no, f"row {rec.no + 1}", items


def _txt_stream(path: str, info: Dict[str, Any], options: Dict[str, Any]) -> Iterator[Tuple[int, str, List[Tuple[str, int, str]]]]:
    chunks = (t for _c, t in text_chunks(path, info["codec"]))
    for line_no, start, line in line_records(chunks):
        items = [(m.group(0), start + m.start(), "") for m in RE_TOKEN.finditer(line.upper())
                 if line[m.start():m.end()].upper() == m.group(0)]
        yield line_no - 1, f"line {line_no}", items


def _edifact_stream(path: str, info: Dict[str, Any], options: Dict[str, Any]) -> Iterator[Tuple[int, str, List[Tuple[str, int, str]]]]:
    s = info["separators"]
    sep = Separators(component=s["component"], element=s["element"], decimal=s["decimal"],
                     release=s["release"], segment=s["segment"])
    chunks = (t for _c, t in text_chunks(path, info["codec"]))
    seg_no = 0
    for start, raw in edifact_segments(chunks, sep.release, sep.segment):
        seg_no += 1
        if not raw.startswith("EQD"):
            continue
        seg = parse_segment(raw, start, sep)
        els = seg.elements
        qualifier = els[1][0] if len(els) > 1 and els[1] else ""
        if qualifier != "CN":
            continue
        eqid = els[2][0] if len(els) > 2 and els[2] else ""
        if not eqid:
            yield seg_no, f"segment {seg_no} EQD", [("", start, raw)]
            continue
        off = raw.find(eqid)
        attrs = raw.replace(eqid, "", 1)
        yield seg_no, f"segment {seg_no} EQD", [(eqid, start + off, attrs)]


STREAMS = {"csv": _csv_stream, "txt": _txt_stream, "edifact": _edifact_stream}


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

def _context(options: Dict[str, Any], fmt: str) -> kernel.FieldContext:
    name = options.get("context")
    if name:
        return kernel.FieldContext(name)
    return kernel.FieldContext.FREE_TEXT if fmt == "txt" else kernel.FieldContext.EQUIPMENT_ID


def add_finding(conn: sqlite3.Connection, job_id: str, code: str, detail: str = "", location: str = "") -> None:
    from contracts import finding
    base = finding(code)
    conn.execute("INSERT INTO findings (job_id, code, severity, blocking_scope, location, detail, created_at) "
                 "VALUES (?,?,?,?,?,?,?)",
                 (job_id, code, base["severity"], base["blocking_scope"], location or None, detail, now_iso()))


def run_job(conn: sqlite3.Connection, root: str, job: sqlite3.Row, worker_id: str, *,
            batch_records: int = BATCH_RECORDS, policy=None,
            fail_after_records: Optional[int] = None, lease_seconds: float = jobq.LEASE_SECONDS) -> str:
    """Execute one claimed job to awaiting_review. Returns the final state.

    `fail_after_records` is a test hook that aborts the worker after N records
    of this attempt have been committed, simulating a crash.
    """
    from . import reconcile
    job_id = job["id"]
    options = json.loads(job["options_json"])
    src = conn.execute("SELECT * FROM source_files WHERE id = ?", (job["source_file_id"],)).fetchone()
    ckpt = jobq.load_checkpoint(conn, job_id)
    try:
        if "inspect" not in ckpt:
            jobq.set_step(conn, job_id, worker_id, "inspecting", "detect format")
            info = inspect_source(src["path"], options, src["encoding"])
            if info["format"] not in STREAMS:
                with transaction(conn):
                    add_finding(conn, job_id, "UNSUPPORTED_INPUT",
                                f"format {info['format']!r} is not on the streaming path")
                    jobq.complete(conn, job_id, worker_id, "failed")
                return "failed"
            ckpt = {"inspect": info, "record_no": -1, "ordinal": -1, "done": False}
            with transaction(conn):
                jobq.checkpoint(conn, job_id, worker_id, ckpt)
        info = ckpt["inspect"]
        if not ckpt.get("done"):
            jobq.set_step(conn, job_id, worker_id, "validating", f"stream {info['format']}")
            _stream_records(conn, root, job, worker_id, info, options, ckpt, batch_records, policy,
                            fail_after_records, lease_seconds)
        jobq.set_step(conn, job_id, worker_id, "validating", "reconcile")
        reconcile.finish_job(conn, job, worker_id)
        with transaction(conn):
            jobq.complete(conn, job_id, worker_id, "awaiting_review")
        return "awaiting_review"
    except jobq.JobCancelled:
        with transaction(conn):
            jobq.mark_cancelled(conn, job_id)
        return "cancelled"
    except jobq.LeaseLost:
        return "lease_lost"
    except _SimulatedCrash:
        raise
    except Exception as exc:  # noqa: BLE001 - any failure is recorded on the job
        with transaction(conn):
            jobq.fail(conn, job_id, worker_id, f"{type(exc).__name__}: {exc}")
        state = conn.execute("SELECT state FROM jobs WHERE id = ?", (job_id,)).fetchone()["state"]
        return state


class _SimulatedCrash(RuntimeError):
    """Test hook: the worker process dies without cleaning up."""


def _attrs_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else ""


def _stream_records(conn, root, job, worker_id, info, options, ckpt, batch_records, policy, fail_after,
                    lease_seconds) -> None:
    job_id = job["id"]
    fmt = info["format"]
    context = _context(options, fmt)
    owner_policy = options.get("owner_policy", "strict")
    intent = None
    if job["import_intent_id"]:
        intent = conn.execute("SELECT * FROM import_intents WHERE id = ?", (job["import_intent_id"],)).fetchone()
    stage_members = intent is not None and intent["mode"] != "comparison_only"
    gen_id = job["generation_id"]
    resume_after = ckpt.get("record_no", -1)
    ordinal = ckpt.get("ordinal", -1)
    committed_this_attempt = 0
    obs_rows: List[tuple] = []
    member_rows: List[tuple] = []
    counts: Dict[str, int] = {}
    pending_records = 0
    last_record_no = resume_after

    def flush(final_record_no: int) -> None:
        nonlocal obs_rows, member_rows, counts, pending_records, committed_this_attempt
        with transaction(conn):
            if obs_rows:
                conn.executemany(
                    "INSERT OR IGNORE INTO observations (job_id, ordinal, record_no, location, char_offset, raw, "
                    "normalized, scheme, status, printed_check, computed_check, candidate, reason, attrs_hash) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", obs_rows)
            if member_rows:
                conn.executemany("INSERT OR IGNORE INTO memberships (generation_id, key, attrs_hash) VALUES (?,?,?)",
                                 member_rows)
            for name, delta in counts.items():
                jobq.bump_counter(conn, job_id, name, delta)
            ckpt["record_no"] = final_record_no
            ckpt["ordinal"] = ordinal
            jobq.checkpoint(conn, job_id, worker_id, ckpt)
            jobq.heartbeat(conn, job_id, worker_id, lease_seconds)
        committed_this_attempt += pending_records
        obs_rows, member_rows, counts, pending_records = [], [], {}, 0
        if fail_after is not None and committed_this_attempt >= fail_after:
            raise _SimulatedCrash(f"simulated crash after {committed_this_attempt} records")

    stream = STREAMS[fmt](job_src_path(conn, job), info, options)
    for record_no, location, items in stream:
        if record_no <= resume_after:
            # Already committed by an earlier attempt; observation ordinals for
            # skipped records were restored from the checkpoint.
            continue
        pending_records += 1
        counts["records_total"] = counts.get("records_total", 0) + 1
        last_record_no = record_no
        for raw, offset, attrs in items:
            ordinal += 1
            if raw == "":
                counts["identifiers_missing"] = counts.get("identifiers_missing", 0) + 1
                counts["records_quarantined"] = counts.get("records_quarantined", 0) + 1
                obs_rows.append((job_id, ordinal, record_no, location, offset, "", "", "unknown",
                                 "invalid_structure", None, None, None, "identifier field is empty", ""))
                continue
            res = kernel.correct_identifier(raw, context, owner_policy=owner_policy, policy=policy)
            status = res.status.value
            counts["identifiers_total"] = counts.get("identifiers_total", 0) + 1
            counts[f"status_{status}"] = counts.get(f"status_{status}", 0) + 1
            if status == "invalid_structure":
                counts["records_quarantined"] = counts.get("records_quarantined", 0) + 1
            candidate = res.corrected if res.status is kernel.Status.CORRECTED else None
            ah = _attrs_hash(attrs)
            obs_rows.append((job_id, ordinal, record_no, location, offset, raw, res.normalized, res.id_type.value,
                             status, res.printed_check, res.computed_check, candidate, res.reason, ah))
            if stage_members and status in ("valid", "corrected", "flagged"):
                member_rows.append((gen_id, res.normalized, ah))
        if pending_records >= batch_records:
            flush(record_no)
    if pending_records or not ckpt.get("done"):
        ckpt["done"] = True
        flush(last_record_no)


def job_src_path(conn: sqlite3.Connection, job: sqlite3.Row) -> str:
    return conn.execute("SELECT path FROM source_files WHERE id = ?", (job["source_file_id"],)).fetchone()["path"]
