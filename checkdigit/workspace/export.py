"""
export.py
=========
Streamed artifact building with atomic publication.

The corrected file is produced by streaming the immutable source a second
time and splicing the approved edits at their recorded character offsets;
the text around each edit is copied through the same codec byte for byte.
Every edit is verified against the recorded raw text before it is written.
The artifact is written to a temporary path, renamed into place, and only
then does its row switch from building to ready with the final hash and
size. A retry under the same idempotency key returns the existing artifact.
An artifact whose build fails or is interrupted stays building/failed and is
never served.
"""
from __future__ import annotations

import codecs
import hashlib
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

from .review import ReviewError, check_approval
from .store import audit, iter_rows, new_id, now_iso, transaction
from .stream import CHUNK_BYTES, text_chunks


class ExportError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _edits(conn: sqlite3.Connection, job_id: str, approval_id: str):
    """Approved, unchanged-version members of the selection, in file order."""
    return iter_rows(conn,
                     "SELECT o.ordinal, o.char_offset, o.raw, o.candidate, o.location FROM selection_members s "
                     "JOIN observations o ON o.job_id = ? AND o.ordinal = s.ordinal "
                     "WHERE s.approval_id = ? AND o.decision = 'approved' AND o.version = s.version "
                     "AND o.candidate IS NOT NULL ORDER BY o.ordinal", (job_id, approval_id))


def build_artifact(conn: sqlite3.Connection, root: str, job_id: str, approval_id: Optional[str], actor: str, *,
                   idempotency_key: str, fail_after_edits: Optional[int] = None) -> Dict[str, Any]:
    """Build the corrected file plus exceptions, ledger and manifest for one approval."""
    existing = conn.execute("SELECT * FROM export_artifacts WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
    if existing is not None and existing["state"] == "ready":
        return artifact_view(conn, existing["id"], replayed=True)
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise ExportError("UNKNOWN_JOB")
    if approval_id is not None:
        chk = check_approval(conn, approval_id)
        if not chk["applicable"]:
            raise ExportError(chk["problems"][0], f"approval {approval_id}: {chk}")
    src = conn.execute("SELECT * FROM source_files WHERE id = ?", (job["source_file_id"],)).fetchone()
    art_id = existing["id"] if existing is not None else new_id("art")
    out_dir = os.path.join(root, "artifacts", job["workspace_id"], art_id)
    os.makedirs(out_dir, exist_ok=True)
    with transaction(conn):
        if existing is None:
            conn.execute("INSERT INTO export_artifacts (id, job_id, approval_id, idempotency_key, state, created_at) "
                         "VALUES (?,?,?,?,'building',?)", (art_id, job_id, approval_id, idempotency_key, now_iso()))
        else:
            conn.execute("UPDATE export_artifacts SET state = 'building', error = NULL WHERE id = ?", (art_id,))
        conn.execute("UPDATE jobs SET state = 'exporting', updated_at = ? WHERE id = ?", (now_iso(), job_id))
    try:
        result = _write_outputs(conn, job, src, approval_id, out_dir, fail_after_edits)
    except Exception as exc:  # noqa: BLE001
        with transaction(conn):
            conn.execute("UPDATE export_artifacts SET state = 'failed', error = ? WHERE id = ?",
                         (f"{type(exc).__name__}: {exc}"[:2000], art_id))
            conn.execute("UPDATE jobs SET state = 'awaiting_review', updated_at = ? WHERE id = ?", (now_iso(), job_id))
        raise
    manifest = {
        "artifact_id": art_id, "job_id": job_id, "approval_id": approval_id,
        "source": {"filename": src["filename"], "sha256": src["sha256"], "size_bytes": src["size_bytes"],
                   "encoding": result["codec"]},
        "parser_version": job["parser_version"], "ruleset_version": job["ruleset_version"],
        "analysis_version": job["analysis_version"], "offset_kind": "text_codepoint",
        "output": {"filename": result["out_name"], "sha256": result["sha256"], "size_bytes": result["size"]},
        "edits_applied": result["edits"], "exceptions": result["exceptions"],
        "files": {"corrected": result["out_name"], "exceptions": "exceptions.csv", "ledger": "ledger.csv"},
        "built_at": now_iso(),
    }
    mpath = os.path.join(out_dir, "manifest.json")
    with open(mpath + ".part", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
    os.replace(mpath + ".part", mpath)
    with transaction(conn):
        conn.execute("UPDATE export_artifacts SET state = 'ready', path = ?, sha256 = ?, size_bytes = ?, "
                     "edits_applied = ?, manifest_json = ?, built_at = ? WHERE id = ?",
                     (out_dir, result["sha256"], result["size"], result["edits"], json.dumps(manifest, sort_keys=True),
                      manifest["built_at"], art_id))
        conn.execute("UPDATE jobs SET state = 'completed', updated_at = ? WHERE id = ?", (now_iso(), job_id))
        audit(conn, job["workspace_id"], actor, "artifact.publish", art_id,
              f"job={job_id} approval={approval_id or ''} sha256={result['sha256']} edits={result['edits']}")
    return artifact_view(conn, art_id)


def _write_outputs(conn, job, src, approval_id, out_dir, fail_after_edits) -> Dict[str, Any]:
    job_id = job["id"]
    base, ext = os.path.splitext(src["filename"])
    out_name = f"{base}.CORRECTED{ext or '.txt'}"
    out_path = os.path.join(out_dir, out_name)
    tmp = out_path + ".part"
    edits = _edits(conn, job_id, approval_id) if approval_id else iter(())
    edit = next(edits, None)
    h = hashlib.sha256()
    size = 0
    applied = 0
    carry = ""
    pos = 0
    codec = src["encoding"] or "utf-8"
    enc = codecs.getincrementalencoder(codec)()

    def emit(fh, text: str) -> None:
        nonlocal size
        if not text:
            return
        data = enc.encode(text)
        h.update(data)
        size += len(data)
        fh.write(data)

    with open(tmp, "wb") as fh:
        for chunk_codec, chunk in text_chunks(src["path"], codec, CHUNK_BYTES):
            codec = chunk_codec
            text = carry + chunk
            base_pos = pos - len(carry)
            carry = ""
            cursor = 0
            while edit is not None and edit["char_offset"] < base_pos + len(text):
                rel = edit["char_offset"] - base_pos
                if rel < cursor:
                    raise ExportError("EDIT_OVERLAP", f"ordinal {edit['ordinal']} overlaps a previous edit")
                if rel + len(edit["raw"]) > len(text):
                    break                                   # raw span straddles the chunk end: carry it
                if text[rel:rel + len(edit["raw"])] != edit["raw"]:
                    raise ExportError("EDIT_MISMATCH",
                                      f"ordinal {edit['ordinal']} at offset {edit['char_offset']}: source text differs")
                emit(fh, text[cursor:rel])
                emit(fh, edit["candidate"])
                cursor = rel + len(edit["raw"])
                applied += 1
                if fail_after_edits is not None and applied >= fail_after_edits:
                    raise ExportError("SIMULATED_INTERRUPTION", f"after {applied} edits")
                edit = next(edits, None)
            if edit is not None and edit["char_offset"] < base_pos + len(text):
                rel = edit["char_offset"] - base_pos
                emit(fh, text[cursor:rel])
                carry = text[rel:]
            else:
                emit(fh, text[cursor:])
            pos = base_pos + len(text)
        if carry:
            emit(fh, carry)
        if edit is not None:
            raise ExportError("EDIT_BEYOND_END", f"ordinal {edit['ordinal']} offset {edit['char_offset']} past end")
        tail = enc.encode("", final=True)
        if tail:
            h.update(tail)
            size += len(tail)
            fh.write(tail)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, out_path)
    exceptions = _write_exceptions(conn, job_id, out_dir)
    _write_ledger(conn, job_id, approval_id, out_dir)
    return {"out_name": out_name, "sha256": h.hexdigest(), "size": size, "edits": applied,
            "exceptions": exceptions, "codec": codec}


def _csv_cell(v: Any) -> str:
    s = "" if v is None else str(v)
    if any(c in s for c in ',"\r\n'):
        return '"' + s.replace('"', '""') + '"'
    return s


def _write_exceptions(conn, job_id: str, out_dir: str) -> int:
    path = os.path.join(out_dir, "exceptions.csv")
    n = 0
    with open(path + ".part", "w", encoding="utf-8", newline="") as fh:
        fh.write("ordinal,location,raw,normalized,scheme,status,decision,computed_check,reason\r\n")
        for r in iter_rows(conn, "SELECT ordinal, location, raw, normalized, scheme, status, decision, computed_check, reason "
                                 "FROM observations WHERE job_id = ? AND (status IN ('flagged','invalid_structure') "
                                 "OR (candidate IS NOT NULL AND decision <> 'approved')) ORDER BY ordinal", (job_id,)):
            fh.write(",".join(_csv_cell(r[k]) for k in r.keys()) + "\r\n")
            n += 1
    os.replace(path + ".part", path)
    return n


def _write_ledger(conn, job_id: str, approval_id: Optional[str], out_dir: str) -> None:
    path = os.path.join(out_dir, "ledger.csv")
    with open(path + ".part", "w", encoding="utf-8", newline="") as fh:
        fh.write("ordinal,location,char_offset,before,after,approval_id\r\n")
        if approval_id:
            for r in _edits(conn, job_id, approval_id):
                fh.write(",".join([str(r["ordinal"]), _csv_cell(r["location"]), str(r["char_offset"]),
                                   _csv_cell(r["raw"]), _csv_cell(r["candidate"]), approval_id]) + "\r\n")
    os.replace(path + ".part", path)


def artifact_view(conn: sqlite3.Connection, artifact_id: str, replayed: bool = False) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM export_artifacts WHERE id = ?", (artifact_id,)).fetchone()
    if row is None:
        raise ExportError("UNKNOWN_ARTIFACT")
    out = dict(row)
    m = out.pop("manifest_json")
    out["manifest"] = json.loads(m) if m else None
    out["replayed"] = replayed
    return out


def artifact_file(conn: sqlite3.Connection, artifact_id: str, name: str) -> str:
    row = conn.execute("SELECT state, path, manifest_json FROM export_artifacts WHERE id = ?", (artifact_id,)).fetchone()
    if row is None or row["state"] != "ready":
        raise ExportError("ARTIFACT_NOT_READY")
    manifest = json.loads(row["manifest_json"])
    files = dict(manifest["files"])
    files["manifest"] = "manifest.json"
    if name not in files:
        raise ExportError("UNKNOWN_FILE", name)
    return os.path.join(row["path"], files[name])
