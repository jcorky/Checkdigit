"""
api.py
======
Versioned workspace routes under /v1/workspaces/{workspace}. Mounted by
checkdigit/api.py behind the admin dependency; nothing here is public.

Conventions:
  * every entity is looked up within the workspace of the path, so an id
    from another workspace answers 404;
  * responses carry the contract-shaped entity (contracts/entities.schema.json)
    plus operational fields;
  * list endpoints take `cursor` and `limit` and answer `next_cursor`;
  * writes that must be safe to retry take an operation id (`operation_id`
    or `idempotency_key`) and replay the recorded outcome;
  * uploads are resumable: create, append at an offset, complete;
  * roles: viewer reads, analyst uploads and runs jobs, reviewer decides and
    exports, administrator publishes, purges and assigns roles. A workspace
    with no role assignments treats every admin-session user as administrator.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import (automation, connections as conn_mod, context as ctx_mod, export as export_mod, feedback as feedback_mod, ingest,
               jobs as jobq, maintenance, profiles, reconcile, references, review, runner, store, transmit, views)

INSPECTION_MAX_BYTES = 32 * 1024 * 1024

UPLOAD_PART_MAX = 32 * 1024 * 1024


class IntentIn(BaseModel):
    source_file_id: str
    mode: str
    scope_kind: str = "source_fleet"
    scope_value: str = "all"
    effective_time: str = ""
    baseline_generation_id: Optional[str] = None
    field_update_policy: str = "unknown_blocks"
    change_volume_limit: Optional[int] = None
    empty_scope_decision: Optional[str] = None
    declared_record_count: Optional[int] = None


class JobIn(BaseModel):
    source_file_id: str
    import_intent_id: Optional[str] = None
    profile_id: Optional[str] = None
    options: Dict[str, Any] = Field(default_factory=dict)


class ProfileIn(BaseModel):
    name: str
    mapping_contract: Optional[Dict[str, Any]] = None
    rules: Optional[Dict[str, Any]] = None
    system: str = ""
    system_version: str = ""
    terminal_site: str = ""
    partner: str = ""
    message_family: str = ""
    message_version: str = ""
    correction_policy: Optional[Dict[str, Any]] = None
    output_encoding: str = ""
    acceptance_fixtures: Optional[List[str]] = None


class VerificationIn(BaseModel):
    state: str
    note: str = ""


class DeliveryIn(BaseModel):
    destination: str
    state: str
    idempotency_key: str
    control_reference: Optional[str] = None
    outcome_evidence: Optional[str] = None
    started_at: Optional[str] = None


class FeedbackIn(BaseModel):
    origin: str = "manual_upload"
    text: Optional[str] = None
    manual: Optional[Dict[str, Any]] = None
    response_profile_id: Optional[str] = None
    decision_by: Optional[str] = None


class ConnectionIn(BaseModel):
    name: str
    kind: str
    direction: str
    config: Dict[str, Any] = Field(default_factory=dict)
    partner: str = ""
    profile_id: Optional[str] = None
    duplicate_handling: str = "unknown"


class NoteIn(BaseModel):
    note: str = ""


class TransmitIn(BaseModel):
    connection_id: str
    idempotency_key: str


class ResendIn(BaseModel):
    idempotency_key: str
    note: Optional[str] = None


class OwnerRegisterIn(BaseModel):
    text: str
    version: str
    license_note: str
    source: str = "bic_owner_register"


class EnrichmentIn(BaseModel):
    provider: str
    purpose: str
    fields: List[str] = Field(default_factory=list)
    scope: Dict[str, Any]
    estimated_requests: int = 0
    budget: Dict[str, Any] = Field(default_factory=dict)
    storage_policy: str = "displayed_fields_only"


class DecisionIn(BaseModel):
    filter: Dict[str, Any] = Field(default_factory=dict)
    decision: str
    expected_count: Optional[int] = None
    operation_id: Optional[str] = None
    authorization_scope: str = "draft"


class ExportIn(BaseModel):
    approval_id: Optional[str] = None
    idempotency_key: str


class PublishIn(BaseModel):
    operation_id: Optional[str] = None


class UploadIn(BaseModel):
    filename: str
    size: int = Field(ge=0)


class CompleteIn(BaseModel):
    sha256: Optional[str] = None
    content_type: str = ""


class RoleIn(BaseModel):
    role: str


class ReanalyzeIn(BaseModel):
    reason: str = "re-analysis requested"


class WorkspaceIn(BaseModel):
    name: Optional[str] = None
    retention_days: Optional[int] = Field(default=None, ge=0)
    third_party_data_retention: Optional[str] = None


def build_router(admin_required: Callable[..., Any], root: str, db_path: Optional[str] = None,
                 policy_provider: Optional[Callable[[], Any]] = None) -> APIRouter:
    os.makedirs(root, exist_ok=True)
    db_path = db_path or os.path.join(root, "workspace.db")
    router = APIRouter(prefix="/v1/workspaces")
    policy_provider = policy_provider or (lambda: None)

    def conn_for(workspace: str) -> sqlite3.Connection:
        conn = store.connect(db_path)
        store.ensure_workspace(conn, workspace)
        return conn

    def need(level: str):
        def dep(workspace: str, identity: Any = Depends(admin_required)) -> str:
            user = str(identity) if identity else "admin"
            conn = conn_for(workspace)
            try:
                role = store.role_of(conn, workspace, user)
            finally:
                conn.close()
            if not store.has_role(role, level):
                raise HTTPException(status_code=403, detail={"code": "ROLE_REQUIRED", "required": level,
                                                             "role": role, "user": user})
            return user
        return dep

    def owned(conn: sqlite3.Connection, table: str, entity_id: str, workspace: str) -> sqlite3.Row:
        if table in ("jobs", "generations", "source_files", "uploads", "import_intents"):
            row = conn.execute(f"SELECT * FROM {table} WHERE id = ? AND workspace_id = ?", (entity_id, workspace)).fetchone()
        elif table == "approvals":
            row = conn.execute("SELECT a.* FROM approvals a JOIN jobs j ON j.id = a.job_id WHERE a.id = ? AND j.workspace_id = ?",
                               (entity_id, workspace)).fetchone()
        elif table == "export_artifacts":
            row = conn.execute("SELECT e.* FROM export_artifacts e JOIN jobs j ON j.id = e.job_id WHERE e.id = ? AND j.workspace_id = ?",
                               (entity_id, workspace)).fetchone()
        elif table == "change_sets":
            row = conn.execute("SELECT c.* FROM change_sets c JOIN jobs j ON j.id = c.job_id WHERE c.id = ? AND j.workspace_id = ?",
                               (entity_id, workspace)).fetchone()
        else:
            row = None
        if row is None:
            raise HTTPException(status_code=404, detail=f"{table[:-1]} {entity_id} not found in workspace {workspace}")
        return row

    def conflict(exc: Exception) -> HTTPException:
        code = getattr(exc, "code", type(exc).__name__)
        detail = getattr(exc, "detail", str(exc))
        return HTTPException(status_code=409, detail={"code": code, "detail": detail})

    # ---- workspace, roles, usage ----------------------------------------- #

    @router.get("/{workspace}")
    def workspace_view(workspace: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            return views.workspace_doc(conn, conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace,)).fetchone())
        finally:
            conn.close()

    @router.patch("/{workspace}")
    def workspace_update(workspace: str, body: WorkspaceIn, actor: str = Depends(need("administrator"))):
        conn = conn_for(workspace)
        try:
            with store.transaction(conn):
                if body.name is not None:
                    conn.execute("UPDATE workspaces SET name = ? WHERE id = ?", (body.name, workspace))
                if body.retention_days is not None:
                    conn.execute("UPDATE workspaces SET retention_days = ? WHERE id = ?", (body.retention_days, workspace))
                if body.third_party_data_retention is not None:
                    conn.execute("UPDATE workspaces SET third_party_data_retention = ? WHERE id = ?",
                                 (body.third_party_data_retention, workspace))
                store.audit(conn, workspace, actor, "workspace.update", workspace, body.model_dump_json(exclude_none=True))
            return views.workspace_doc(conn, conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace,)).fetchone())
        finally:
            conn.close()

    @router.get("/{workspace}/roles")
    def list_roles(workspace: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            return {"items": views.role_docs(store.roles(conn, workspace)), "next_cursor": None}
        finally:
            conn.close()

    @router.put("/{workspace}/roles/{user_id}")
    def put_role(workspace: str, user_id: str, body: RoleIn, actor: str = Depends(need("administrator"))):
        conn = conn_for(workspace)
        try:
            try:
                with store.transaction(conn):
                    store.set_role(conn, workspace, user_id, body.role)
                    store.audit(conn, workspace, actor, "role.set", user_id, body.role)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            return {"user_id": user_id, "workspace_id": workspace, "role": body.role}
        finally:
            conn.close()

    @router.delete("/{workspace}/roles/{user_id}")
    def delete_role(workspace: str, user_id: str, actor: str = Depends(need("administrator"))):
        conn = conn_for(workspace)
        try:
            with store.transaction(conn):
                conn.execute("DELETE FROM user_roles WHERE workspace_id = ? AND user_id = ?", (workspace, user_id))
                store.audit(conn, workspace, actor, "role.delete", user_id)
            return {"user_id": user_id, "workspace_id": workspace, "role": None}
        finally:
            conn.close()

    @router.get("/{workspace}/usage")
    def usage_view(workspace: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            out = {"bytes": store.usage(root, workspace), "budget_bytes": store.MAX_BYTES or None,
                   "min_free_bytes": store.MIN_FREE_BYTES}
            out["rows"] = {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE workspace_id = ?", (workspace,)).fetchone()[0]
                           for t in ("source_files", "jobs", "generations", "fleet")}
            return out
        finally:
            conn.close()

    @router.post("/{workspace}/maintenance/purge")
    def purge(workspace: str, actor: str = Depends(need("administrator"))):
        conn = conn_for(workspace)
        try:
            return maintenance.purge(conn, root, workspace, actor)
        finally:
            conn.close()

    # ---- uploads (resumable) ---------------------------------------------- #

    @router.post("/{workspace}/uploads")
    def create_upload(workspace: str, body: UploadIn, actor: str = Depends(need("analyst"))):
        conn = conn_for(workspace)
        try:
            try:
                store.check_store_budget(root, workspace, body.size)
            except store.BudgetExceeded as exc:
                raise HTTPException(status_code=413, detail={"code": "RESOURCE_BUDGET_EXCEEDED", "detail": exc.detail})
            uid = store.new_id("upl")
            path = os.path.join(store.data_dir(root, "uploads", workspace), uid + ".part")
            open(path, "wb").close()
            conn.execute("INSERT INTO uploads (id, workspace_id, filename, declared_size, path, created_at) VALUES (?,?,?,?,?,?)",
                         (uid, workspace, os.path.basename(body.filename), body.size, path, store.now_iso()))
            return {"upload_id": uid, "received_bytes": 0, "declared_size": body.size}
        finally:
            conn.close()

    @router.put("/{workspace}/uploads/{upload_id}")
    async def append_upload(workspace: str, upload_id: str, request: Request, offset: int = Query(ge=0),
                            actor: str = Depends(need("analyst"))):
        data = await request.body()
        if len(data) > UPLOAD_PART_MAX:
            raise HTTPException(status_code=413, detail="upload part too large")
        conn = conn_for(workspace)
        try:
            up = owned(conn, "uploads", upload_id, workspace)
            if up["state"] != "open":
                raise HTTPException(status_code=409, detail="upload is closed")
            if offset != up["received_bytes"]:
                raise HTTPException(status_code=409, detail={"expected_offset": up["received_bytes"]})
            if up["received_bytes"] + len(data) > up["declared_size"]:
                raise HTTPException(status_code=413, detail="upload exceeds its declared size")
            with open(up["path"], "r+b") as fh:
                fh.seek(offset)
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            conn.execute("UPDATE uploads SET received_bytes = ? WHERE id = ?", (offset + len(data), upload_id))
            return {"upload_id": upload_id, "received_bytes": offset + len(data)}
        finally:
            conn.close()

    @router.get("/{workspace}/uploads/{upload_id}")
    def upload_status(workspace: str, upload_id: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            up = owned(conn, "uploads", upload_id, workspace)
            return {"upload_id": upload_id, "received_bytes": up["received_bytes"], "declared_size": up["declared_size"],
                    "state": up["state"], "source_file_id": up["source_file_id"]}
        finally:
            conn.close()

    @router.post("/{workspace}/uploads/{upload_id}/complete")
    def complete_upload(workspace: str, upload_id: str, body: CompleteIn, actor: str = Depends(need("analyst"))):
        conn = conn_for(workspace)
        try:
            up = owned(conn, "uploads", upload_id, workspace)
            if up["state"] == "complete":
                return {"upload_id": upload_id, "source_file_id": up["source_file_id"], "replayed": True}
            if up["received_bytes"] != up["declared_size"]:
                raise HTTPException(status_code=409, detail={"expected_offset": up["received_bytes"],
                                                             "declared_size": up["declared_size"]})
            sha, _size = ingest.hash_file(up["path"])
            if body.sha256 and body.sha256.lower() != sha:
                raise HTTPException(status_code=422, detail={"sha256_expected": body.sha256, "sha256_received": sha})
            with store.transaction(conn):
                sid = ingest.register_source(conn, root, workspace, up["filename"], up["path"], body.content_type, move=True)
                conn.execute("UPDATE uploads SET state = 'complete', source_file_id = ? WHERE id = ?", (sid, upload_id))
            return {"upload_id": upload_id, "source_file_id": sid, "sha256": sha, "replayed": False}
        finally:
            conn.close()

    @router.get("/{workspace}/sources/{source_id}")
    def source_view(workspace: str, source_id: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            return views.source_doc(owned(conn, "source_files", source_id, workspace))
        finally:
            conn.close()

    # ---- intents and jobs ------------------------------------------------- #

    @router.post("/{workspace}/intents")
    def create_intent(workspace: str, body: IntentIn, actor: str = Depends(need("analyst"))):
        conn = conn_for(workspace)
        try:
            owned(conn, "source_files", body.source_file_id, workspace)
            if body.baseline_generation_id:
                owned(conn, "generations", body.baseline_generation_id, workspace)
            try:
                with store.transaction(conn):
                    iid, dup = ingest.create_intent(conn, workspace, **body.model_dump())
            except ingest.IngestError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            out = views.intent_doc(conn, conn.execute("SELECT * FROM import_intents WHERE id = ?", (iid,)).fetchone())
            out["import_intent_id"] = iid
            out["duplicate"] = dup
            return out
        finally:
            conn.close()

    @router.get("/{workspace}/intents/{intent_id}")
    def intent_view(workspace: str, intent_id: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            return views.intent_doc(conn, owned(conn, "import_intents", intent_id, workspace))
        finally:
            conn.close()

    @router.post("/{workspace}/jobs", status_code=202)
    def create_job(workspace: str, body: JobIn, actor: str = Depends(need("analyst"))):
        conn = conn_for(workspace)
        try:
            owned(conn, "source_files", body.source_file_id, workspace)
            if body.import_intent_id:
                owned(conn, "import_intents", body.import_intent_id, workspace)
            if body.profile_id:
                conn.execute("SELECT 1").fetchone()
                if conn.execute("SELECT 1 FROM feed_profiles WHERE id = ? AND workspace_id = ?",
                                (body.profile_id, workspace)).fetchone() is None:
                    raise HTTPException(status_code=404, detail=f"profile {body.profile_id} not found in workspace {workspace}")
            try:
                return runner.submit(conn, workspace_id=workspace, source_file_id=body.source_file_id,
                                     intent_id=body.import_intent_id, options=body.options, actor=actor,
                                     profile_id=body.profile_id)
            except ingest.IngestError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
        finally:
            conn.close()

    @router.get("/{workspace}/jobs")
    def list_jobs(workspace: str, cursor: str = "", limit: int = Query(50, ge=1, le=500),
                  actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            rows = conn.execute("SELECT * FROM jobs WHERE workspace_id = ? AND id > ? ORDER BY id LIMIT ?",
                                (workspace, cursor, limit + 1)).fetchall()
            items = [views.job_doc(conn, r) for r in rows[:limit]]
            return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None}
        finally:
            conn.close()

    @router.get("/{workspace}/jobs/{job_id}")
    def job_view(workspace: str, job_id: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            return views.job_doc(conn, owned(conn, "jobs", job_id, workspace))
        finally:
            conn.close()

    @router.post("/{workspace}/jobs/{job_id}/cancel")
    def cancel_job(workspace: str, job_id: str, actor: str = Depends(need("analyst"))):
        conn = conn_for(workspace)
        try:
            owned(conn, "jobs", job_id, workspace)
            jobq.cancel(conn, job_id)
            store.audit(conn, workspace, actor, "job.cancel", job_id)
            return {"job_id": job_id, "cancel_requested": True}
        finally:
            conn.close()

    @router.post("/{workspace}/jobs/{job_id}/run")
    def run_job_inline(workspace: str, job_id: str, actor: str = Depends(need("analyst"))):
        """Run queued work in this process (deployments without a worker process)."""
        conn = conn_for(workspace)
        try:
            owned(conn, "jobs", job_id, workspace)
            n = runner.run_until_idle(conn, root, max_jobs=10, policy=policy_provider())
            out = views.job_doc(conn, owned(conn, "jobs", job_id, workspace))
            out["ran"] = n
            return out
        finally:
            conn.close()

    @router.get("/{workspace}/jobs/{job_id}/observations")
    def observations(workspace: str, job_id: str, cursor: str = "-1", limit: int = Query(200, ge=1, le=1000),
                     status: Optional[str] = None, scheme: Optional[str] = None, prefix: Optional[str] = None,
                     decision: Optional[str] = None, token: Optional[str] = None,
                     actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            owned(conn, "jobs", job_id, workspace)
            flt = {k: v for k, v in (("status", status), ("scheme", scheme), ("prefix", prefix),
                                     ("decision", decision), ("token", token)) if v}
            try:
                return review.observations_page(conn, job_id, flt, after=int(cursor), limit=limit)
            except (review.ReviewError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=str(exc))
        finally:
            conn.close()

    @router.get("/{workspace}/jobs/{job_id}/observations/count")
    def observation_count(workspace: str, job_id: str, status: Optional[str] = None, scheme: Optional[str] = None,
                          prefix: Optional[str] = None, decision: Optional[str] = None, token: Optional[str] = None,
                          actor: str = Depends(need("viewer"))):
        """How many observations a filter matches, and how many of them are undecided proposals."""
        conn = conn_for(workspace)
        try:
            owned(conn, "jobs", job_id, workspace)
            flt = {k: v for k, v in (("status", status), ("scheme", scheme), ("prefix", prefix),
                                     ("decision", decision), ("token", token)) if v}
            try:
                where, params = review._where(job_id, flt)
            except review.ReviewError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            population = (flt["decision"],) if "decision" in flt else review.DEFAULT_POPULATION
            total = conn.execute(f"SELECT COUNT(*) FROM observations WHERE {where}", params).fetchone()[0]
            proposals = conn.execute(f"SELECT COUNT(*) FROM observations WHERE {where} AND candidate IS NOT NULL AND decision IN (%s)"
                                     % ",".join("?" * len(population)), params + list(population)).fetchone()[0]
            return {"count": total, "proposals": proposals}
        finally:
            conn.close()

    @router.get("/{workspace}/jobs/{job_id}/findings")
    def findings(workspace: str, job_id: str, cursor: str = "0", limit: int = Query(200, ge=1, le=1000),
                 code: Optional[str] = None, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            owned(conn, "jobs", job_id, workspace)
            page = review.findings_page(conn, job_id, after=int(cursor), limit=limit, code=code)
            rows = conn.execute("SELECT * FROM findings WHERE job_id = ? AND id IN (%s) ORDER BY id" %
                                ",".join("?" * len(page["items"])), (job_id, *[i["id"] for i in page["items"]])).fetchall() \
                if page["items"] else []
            return {"items": [views.finding_doc(r) for r in rows], "next_cursor": page["next_cursor"]}
        finally:
            conn.close()

    @router.get("/{workspace}/jobs/{job_id}/change-set")
    def change_set(workspace: str, job_id: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            owned(conn, "jobs", job_id, workspace)
            return views.change_set_doc(conn, review.ensure_change_set(conn, job_id))
        finally:
            conn.close()

    @router.post("/{workspace}/jobs/{job_id}/decisions")
    def decide(workspace: str, job_id: str, body: DecisionIn, actor: str = Depends(need("reviewer"))):
        conn = conn_for(workspace)
        try:
            job = owned(conn, "jobs", job_id, workspace)
            if job["state"] not in ("awaiting_review", "completed"):
                raise HTTPException(status_code=409, detail={"code": "JOB_NOT_REVIEWABLE", "detail": f"job is {job['state']}"})
            try:
                out = review.decide(conn, job_id, body.filter, body.decision, actor,
                                    operation_id=body.operation_id, authorization_scope=body.authorization_scope,
                                    expected_count=body.expected_count)
            except review.ReviewError as exc:
                raise conflict(exc)
            doc = views.approval_doc(conn.execute("SELECT * FROM approvals WHERE id = ?", (out["id"],)).fetchone())
            doc["replayed"] = out["replayed"]
            doc["selection_count"] = out["selection_count"]
            doc["selection_digest"] = out["selection_digest"]
            return doc
        finally:
            conn.close()

    @router.get("/{workspace}/jobs/{job_id}/approvals")
    def list_approvals(workspace: str, job_id: str, cursor: str = "", limit: int = Query(100, ge=1, le=500),
                       actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            owned(conn, "jobs", job_id, workspace)
            rows = conn.execute("SELECT * FROM approvals WHERE job_id = ? AND id > ? ORDER BY id LIMIT ?",
                                (job_id, cursor, limit + 1)).fetchall()
            items = [views.approval_doc(r) for r in rows[:limit]]
            return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None}
        finally:
            conn.close()

    @router.get("/{workspace}/approvals/{approval_id}")
    def approval(workspace: str, approval_id: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            row = owned(conn, "approvals", approval_id, workspace)
            return views.approval_doc(row, review.check_approval(conn, approval_id))
        finally:
            conn.close()

    @router.post("/{workspace}/jobs/{job_id}/reanalyze")
    def reanalyze(workspace: str, job_id: str, body: ReanalyzeIn = ReanalyzeIn(), actor: str = Depends(need("analyst"))):
        conn = conn_for(workspace)
        try:
            owned(conn, "jobs", job_id, workspace)
            try:
                return review.reanalyze(conn, job_id, body.reason, policy=policy_provider())
            except review.ReviewError as exc:
                raise conflict(exc)
        finally:
            conn.close()

    # ---- exports ---------------------------------------------------------- #

    @router.post("/{workspace}/jobs/{job_id}/exports", status_code=201)
    def create_export(workspace: str, job_id: str, body: ExportIn, actor: str = Depends(need("reviewer"))):
        conn = conn_for(workspace)
        try:
            owned(conn, "jobs", job_id, workspace)
            if body.approval_id:
                owned(conn, "approvals", body.approval_id, workspace)
            try:
                out = export_mod.build_artifact(conn, root, job_id, body.approval_id, actor,
                                                idempotency_key=f"{workspace}:{job_id}:{body.idempotency_key}")
            except (export_mod.ExportError, review.ReviewError) as exc:
                raise conflict(exc)
            doc = views.artifact_doc(conn.execute("SELECT * FROM export_artifacts WHERE id = ?", (out["id"],)).fetchone())
            doc["replayed"] = out["replayed"]
            return doc
        finally:
            conn.close()

    @router.get("/{workspace}/jobs/{job_id}/artifacts")
    def list_artifacts(workspace: str, job_id: str, cursor: str = "", limit: int = Query(100, ge=1, le=500),
                       actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            owned(conn, "jobs", job_id, workspace)
            rows = conn.execute("SELECT * FROM export_artifacts WHERE job_id = ? AND id > ? ORDER BY id LIMIT ?",
                                (job_id, cursor, limit + 1)).fetchall()
            items = [views.artifact_doc(r) for r in rows[:limit]]
            return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None}
        finally:
            conn.close()

    @router.get("/{workspace}/artifacts/{artifact_id}")
    def artifact(workspace: str, artifact_id: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            return views.artifact_doc(owned(conn, "export_artifacts", artifact_id, workspace))
        finally:
            conn.close()

    @router.get("/{workspace}/artifacts/{artifact_id}/files/{name}")
    def artifact_file(workspace: str, artifact_id: str, name: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            owned(conn, "export_artifacts", artifact_id, workspace)
            try:
                path = export_mod.artifact_file(conn, artifact_id, name)
            except export_mod.ExportError as exc:
                raise HTTPException(status_code=409 if exc.code == "ARTIFACT_NOT_READY" else 404, detail=exc.code)
            return FileResponse(path, filename=os.path.basename(path), media_type="application/octet-stream")
        finally:
            conn.close()

    # ---- generations and fleet ------------------------------------------- #

    @router.get("/{workspace}/generations")
    def list_generations(workspace: str, cursor: str = "", limit: int = Query(100, ge=1, le=500),
                         actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            rows = conn.execute("SELECT * FROM generations WHERE workspace_id = ? AND id > ? ORDER BY id LIMIT ?",
                                (workspace, cursor, limit + 1)).fetchall()
            items = [views.generation_doc(r) for r in rows[:limit]]
            return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None}
        finally:
            conn.close()

    @router.get("/{workspace}/generations/{generation_id}")
    def generation(workspace: str, generation_id: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            return views.generation_doc(owned(conn, "generations", generation_id, workspace))
        finally:
            conn.close()

    @router.get("/{workspace}/generations/{generation_id}/members")
    def members(workspace: str, generation_id: str, cursor: str = "", limit: int = Query(500, ge=1, le=5000),
                actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            owned(conn, "generations", generation_id, workspace)
            items = reconcile.members_page(conn, generation_id, after=cursor, limit=limit + 1)
            page = items[:limit]
            return {"items": page, "next_cursor": page[-1]["key"] if len(items) > limit else None}
        finally:
            conn.close()

    @router.post("/{workspace}/generations/{generation_id}/publish")
    def publish(workspace: str, generation_id: str, body: PublishIn, actor: str = Depends(need("administrator"))):
        conn = conn_for(workspace)
        try:
            owned(conn, "generations", generation_id, workspace)
            try:
                return reconcile.publish(conn, generation_id, actor, operation_id=body.operation_id)
            except reconcile.PublishError as exc:
                raise conflict(exc)
        finally:
            conn.close()

    @router.post("/{workspace}/generations/{generation_id}/abandon")
    def abandon(workspace: str, generation_id: str, actor: str = Depends(need("administrator"))):
        conn = conn_for(workspace)
        try:
            owned(conn, "generations", generation_id, workspace)
            try:
                return reconcile.abandon(conn, generation_id, actor)
            except reconcile.PublishError as exc:
                raise conflict(exc)
        finally:
            conn.close()

    @router.get("/{workspace}/fleet")
    def fleet(workspace: str, cursor: str = "", limit: int = Query(500, ge=1, le=5000),
              scope_kind: Optional[str] = None, scope_value: Optional[str] = None,
              actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            items = reconcile.fleet_page(conn, workspace, after=cursor, limit=limit + 1, scope_kind=scope_kind,
                                         scope_value=scope_value)
            page = items[:limit]
            return {"items": page, "next_cursor": page[-1]["key"] if len(items) > limit else None}
        finally:
            conn.close()

    @router.get("/{workspace}/fleet/{key}")
    def fleet_member(workspace: str, key: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            import equipment_checkdigit as kernel
            member = reconcile.fleet_member(conn, workspace, kernel.normalize(key))
            if member is None:
                raise HTTPException(status_code=404, detail=f"{key} is not in the published fleet")
            return member
        finally:
            conn.close()

    # ---- profiles --------------------------------------------------------- #

    @router.post("/{workspace}/profiles", status_code=201)
    def create_profile(workspace: str, body: ProfileIn, actor: str = Depends(need("analyst"))):
        conn = conn_for(workspace)
        try:
            try:
                return profiles.create_profile(conn, workspace, actor, **body.model_dump())
            except (profiles.ProfileError, ValueError) as exc:
                raise HTTPException(status_code=422, detail={"code": getattr(exc, "code", "BAD_PROFILE"),
                                                             "detail": getattr(exc, "detail", str(exc))})
        finally:
            conn.close()

    @router.get("/{workspace}/profiles")
    def list_profiles(workspace: str, cursor: str = "", limit: int = Query(100, ge=1, le=500),
                      actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            items = profiles.list_profiles(conn, workspace, after=cursor, limit=limit + 1)
            page = items[:limit]
            return {"items": page, "next_cursor": page[-1]["id"] if len(items) > limit else None}
        finally:
            conn.close()

    @router.get("/{workspace}/profiles/{profile_id}")
    def profile_view(workspace: str, profile_id: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            row = conn.execute("SELECT * FROM feed_profiles WHERE id = ? AND workspace_id = ?", (profile_id, workspace)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"profile {profile_id} not found in workspace {workspace}")
            return profiles.profile_doc(row)
        finally:
            conn.close()

    @router.post("/{workspace}/profiles/{profile_id}/verify-fixtures")
    def verify_fixtures(workspace: str, profile_id: str, actor: str = Depends(need("analyst"))):
        conn = conn_for(workspace)
        try:
            if conn.execute("SELECT 1 FROM feed_profiles WHERE id = ? AND workspace_id = ?", (profile_id, workspace)).fetchone() is None:
                raise HTTPException(status_code=404, detail="profile not found")
            try:
                return profiles.verify_fixtures(conn, root, profile_id, actor)
            except profiles.ProfileError as exc:
                raise conflict(exc)
        finally:
            conn.close()

    @router.post("/{workspace}/profiles/{profile_id}/verification")
    def set_verification(workspace: str, profile_id: str, body: VerificationIn, actor: str = Depends(need("administrator"))):
        conn = conn_for(workspace)
        try:
            if conn.execute("SELECT 1 FROM feed_profiles WHERE id = ? AND workspace_id = ?", (profile_id, workspace)).fetchone() is None:
                raise HTTPException(status_code=404, detail="profile not found")
            try:
                return profiles.set_verification(conn, profile_id, body.state, body.note, actor)
            except profiles.ProfileError as exc:
                raise HTTPException(status_code=422, detail={"code": exc.code, "detail": exc.detail})
        finally:
            conn.close()

    # ---- messages, context, layers, inspection --------------------------- #

    @router.get("/{workspace}/jobs/{job_id}/messages")
    def list_messages(workspace: str, job_id: str, cursor: str = "0", limit: int = Query(100, ge=1, le=1000),
                      actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            owned(conn, "jobs", job_id, workspace)
            rows = conn.execute("SELECT * FROM message_transactions WHERE job_id = ? AND message_no > ? ORDER BY message_no LIMIT ?",
                                (job_id, int(cursor), limit + 1)).fetchall()
            items = [views.message_doc(r) for r in rows[:limit]]
            return {"items": items, "next_cursor": str(items[-1]["message_no"]) if len(rows) > limit else None}
        finally:
            conn.close()

    @router.get("/{workspace}/jobs/{job_id}/events")
    def list_events(workspace: str, job_id: str, cursor: str = "", limit: int = Query(200, ge=1, le=1000),
                    actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            owned(conn, "jobs", job_id, workspace)
            rows = conn.execute("SELECT * FROM events WHERE job_id = ? AND id > ? ORDER BY id LIMIT ?",
                                (job_id, cursor, limit + 1)).fetchall()
            items = [views.event_doc(r) for r in rows[:limit]]
            return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None}
        finally:
            conn.close()

    @router.get("/{workspace}/jobs/{job_id}/observations/{ordinal}/layers")
    def observation_layers(workspace: str, job_id: str, ordinal: int, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            job = owned(conn, "jobs", job_id, workspace)
            obs = conn.execute("SELECT * FROM observations WHERE job_id = ? AND ordinal = ?", (job_id, ordinal)).fetchone()
            if obs is None:
                raise HTTPException(status_code=404, detail="observation not found")
            prof = profiles.profile_for_job(conn, job)
            ctx = conn.execute("SELECT * FROM observation_context WHERE job_id = ? AND ordinal = ?", (job_id, ordinal)).fetchone()
            return {"job_id": job_id, "ordinal": ordinal, "raw": obs["raw"], "normalized": obs["normalized"],
                    "scheme": obs["scheme"], "candidate": obs["candidate"], "decision": obs["decision"],
                    "layers": ctx_mod.layers_for(conn, job, obs, prof), "context": dict(ctx) if ctx else None}
        finally:
            conn.close()

    @router.get("/{workspace}/jobs/{job_id}/inspection")
    def inspection(workspace: str, job_id: str, max_bays: int = Query(24, ge=1, le=200), actor: str = Depends(need("viewer"))):
        """Connected visual inspection: the message's scene with each unit's observation state."""
        conn = conn_for(workspace)
        try:
            job = owned(conn, "jobs", job_id, workspace)
            src = conn.execute("SELECT * FROM source_files WHERE id = ?", (job["source_file_id"],)).fetchone()
            if src["size_bytes"] > INSPECTION_MAX_BYTES:
                raise HTTPException(status_code=413, detail={"code": "RESOURCE_BUDGET_EXCEEDED",
                                                             "detail": f"inspection renders sources up to {INSPECTION_MAX_BYTES} bytes"})
            with open(src["path"], "rb") as fh:
                text = fh.read().decode(src["encoding"] or "utf-8", errors="replace")
            import baplie_scene, bayplan_render, intermodal_scene
            scene = baplie_scene.parse_scene(text)
            kind = "vessel"
            if scene is None:
                inter = intermodal_scene.parse_intermodal(text)
                if inter is None:
                    raise HTTPException(status_code=409, detail={"code": "UNSUPPORTED_INPUT",
                                                                 "detail": "no bay plan or intermodal consist to draw"})
                kind = "intermodal"
                payload = inter.as_dict() if hasattr(inter, "as_dict") else {"units": []}
                units = payload.get("units", [])
            else:
                payload = bayplan_render.render_scene(scene, max_bays=max_bays)
                payload["units"] = scene.as_dict()["units"]
                units = payload["units"]
            ids = sorted({u.get("container_id") for u in units if u.get("container_id")})
            states: Dict[str, Any] = {}
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                for r in conn.execute("SELECT ordinal, normalized, raw, status, decision, candidate FROM observations WHERE job_id = ? "
                                      "AND (raw IN (%s) OR normalized IN (%s))" % (",".join("?" * len(chunk)), ",".join("?" * len(chunk))),
                                      (job_id, *chunk, *chunk)):
                    states.setdefault(r["raw"], []).append(dict(r))
                    states.setdefault(r["normalized"], []).append(dict(r))
            for u in units:
                u["observations"] = states.get(u.get("container_id"), [])
            payload["kind"] = kind
            payload["job_id"] = job_id
            payload["findings"] = {r["code"]: r["n"] for r in conn.execute(
                "SELECT code, COUNT(*) AS n FROM findings WHERE job_id = ? GROUP BY code", (job_id,))}
            return payload
        finally:
            conn.close()

    @router.get("/{workspace}/visits")
    def list_visits(workspace: str, cursor: str = "", limit: int = Query(100, ge=1, le=500),
                    actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            rows = conn.execute("SELECT * FROM visits WHERE workspace_id = ? AND id > ? ORDER BY id LIMIT ?",
                                (workspace, cursor, limit + 1)).fetchall()
            items = [views.visit_doc(conn, r) for r in rows[:limit]]
            return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None}
        finally:
            conn.close()

    @router.get("/{workspace}/visits/{visit_id}")
    def visit_view(workspace: str, visit_id: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            row = conn.execute("SELECT * FROM visits WHERE id = ? AND workspace_id = ?", (visit_id, workspace)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="visit not found")
            return views.visit_doc(conn, row)
        finally:
            conn.close()

    # ---- deliveries and receiver feedback -------------------------------- #

    @router.post("/{workspace}/artifacts/{artifact_id}/deliveries", status_code=201)
    def record_delivery(workspace: str, artifact_id: str, body: DeliveryIn, actor: str = Depends(need("reviewer"))):
        conn = conn_for(workspace)
        try:
            owned(conn, "export_artifacts", artifact_id, workspace)
            try:
                out = feedback_mod.record_delivery(conn, workspace, artifact_id, destination=body.destination, state=body.state,
                                                   idempotency_key=f"{workspace}:{artifact_id}:{body.idempotency_key}", actor=actor,
                                                   control_reference=body.control_reference, outcome_evidence=body.outcome_evidence,
                                                   started_at=body.started_at)
            except feedback_mod.FeedbackError as exc:
                raise HTTPException(status_code=422 if exc.code in ("BAD_STATE", "EVIDENCE_REQUIRED") else 409,
                                    detail={"code": exc.code, "detail": exc.detail})
            doc = views.delivery_doc(out)
            doc["replayed"] = out["replayed"]
            return doc
        finally:
            conn.close()

    @router.get("/{workspace}/artifacts/{artifact_id}/deliveries")
    def list_deliveries(workspace: str, artifact_id: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            owned(conn, "export_artifacts", artifact_id, workspace)
            return {"items": [views.delivery_doc(d) for d in feedback_mod.deliveries_for_artifact(conn, artifact_id)],
                    "next_cursor": None}
        finally:
            conn.close()

    @router.post("/{workspace}/feedback", status_code=201)
    def record_feedback(workspace: str, body: FeedbackIn, actor: str = Depends(need("reviewer"))):
        conn = conn_for(workspace)
        try:
            prof = None
            if body.response_profile_id:
                row = conn.execute("SELECT * FROM feed_profiles WHERE id = ? AND workspace_id = ?",
                                   (body.response_profile_id, workspace)).fetchone()
                if row is None:
                    raise HTTPException(status_code=404, detail="response profile not found")
                prof = profiles.profile_doc(row)
            try:
                return feedback_mod.ingest_feedback(conn, root, workspace, actor, origin=body.origin, text=body.text,
                                                    manual=body.manual, response_profile=prof, decision_by=body.decision_by)
            except feedback_mod.FeedbackError as exc:
                raise HTTPException(status_code=422, detail={"code": exc.code, "detail": exc.detail})
        finally:
            conn.close()

    @router.get("/{workspace}/feedback")
    def list_feedback(workspace: str, cursor: str = "", limit: int = Query(100, ge=1, le=500),
                      actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            rows = conn.execute("SELECT id FROM receiver_feedback WHERE workspace_id = ? AND id > ? ORDER BY id LIMIT ?",
                                (workspace, cursor, limit + 1)).fetchall()
            items = [feedback_mod.feedback_doc(conn, r["id"]) for r in rows[:limit]]
            return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None}
        finally:
            conn.close()

    @router.get("/{workspace}/feedback/{feedback_id}")
    def feedback_view(workspace: str, feedback_id: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            if conn.execute("SELECT 1 FROM receiver_feedback WHERE id = ? AND workspace_id = ?", (feedback_id, workspace)).fetchone() is None:
                raise HTTPException(status_code=404, detail="feedback not found")
            return feedback_mod.feedback_doc(conn, feedback_id)
        finally:
            conn.close()

    @router.post("/{workspace}/feedback/{feedback_id}/repair", status_code=202)
    def start_repair(workspace: str, feedback_id: str, actor: str = Depends(need("reviewer"))):
        conn = conn_for(workspace)
        try:
            if conn.execute("SELECT 1 FROM receiver_feedback WHERE id = ? AND workspace_id = ?", (feedback_id, workspace)).fetchone() is None:
                raise HTTPException(status_code=404, detail="feedback not found")
            try:
                return feedback_mod.start_repair(conn, root, workspace, feedback_id, actor, policy=policy_provider())
            except feedback_mod.FeedbackError as exc:
                raise conflict(exc)
        finally:
            conn.close()

    # ---- connections, transmission, automation --------------------------- #

    def owned_connection(conn: sqlite3.Connection, connection_id: str, workspace: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM connections WHERE id = ? AND workspace_id = ?", (connection_id, workspace)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"connection {connection_id} not found in workspace {workspace}")
        return row

    @router.post("/{workspace}/connections", status_code=201)
    def create_connection(workspace: str, body: ConnectionIn, actor: str = Depends(need("administrator"))):
        conn = conn_for(workspace)
        try:
            try:
                return conn_mod.create_connection(conn, workspace, actor, **body.model_dump())
            except conn_mod.ConnectionError_ as exc:
                raise HTTPException(status_code=422, detail={"code": exc.code, "detail": exc.detail})
        finally:
            conn.close()

    @router.get("/{workspace}/connections")
    def list_connections(workspace: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            return {"items": conn_mod.list_connections(conn, workspace), "next_cursor": None}
        finally:
            conn.close()

    @router.get("/{workspace}/connections/{connection_id}")
    def connection_view(workspace: str, connection_id: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            owned_connection(conn, connection_id, workspace)
            return conn_mod.connection_doc(conn, connection_id)
        finally:
            conn.close()

    @router.post("/{workspace}/connections/{connection_id}/authorize")
    def authorize_connection(workspace: str, connection_id: str, body: NoteIn, actor: str = Depends(need("administrator"))):
        conn = conn_for(workspace)
        try:
            owned_connection(conn, connection_id, workspace)
            try:
                return conn_mod.authorize(conn, connection_id, actor, body.note)
            except conn_mod.ConnectionError_ as exc:
                raise HTTPException(status_code=422, detail={"code": exc.code, "detail": exc.detail})
        finally:
            conn.close()

    @router.post("/{workspace}/connections/{connection_id}/verify")
    def verify_connection(workspace: str, connection_id: str, actor: str = Depends(need("administrator"))):
        conn = conn_for(workspace)
        try:
            owned_connection(conn, connection_id, workspace)
            try:
                return conn_mod.verify(conn, connection_id, actor)
            except conn_mod.ConnectionError_ as exc:
                raise conflict(exc)
        finally:
            conn.close()

    @router.post("/{workspace}/connections/{connection_id}/enable")
    def enable_connection(workspace: str, connection_id: str, actor: str = Depends(need("administrator"))):
        conn = conn_for(workspace)
        try:
            owned_connection(conn, connection_id, workspace)
            try:
                return conn_mod.enable(conn, connection_id, actor)
            except conn_mod.ConnectionError_ as exc:
                raise conflict(exc)
        finally:
            conn.close()

    @router.post("/{workspace}/connections/{connection_id}/disable")
    def disable_connection(workspace: str, connection_id: str, body: NoteIn = NoteIn(), actor: str = Depends(need("administrator"))):
        conn = conn_for(workspace)
        try:
            owned_connection(conn, connection_id, workspace)
            return conn_mod.disable(conn, connection_id, actor, body.note)
        finally:
            conn.close()

    @router.post("/{workspace}/connections/{connection_id}/poll")
    def poll_connection(workspace: str, connection_id: str, actor: str = Depends(need("analyst"))):
        conn = conn_for(workspace)
        try:
            owned_connection(conn, connection_id, workspace)
            try:
                return automation.poll_connection(conn, root, connection_id, actor, policy=policy_provider())
            except conn_mod.ConnectionError_ as exc:
                raise conflict(exc)
        finally:
            conn.close()

    @router.post("/{workspace}/automation/tick")
    def automation_tick(workspace: str, actor: str = Depends(need("analyst"))):
        conn = conn_for(workspace)
        try:
            return automation.tick(conn, root, workspace, policy=policy_provider())
        finally:
            conn.close()

    @router.get("/{workspace}/inbox")
    def inbox(workspace: str, cursor: str = "", limit: int = Query(100, ge=1, le=500), actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            items = automation.inbox_page(conn, workspace, after=cursor, limit=limit + 1)
            page = items[:limit]
            return {"items": page, "next_cursor": page[-1]["id"] if len(items) > limit else None}
        finally:
            conn.close()

    @router.post("/{workspace}/approvals/{approval_id}/authorize-transmission")
    def authorize_transmission(workspace: str, approval_id: str, body: NoteIn, actor: str = Depends(need("administrator"))):
        conn = conn_for(workspace)
        try:
            owned(conn, "approvals", approval_id, workspace)
            try:
                return transmit.authorize_transmission(conn, approval_id, actor, body.note)
            except transmit.TransmitError as exc:
                raise HTTPException(status_code=422 if exc.code == "EVIDENCE_REQUIRED" else 409, detail={"code": exc.code, "detail": exc.detail})
        finally:
            conn.close()

    @router.post("/{workspace}/artifacts/{artifact_id}/transmit", status_code=201)
    def transmit_artifact(workspace: str, artifact_id: str, body: TransmitIn, actor: str = Depends(need("administrator"))):
        conn = conn_for(workspace)
        try:
            owned(conn, "export_artifacts", artifact_id, workspace)
            owned_connection(conn, body.connection_id, workspace)
            try:
                return transmit.send_artifact(conn, root, workspace, artifact_id, body.connection_id, actor,
                                              idempotency_key=f"{workspace}:{artifact_id}:{body.idempotency_key}")
            except transmit.TransmitError as exc:
                raise conflict(exc)
        finally:
            conn.close()

    @router.get("/{workspace}/transmissions")
    def list_transmissions(workspace: str, cursor: str = "", limit: int = Query(100, ge=1, le=500),
                           actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            items = transmit.list_attempts(conn, workspace, after=cursor, limit=limit + 1)
            page = items[:limit]
            return {"items": page, "next_cursor": page[-1]["id"] if len(items) > limit else None}
        finally:
            conn.close()

    @router.post("/{workspace}/transmissions/{attempt_id}/resend", status_code=201)
    def resend_transmission(workspace: str, attempt_id: str, body: ResendIn, actor: str = Depends(need("administrator"))):
        conn = conn_for(workspace)
        try:
            try:
                return transmit.resend(conn, root, workspace, attempt_id, actor,
                                       idempotency_key=f"{workspace}:resend:{attempt_id}:{body.idempotency_key}", note=body.note)
            except transmit.TransmitError as exc:
                raise conflict(exc)
        finally:
            conn.close()

    # ---- reference snapshots and enrichment requests --------------------- #

    @router.post("/{workspace}/reference/owner-register", status_code=201)
    def import_owner_register(workspace: str, body: OwnerRegisterIn, actor: str = Depends(need("administrator"))):
        conn = conn_for(workspace)
        try:
            try:
                return references.import_owner_register(conn, root, workspace, actor, text=body.text, version=body.version,
                                                        license_note=body.license_note, source=body.source)
            except references.ReferenceError as exc:
                raise HTTPException(status_code=422, detail={"code": exc.code, "detail": exc.detail})
        finally:
            conn.close()

    @router.get("/{workspace}/reference")
    def list_reference(workspace: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            return {"items": references.list_snapshots(conn, workspace), "next_cursor": None,
                    "owner_codes": conn.execute("SELECT COUNT(*) FROM owner_register WHERE workspace_id = ?", (workspace,)).fetchone()[0]}
        finally:
            conn.close()

    @router.post("/{workspace}/enrichment", status_code=201)
    def create_enrichment(workspace: str, body: EnrichmentIn, actor: str = Depends(need("analyst"))):
        conn = conn_for(workspace)
        try:
            try:
                return references.create_request(conn, workspace, actor, **body.model_dump())
            except references.ReferenceError as exc:
                raise HTTPException(status_code=422, detail={"code": exc.code, "detail": exc.detail})
        finally:
            conn.close()

    @router.get("/{workspace}/enrichment")
    def list_enrichment(workspace: str, actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            return {"items": references.list_requests(conn, workspace), "next_cursor": None,
                    "providers_configured": sorted(references.default_providers().keys())}
        finally:
            conn.close()

    @router.post("/{workspace}/enrichment/{request_id}/authorize")
    def authorize_enrichment(workspace: str, request_id: str, body: NoteIn, actor: str = Depends(need("administrator"))):
        conn = conn_for(workspace)
        try:
            if conn.execute("SELECT 1 FROM enrichment_requests WHERE id = ? AND workspace_id = ?", (request_id, workspace)).fetchone() is None:
                raise HTTPException(status_code=404, detail="request not found")
            try:
                return references.authorize_request(conn, request_id, actor, body.note)
            except references.ReferenceError as exc:
                raise HTTPException(status_code=422, detail={"code": exc.code, "detail": exc.detail})
        finally:
            conn.close()

    @router.post("/{workspace}/enrichment/{request_id}/run")
    def run_enrichment(workspace: str, request_id: str, actor: str = Depends(need("administrator"))):
        conn = conn_for(workspace)
        try:
            if conn.execute("SELECT 1 FROM enrichment_requests WHERE id = ? AND workspace_id = ?", (request_id, workspace)).fetchone() is None:
                raise HTTPException(status_code=404, detail="request not found")
            try:
                return references.run_request(conn, request_id, actor)
            except references.ReferenceError as exc:
                raise conflict(exc)
        finally:
            conn.close()

    @router.get("/{workspace}/enrichment/{request_id}/results")
    def enrichment_results(workspace: str, request_id: str, cursor: str = "", limit: int = Query(200, ge=1, le=1000),
                           actor: str = Depends(need("viewer"))):
        conn = conn_for(workspace)
        try:
            if conn.execute("SELECT 1 FROM enrichment_requests WHERE id = ? AND workspace_id = ?", (request_id, workspace)).fetchone() is None:
                raise HTTPException(status_code=404, detail="request not found")
            items = references.results_for(conn, request_id, after=cursor, limit=limit + 1)
            page = items[:limit]
            return {"items": page, "next_cursor": page[-1]["identifier"] if len(items) > limit else None}
        finally:
            conn.close()

    return router
