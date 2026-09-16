#!/usr/bin/env python3
"""
run_pass12.py -- history/dossier data-path test.

The SPA's History and Containers views render straight off /events, /containers,
and /containers/{eqid}. FastAPI cannot execute in this sandbox, so this driver
exercises the exact queries and payload composition those endpoints perform,
against a real multi-event SQLite DB built through the dispatcher: the same
container appearing in three runs (valid in txt, corrected in SNX-style paste,
again in txt), plus enrichment, plus the per-event CSV the History view links to.
"""
import os
import tempfile

import db
import dispatcher
import enrichment as E
import service


def main():
    print("Pass 12 -- history/dossier data path")
    path = os.path.join(tempfile.mkdtemp(), "p12.db")
    conn = db.connect(path, create_schema=True)
    reg = E.OwnerRegistry.from_rows([{"code": "CBH", "company": "CBH Lines Test", "country": "PA"}])
    svc = E.EnrichmentService(registry=reg)

    # Three runs; CBHU6818017 appears in #1 and #3, APLU9192812 only in #2.
    runs = [
        ("gate_log.txt", "in-gate CBHU6818017 ok, MSKU7351770 ok"),
        ("paste",        "yard recheck: CBHU6818017 and APLU9192812 staged"),  # APLU has bad check
        ("night_list.txt", "second shift: CBHU6818017 again"),
    ]
    for name, text in runs:
        res = service.process_upload(conn, text.encode("utf-8"), filename=name,
                                     content_type="text/plain", user_agent="p12",
                                     owner_policy="strict", trust=True, enrichment=svc)
        assert res["status"] == "processed", res

    # --- /events composition --------------------------------------------- #
    rows = conn.execute("SELECT * FROM ingestion_events ORDER BY id DESC").fetchall()
    total = conn.execute("SELECT COUNT(*) AS n FROM ingestion_events").fetchone()["n"]
    assert total == 3 and len(rows) == 3
    newest = dict(rows[0])
    assert newest["filename"] == "night_list.txt" and newest["detected_format"] == "txt"
    assert newest["total_containers"] == 1 and "ip" not in " ".join(newest.keys()).lower()
    print(f"  events: {total} runs, newest-first, counts present, no IP key")

    # --- /containers composition ------------------------------------------ #
    regs = [dict(r) for r in conn.execute(
        "SELECT * FROM containers ORDER BY times_seen DESC, eqid").fetchall()]
    assert regs[0]["eqid"] == "CBHU6818017" and regs[0]["times_seen"] == 3, regs[0]
    eqids = {r["eqid"] for r in regs}
    assert "APLU9192819" in eqids and "MSKU7351770" in eqids   # canonical (corrected) form
    print(f"  registry: {len(regs)} distinct, CBHU6818017 seen 3x ranks first, APLU stored canonical")

    # --- dossier: container_history (the previously-untested JOIN) -------- #
    hist = db.container_history(conn, "CBHU6818017")
    assert len(hist) == 3
    assert [h["filename"] for h in hist] == ["night_list.txt", "paste", "gate_log.txt"]  # DESC
    assert all(h["role"] == "valid" and h["as_found"] == "CBHU6818017" for h in hist)
    assert all(set(h) >= {"event_id", "ts", "filename", "detected_format", "as_found",
                          "role", "printed_check", "computed_check", "occurrences"} for h in hist)
    hist2 = db.container_history(conn, "APLU9192819")
    assert len(hist2) == 1 and hist2[0]["role"] == "corrected"
    assert hist2[0]["as_found"] == "APLU9192812" and hist2[0]["computed_check"] == "9"
    assert db.container_history(conn, "ZZZU0000000") == []
    print("  dossier history: 3 appearances DESC, corrected as_found 2->9 preserved, unknown -> []")

    # --- dossier: full payload as /containers/{eqid} composes it ---------- #
    c = conn.execute("SELECT * FROM containers WHERE eqid = ?", ("CBHU6818017",)).fetchone()
    dossier = {"container": dict(c), "enrichment": db.enrichment_for(conn, "CBHU6818017"),
               "history": db.container_history(conn, "CBHU6818017")}
    assert dossier["container"]["owner"] == "CBH"
    assert dossier["enrichment"]["owner_name"] == "CBH Lines Test"
    assert dossier["enrichment"]["details"] == {}            # registry-only run: no externals
    assert db.enrichment_for(conn, "MSKU7351770") is None    # MSK not in this registry
    print("  dossier payload: container + enrichment(owner CBH Lines Test) + history compose")

    # --- per-event CSV the History view links to --------------------------- #
    ev2 = hist2[0]["event_id"]
    out = db.export_event_csv(conn, ev2)
    assert "APLU9192819,APLU9192812,corrected" in out and "CBHU6818017" in out
    print(f"  csv: event {ev2} verdict table downloadable (corrected + valid rows)")

    conn.close()
    print("\nPASS: history/dossier data path verified end-to-end on a real multi-event DB.")


if __name__ == "__main__":
    main()
