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
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import export as export_mod, ingest, jobs as jobq, maintenance, reconcile, review, runner, store, views

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
    options: Dict[str, Any] = Field(default_factory=dict)


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
            try:
                return runner.submit(conn, workspace_id=workspace, source_file_id=body.source_file_id,
                                     intent_id=body.import_intent_id, options=body.options, actor=actor)
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

    return router
