"""
api.py
======
FastAPI HTTP layer over the correction service core. A thin wrapper: it parses
the HTTP request, calls service.process_upload() / queries the DB, and serializes
the result. All correctness lives below this layer.

Endpoints (the SPA's contract):
  POST /correct?owner_policy=&trust=   multipart file -> report.to_dict()
  GET  /events                          ingestion / audit log
  GET  /containers                      container registry
  GET  /containers/{eqid}               one container + its event history
  GET  /health

Notes:
  * Sync endpoints with a per-request SQLite connection -- the thread-safe
    pattern under FastAPI's threadpool (each request, one connection, one thread).
  * The User-Agent header is recorded; the client IP is never read or stored.
  * Upload size is capped in the service (MAX_BYTES) and the rejection is audited;
    a reverse proxy body-size limit (Caddy `request_body max_size`) is the real
    DoS guard and should also be set -- see DEPLOY.md.
  * Run uvicorn with --proxy-headers --forwarded-allow-ips=<proxy-ip>, never '*'.

Run (local):  uvicorn api:app --reload
Run (prod):   see docker-compose.yml / DEPLOY.md
"""
from __future__ import annotations

import json
import os
import sys
import threading
from typing import List, Optional

from fastapi import BackgroundTasks, Body, Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware

import db
import equipment_checkdigit as kernel
import nearmiss
import service
import policy as policy_mod
import enrichment as enrich_mod
import auth

DB_PATH = os.environ.get("CHECKDIGIT_DB", "checkdigit.db")
STATIC_DIR = os.environ.get("CHECKDIGIT_STATIC", "static")
CORS_ORIGINS = [o.strip() for o in os.environ.get("CHECKDIGIT_CORS_ORIGINS", "*").split(",") if o.strip()]
# Optional BIC owner-code register (CSV/JSON). Enables free-text corroboration
# and owner enrichment. Obtain the authoritative register from bic-code.org; the
# bundled owner_registry.seed.csv is an illustrative starter only.
OWNER_REGISTRY_PATH = os.environ.get("CHECKDIGIT_OWNER_REGISTRY")
# Optional operator policy file (JSON: default_policy, allow, deny, per_prefix).
# Loaded at startup; editable live via PUT /policy (in-memory; not persisted to
# the file unless you mount it and the process can write it).
POLICY_FILE = os.environ.get("CHECKDIGIT_POLICY_FILE")

_ENRICHMENT = None      # EnrichmentService | None, built at startup
_POLICY = policy_mod.Policy()   # operator rule layer; default = global strict
# Guards reads/writes of the mutable _POLICY across concurrent requests: a
# PUT /policy must not tear a /correct that is reading the policy at the same
# moment. Policy objects are immutable once built, so we only lock the swap and
# the snapshot read (cheap), then use the snapshot lock-free during correction.
_POLICY_LOCK = threading.Lock()


def _current_policy() -> "policy_mod.Policy":
    with _POLICY_LOCK:
        return _POLICY

# Swagger UI / ReDoc / OpenAPI JSON expose every route's shape, including the admin
# endpoints. Keep them in dev, but disable in prod so they don't advertise the admin
# surface. CHECKDIGIT_DOCS=0 (set in docker-compose.public.yml) turns all three off;
# default-on preserves local /docs.
_DOCS_ENABLED = os.environ.get("CHECKDIGIT_DOCS", "1").strip().lower() not in ("0", "false", "no", "")
app = FastAPI(
    title="CHECKDIGIT", version="1.0",
    description="Container check-digit correction service",
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
)

# Admin auth (dashboard/history/registry/policy) is cookie-based and SAME-ORIGIN:
# the built SPA is served from "/" below, so the admin session cookie rides along
# on same-origin requests and CORS stays credential-less. Keep allow_credentials
# False and do not widen origins for the cookie -- a wildcard origin WITH
# credentials is invalid anyway. Restrict origins in prod.
app.add_middleware(
    CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=False,
    allow_methods=["GET", "POST"], allow_headers=["*"],
)

# --- Admin access boundary (Pass C) --------------------------------------- #
# WRITE path (/correct, /correct/batch, /visualize, /check, /health) stays OPEN
# and keeps logging every upload. READ path (insights/events/containers/policy/
# sources/portwatch) is gated by `admin_required` below.
#
# Fail-safe by construction -- the missing-config outcome is always CLOSED:
#   * CHECKDIGIT_ADMIN_AUTH=google -> Google sign-in is mounted; auth.load_config()
#     refuses to boot if any client id/secret/redirect/session-secret/allowlist is
#     missing (see auth.py). Never silently open.
#   * unset -> no sign-in route exists and admin_required denies every request
#     with 401. The dashboard is unreachable, not open.
if auth.auth_enabled():
    _AUTH_CFG = auth.load_config()                     # fail-loud on misconfig
    app.include_router(auth.build_router(_AUTH_CFG))   # /admin/auth/login, /callback, /logout, /whoami
    admin_required = auth.make_admin_required(_AUTH_CFG)
else:
    def admin_required() -> None:
        raise HTTPException(
            status_code=401,
            detail="Admin access is disabled on this server. Set "
                   "CHECKDIGIT_ADMIN_AUTH=google and configure Google sign-in "
                   "(see .env.example) to enable the dashboard.")


@app.on_event("startup")
def _startup() -> None:
    db.connect(DB_PATH, create_schema=True).close()
    global _ENRICHMENT
    # Shared bootstrap with watch_folder.py: registry (fail-loud if set-but-missing)
    # + external enrichers from env credentials. See enrichment.from_app_env.
    _ENRICHMENT = enrich_mod.from_app_env()
    global _POLICY
    if POLICY_FILE:
        if not os.path.exists(POLICY_FILE):
            raise RuntimeError(
                f"CHECKDIGIT_POLICY_FILE is set to {POLICY_FILE!r} but no such file "
                f"exists. Provide the policy JSON there, or unset the variable.")
        _POLICY = policy_mod.Policy.load_file(POLICY_FILE)   # PolicyError -> loud fail


def _bg_enrich(eqids) -> None:
    """Background task: query enabled external sources for each container and persist.
    Runs after the /correct response so network latency/limits never block it. Errors
    are isolated per container and logged to stderr (visible in the container logs).

    Storage is two-layered: every successful return is warehoused WHOLE
    (enrichment_fetches + normalized container_events with deduplicated
    vessel/location/equipment dimensions), then the merged flat summary updates
    the container_enrichment cache the dossier UI reads."""
    if _ENRICHMENT is None:
        return
    conn = db.connect(DB_PATH, create_schema=False)
    try:
        for eqid in eqids:
            try:
                triples, merged = _ENRICHMENT.enrich_detailed(eqid)
            except enrich_mod.EnricherError as exc:                  # misconfiguration
                print(f"[enrichment] config error for {eqid}: {exc}", file=sys.stderr)
                continue
            except Exception as exc:                                 # transient/network
                print(f"[enrichment] {eqid}: {exc}", file=sys.stderr)
                continue
            if triples:
                stats = db.persist_enrichment_payloads(conn, eqid, triples)
                print(f"[enrichment] {eqid}: {stats['fetches']} fetch(es), "
                      f"{stats['events']} new event(s) warehoused", file=sys.stderr)
            if merged:
                db.upsert_enrichment(conn, eqid, merged)
    finally:
        conn.close()


@app.post("/visualize")
def visualize(request: Request,
              file: Optional[UploadFile] = File(None),
              text: Optional[str] = Form(None),
              correct_first: bool = Query(True)):
    """Render a BAPLIE as an isometric bay-plan (scene model + per-bay SVG) without
    persisting an audit event. Supply `file` or `text`. By default the file is
    corrected first so the drawing reflects fixed container numbers; set
    correct_first=false to visualize exactly as received. Returns 422 if the input
    is not a BAPLIE."""
    import baplie_scene
    import bayplan_render
    import dispatcher

    has_file = file is not None and (file.filename or "") != ""
    has_text = bool(text and text.strip())
    if has_file == has_text:
        raise HTTPException(status_code=422, detail="Provide either a file or text, not both/neither.")
    raw = (file.file.read() if has_file else (text or "").encode("utf-8"))
    try:
        body = raw.decode("utf-8")
    except UnicodeDecodeError:
        body = raw.decode("latin-1")

    if correct_first:
        try:
            _, rep = dispatcher.correct(body, owner_policy="strict")
            if rep is not None and rep.corrected_text:
                body = rep.corrected_text
        except Exception:
            pass                                       # fall back to as-received
    # BAPLIE vessel bay plan first; else rail/truck intermodal.
    scene = baplie_scene.parse_scene(body)
    if scene is not None and scene.units:
        return bayplan_render.render_scene(scene)
    import intermodal_scene
    import intermodal_render
    iscene = intermodal_scene.parse_intermodal(body)
    if iscene is not None and iscene.units:
        return intermodal_render.render_intermodal(iscene)
    raise HTTPException(status_code=422,
                        detail="Input is not a recognized BAPLIE, rail (X12 404/418/322), "
                               "or truck (COPINO/CODECO) message with drawable units.")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/correct")
def correct(request: Request,
            background_tasks: BackgroundTasks,
            file: Optional[UploadFile] = File(None),
            text: Optional[str] = Form(None),
            owner_policy: str = Query("strict", pattern="^(strict|lenient)$"),
            trust: bool = Query(False),
            format_hint: Optional[str] = Query(None, pattern="^(csv|fixed|fixedwidth|fixed_width)$"),
            columns: Optional[str] = Query(None, description="CSV: comma-separated column indices or header names"),
            ranges: Optional[str] = Query(None, description="fixed-width: e.g. '5-15,20-30' (1-based inclusive)")):
    """Correct an uploaded file (multipart field `file`) OR pasted text (form field
    `text`). Exactly one must be supplied. Pasted text goes through the identical
    pipeline: content-based format detection (pasted EDIFACT is treated as EDIFACT,
    not free text), correction, audit, and enrichment.

        curl -F 'text=MSKU... free text or EDI...' http://host/correct?trust=true
    """
    has_file = file is not None and (file.filename or "") != ""
    has_text = bool(text and text.strip())
    if has_file and has_text:
        raise HTTPException(status_code=422,
                            detail="Provide either a file or pasted text, not both.")
    if not has_file and not has_text:
        raise HTTPException(status_code=422,
                            detail="Provide a file upload or non-empty pasted text.")

    if has_file:
        content = file.file.read()                   # sync read; same thread as DB
        filename = os.path.basename(file.filename or "upload")
        content_type = file.content_type or ""
    else:
        content = text.encode("utf-8")
        filename = "pasted.txt"
        content_type = "text/plain"

    # Build parse_options for the opt-in structured-text formats from query params.
    parse_options = None
    if format_hint in ("csv",):
        cols = [c.strip() for c in (columns or "").split(",") if c.strip()]
        # numeric strings -> indices; else header names
        parse_options = {"columns": [int(c) if c.lstrip("-").isdigit() else c for c in cols]}
    elif format_hint in ("fixed", "fixedwidth", "fixed_width"):
        rngs = []
        for part in (ranges or "").split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                rngs.append([int(a), int(b)])
        parse_options = {"ranges": rngs}

    conn = db.connect(DB_PATH, create_schema=False)
    try:
        res = service.process_upload(
            conn, content,
            filename=filename,
            content_type=content_type,
            user_agent=request.headers.get("user-agent", ""),   # IP is never read
            owner_policy=owner_policy, trust=trust, enrichment=_ENRICHMENT,
            format_hint=format_hint, parse_options=parse_options, policy=_current_policy())
    finally:
        conn.close()

    if res["status"] == "rejected":
        code = 413 if "size" in (res.get("reason") or "") else 415
        raise HTTPException(status_code=code, detail=res.get("reason", "rejected"))

    report = res["report"]
    # Query external sources (carrier T&T, BoxTech, ...) out of band. Only runs if
    # you configured credentials; otherwise this is a no-op and nothing is queried.
    if _ENRICHMENT is not None and _ENRICHMENT.has_external:
        background_tasks.add_task(_bg_enrich, sorted({c.canonical for c in report.containers}))

    return {
        "status": res["status"],
        "detected_format": res["detected_format"],
        "filename": filename,
        "event_id": res["event_id"],
        "report": report.to_dict(),
    }


@app.post("/correct/batch")
def correct_batch(request: Request,
                  files: Optional[List[UploadFile]] = File(None),
                  archive: Optional[UploadFile] = File(None),
                  owner_policy: str = Query("strict", pattern="^(strict|lenient)$"),
                  trust: bool = Query(False),
                  format_hint: Optional[str] = Query(None, pattern="^(csv|fixed|fixedwidth|fixed_width)$"),
                  columns: Optional[str] = Query(None),
                  ranges: Optional[str] = Query(None)):
    """Bulk correction. Supply EITHER multiple `files` OR a single `archive` (.zip
    of files). Returns a .zip containing corrected/<name> for each processed file,
    plus report.csv and manifest.json. Every member is audited; failures are
    recorded in the manifest and skipped, never fatal. A single format_hint +
    columns/ranges applies to all members (use for a uniform CSV/fixed batch).

        curl -F 'archive=@inbox.zip' 'http://host/correct/batch' -o out.zip
        curl -F 'files=@a.edi' -F 'files=@b.snx' 'http://host/correct/batch' -o out.zip
    """
    import batch as batch_mod

    has_files = bool(files) and any((f.filename or "") for f in files)
    has_archive = archive is not None and (archive.filename or "") != ""
    if has_files == has_archive:
        raise HTTPException(status_code=422,
                            detail="Provide either multiple `files` or one `archive` (.zip), not both/neither.")

    # opt-in structured-text spec (same parsing as /correct)
    parse_options = None
    if format_hint == "csv":
        cols = [c.strip() for c in (columns or "").split(",") if c.strip()]
        parse_options = {"columns": [int(c) if c.lstrip("-").isdigit() else c for c in cols]}
    elif format_hint in ("fixed", "fixedwidth", "fixed_width"):
        rngs = []
        for part in (ranges or "").split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                rngs.append([int(a), int(b)])
        parse_options = {"ranges": rngs}

    ua = request.headers.get("user-agent", "")
    conn = db.connect(DB_PATH, create_schema=False)
    try:
        if has_archive:
            try:
                result = batch_mod.process_batch_zip(
                    conn, archive.file.read(), owner_policy=owner_policy, trust=trust,
                    enrichment=_ENRICHMENT, policy=_current_policy(),
                    format_hint=format_hint, parse_options=parse_options,
                    user_agent=ua or "checkdigit-batch/1")
            except Exception as exc:
                raise HTTPException(status_code=422, detail=f"could not read archive: {exc}")
        else:
            items = [(os.path.basename(f.filename or "file"), f.file.read()) for f in files]
            result = batch_mod.process_batch(
                conn, items, owner_policy=owner_policy, trust=trust,
                enrichment=_ENRICHMENT, policy=_current_policy(),
                format_hint=format_hint, parse_options=parse_options,
                user_agent=ua or "checkdigit-batch/1")
    finally:
        conn.close()

    headers = {"Content-Disposition": 'attachment; filename="corrected_batch.zip"',
               "X-Batch-Totals": json.dumps(result.totals)}
    return Response(content=result.zip_bytes, media_type="application/zip", headers=headers)


@app.get("/insights", dependencies=[Depends(admin_required)])
def insights(limit: int = Query(25, ge=1, le=200), trend_days: int = Query(30, ge=1, le=365)):
    """Cross-container analytics from the warehouse tables, powering the dashboard:
    headline totals, per-owner data-quality league table (who ships broken
    numbers), fix-rate by file format, an error/volume time series, busiest
    locations and vessels, and origin->destination lanes from carrier events."""
    conn = db.connect(DB_PATH, create_schema=False)
    try:
        return {"totals": db.insight_totals(conn),
                "owner_quality": db.insight_owner_quality(conn, limit),
                "by_format": db.insight_by_format(conn),
                "error_trend": db.insight_error_trend(conn, trend_days),
                "top_locations": db.insight_top_locations(conn, limit),
                "top_vessels": db.insight_top_vessels(conn, limit),
                "lanes": db.insight_lanes(conn, limit)}
    finally:
        conn.close()


@app.get("/portwatch/{iso3}", dependencies=[Depends(admin_required)])
def portwatch(iso3: str, limit: int = Query(25, ge=1, le=200)):
    """IMF PortWatch port-activity for a country (ISO3, e.g. USA) -- no account,
    open IMF data. This is PORT-LEVEL context, NOT container enrichment (PortWatch
    has no container-number field), so it is a separate, on-demand endpoint."""
    client = enrich_mod.PortWatchClient()
    try:
        rows = client.latest_for_country(iso3, limit=limit)
    except enrich_mod.EnricherError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"PortWatch upstream error: {exc}")
    return {"iso3": iso3.upper(), "count": len(rows), "ports": rows,
            "source": "imf-portwatch", "note": "port-level activity, updated weekly"}


@app.get("/policy", dependencies=[Depends(admin_required)])
def get_policy():
    """The operator rule layer currently in effect (default_policy, allow, deny,
    per_prefix). Applied to every correction. See PUT /policy to change it."""
    return _current_policy().to_dict()


@app.put("/policy", dependencies=[Depends(admin_required)])
def put_policy(body: dict = Body(...)):
    """Replace the in-effect operator policy. Body is the policy JSON object:
        {"default_policy":"strict","allow":["LEA"],"deny":["TES*"],
         "per_prefix":{"MSC":"lenient"}}
    Validated and rejected loudly on malformed input. The swap is atomic under a
    lock so a concurrent /correct never sees a half-updated policy. In-memory only
    unless a writable CHECKDIGIT_POLICY_FILE is mounted (then it is also persisted)."""
    global _POLICY
    try:
        new_policy = policy_mod.Policy.from_dict(body)   # validate before locking
    except policy_mod.PolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    with _POLICY_LOCK:
        _POLICY = new_policy                             # atomic reference swap
        snapshot = _POLICY.to_dict()
    if POLICY_FILE:
        try:
            with open(POLICY_FILE, "w", encoding="utf-8") as fh:
                import json as _json
                fh.write(_json.dumps(snapshot, indent=2))
        except OSError as exc:
            # Non-fatal: policy is live in memory; just report it wasn't persisted.
            return {**snapshot, "_warning": f"not persisted to file: {exc}"}
    return snapshot


@app.get("/sources", dependencies=[Depends(admin_required)])
def sources():
    """Read-only registry of the enrichment data sources and their actual addresses,
    plus which are currently enabled (have credentials configured). No secrets."""
    from dataclasses import asdict
    return {
        "sources": [asdict(s) for s in enrich_mod.SOURCES],
        "external_enabled": bool(_ENRICHMENT is not None and _ENRICHMENT.has_external),
    }


@app.get("/check/{token}")
def check(token: str):
    """Single-number check-digit calculator with the worked algorithm steps
    (letter values, weights, products, sum, mod, remainder-10 rule).

    READ-ONLY BY DESIGN: looking a number up is not ingesting it, so this writes
    no audit event and no container row. Malformed input -> 422 with the reason;
    a check-digit MISMATCH is a successful analysis and returns 200.
    """
    res = kernel.explain(token)
    if not res.get("ok"):
        raise HTTPException(status_code=422, detail=res.get("error", "unrecognized identifier"))
    if _ENRICHMENT is not None and res.get("kind") == "iso6346_ilu":
        res["owner"] = _ENRICHMENT.enrich_owner(res["normalized"]) or None
    # On a mismatch, propose previously-seen check-valid containers within small
    # edit distance of the body -- the "body might be wrong" branch. Read-only.
    if res.get("kind") == "iso6346_ilu" and res.get("verdict") == "mismatch":
        conn = db.connect(DB_PATH, create_schema=False)
        try:
            pool = [(e, s) for e, s in db.candidate_pool(conn)
                    if service._pool_check_valid(e)]
        finally:
            conn.close()
        res["near_misses"] = nearmiss.suggest(
            res["body"], pool, exclude=res["normalized"]) if pool else []
    return res


@app.get("/events/{event_id}/containers.csv", dependencies=[Depends(admin_required)])
def event_containers_csv(event_id: int):
    """The per-container verdict table of a past run, as a downloadable CSV --
    the handoff artifact ('here are your flagged numbers'). 404 if no such event."""
    conn = db.connect(DB_PATH, create_schema=False)
    try:
        text = db.export_event_csv(conn, event_id)
    finally:
        conn.close()
    if text is None:
        raise HTTPException(status_code=404, detail=f"event {event_id} not found")
    return Response(
        content=text, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="event-{event_id}-containers.csv"'})


@app.get("/events", dependencies=[Depends(admin_required)])
def events(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    conn = db.connect(DB_PATH, create_schema=False)
    try:
        rows = conn.execute(
            "SELECT * FROM ingestion_events ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset)).fetchall()
        total = conn.execute("SELECT COUNT(*) AS n FROM ingestion_events").fetchone()["n"]
    finally:
        conn.close()
    return {"total": total, "limit": limit, "offset": offset,
            "events": [dict(r) for r in rows]}


@app.get("/containers", dependencies=[Depends(admin_required)])
def containers(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
               owner: str | None = Query(None)):
    sql = "SELECT * FROM containers"
    params: list = []
    if owner:
        sql += " WHERE owner = ?"
        params.append(owner.upper())
    sql += " ORDER BY times_seen DESC, eqid LIMIT ? OFFSET ?"
    params += [limit, offset]
    conn = db.connect(DB_PATH, create_schema=False)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return {"limit": limit, "offset": offset, "containers": [dict(r) for r in rows]}


@app.get("/containers/{eqid}", dependencies=[Depends(admin_required)])
def container_detail(eqid: str):
    conn = db.connect(DB_PATH, create_schema=False)
    try:
        c = conn.execute("SELECT * FROM containers WHERE eqid = ?", (eqid,)).fetchone()
        if c is None:
            raise HTTPException(status_code=404, detail=f"container {eqid!r} not found")
        history = db.container_history(conn, eqid)
        enrichment = db.enrichment_for(conn, eqid)
    finally:
        conn.close()
    return {"container": dict(c), "enrichment": enrichment,
            "history": history}


# Serve the built SPA from STATIC_DIR at "/" when present (same-origin, no CORS).
# Registered last so the API routes above take precedence.
if os.path.isdir(STATIC_DIR):
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="spa")
else:
    @app.get("/")
    def root():
        return {"service": "checkdigit", "docs": "/docs",
                "endpoints": ["/correct", "/events", "/containers", "/health"]}
