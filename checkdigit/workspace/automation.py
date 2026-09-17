"""
automation.py
=============
Inbound automation over enabled inbound connections and recovery of stale
transmissions. Every file found in an inbox is remembered by its hash, so a
tick can run any number of times without registering a feed or an
acknowledgment twice.

An acknowledgment (CONTRL, APERAK, 997, 824) becomes receiver feedback with
origin `connector` and is correlated like an upload; a rejection with an
exact correlation starts the linked repair draft. Any other file becomes a
source, gets the connection's declared intent and profile, and runs as a job
(inline when the connection says so, otherwise queued for a worker).
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

from . import connections as conn_mod, feedback as feedback_mod, ingest, reconcile, runner, transmit
from .store import audit, new_id, now_iso, transaction


def _looks_like_ack(text: str) -> bool:
    head = text.lstrip("﻿ \r\n\t")[:4000]
    if head[:3] in ("UNA", "UNB", "UNH"):
        return "+CONTRL:" in head or "+APERAK:" in head
    if head[:3] in ("ISA", "GS*", "ST*"):
        return "ST*997*" in head or "ST*824*" in head
    return False


def poll_connection(conn: sqlite3.Connection, root: str, connection_id: str, actor: str = "automation", *,
                    policy=None, transport=None) -> Dict[str, Any]:
    con = conn_mod.connection_row(conn, connection_id)
    ws = con["workspace_id"]
    if con["state"] != "enabled" or con["direction"] != "inbound":
        raise conn_mod.ConnectionError_("CONNECTION_NOT_ENABLED", f"connection is {con['state']} {con['direction']}")
    config = json.loads(con["config_json"] or "{}")
    transport = transport or conn_mod.transport_for(con)
    summary: Dict[str, Any] = {"connection_id": connection_id, "seen": 0, "new": 0, "jobs": [], "feedback": [], "ignored": [],
                               "repairs": []}
    try:
        names = transport.list_inbox()
    except conn_mod.TransportUnavailable as exc:
        return {**summary, "error": str(exc)}
    for name, size in names:
        summary["seen"] += 1
        local = transport.fetch(name)
        sha, _size, codec = ingest.hash_and_sniff(local)
        if conn.execute("SELECT 1 FROM inbox_files WHERE connection_id = ? AND sha256 = ?", (connection_id, sha)).fetchone():
            transport.archive(name)
            continue
        summary["new"] += 1
        fid = new_id("inb")
        with transaction(conn):
            conn.execute("INSERT INTO inbox_files (id, workspace_id, connection_id, filename, sha256, size_bytes, state, seen_at) "
                         "VALUES (?,?,?,?,?,?,'registered',?)", (fid, ws, connection_id, name, sha, size, now_iso()))
        with open(local, "rb") as fh:
            head = fh.read(8192).decode(codec, errors="replace")
        try:
            if _looks_like_ack(head):
                with open(local, "rb") as fh:
                    text = fh.read().decode(codec, errors="replace")
                prof = None
                if con["profile_id"]:
                    from .profiles import get_profile
                    prof = get_profile(conn, con["profile_id"])
                fb = feedback_mod.ingest_feedback(conn, root, ws, actor, origin="connector", text=text, response_profile=prof)
                detail = f"{fb['response_kind']} correlation={fb['correlation']['state']} technical={fb['technical_ack_state']}"
                rejected = fb["technical_ack_state"] == "rejected" or fb["business_processing_state"] in ("rejected", "partially_accepted")
                if rejected and fb["correlation"]["state"] == "exact" and not fb.get("repair_job_id"):
                    rep = feedback_mod.start_repair(conn, root, ws, fb["id"], actor, policy=policy)
                    summary["repairs"].append(rep["job_id"])
                    detail += f" repair={rep['job_id']}"
                with transaction(conn):
                    conn.execute("UPDATE inbox_files SET state = 'feedback_recorded', feedback_id = ?, detail = ? WHERE id = ?",
                                 (fb["id"], detail, fid))
                summary["feedback"].append(fb["id"])
            else:
                sid = ingest.register_source(conn, root, ws, name, local)
                intent_cfg = config.get("intent") or {"mode": "comparison_only"}
                intent_id = None
                if intent_cfg.get("mode") != "comparison_only":
                    intent_id, _ = ingest.create_intent(conn, ws, sid, mode=intent_cfg["mode"],
                                                        scope_kind=intent_cfg.get("scope_kind", "source_fleet"),
                                                        scope_value=intent_cfg.get("scope_value", "all"),
                                                        baseline_generation_id=reconcile.current_generation(conn, ws),
                                                        declared_record_count=intent_cfg.get("declared_record_count"))
                sub = runner.submit(conn, workspace_id=ws, source_file_id=sid, intent_id=intent_id,
                                    options=dict(config.get("options") or {}), actor=actor, profile_id=con["profile_id"])
                detail = f"job {sub['job_id']}" + (" (duplicate intent)" if sub["duplicate"] else "")
                if config.get("run_inline", True) and not sub["duplicate"]:
                    r = runner.run_once(conn, root, wid=f"automation:{connection_id}", policy=policy)
                    detail += f" -> {r['state'] if r else 'not claimed'}"
                with transaction(conn):
                    conn.execute("UPDATE inbox_files SET state = 'job_created', source_file_id = ?, job_id = ?, detail = ? WHERE id = ?",
                                 (sid, sub["job_id"], detail, fid))
                summary["jobs"].append(sub["job_id"])
        except Exception as exc:  # noqa: BLE001 - one bad file never stops the inbox
            with transaction(conn):
                conn.execute("UPDATE inbox_files SET state = 'failed', detail = ? WHERE id = ?", (f"{type(exc).__name__}: {exc}"[:500], fid))
            summary["ignored"].append({"file": name, "error": f"{type(exc).__name__}: {exc}"[:200]})
        transport.archive(name)
    audit(conn, ws, actor, "connection.poll", connection_id, json.dumps({k: v for k, v in summary.items() if k != "connection_id"})[:400])
    return summary


def tick(conn: sqlite3.Connection, root: str, workspace_id: Optional[str] = None, *, policy=None) -> Dict[str, Any]:
    """One automation pass: recover stale sends, poll every enabled inbound connection."""
    recovered = transmit.recover_stale(conn, workspace_id)
    rows = conn.execute("SELECT id FROM connections WHERE state = 'enabled' AND direction = 'inbound'"
                        + (" AND workspace_id = ?" if workspace_id else ""), (workspace_id,) if workspace_id else ()).fetchall()
    polls = []
    for r in rows:
        try:
            polls.append(poll_connection(conn, root, r["id"], policy=policy))
        except conn_mod.ConnectionError_ as exc:
            polls.append({"connection_id": r["id"], "error": exc.code})
    return {"recovered_deliveries": recovered, "polls": polls, "at": now_iso()}


def inbox_page(conn: sqlite3.Connection, workspace_id: str, after: str = "", limit: int = 100) -> List[Dict[str, Any]]:
    rows = conn.execute("SELECT * FROM inbox_files WHERE workspace_id = ? AND id > ? ORDER BY id LIMIT ?",
                        (workspace_id, after, limit)).fetchall()
    return [dict(r) for r in rows]
