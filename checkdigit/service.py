"""
service.py
==========
The correction service core (transport-independent). One call takes raw uploaded
bytes + request metadata, runs detection -> correction, records a privacy-
preserving audit event, and returns the result. The FastAPI layer (next pass) is
a thin wrapper over process_upload(); nothing here imports a web framework.

Security posture (pre-exposure):
  * size cap on uploads (reject oversized before doing any work);
  * filename is reduced to a basename (no path traversal);
  * decode as UTF-8, falling back to latin-1 (EDI is frequently latin-1);
  * XML is parsed only through the XXE-hardened SNX path (via the dispatcher);
  * NO client IP is accepted or stored -- only user-agent is recorded.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional, Set

import dispatcher
import db
import equipment_checkdigit as kernel
import nearmiss
import xlsx_corrector
from snx_locator import XmlSecurityError
from xlsx_locator import XlsxError

MAX_BYTES = 5 * 1024 * 1024          # 5 MB upload cap


def process_upload(conn, content: bytes, *, filename: str, content_type: str = "",
                   user_agent: str = "", owner_policy: str = "strict",
                   trust: bool = False, known_owner_prefixes: Optional[Set[str]] = None,
                   enrichment=None, format_hint: Optional[str] = None,
                   parse_options: Optional[dict] = None, policy=None
                   ) -> Dict[str, Any]:
    safe_name = os.path.basename(filename or "")     # strip any path components

    # --- size cap (reject before parsing) --------------------------------- #
    if len(content) > MAX_BYTES:
        db.record_event(conn, filename=safe_name, content_type=content_type,
                        detected_format="rejected", file_size=len(content),
                        user_agent=user_agent, owner_policy=owner_policy,
                        status="rejected", reason="file exceeds size cap", report=None)
        return {"status": "rejected", "reason": "file exceeds size cap",
                "max_bytes": MAX_BYTES}

    # --- decode ----------------------------------------------------------- #
    # An owner registry (enrichment) corroborates free-text matches: a .txt or
    # cell-text number whose owner is a registrant is corrected, not just
    # flagged. Explicit known_owner_prefixes still win if the caller supplied them.
    if enrichment is not None and known_owner_prefixes is None:
        known_owner_prefixes = enrichment.owner_prefixes or None

    # --- binary sniff BEFORE text decode ----------------------------------- #
    # A ZIP "decodes" under latin-1 and would be garbage-scanned as txt; sniff
    # magic bytes first. xlsx routes to the workbook corrector; other recognized
    # binaries are rejected with an explicit reason.
    bdet = dispatcher.detect_bytes(content)
    if bdet is not None:
        det = bdet
        codec = None                                  # binary path: no text codec
        if det.fmt == "xlsx":
            try:
                report = xlsx_corrector.correct_workbook(
                    content, owner_policy=owner_policy, trust=trust,
                    known_owner_prefixes=known_owner_prefixes, policy=policy)
            except (XlsxError, XmlSecurityError) as exc:   # structural/security: reject loud
                det = dispatcher.Detection(fmt="unsupported",
                                           reason=f"Excel workbook rejected: {exc}")
                report = None
        else:
            report = None
    else:
        # --- decode --------------------------------------------------------- #
        try:
            text = content.decode("utf-8")
            codec = "utf-8"
        except UnicodeDecodeError:
            text = content.decode("latin-1")          # EDI fallback; never fails
            codec = "latin-1"

        # --- detect + correct ------------------------------------------------ #
        if format_hint:
            # Opt-in structured-text path: the declared column is authoritative,
            # so trust defaults True here (the user asserted "this is the
            # container column"). They can still pass trust=False to keep it advisory.
            det, report = dispatcher.correct_with_hint(
                text, format_hint=format_hint, parse_options=parse_options,
                owner_policy=owner_policy, trust=True if not trust else trust,
                known_owner_prefixes=known_owner_prefixes, policy=policy)
        else:
            det, report = dispatcher.correct(
                text, owner_policy=owner_policy, trust=trust,
                known_owner_prefixes=known_owner_prefixes, policy=policy)

    if report is None:                                # unsupported format
        db.record_event(conn, filename=safe_name, content_type=content_type,
                        detected_format=det.fmt, file_size=len(content),
                        user_agent=user_agent, owner_policy=owner_policy,
                        status="rejected", reason=det.reason, report=None)
        return {"status": "rejected", "reason": det.reason, "detected_format": det.fmt}

    event_id = db.record_event(conn, filename=safe_name, content_type=content_type,
                              detected_format=det.fmt, file_size=len(content),
                              user_agent=user_agent, owner_policy=owner_policy,
                              status="processed", reason="", report=report)

    # --- near-miss suggestions for flagged / invalid identifiers ---------- #
    # A failing check means the digit OR the body is wrong; the corrector handles
    # the digit branch, this serves the body branch by proposing previously-seen,
    # check-valid containers within small edit distance. Pool is read AFTER this
    # event is recorded -- deliberately, so siblings on the same load list can be
    # matched (one row fat-fingering another box on the list is the classic case).
    needing = [c for c in report.containers
               if c.status in ("flagged", "invalid_structure")]
    if needing:
        pool = [(e, s) for e, s in db.candidate_pool(conn) if _pool_check_valid(e)]
        if pool:
            for c in needing:
                body = _body10(c.as_found)
                if body:
                    c.near_misses = nearmiss.suggest(
                        body, pool, exclude=kernel.normalize(c.as_found))

    # --- enrich inline with OFFLINE owner identity only ------------------- #
    # Network sources (carrier T&T, BoxTech, ...) are queried out of band so a slow
    # or rate-limited API never blocks correction; api.py schedules that via a
    # background task. Here we only attach the instant, no-network owner identity.
    if enrichment is not None:
        for eqid in {c.canonical for c in report.containers}:
            data = enrichment.enrich_owner(eqid)
            if data:
                db.upsert_enrichment(conn, eqid, data)

    # --- transport visualization (auxiliary, fail-soft) ------------------- #
    # When the file describes a physical move we can draw, attach an isometric
    # scene + SVG built from the CORRECTED text. BAPLIE -> vessel bay plan;
    # X12 404/418/322 or EDIFACT COPINO/CODECO -> rail/truck. This is a view, not
    # part of correctness: any failure is swallowed, never breaking correction.
    visualization = None
    if report.corrected_text is not None and det.fmt in ("edifact", "x12"):
        try:
            if det.fmt == "edifact":
                import baplie_scene
                import bayplan_render
                scene = baplie_scene.parse_scene(report.corrected_text)
                if scene is not None and scene.units:
                    visualization = bayplan_render.render_scene(scene)
            if visualization is None:                  # not a BAPLIE -> try rail/truck
                import intermodal_scene
                import intermodal_render
                iscene = intermodal_scene.parse_intermodal(report.corrected_text)
                if iscene is not None and iscene.units:
                    visualization = intermodal_render.render_intermodal(iscene)
        except Exception as exc:                       # auxiliary: never fatal
            visualization = {"error": f"visualization failed: {exc}"}

    return {
        "status": "processed",
        "detected_format": det.fmt,
        "event_id": event_id,
        "summary": report.summary(),
        "corrected_text": report.corrected_text,
        "encoding": codec,                            # codec the text was decoded with
                                                      # (None for binary formats); writers
                                                      # MUST re-encode with this for
                                                      # byte-faithful output
        "visualization": visualization,               # BAPLIE bay-plan scene+SVG, else None
        "report": report,                             # full object for callers that want detail
    }


# ── near-miss helpers ─────────────────────────────────────────────────────────
_RE_BODY10 = re.compile(r"^([A-Z]{4}\d{6})\d?$")


def _body10(token: str) -> str:
    """The 10-char ISO/ILU body of a token (normalized), or '' if not that shape."""
    m = _RE_BODY10.match(kernel.normalize(token or ""))
    return m.group(1) if m else ""


def _pool_check_valid(eqid: str) -> bool:
    """True when an 11-char candidate's printed check digit is internally valid.
    Only such candidates may ever be suggested as fixes."""
    if len(eqid) != 11 or not eqid[10].isdigit():
        return False
    try:
        return kernel.iso6346_check_digit(eqid[:10]) == int(eqid[10])
    except ValueError:
        return False
