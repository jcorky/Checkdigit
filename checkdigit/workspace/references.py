"""
references.py
=============
Reference snapshots and enrichment requests.

A reference snapshot is data the workspace holds from an external source with
its version, retrieval time and licence note: the owner-code register a user
obtained from the BIC is the first. It feeds the prefix_registration layer.

An enrichment request names a provider, a purpose, the fields wanted, the
scope (a job's identifiers or an explicit list), an estimated request count
and a budget. It runs only after an administrator authorizes it, stops at
the budget, and keeps only what the provider's storage policy allows.
Providers come from the service's enrichment adapters, enabled solely by
credentials in the environment; an unconfigured provider fails the request
with that reason instead of pretending.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sqlite3
from typing import Any, Callable, Dict, List, Optional

import enrichment as enrich_mod

from .store import audit, new_id, now_iso, transaction

STATUSES = ("planned", "running", "paused", "completed", "failed")


class ReferenceError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------- #
# Owner-code register snapshot
# --------------------------------------------------------------------------- #

def import_owner_register(conn: sqlite3.Connection, root: str, workspace_id: str, actor: str, *, text: str,
                          version: str, license_note: str, source: str = "bic_owner_register") -> Dict[str, Any]:
    if not version.strip():
        raise ReferenceError("VERSION_REQUIRED", "name the register version or download date")
    if len(license_note.strip()) < 8:
        raise ReferenceError("LICENSE_REQUIRED", "record the licence terms the register was obtained under")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "code" not in rows[0]:
        raise ReferenceError("BAD_REGISTER", "expected a CSV with columns code,company,city,country")
    registry = enrich_mod.OwnerRegistry.from_rows(rows)
    if len(registry) == 0:
        raise ReferenceError("BAD_REGISTER", "no owner codes found")
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    snap_dir = os.path.join(root, "references", workspace_id)
    os.makedirs(snap_dir, exist_ok=True)
    sid = new_id("ref")
    path = os.path.join(snap_dir, f"{sid}.csv")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    with transaction(conn):
        conn.execute("INSERT INTO reference_snapshots (id, workspace_id, source, version, retrieved_at, license_note, path, sha256, "
                     "row_count, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (sid, workspace_id, source, version, now_iso(), license_note, path, sha, len(registry), actor, now_iso()))
        conn.execute("DELETE FROM owner_register WHERE workspace_id = ?", (workspace_id,))
        conn.executemany("INSERT INTO owner_register (workspace_id, prefix, company, city, country, snapshot_id) VALUES (?,?,?,?,?,?)",
                         [(workspace_id, code, info.company, info.city, info.country, sid) for code, info in registry._by_code.items()])
        audit(conn, workspace_id, actor, "reference.import", sid, f"{source} {version} {len(registry)} codes")
    return snapshot_doc(conn, sid)


def snapshot_doc(conn: sqlite3.Connection, snapshot_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM reference_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    if row is None:
        raise ReferenceError("UNKNOWN_SNAPSHOT")
    return {"id": row["id"], "workspace_id": row["workspace_id"], "source": row["source"], "version": row["version"],
            "retrieved_at": row["retrieved_at"], "license_note": row["license_note"], "sha256": row["sha256"],
            "row_count": row["row_count"], "created_by": row["created_by"], "created_at": row["created_at"]}


def list_snapshots(conn: sqlite3.Connection, workspace_id: str) -> List[Dict[str, Any]]:
    return [snapshot_doc(conn, r["id"]) for r in conn.execute("SELECT id FROM reference_snapshots WHERE workspace_id = ? ORDER BY id",
                                                              (workspace_id,))]


def prefix_registration(conn: sqlite3.Connection, workspace_id: str, normalized: str) -> Optional[Dict[str, Any]]:
    """None when the workspace holds no register; else {registered, owner, snapshot version}."""
    snap = conn.execute("SELECT id, version, source FROM reference_snapshots WHERE workspace_id = ? AND source LIKE '%owner%' "
                        "ORDER BY rowid DESC LIMIT 1", (workspace_id,)).fetchone()
    if snap is None:
        return None
    prefix = (normalized or "")[:3].upper()
    row = conn.execute("SELECT company, country FROM owner_register WHERE workspace_id = ? AND prefix = ?", (workspace_id, prefix)).fetchone()
    return {"registered": row is not None, "prefix": prefix, "owner": row["company"] if row else None,
            "country": row["country"] if row else None, "version": snap["version"], "source": snap["source"]}


# --------------------------------------------------------------------------- #
# Enrichment requests
# --------------------------------------------------------------------------- #

def create_request(conn: sqlite3.Connection, workspace_id: str, actor: str, *, provider: str, purpose: str, fields: List[str],
                   scope: Dict[str, Any], estimated_requests: int, budget: Dict[str, Any],
                   storage_policy: str = "displayed_fields_only") -> Dict[str, Any]:
    if not provider.strip() or not purpose.strip():
        raise ReferenceError("BAD_REQUEST", "provider and purpose are required")
    if not isinstance(scope, dict) or not (scope.get("job_id") or scope.get("identifiers")):
        raise ReferenceError("BAD_REQUEST", "scope names a job_id or an identifiers list")
    if scope.get("job_id") and conn.execute("SELECT 1 FROM jobs WHERE id = ? AND workspace_id = ?", (scope["job_id"], workspace_id)).fetchone() is None:
        raise ReferenceError("BAD_REQUEST", "unknown job in scope")
    if not isinstance(budget, dict) or int(budget.get("max_requests", 0)) <= 0:
        raise ReferenceError("BAD_REQUEST", "budget.max_requests must be a positive integer")
    rid = new_id("enr")
    ts = now_iso()
    with transaction(conn):
        conn.execute("INSERT INTO enrichment_requests (id, workspace_id, provider, purpose, fields_json, scope_json, estimated_requests, "
                     "budget_json, status, storage_policy, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,'planned',?,?,?,?)",
                     (rid, workspace_id, provider, purpose, json.dumps(list(fields)), json.dumps(scope, sort_keys=True), int(estimated_requests),
                      json.dumps(budget, sort_keys=True), storage_policy, actor, ts, ts))
        audit(conn, workspace_id, actor, "enrichment.request", rid, f"{provider} {purpose} est={estimated_requests}")
    return request_doc(conn, rid)


def authorize_request(conn: sqlite3.Connection, request_id: str, actor: str, note: str) -> Dict[str, Any]:
    row = conn.execute("SELECT workspace_id, status FROM enrichment_requests WHERE id = ?", (request_id,)).fetchone()
    if row is None:
        raise ReferenceError("UNKNOWN_REQUEST")
    if len(note.strip()) < 8:
        raise ReferenceError("EVIDENCE_REQUIRED", "name the authorization (who approved the provider terms and the spend)")
    with transaction(conn):
        conn.execute("UPDATE enrichment_requests SET authorized_by = ?, authorized_at = ?, authorization_note = ?, updated_at = ? WHERE id = ?",
                     (actor, now_iso(), note, now_iso(), request_id))
        audit(conn, row["workspace_id"], actor, "enrichment.authorize", request_id, note)
    return request_doc(conn, request_id)


def default_providers() -> Dict[str, Callable[[str], Dict[str, str]]]:
    """Adapters enabled by credentials in the environment; nothing else."""
    out: Dict[str, Callable[[str], Dict[str, str]]] = {}
    try:
        service = enrich_mod.EnrichmentService.build_from_env()
    except Exception:  # noqa: BLE001
        return out
    for e in getattr(service, "extra", []) or []:
        try:
            enabled = e.enabled
            enabled = enabled() if callable(enabled) else enabled
            if enabled:
                out[str(getattr(e, "name", type(e).__name__)).lower()] = e.enrich
        except Exception:  # noqa: BLE001
            continue
    return out


def _scope_identifiers(conn: sqlite3.Connection, workspace_id: str, scope: Dict[str, Any]) -> List[str]:
    if scope.get("identifiers"):
        return sorted({str(i).strip().upper() for i in scope["identifiers"] if str(i).strip()})
    rows = conn.execute("SELECT DISTINCT normalized FROM observations WHERE job_id = ? AND status IN ('valid','corrected') "
                        "ORDER BY normalized", (scope["job_id"],)).fetchall()
    return [r["normalized"] for r in rows]


def run_request(conn: sqlite3.Connection, request_id: str, actor: str, *, providers: Optional[Dict[str, Callable]] = None) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM enrichment_requests WHERE id = ?", (request_id,)).fetchone()
    if row is None:
        raise ReferenceError("UNKNOWN_REQUEST")
    if not row["authorized_by"]:
        raise ReferenceError("NOT_AUTHORIZED", "an administrator authorizes the request before any provider is called")
    if row["status"] in ("completed", "failed"):
        return request_doc(conn, request_id)
    ws = row["workspace_id"]
    providers = providers if providers is not None else default_providers()
    fn = providers.get(row["provider"])
    if fn is None:
        with transaction(conn):
            conn.execute("UPDATE enrichment_requests SET status = 'failed', results_json = ?, updated_at = ? WHERE id = ?",
                         (json.dumps({"error": f"provider {row['provider']} is not configured (no credentials in the environment)"}),
                          now_iso(), request_id))
        return request_doc(conn, request_id)
    policy = enrich_mod.storage_policy_for(row["provider"])
    budget = json.loads(row["budget_json"] or "{}")
    max_requests = int(budget.get("max_requests", 0))
    fields = json.loads(row["fields_json"] or "[]")
    scope = json.loads(row["scope_json"] or "{}")
    done = conn.execute("SELECT COUNT(*) FROM enrichment_results WHERE request_id = ?", (request_id,)).fetchone()[0]
    used = int(json.loads(row["results_json"] or "{}").get("requests_made", 0))
    with transaction(conn):
        conn.execute("UPDATE enrichment_requests SET status = 'running', updated_at = ? WHERE id = ?", (now_iso(), request_id))
    identifiers = _scope_identifiers(conn, ws, scope)
    pending = [i for i in identifiers if conn.execute("SELECT 1 FROM enrichment_results WHERE request_id = ? AND identifier = ?",
                                                      (request_id, i)).fetchone() is None]
    errors: List[str] = []
    made = 0
    status = "completed"
    for ident in pending:
        if used + made >= max_requests:
            status = "paused"
            break
        try:
            raw = fn(ident) or {}
            made += 1
        except Exception as exc:  # noqa: BLE001
            made += 1
            errors.append(f"{ident}: {type(exc).__name__}: {exc}"[:200])
            if len(errors) >= 5:
                status = "failed"
                break
            continue
        flat = {k: str(v) for k, v in raw.items() if not fields or k in fields or k == "source"}
        kept, _payload = policy.apply(flat, None)
        with transaction(conn):
            conn.execute("INSERT OR REPLACE INTO enrichment_results (workspace_id, request_id, identifier, provider, fields_json, retrieved_at) "
                         "VALUES (?,?,?,?,?,?)", (ws, request_id, ident, row["provider"], json.dumps(kept, sort_keys=True), now_iso()))
    results = {"requests_made": used + made, "identifiers": len(identifiers), "enriched": done + made - len(errors),
               "errors": errors, "budget_max_requests": max_requests, "storage_policy_note": policy.note,
               "retained_fields": list(policy.retain_fields) if policy.retain_fields else "all normalized fields"}
    with transaction(conn):
        conn.execute("UPDATE enrichment_requests SET status = ?, results_json = ?, updated_at = ? WHERE id = ?",
                     (status, json.dumps(results, sort_keys=True), now_iso(), request_id))
        audit(conn, ws, actor, "enrichment.run", request_id, f"status={status} made={made}")
    return request_doc(conn, request_id)


def request_doc(conn: sqlite3.Connection, request_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM enrichment_requests WHERE id = ?", (request_id,)).fetchone()
    if row is None:
        raise ReferenceError("UNKNOWN_REQUEST")
    return {"id": row["id"], "workspace_id": row["workspace_id"], "provider": row["provider"], "purpose": row["purpose"],
            "fields": json.loads(row["fields_json"] or "[]"), "scope": json.dumps(json.loads(row["scope_json"] or "{}"), sort_keys=True),
            "scope_detail": json.loads(row["scope_json"] or "{}"), "authorized_by": row["authorized_by"] or "",
            "authorized_at": row["authorized_at"], "authorization_note": row["authorization_note"],
            "estimated_requests": row["estimated_requests"], "budget": json.loads(row["budget_json"] or "{}"),
            "status": row["status"], "storage_policy": row["storage_policy"], "results": json.loads(row["results_json"] or "{}"),
            "created_by": row["created_by"], "created_at": row["created_at"], "updated_at": row["updated_at"]}


def list_requests(conn: sqlite3.Connection, workspace_id: str) -> List[Dict[str, Any]]:
    return [request_doc(conn, r["id"]) for r in conn.execute("SELECT id FROM enrichment_requests WHERE workspace_id = ? ORDER BY id",
                                                             (workspace_id,))]


def results_for(conn: sqlite3.Connection, request_id: str, after: str = "", limit: int = 200) -> List[Dict[str, Any]]:
    rows = conn.execute("SELECT identifier, provider, fields_json, retrieved_at FROM enrichment_results WHERE request_id = ? "
                        "AND identifier > ? ORDER BY identifier LIMIT ?", (request_id, after, limit)).fetchall()
    return [{"identifier": r["identifier"], "provider": r["provider"], "fields": json.loads(r["fields_json"]), "retrieved_at": r["retrieved_at"]}
            for r in rows]
