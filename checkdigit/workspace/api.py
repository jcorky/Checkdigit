"""
api.py
======
Versioned workspace routes under /v1/workspaces/{workspace}. Mounted by
checkdigit/api.py behind the admin dependency; nothing here is public.

Conventions:
  * every entity is looked up within the workspace of the path, so an id
    from another workspace answers 404;
  * list endpoints take `cursor` and `limit` and answer `next_cursor`;
  * writes that must be safe to retry take an operation id (`operation_id`
    or `idempotency_key`) and replay the recorded outcome;
  * uploads are resumable: create, append at an offset, complete.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import export as export_mod, ingest, jobs as jobq, reconcile, review, runner, store

UPLOAD_PART_MAX = 32 * 1024 * 1024


class IntentIn(BaseModel):
    source_file_id: str
    mode: str
    scope_kind: str = "fleet"
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


def build_router(admin_required: Callable[..., Any], root: str, db_path: Optional[str] = None) -> APIRouter:
    os.makedirs(root, exist_ok=True)
    db_path = db_path or os.path.join(root, "workspace.db")
    router = APIRouter(prefix="/v1/workspaces", dependencies=[Depends(admin_required)])

    def conn_for(workspace: str) -> sqlite3.Connection:
        conn = store.connect(db_path)
        store.ensure_workspace(conn, workspace)
        return conn

    def actor(request: Request) -> str:
        user = getattr(request.state, "user", None)
        return str(user) if user else "admin"

    def owned(conn: sqlite3.Connection, table: str, entity_id: str, workspace: str) -> sqlite3.Row:
        if table in ("jobs", "generations", "source_files", "uploads", "import_intents"):
            row = conn.execute(f"SELECT * FROM {table} WHERE id = ? AND workspace_id = ?", (entity_id, workspace)).fetchone()
        elif table == "approvals":
            row = conn.execute("SELECT a.* FROM approvals a JOIN jobs j ON j.id = a.job_id WHERE a.id = ? AND j.workspace_id = ?",
                               (entity_id, workspace)).fetchone()
        elif table == "export_artifacts":
            row = conn.execute("SELECT e.* FROM export_artifacts e JOIN jobs j ON j.id = e.job_id WHERE e.id = ? AND j.workspace_id = ?",
                               (entity_id, workspace)).fetchone()
        else:
            row = None
        if row is None:
            raise HTTPException(status_code=404, detail=f"{table[:-1]} {entity_id} not found in workspace {workspace}")
        return row

    # ---- uploads (resumable) ---------------------------------------------- #

    @router.post("/{workspace}/uploads")
    def create_upload(workspace: str, body: UploadIn):
        conn = conn_for(workspace)
        try:
            uid = store.new_id("upl")
            path = os.path.join(store.data_dir(root, "uploads", workspace), uid + ".part")
            open(path, "wb").close()
            conn.execute("INSERT INTO uploads (id, workspace_id, filename, declared_size, path, created_at) VALUES (?,?,?,?,?,?)",
                         (uid, workspace, os.path.basename(body.filename), body.size, path, store.now_iso()))
            return {"upload_id": uid, "received_bytes": 0, "declared_size": body.size}
        finally:
            conn.close()

    @router.put("/{workspace}/uploads/{upload_id}")
    async def append_upload(workspace: str, upload_id: str, request: Request, offset: int = Query(ge=0)):
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
    def upload_status(workspace: str, upload_id: str):
        conn = conn_for(workspace)
        try:
            up = owned(conn, "uploads", upload_id, workspace)
            return {"upload_id": upload_id, "received_bytes": up["received_bytes"], "declared_size": up["declared_size"],
                    "state": up["state"], "source_file_id": up["source_file_id"]}
        finally:
            conn.close()

    @router.post("/{workspace}/uploads/{upload_id}/complete")
    def complete_upload(workspace: str, upload_id: str, body: CompleteIn):
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

    # ---- intents and jobs ------------------------------------------------- #

    @router.post("/{workspace}/intents")
    def create_intent(workspace: str, body: IntentIn):
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
            return {"import_intent_id": iid, "duplicate": dup}
        finally:
            conn.close()

    @router.post("/{workspace}/jobs", status_code=202)
    def create_job(workspace: str, body: JobIn, request: Request):
        conn = conn_for(workspace)
        try:
            owned(conn, "source_files", body.source_file_id, workspace)
            if body.import_intent_id:
                owned(conn, "import_intents", body.import_intent_id, workspace)
            try:
                return runner.submit(conn, workspace_id=workspace, source_file_id=body.source_file_id,
                                     intent_id=body.import_intent_id, options=body.options, actor=actor(request))
            except ingest.IngestError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
        finally:
            conn.close()

    @router.get("/{workspace}/jobs")
    def list_jobs(workspace: str, cursor: str = "", limit: int = Query(50, ge=1, le=500)):
        conn = conn_for(workspace)
        try:
            rows = conn.execute("SELECT id, state, step, attempts, created_at, updated_at, import_intent_id, generation_id "
                                "FROM jobs WHERE workspace_id = ? AND id > ? ORDER BY id LIMIT ?",
                                (workspace, cursor, limit + 1)).fetchall()
            items = [dict(r) for r in rows[:limit]]
            return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None}
        finally:
            conn.close()

    @router.get("/{workspace}/jobs/{job_id}")
    def job_summary(workspace: str, job_id: str):
        conn = conn_for(workspace)
        try:
            owned(conn, "jobs", job_id, workspace)
            return review.summary(conn, job_id)
        finally:
            conn.close()

    @router.post("/{workspace}/jobs/{job_id}/cancel")
    def cancel_job(workspace: str, job_id: str):
        conn = conn_for(workspace)
        try:
            owned(conn, "jobs", job_id, workspace)
            jobq.cancel(conn, job_id)
            return {"job_id": job_id, "cancel_requested": True}
        finally:
            conn.close()

    @router.post("/{workspace}/jobs/{job_id}/run")
    def run_job_inline(workspace: str, job_id: str):
        """Run queued work in this process (deployments without a worker process)."""
        conn = conn_for(workspace)
        try:
            owned(conn, "jobs", job_id, workspace)
            n = runner.run_until_idle(conn, root, max_jobs=10)
            return {"ran": n, **review.summary(conn, job_id)}
        finally:
            conn.close()

    @router.get("/{workspace}/jobs/{job_id}/observations")
    def observations(workspace: str, job_id: str, cursor: str = "-1", limit: int = Query(200, ge=1, le=1000),
                     status: Optional[str] = None, scheme: Optional[str] = None, prefix: Optional[str] = None,
                     decision: Optional[str] = None):
        conn = conn_for(workspace)
        try:
            owned(conn, "jobs", job_id, workspace)
            flt = {k: v for k, v in (("status", status), ("scheme", scheme), ("prefix", prefix), ("decision", decision)) if v}
            try:
                return review.observations_page(conn, job_id, flt, after=int(cursor), limit=limit)
            except (review.ReviewError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=str(exc))
        finally:
            conn.close()

    @router.get("/{workspace}/jobs/{job_id}/findings")
    def findings(workspace: str, job_id: str, cursor: str = "0", limit: int = Query(200, ge=1, le=1000),
                 code: Optional[str] = None):
        conn = conn_for(workspace)
        try:
            owned(conn, "jobs", job_id, workspace)
            return review.findings_page(conn, job_id, after=int(cursor), limit=limit, code=code)
        finally:
            conn.close()

    @router.post("/{workspace}/jobs/{job_id}/decisions")
    def decide(workspace: str, job_id: str, body: DecisionIn, request: Request):
        conn = conn_for(workspace)
        try:
            job = owned(conn, "jobs", job_id, workspace)
            if job["state"] not in ("awaiting_review", "completed"):
                raise HTTPException(status_code=409, detail=f"job is {job['state']}")
            try:
                return review.decide(conn, job_id, body.filter, body.decision, actor(request),
                                     operation_id=body.operation_id, authorization_scope=body.authorization_scope,
                                     expected_count=body.expected_count)
            except review.ReviewError as exc:
                raise HTTPException(status_code=409, detail={"code": exc.code, "detail": exc.detail})
        finally:
            conn.close()

    @router.get("/{workspace}/approvals/{approval_id}")
    def approval(workspace: str, approval_id: str):
        conn = conn_for(workspace)
        try:
            owned(conn, "approvals", approval_id, workspace)
            out = review.approval_view(conn, approval_id)
            out["check"] = review.check_approval(conn, approval_id)
            return out
        finally:
            conn.close()

    @router.post("/{workspace}/jobs/{job_id}/reanalyze")
    def reanalyze(workspace: str, job_id: str, request: Request, reason: str = "re-analysis requested"):
        conn = conn_for(workspace)
        try:
            owned(conn, "jobs", job_id, workspace)
            n = review.reanalyze(conn, job_id, reason)
            store.audit(conn, workspace, actor(request), "job.reanalyze", job_id, f"stale={n} reason={reason}")
            return {"job_id": job_id, "approvals_stale": n}
        finally:
            conn.close()

    # ---- exports ---------------------------------------------------------- #

    @router.post("/{workspace}/jobs/{job_id}/exports", status_code=201)
    def create_export(workspace: str, job_id: str, body: ExportIn, request: Request):
        conn = conn_for(workspace)
        try:
            owned(conn, "jobs", job_id, workspace)
            if body.approval_id:
                owned(conn, "approvals", body.approval_id, workspace)
            try:
                return export_mod.build_artifact(conn, root, job_id, body.approval_id, actor(request),
                                                 idempotency_key=f"{workspace}:{job_id}:{body.idempotency_key}")
            except (export_mod.ExportError, review.ReviewError) as exc:
                raise HTTPException(status_code=409, detail={"code": exc.code, "detail": exc.detail})
        finally:
            conn.close()

    @router.get("/{workspace}/artifacts/{artifact_id}")
    def artifact(workspace: str, artifact_id: str):
        conn = conn_for(workspace)
        try:
            owned(conn, "export_artifacts", artifact_id, workspace)
            return export_mod.artifact_view(conn, artifact_id)
        finally:
            conn.close()

    @router.get("/{workspace}/artifacts/{artifact_id}/files/{name}")
    def artifact_file(workspace: str, artifact_id: str, name: str):
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

    # ---- generations ------------------------------------------------------ #

    @router.get("/{workspace}/generations/{generation_id}")
    def generation(workspace: str, generation_id: str):
        conn = conn_for(workspace)
        try:
            owned(conn, "generations", generation_id, workspace)
            return reconcile.effects(conn, generation_id)
        finally:
            conn.close()

    @router.get("/{workspace}/generations/{generation_id}/members")
    def members(workspace: str, generation_id: str, cursor: str = "", limit: int = Query(500, ge=1, le=5000)):
        conn = conn_for(workspace)
        try:
            owned(conn, "generations", generation_id, workspace)
            items = reconcile.members_page(conn, generation_id, after=cursor, limit=limit + 1)
            page = items[:limit]
            return {"items": page, "next_cursor": page[-1]["key"] if len(items) > limit else None}
        finally:
            conn.close()

    @router.post("/{workspace}/generations/{generation_id}/publish")
    def publish(workspace: str, generation_id: str, body: PublishIn, request: Request):
        conn = conn_for(workspace)
        try:
            owned(conn, "generations", generation_id, workspace)
            try:
                return reconcile.publish(conn, generation_id, actor(request), operation_id=body.operation_id)
            except reconcile.PublishError as exc:
                raise HTTPException(status_code=409, detail={"code": exc.code, "detail": exc.detail})
        finally:
            conn.close()

    @router.get("/{workspace}/fleet")
    def fleet(workspace: str, cursor: str = "", limit: int = Query(500, ge=1, le=5000)):
        conn = conn_for(workspace)
        try:
            items = reconcile.fleet_page(conn, workspace, after=cursor, limit=limit + 1)
            page = items[:limit]
            return {"items": page, "next_cursor": page[-1]["key"] if len(items) > limit else None}
        finally:
            conn.close()

    @router.get("/{workspace}")
    def workspace_view(workspace: str):
        conn = conn_for(workspace)
        try:
            ws = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace,)).fetchone()
            out = dict(ws)
            out["jobs"] = {r["state"]: r["n"] for r in conn.execute(
                "SELECT state, COUNT(*) AS n FROM jobs WHERE workspace_id = ? GROUP BY state", (workspace,))}
            out["fleet_count"] = reconcile.fleet_count(conn, workspace)
            return out
        finally:
            conn.close()

    return router
