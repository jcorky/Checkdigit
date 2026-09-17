"""
ingest.py
=========
Source registration, import intents and the streaming job runner.

A job streams its source once, emits one observation per identifier found,
stages generation memberships when the intent asks for reconciliation, and
checkpoints after every committed batch. All work units are idempotent:
observation, membership, message, movement, event and context rows carry
natural keys and a replayed batch is a no-op, so a job interrupted at any
point resumes from its checkpoint without double counting.

Formats on the streaming path: CSV (mapped columns or a profile's mapping
contract), plain text lines, EDIFACT (EQD equipment segments), X12 (N7 split
identifiers and N9*EQ references) and container XML (id-bearing attributes).
Message formats also feed a collector that records one transaction per
message with its context (messages.py, context.py). Anything else is refused
with UNSUPPORTED_INPUT and remains on the whole-file service path.
"""
from __future__ import annotations

import codecs
import hashlib
import json
import os
import re
import shutil
import sqlite3
from typing import Any, Dict, Iterator, List, NamedTuple, Optional, Tuple

import equipment_checkdigit as kernel
from edifact_locator import Separators, parse_segment
from x12_locator import detect_delims

from . import jobs as jobq
from .store import BudgetExceeded, audit, check_free_space, check_store_budget, new_id, now_iso, transaction
from .stream import XmlSecurityError, csv_records, delimited_segments, line_records, text_chunks, xml_start_tags

PARSER_VERSION = "workspace-py/3"
RULESET_VERSION = "kernel/2"
BATCH_RECORDS = 5000
INSPECT_BYTES = 256 * 1024
SCOPE_KINDS = ("terminal", "source_fleet", "location", "voyage", "profile_population")
IMPORT_MODES = ("comparison_only", "full_snapshot", "incremental", "explicit_removal")
FIELD_UPDATE_POLICIES = ("absent_means_no_update", "explicit_null_clears", "empty_string_declared", "unknown_blocks")

RE_TOKEN = re.compile(r"(?<![A-Z0-9])[A-Z]{4} ?[0-9]{6} ?[0-9](?![A-Z0-9])")

# Container XML: (element localname, attribute localname) pairs that carry the
# container number; the same table as snx_locator.
XML_EQID_ATTRS = {("container", "eqid"), ("equipment", "eqid"), ("unit", "id"), ("unit", "unique-key"),
                  ("line-discharge-list", "unit-id")}
XML_ELEMENTS = {e for e, _a in XML_EQID_ATTRS}
RE_XML_NAME = re.compile(r"[A-Za-z_][\w.:-]*")
RE_XML_ATTR = re.compile(r"""\s([A-Za-z_][\w.:-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')""")


class IngestError(Exception):
    pass


class Item(NamedTuple):
    """One identifier occurrence inside a record.

    token   : the identifier text handed to the kernel
    raw     : the exact source span an approved correction replaces
    offset  : absolute character offset of `raw`
    attrs   : the record's other attributes, hashed for identity comparison
    x12     : (initial, number, printed_check or None) for an X12 N7 segment
    context : message context dict from the collector (message formats)
    """
    token: str
    raw: str
    offset: int
    attrs: str
    x12: Optional[Tuple[str, str, Optional[str]]] = None
    context: Optional[Dict[str, Any]] = None


# --------------------------------------------------------------------------- #
# Sources and intents
# --------------------------------------------------------------------------- #

def hash_file(path: str) -> Tuple[str, int]:
    sha, size, _codec = hash_and_sniff(path)
    return sha, size


def hash_and_sniff(path: str) -> Tuple[str, int, str]:
    """One sequential read: SHA-256, size and the codec (strict UTF-8 or ISO-8859-1)."""
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
                    src_path: str, declared_content_type: str = "", *, move: bool = False,
                    max_bytes: Optional[int] = None) -> str:
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
        check_store_budget(root, workspace_id, 0 if move else size, max_bytes)
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
                  mode: str, scope_kind: str = "source_fleet", scope_value: str = "all",
                  effective_time: str = "", baseline_generation_id: Optional[str] = None,
                  field_update_policy: str = "unknown_blocks", change_volume_limit: Optional[int] = None,
                  empty_scope_decision: Optional[str] = None, declared_record_count: Optional[int] = None
                  ) -> Tuple[str, bool]:
    """Record an import intent. Returns (intent_id, duplicate).

    The fingerprint covers everything that decides the intent's effect, so a
    replayed request maps onto the existing intent instead of a second one.
    """
    if mode not in IMPORT_MODES:
        raise IngestError(f"unknown import mode {mode!r}")
    if field_update_policy not in FIELD_UPDATE_POLICIES:
        raise IngestError(f"unknown field update policy {field_update_policy!r}")
    if scope_kind not in SCOPE_KINDS:
        raise IngestError(f"unknown coverage scope kind {scope_kind!r}; one of {SCOPE_KINDS}")
    if not scope_value:
        raise IngestError("coverage scope value is required")
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

def _head_text(path: str, codec: str, nbytes: int = INSPECT_BYTES) -> str:
    with open(path, "rb") as fh:
        head = fh.read(nbytes)
    return head.decode(codec, errors="replace")


def inspect_source(path: str, options: Dict[str, Any], codec: Optional[str] = None) -> Dict[str, Any]:
    """Decide the streaming format from the first bytes and the job options."""
    from .stream import sniff_codec
    codec = codec or sniff_codec(path)
    text = _head_text(path, codec)
    fmt = options.get("format")
    stripped = text.lstrip("﻿ \r\n\t")
    if not fmt:
        if stripped[:3] in ("UNA", "UNB", "UNH"):
            fmt = "edifact"
        elif re.match(r"(ISA|GS|ST)\*", stripped):
            fmt = "x12"
        elif stripped.startswith("<"):
            fmt = "xml"
        elif text.startswith("PK"):
            fmt = "archive"
        elif options.get("columns") or options.get("mapping_contract"):
            fmt = "csv"
        else:
            first = stripped.split("\n", 1)[0]
            fmt = "csv" if ("," in first or ";" in first or "\t" in first) else "txt"
    out: Dict[str, Any] = {"format": fmt, "codec": codec}
    if fmt == "csv":
        first = text.lstrip("﻿").split("\n", 1)[0]
        delim = options.get("delimiter") or (options.get("mapping_contract") or {}).get("delimiter")
        if not delim:
            counts = {d: first.count(d) for d in (",", ";", "\t", "|")}
            delim = max(counts, key=lambda d: counts[d]) if any(counts.values()) else ","
        out["delimiter"] = delim
        contract = options.get("mapping_contract")
        out["has_header"] = bool(contract["has_header"]) if contract else bool(options.get("has_header", True))
    if fmt == "edifact":
        sep = Separators()
        if stripped[:3] == "UNA" and len(stripped) >= 9:
            s = stripped[3:9]
            sep = Separators(component=s[0], element=s[1], decimal=s[2], release=s[3], segment=s[5])
        out["separators"] = {"component": sep.component, "element": sep.element, "decimal": sep.decimal,
                             "release": sep.release, "segment": sep.segment}
    if fmt == "x12":
        d = detect_delims(stripped)
        out["delims"] = {"element": d.element, "subelement": d.subelement, "segment": d.segment}
    return out


# --------------------------------------------------------------------------- #
# Record streams: yield (record_no, location, [Item])
# --------------------------------------------------------------------------- #

def _resolve_columns(header: Dict[str, int], columns: List[Any], has_header: bool, what: str) -> List[int]:
    from csv_locator import CsvError
    out: List[int] = []
    for sel in columns:
        if isinstance(sel, int) or (isinstance(sel, str) and sel.isdigit() and not has_header):
            out.append(int(sel))
        else:
            key = str(sel).strip().lower()
            if key not in header:
                raise CsvError(f"{what} column {sel!r} not found in header row (have: {sorted(header)})")
            out.append(header[key])
    return out


def _csv_stream(path: str, info: Dict[str, Any], options: Dict[str, Any], state: Optional[Dict[str, Any]] = None
                ) -> Iterator[Tuple[int, str, List[Item]]]:
    """CSV records. With a mapping contract in `state`, the header is checked against it
    before any row is read and every identifier cell is validated as it streams."""
    from . import mapping
    delim = info["delimiter"]
    has_header = info["has_header"]
    columns = list(options.get("columns") or [])
    attribute_columns = options.get("attribute_columns")
    contract = (state or {}).get("contract")
    if not columns and not contract:
        raise IngestError("csv jobs need a 'columns' option naming the identifier column(s), or a bound profile with a mapping contract")
    chunks = (t for _c, t in text_chunks(path, info["codec"]))
    indices: Optional[List[int]] = None
    attr_indices: Optional[List[int]] = None
    validator: Optional[mapping.StreamValidator] = None
    for rec in csv_records(chunks, delim):
        if indices is None:
            header: Dict[str, int] = {}
            header_list: Optional[List[str]] = None
            if has_header:
                header_list = [f.value.strip().lstrip("﻿") for f in rec.fields]
                header = {h.lower(): i for i, h in enumerate(header_list)}
            if contract:
                check = mapping.check_header(contract, mapping.fingerprint_of(header_list, len(rec.fields), delim))
                state["check"] = check
                state["fingerprint"] = mapping.fingerprint_of(header_list, len(rec.fields), delim)["sha256"]
                if check["blocked"]:
                    state["validation"] = {"rows": 0, "violations": 0, "blocked": True, "findings": [],
                                           "treatment": check["treatment"]}
                    return
                id_fields = [f for f in contract["fields"] if f["type"] == "identifier"]
                indices = [check["resolved"][f["name"]] for f in id_fields if check["resolved"].get(f["name"]) is not None]
                if columns:
                    indices = _resolve_columns(header, columns, has_header, "identifier")
                validator = mapping.StreamValidator(indices)
                state["validator"] = validator
            else:
                indices = _resolve_columns(header, columns, has_header, "identifier")
            if attribute_columns is not None:
                attr_indices = _resolve_columns(header, attribute_columns, has_header, "attribute")
            if has_header:
                continue
        if len(rec.fields) == 1 and rec.fields[0].value == "":
            continue
        values = [f.value for f in rec.fields]
        if validator is not None:
            validator.observe(rec.no, values)
        if attr_indices is None:
            others = [v for k, v in enumerate(values) if k not in indices]
        else:
            others = [values[k] if k < len(values) else "" for k in attr_indices]
        attrs = "\x1f".join(others)
        items: List[Item] = []
        for idx in indices:
            if idx >= len(rec.fields):
                items.append(Item("", "", rec.end, attrs))
                continue
            f = rec.fields[idx]
            offset = f.start + (1 if f.quoted else 0)
            raw = f.value.strip()
            if raw == "" or (f.quoted and '"' in raw):
                items.append(Item("", "", offset, attrs))
                continue
            offset += len(f.value) - len(f.value.lstrip())
            items.append(Item(raw, raw, offset, attrs))
        yield rec.no, f"row {rec.no + 1}", items
    if validator is not None:
        state["validation"] = validator.finish()


def _txt_stream(path: str, info: Dict[str, Any], options: Dict[str, Any], state=None) -> Iterator[Tuple[int, str, List[Item]]]:
    chunks = (t for _c, t in text_chunks(path, info["codec"]))
    for line_no, start, line in line_records(chunks):
        upper = line.upper()
        items = [Item(m.group(0), m.group(0), start + m.start(), "")
                 for m in RE_TOKEN.finditer(upper) if line[m.start():m.end()] == m.group(0)]
        yield line_no - 1, f"line {line_no}", items


def _edifact_stream(path: str, info: Dict[str, Any], options: Dict[str, Any], state=None) -> Iterator[Tuple[int, str, List[Item]]]:
    s = info["separators"]
    sep = Separators(component=s["component"], element=s["element"], decimal=s["decimal"],
                     release=s["release"], segment=s["segment"])
    collector = (state or {}).get("collector")
    chunks = (t for _c, t in text_chunks(path, info["codec"]))
    seg_no = 0
    message = 0
    end = 0
    for start, raw in delimited_segments(chunks, sep.segment, sep.release, skip_una=True):
        seg_no += 1
        end = start + len(raw)
        ctx = collector.segment(seg_no, start, raw, sep) if collector is not None else None
        if raw.startswith("UNH"):
            message += 1
            continue
        if not raw.startswith("EQD"):
            continue
        seg = parse_segment(raw, start, sep)
        els = seg.elements
        qualifier = els[1][0] if len(els) > 1 and els[1] else ""
        if qualifier != "CN":
            continue
        eqid = els[2][0] if len(els) > 2 and els[2] else ""
        location = f"message {message} segment {seg_no} EQD"
        if not eqid:
            yield seg_no, location, [Item("", "", start, raw, None, ctx)]
            continue
        off = raw.find(eqid)
        yield seg_no, location, [Item(eqid, eqid, start + off, raw.replace(eqid, "", 1), None, ctx)]
    if state is not None:
        state["end"] = end


def _x12_stream(path: str, info: Dict[str, Any], options: Dict[str, Any], state=None) -> Iterator[Tuple[int, str, List[Item]]]:
    d = info["delims"]
    ele, seg_term = d["element"], d["segment"]
    collector = (state or {}).get("collector")
    chunks = (t for _c, t in text_chunks(path, info["codec"]))
    seg_no = 0
    transaction_set = 0
    end = 0
    for start, raw in delimited_segments(chunks, seg_term, "", skip_una=False):
        seg_no += 1
        end = start + len(raw)
        ctx = collector.segment(seg_no, start, raw) if collector is not None else None
        els = raw.split(ele)
        tag = els[0]
        if tag == "ST":
            transaction_set += 1
            continue
        if tag == "N7":
            initial = els[1] if len(els) > 1 else ""
            number = els[2] if len(els) > 2 else ""
            has18 = len(els) > 18
            location = f"set {transaction_set} segment {seg_no} N7"
            attrs = ele.join(e for k, e in enumerate(els) if k not in (1, 2, 18))
            if not initial and not number:
                yield seg_no, location, [Item("", "", start, attrs, None, ctx)]
                continue
            printed = els[18] if has18 else None
            offset = start + sum(len(e) + 1 for e in els[:18]) if has18 else start + len(raw)
            # The token keeps the X12 split visible: initial*number*check. Two
            # parts mean the check digit element is absent from the segment.
            token = f"{initial}*{number}*{printed}" if has18 else f"{initial}*{number}"
            yield seg_no, location, [Item(token, printed or "", offset, attrs, (initial, number, printed), ctx)]
        elif tag == "N9" and len(els) > 2 and els[1] == "EQ":
            token = els[2]
            offset = start + len(els[0]) + 1 + len(els[1]) + 1
            location = f"set {transaction_set} segment {seg_no} N9*EQ"
            if token:
                yield seg_no, location, [Item(token, token, offset, ele.join(els[3:]))]
            else:
                yield seg_no, location, [Item("", "", offset, "")]
    if state is not None:
        state["end"] = end


def _localname(name: str) -> str:
    return name.rsplit(":", 1)[-1]


def _xml_stream(path: str, info: Dict[str, Any], options: Dict[str, Any], state=None) -> Iterator[Tuple[int, str, List[Item]]]:
    collector = (state or {}).get("collector")
    chunks = (t for _c, t in text_chunks(path, info["codec"]))
    record = 0
    end = 0
    for tag_start, tag in xml_start_tags(chunks):
        end = tag_start + len(tag)
        m = RE_XML_NAME.match(tag, 1)
        if not m:
            continue
        element = _localname(m.group(0))
        if element not in XML_ELEMENTS:
            continue
        items: List[Item] = []
        attr_text = tag
        size_type = ""
        for am in RE_XML_ATTR.finditer(tag, m.end()):
            attr = _localname(am.group(1))
            value = am.group(2) if am.group(2) is not None else am.group(3)
            if attr == "type" and element in ("container", "equipment"):
                size_type = value
            if (element, attr) not in XML_EQID_ATTRS:
                continue
            value_offset = tag_start + am.start(2 if am.group(2) is not None else 3)
            attr_text = attr_text.replace(value, "", 1) if value else attr_text
            items.append(Item(value or "", value or "", value_offset, ""))
        if not items:
            continue
        ctx = collector.element_context(items[0].token, size_type) if collector is not None else None
        items = [Item(i.token, i.raw, i.offset, attr_text, i.x12, ctx) for i in items]
        record += 1
        yield record - 1, f"element {record} {element}", items
    if state is not None:
        state["end"] = end


STREAMS = {"csv": _csv_stream, "txt": _txt_stream, "edifact": _edifact_stream, "x12": _x12_stream, "xml": _xml_stream}
MESSAGE_FORMATS = ("edifact", "x12", "xml")


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

def _context(options: Dict[str, Any], fmt: str) -> kernel.FieldContext:
    name = options.get("context")
    if name:
        return kernel.FieldContext(name)
    return kernel.FieldContext.FREE_TEXT if fmt == "txt" else kernel.FieldContext.EQUIPMENT_ID


def policy_fingerprint(policy) -> str:
    if policy is None:
        return ""
    try:
        canon = json.dumps(policy.to_dict(), sort_keys=True, separators=(",", ":"))
    except Exception:  # noqa: BLE001 - a policy without to_dict is fingerprinted by repr
        canon = repr(policy)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def add_finding(conn: sqlite3.Connection, job_id: str, code: str, detail: str = "", location: str = "") -> None:
    from contracts import finding
    base = finding(code)
    conn.execute("INSERT INTO findings (job_id, code, severity, blocking_scope, location, detail, created_at) "
                 "VALUES (?,?,?,?,?,?,?)",
                 (job_id, code, base["severity"], base["blocking_scope"], location or None, detail, now_iso()))


def x12_parts(token: str) -> Optional[Tuple[str, str, Optional[str]]]:
    """Decode an X12 N7 token written by _x12_stream; None for other tokens."""
    parts = token.split("*")
    if len(parts) == 2:
        return parts[0], parts[1], None
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    return None


def evaluate_item(item: Item, context: kernel.FieldContext, owner_policy: str, policy) -> Tuple[Any, Optional[str]]:
    """Kernel evaluation of one item. Returns (CorrectionResult or None, candidate span)."""
    if item.x12 is not None:
        initial, number, printed = item.x12
        if printed is None:
            return None, None
        res = kernel.correct_x12_equipment(initial, number, printed or None, policy=policy)
        candidate = res.computed_check if res.status is kernel.Status.CORRECTED else None
        return res, candidate
    res = kernel.correct_identifier(item.token, context, owner_policy=owner_policy, policy=policy)
    candidate = res.corrected if res.status is kernel.Status.CORRECTED else None
    return res, candidate


def _build_stream_state(conn: sqlite3.Connection, job: sqlite3.Row, info: Dict[str, Any], options: Dict[str, Any],
                        src: sqlite3.Row) -> Dict[str, Any]:
    """Per-format helpers: the message collector or the mapping contract from the bound profile."""
    from . import context as ctx_mod, messages, mapping, profiles
    state: Dict[str, Any] = {}
    profile = profiles.profile_for_job(conn, job)
    fmt = info["format"]
    ws, job_id = job["workspace_id"], job["id"]
    if fmt in MESSAGE_FORMATS:
        rules = profiles.normalize_rules(profile["rules"], profile.get("message_family") or "") if profile else None
        terminal = profile["terminal_site"] if profile else ""

        def resolver(message_no: int, *, terminal: str, vessel: str, voyage: str) -> Dict[str, Any]:
            visit = ctx_mod.resolve_visit(conn, ws, job_id, message_no, terminal=terminal, vessel=vessel, voyage=voyage)
            row = conn.execute("SELECT id FROM movements WHERE job_id = ? AND message_no = ?", (job_id, message_no)).fetchone()
            if row is None:
                mode = "rail" if fmt == "x12" else ("vessel" if vessel or voyage else "unknown")
                conn.execute(messages.MOVEMENT_INSERT,
                             ctx_mod.movement_row(ws, job_id, message_no, mode=mode, vessel=vessel, voyage=voyage,
                                                  origin="", destination="", visit_id=visit["id"]))
                row = conn.execute("SELECT id FROM movements WHERE job_id = ? AND message_no = ?", (job_id, message_no)).fetchone()
            visit["movement_id"] = row["id"]
            return visit
        kwargs = dict(receipt_time=now_iso(), visit_resolver=resolver, source_sha256=src["sha256"], terminal_site=terminal)
        if fmt == "edifact":
            state["collector"] = messages.EdifactCollector(ws, job_id, rules, **kwargs)
        elif fmt == "x12":
            state["collector"] = messages.X12Collector(ws, job_id, rules, element=info["delims"]["element"], **kwargs)
        else:
            state["collector"] = messages.XmlCollector(ws, job_id, rules, **kwargs)
    elif fmt == "csv" and profile and (profile.get("mapping_contract") or {}).get("fields"):
        state["contract"] = mapping.normalize_contract(profile["mapping_contract"])
    return state


def run_job(conn: sqlite3.Connection, root: str, job: sqlite3.Row, worker_id: str, *,
            batch_records: int = BATCH_RECORDS, policy=None,
            fail_after_records: Optional[int] = None, lease_seconds: float = jobq.LEASE_SECONDS,
            min_free_bytes: Optional[int] = None) -> str:
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
        check_free_space(root, min_free_bytes)
        if "inspect" not in ckpt:
            jobq.set_step(conn, job_id, worker_id, "inspecting", "detect format")
            profile_contract = None
            if job["profile_id"]:
                from . import profiles
                prof = profiles.profile_for_job(conn, job)
                if prof and (prof.get("mapping_contract") or {}).get("fields"):
                    from . import mapping
                    profile_contract = mapping.normalize_contract(prof["mapping_contract"])
            info = inspect_source(src["path"], {**options, **({"mapping_contract": profile_contract} if profile_contract else {})},
                                  src["encoding"])
            if info["format"] not in STREAMS:
                with transaction(conn):
                    add_finding(conn, job_id, "UNSUPPORTED_INPUT",
                                f"format {info['format']!r} is not on the streaming path")
                    jobq.complete(conn, job_id, worker_id, "failed")
                return "failed"
            ckpt = {"inspect": info, "record_no": -1, "ordinal": -1, "done": False}
            with transaction(conn):
                conn.execute("UPDATE jobs SET policy_fingerprint = ? WHERE id = ?", (policy_fingerprint(policy), job_id))
                jobq.checkpoint(conn, job_id, worker_id, ckpt)
        info = ckpt["inspect"]
        if not ckpt.get("done"):
            jobq.set_step(conn, job_id, worker_id, "validating", f"stream {info['format']}")
            _stream_records(conn, root, job, worker_id, info, options, ckpt, batch_records, policy,
                            fail_after_records, lease_seconds, src)
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
    except BudgetExceeded as exc:
        with transaction(conn):
            add_finding(conn, job_id, "RESOURCE_BUDGET_EXCEEDED", exc.detail)
            jobq.complete(conn, job_id, worker_id, "failed")
        return "failed"
    except XmlSecurityError as exc:
        with transaction(conn):
            add_finding(conn, job_id, "UNSUPPORTED_INPUT", str(exc))
            jobq.complete(conn, job_id, worker_id, "failed")
        return "failed"
    except Exception as exc:  # noqa: BLE001 - any failure is recorded on the job
        with transaction(conn):
            jobq.fail(conn, job_id, worker_id, f"{type(exc).__name__}: {exc}")
        state = conn.execute("SELECT state FROM jobs WHERE id = ?", (job_id,)).fetchone()["state"]
        return state


class _SimulatedCrash(RuntimeError):
    """Test hook: the worker process dies without cleaning up."""


def _attrs_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else ""


def observation_row(job_id: str, ordinal: int, record_no: int, location: str, item: Item,
                    context: kernel.FieldContext, owner_policy: str, policy) -> Tuple[tuple, str]:
    """Build one observations row; returns (row, status)."""
    ah = _attrs_hash(item.attrs)
    if item.token == "" and item.x12 is None:
        return ((job_id, ordinal, record_no, location, item.offset, "", "", "unknown", "invalid_structure",
                 None, None, None, "identifier field is empty", ah, ""), "invalid_structure")
    res, candidate = evaluate_item(item, context, owner_policy, policy)
    if res is None:
        initial, number, _p = item.x12  # type: ignore[misc]
        return ((job_id, ordinal, record_no, location, item.offset, "", f"{initial}{number}", "iso6346", "flagged",
                 None, None, None, "N7-18 check digit element absent; the segment is not restructured", ah,
                 item.token), "flagged")
    status = res.status.value
    return ((job_id, ordinal, record_no, location, item.offset, item.raw, res.normalized, res.id_type.value, status,
             res.printed_check, res.computed_check, candidate, res.reason, ah, item.token), status)


def _stream_records(conn, root, job, worker_id, info, options, ckpt, batch_records, policy, fail_after,
                    lease_seconds, src) -> None:
    from . import context as ctx_mod, messages
    job_id = job["id"]
    ws = job["workspace_id"]
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
    msg_rows: List[tuple] = []
    event_rows: List[tuple] = []
    ctx_rows: List[tuple] = []
    counts: Dict[str, int] = {}
    pending_records = 0
    last_record_no = resume_after
    records_since_check = 0
    state = _build_stream_state(conn, job, info, options, src)
    collector = state.get("collector")
    receipt = now_iso()

    def ctx_tuple(c: Dict[str, Any]) -> tuple:
        return (job_id, c["ordinal"], c["message_no"], c.get("visit_id"), c.get("movement_id"), c.get("full_empty"),
                c.get("stow_position"), c.get("size_type"))

    def drain_messages() -> None:
        if collector is None:
            return
        for m in collector.take_messages():
            msg_rows.append(messages.message_row(ws, job_id, src["id"], m))
            subject = {"message_no": m["message_no"], "vessel": m["vessel"], "voyage": m["voyage"]}
            event_rows.extend(ctx_mod.event_rows(ws, job_id, m["message_no"], m["events"], receipt, subject,
                                                 f"{fmt}:{m['message_type'] or 'file'}"))
            if m["visit"] is not None:
                conn.execute("UPDATE movements SET origin = ?, destination = ? WHERE job_id = ? AND message_no = ?",
                             (m["pol"] or None, m["pod"] or None, job_id, m["message_no"]))
            for c in m["contexts"]:
                if "ordinal" in c:
                    ctx_rows.append(ctx_tuple(c))

    def flush(final_record_no: int) -> None:
        nonlocal obs_rows, member_rows, msg_rows, event_rows, ctx_rows, counts, pending_records, committed_this_attempt
        drain_messages()
        with transaction(conn):
            if obs_rows:
                conn.executemany(
                    "INSERT OR IGNORE INTO observations (job_id, ordinal, record_no, location, char_offset, raw, "
                    "normalized, scheme, status, printed_check, computed_check, candidate, reason, attrs_hash, token) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", obs_rows)
            if member_rows:
                conn.executemany("INSERT OR IGNORE INTO memberships (generation_id, key, attrs_hash) VALUES (?,?,?)",
                                 member_rows)
            if msg_rows:
                conn.executemany(messages.MESSAGE_INSERT, msg_rows)
            if event_rows:
                conn.executemany(messages.EVENT_INSERT, event_rows)
            if ctx_rows:
                conn.executemany(messages.CONTEXT_INSERT, ctx_rows)
            for name, delta in counts.items():
                jobq.bump_counter(conn, job_id, name, delta)
            ckpt["record_no"] = final_record_no
            ckpt["ordinal"] = ordinal
            jobq.checkpoint(conn, job_id, worker_id, ckpt)
            jobq.heartbeat(conn, job_id, worker_id, lease_seconds)
        committed_this_attempt += pending_records
        obs_rows, member_rows, msg_rows, event_rows, ctx_rows, counts, pending_records = [], [], [], [], [], {}, 0
        if fail_after is not None and committed_this_attempt >= fail_after:
            raise _SimulatedCrash(f"simulated crash after {committed_this_attempt} records")

    stream = STREAMS[fmt](job_src_path(conn, job), info, options, state)
    for record_no, location, items in stream:
        if record_no <= resume_after:
            # Already committed by an earlier attempt; observation ordinals for
            # skipped records were restored from the checkpoint.
            for item in items:
                if item.context is not None and "ordinal" not in item.context:
                    item.context["ordinal"] = -1
            continue
        pending_records += 1
        counts["records_total"] = counts.get("records_total", 0) + 1
        last_record_no = record_no
        for item in items:
            ordinal += 1
            row, status = observation_row(job_id, ordinal, record_no, location, item, context, owner_policy, policy)
            obs_rows.append(row)
            if item.context is not None:
                c = item.context
                c["ordinal"] = ordinal
                if c.get("movement_id") is None and c.get("visit_id") is not None and collector is not None \
                        and collector.current is not None and collector.current.get("visit"):
                    c["movement_id"] = collector.current["visit"].get("movement_id")
                if not c.get("defer"):
                    ctx_rows.append(ctx_tuple(c))
            if item.token == "" and item.x12 is None:
                counts["identifiers_missing"] = counts.get("identifiers_missing", 0) + 1
                counts["records_quarantined"] = counts.get("records_quarantined", 0) + 1
                continue
            counts["identifiers_total"] = counts.get("identifiers_total", 0) + 1
            counts[f"status_{status}"] = counts.get(f"status_{status}", 0) + 1
            if status == "invalid_structure":
                counts["records_quarantined"] = counts.get("records_quarantined", 0) + 1
            if stage_members and status in ("valid", "corrected", "flagged"):
                member_rows.append((gen_id, row[6], row[13]))
        if pending_records >= batch_records:
            flush(record_no)
            records_since_check += batch_records
            if records_since_check >= 200_000:
                records_since_check = 0
                check_free_space(root)
    if collector is not None:
        summary = collector.finish(state.get("end", 0))
        ckpt["messages"] = summary
    if "validation" in state or "check" in state:
        ckpt["mapping"] = {"check": {k: v for k, v in (state.get("check") or {}).items() if k != "resolved"},
                           "resolved": (state.get("check") or {}).get("resolved"),
                           "validation": state.get("validation"), "fingerprint": state.get("fingerprint")}
    ckpt["done"] = True
    flush(last_record_no)


def job_src_path(conn: sqlite3.Connection, job: sqlite3.Row) -> str:
    return conn.execute("SELECT path FROM source_files WHERE id = ?", (job["source_file_id"],)).fetchone()["path"]
