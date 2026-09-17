"""
test_workspace_phase_d.py
=========================
Phase D regression tests: versioned profiles, message syntax, schema and
partner validation, the message lifecycle, visit and event context, mapping
contracts on profile-bound feeds, deliveries and receiver feedback with
linked repair, linked edits, connected inspection and the new API routes.

Run:  python3 test_workspace_phase_d.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contracts                                                                        # noqa: E402
import test_workspace as T                                                              # noqa: E402
from test_workspace import HAVE_FASTAPI, _bic, _client, _root, _run_full, _write, _ws  # noqa: E402
from workspace import (context as ctx_mod, export as export_mod, feedback as feedback_mod, ingest, jobs as jobq,  # noqa: E402
                       messages, profiles, reconcile, review, runner, store)


# --------------------------------------------------------------------------- #
# Fixtures: EDIFACT interchanges built segment by segment
# --------------------------------------------------------------------------- #

def edi_message(ref: str, doc: str, function: str, containers, *, voyage="VOY1", vessel="EVER GIVEN", pol="NLRTM",
                pod="USLAX", extra_segments=(), unt_delta=0, unt_ref=None, stow=True, tdt=True, msg_type="BAPLIE"):
    segs = [f"UNH+{ref}+{msg_type}:D:95B:UN:SMDG20", f"BGM++{doc}:1+{function}"]
    if tdt:
        segs.append(f"TDT+20+{voyage}+1++CARR:172:20+++IMO9811000:146:11:{vessel}")
    segs += [f"LOC+9+{pol}", f"LOC+11+{pod}", "DTM+132:202609201400:203", "DTM+178:202609201415?+0100:303"]
    segs += list(extra_segments)
    for i, c in enumerate(containers):
        cid, size, fe = (c + ("22G1", "5"))[:3] if isinstance(c, tuple) else (c, "22G1", "5")
        if stow:
            segs.append(f"LOC+147+00{i + 1:02d}0102::5")
        segs.append(f"EQD+CN+{cid}+{size}+++{fe}")
    count = len(segs) + 1 + unt_delta
    segs.append(f"UNT+{count}+{unt_ref if unt_ref is not None else ref}")
    return segs


def edi_interchange(message_lists, *, sender="TERMINAL", receiver="LINE", icref="ICR1", unz_count=None):
    segs = [f"UNB+UNOA:2+{sender}+{receiver}+260916:1200+{icref}"]
    for m in message_lists:
        segs += m
    segs.append(f"UNZ+{unz_count if unz_count is not None else len(message_lists)}+{icref}")
    return "'\n".join(segs) + "'\n"


BAPLIE_RULES = {"family": "BAPLIE", "required_segments": ["BGM", "TDT"], "expected_sender": "TERMINAL",
                "expected_receiver": "LINE", "expected_message_version": "D:95B:UN:SMDG20",
                "sequence": "message_ref_numeric"}


def _profile(conn, ws="ws", rules=None, **kw):
    kw.setdefault("name", "baplie-line")
    kw.setdefault("message_family", "BAPLIE")
    kw.setdefault("terminal_site", "USLAX")
    return profiles.create_profile(conn, ws, "tester", rules=rules if rules is not None else BAPLIE_RULES, **kw)


def _run_msg(conn, root, text, *, profile=None, mode="incremental", baseline=None, filename="bay.edi", options=None):
    path = _write(root, filename, text.encode("utf-8"))
    sid = ingest.register_source(conn, root, "ws", filename, path)
    iid = None
    if mode:
        iid, _ = ingest.create_intent(conn, "ws", sid, mode=mode, baseline_generation_id=baseline)
    sub = runner.submit(conn, workspace_id="ws", source_file_id=sid, intent_id=iid, options=options or {},
                        profile_id=profile["id"] if profile else None)
    r = runner.run_once(conn, root)
    assert r is not None and r["state"] == "awaiting_review", (r, conn.execute("SELECT error FROM jobs WHERE id = ?", (sub["job_id"],)).fetchone()[0])
    return sub


def _messages(conn, job_id):
    return [dict(r) for r in conn.execute("SELECT * FROM message_transactions WHERE job_id = ? ORDER BY message_no", (job_id,))]


# --------------------------------------------------------------------------- #
# 1. Profiles
# --------------------------------------------------------------------------- #

def test_profiles_are_versioned_and_verified_with_evidence():
    root = _root()
    conn = _ws(root)
    p1 = _profile(conn, partner="LINE", system="Navis N4", system_version="3.8")
    assert p1["version"] == 1 and p1["verification_state"] == "unverified" and "previous_version_id" not in p1
    p2 = _profile(conn, rules={**BAPLIE_RULES, "expected_sender": "TERMINAL2"})
    assert p2["version"] == 2 and p2["previous_version_id"] == p1["id"] and p2["rules"]["expected_sender"] == "TERMINAL2"
    assert profiles.latest(conn, "ws", "baplie-line")["id"] == p2["id"]
    for bad in ({"family": "NOPE"}, {"business_key": ["nonsense"]}, {"sequence": "arrival_time"}, {"function_map": {"9": "weird"}}):
        try:
            profiles.normalize_rules(bad)
            assert False, bad
        except profiles.ProfileError as exc:
            assert exc.code == "BAD_RULES"
    try:
        profiles.set_verification(conn, p1["id"], "partner_tested", "", "admin")
        assert False
    except profiles.ProfileError as exc:
        assert exc.code == "EVIDENCE_REQUIRED"
    try:
        profiles.set_verification(conn, p1["id"], "fixture_tested", "by hand", "admin")
        assert False
    except profiles.ProfileError as exc:
        assert exc.code == "BAD_STATE"
    doc = profiles.set_verification(conn, p1["id"], "partner_tested", "partner test 2026-09-01 with LINE, ticket 4711", "admin")
    assert doc["verification_state"] == "partner_tested" and doc["verified_at"]
    assert not contracts.validate_entity("feed_profile", doc), contracts.validate_entity("feed_profile", doc)
    # fixtures: a clean file verifies, a broken one does not
    good = edi_interchange([edi_message("1", "DOC1", "9", [_bic("MSKU", 1)])])
    bad = edi_interchange([edi_message("1", "DOC1", "9", [_bic("MSKU", 1)], unt_delta=3)])
    gsid = ingest.register_source(conn, root, "ws", "good.edi", _write(root, "good.edi", good.encode()))
    bsid = ingest.register_source(conn, root, "ws", "bad.edi", _write(root, "bad.edi", bad.encode()))
    p3 = _profile(conn, acceptance_fixtures=[gsid])
    out = profiles.verify_fixtures(conn, root, p3["id"], "tester")
    assert out["ok"] is True and out["state"] == "fixture_tested"
    p4 = _profile(conn, acceptance_fixtures=[gsid, bsid])
    out = profiles.verify_fixtures(conn, root, p4["id"], "tester")
    assert out["ok"] is False and out["state"] == "unverified"
    assert any(f["code"] == "MESSAGE_SYNTAX_INVALID" for r in out["results"] for f in r["findings"])
    try:
        profiles.verify_fixtures(conn, root, p1["id"], "tester")
        assert False
    except profiles.ProfileError as exc:
        assert exc.code == "NO_FIXTURES"


# --------------------------------------------------------------------------- #
# 2. Message validation, context and events
# --------------------------------------------------------------------------- #

def test_edifact_messages_context_events_and_layers():
    root = _root()
    conn = _ws(root)
    prof = _profile(conn)
    wrong = _bic("MSKU", 7)[:-1] + str((int(_bic("MSKU", 7)[-1]) + 1) % 10)
    text = edi_interchange([edi_message("1", "DOC1", "9", [(_bic("MSKU", 1), "22G1", "5"), (wrong, "45G1", "4"), (_bic("MSKU", 3), "ZZZZ", "5")]),
                            edi_message("2", "DOC2", "9", [_bic("HLCU", 9)], voyage="VOY2", vessel="MAERSK ALABAMA")])
    sub = _run_msg(conn, root, text, profile=prof)
    job = sub["job_id"]
    ms = _messages(conn, job)
    assert [m["message_no"] for m in ms] == [1, 2]
    m1 = ms[0]
    assert m1["message_ref"] == "1" and m1["document_id"] == "DOC1" and m1["function"] == "original"
    assert m1["business_key"] == "TERMINAL|LINE|BAPLIE|DOC1" and m1["revision_key"] == "TERMINAL|LINE|BAPLIE|DOC1|1"
    assert m1["sequence_no"] == 1 and m1["sender_validated"] == 1
    assert (m1["syntax_state"], m1["schema_state"], m1["partner_state"]) == ("passed", "passed", "passed")
    assert m1["identifier_count"] == 3 and m1["lifecycle_resolution"] == "staged" and m1["declared_segment_count"] == m1["segment_count"]
    s = review.summary(conn, job)
    assert s["findings"] == {"PROFILE_UNVERIFIED": 1}, s["findings"]
    assert reconcile.effects(conn, sub["generation_id"])["state"] == "publishable"
    visits = sorted((dict(r) for r in conn.execute("SELECT * FROM visits")), key=lambda v: v["composite_key"])
    assert [v["composite_key"] for v in visits] == ["USLAX|EVER GIVEN|VOY1", "USLAX|MAERSK ALABAMA|VOY2"]
    movements = [dict(r) for r in conn.execute("SELECT * FROM movements ORDER BY message_no")]
    assert movements[0]["origin"] == "NLRTM" and movements[0]["destination"] == "USLAX" and movements[0]["visit_id"] == visits[0]["id"]
    events = [dict(r) for r in conn.execute("SELECT * FROM events WHERE message_no = 1 ORDER BY source_event_id")]
    by_type = {(e["event_type"], e["classifier"]): e for e in events}
    est = by_type[("arrival", "estimated")]
    assert est["precision"] == "minute" and est["tz_offset"] is None and est["parsed_utc"] is None and est["ambiguous"] == 0
    act = by_type[("arrival", "actual")]
    assert act["tz_offset"] == "+01:00" and act["parsed_utc"] == "2026-09-20T13:15:00Z"
    ctx = [dict(r) for r in conn.execute("SELECT * FROM observation_context WHERE job_id = ? ORDER BY ordinal", (job,))]
    assert [(c["full_empty"], c["stow_position"], c["size_type"]) for c in ctx][:3] == [
        ("full", "00010102", "22G1"), ("empty", "00020102", "45G1"), ("full", "00030102", "ZZZZ")]
    assert all(c["visit_id"] == visits[0]["id"] and c["movement_id"] == movements[0]["id"] for c in ctx[:3])
    jobrow = conn.execute("SELECT * FROM jobs WHERE id = ?", (job,)).fetchone()
    obs = conn.execute("SELECT * FROM observations WHERE job_id = ? AND ordinal = 1", (job,)).fetchone()
    layers = {l["layer"]: l for l in ctx_mod.layers_for(conn, jobrow, obs, prof)}
    assert layers["check_digit"]["state"] == "failed" and layers["structure"]["state"] == "passed"
    assert layers["operational_state"]["state"] == "passed" and "empty" in layers["operational_state"]["detail"]
    assert layers["message_profile_acceptance"]["state"] == "warning" and "unverified" in layers["message_profile_acceptance"]["detail"]
    assert layers["equipment_record"]["state"] == "insufficient_information"
    obs3 = conn.execute("SELECT * FROM observations WHERE job_id = ? AND ordinal = 2", (job,)).fetchone()
    layers3 = {l["layer"]: l for l in ctx_mod.layers_for(conn, jobrow, obs3, prof)}
    assert layers3["attribute_consistency"]["state"] == "warning" and "ZZZZ" in layers3["attribute_consistency"]["detail"]
    for l in layers.values():
        assert not contracts.validate_entity("layer_result", l), l
    reconcile.publish(conn, sub["generation_id"], "t")
    assert [m["lifecycle_resolution"] for m in _messages(conn, job)] == ["applied_scoped", "applied_scoped"]
    layers_after = {l["layer"]: l for l in ctx_mod.layers_for(conn, jobrow, obs, prof)}
    assert layers_after["equipment_record"]["state"] == "passed"


def test_syntax_schema_partner_and_context_findings():
    root = _root()
    conn = _ws(root)
    prof = _profile(conn)
    # UNT count wrong, UNZ count wrong
    bad = edi_interchange([edi_message("1", "DOC1", "9", [_bic("MSKU", 1)], unt_delta=2)], unz_count=3)
    sub = _run_msg(conn, root, bad, profile=prof)
    s = review.summary(conn, sub["job_id"])
    assert s["findings"].get("MESSAGE_SYNTAX_INVALID") == 2, s["findings"]
    assert _messages(conn, sub["job_id"])[0]["syntax_state"] == "failed"
    fx = reconcile.effects(conn, sub["generation_id"])["effects"]
    assert fx["blocked"] == ["MESSAGE"]
    # required segment missing (no TDT) and unsupported function code
    schema = edi_interchange([edi_message("1", "DOC1", "77", [_bic("MSKU", 1)], tdt=False)])
    sub = _run_msg(conn, root, schema, profile=prof, filename="schema.edi")
    s = review.summary(conn, sub["job_id"])
    assert s["findings"].get("MESSAGE_SCHEMA_VIOLATION") == 2, s["findings"]
    m = _messages(conn, sub["job_id"])[0]
    assert m["function"] == "unsupported" and m["lifecycle_resolution"] == "blocked_unsupported"
    assert s["findings"].get("CONTEXT_AMBIGUOUS") == 1, "no TDT: vessel and voyage missing, visit unresolved"
    visit = conn.execute("SELECT * FROM visits WHERE first_job_id = ?", (sub["job_id"],)).fetchone()
    assert visit["composite_key"] is None and "missing vessel, voyage" in visit["unresolved_association"]
    # partner mismatch blocks transmission, not publication
    partner = edi_interchange([edi_message("1", "DOC1", "9", [_bic("MSKU", 1)])], sender="OTHER")
    sub = _run_msg(conn, root, partner, profile=prof, filename="partner.edi")
    s = review.summary(conn, sub["job_id"])
    assert s["findings"].get("PARTNER_RULE_VIOLATION") == 1
    m = _messages(conn, sub["job_id"])[0]
    assert m["partner_state"] == "failed" and m["sender_validated"] == 0 and m["business_key"].startswith("OTHER|")
    assert reconcile.effects(conn, sub["generation_id"])["state"] == "publishable"
    # without a profile: keys from defaults, no partner checks, PROFILE_UNVERIFIED
    sub = _run_msg(conn, root, partner, profile=None, filename="noprof.edi", mode=None)
    s = review.summary(conn, sub["job_id"])
    assert s["findings"] == {"PROFILE_UNVERIFIED": 1, "IMPORT_INTENT_UNRESOLVED": 1, "CONTEXT_AMBIGUOUS": 1}, s["findings"]
    assert "missing terminal site" in conn.execute(
        "SELECT detail FROM findings WHERE job_id = ? AND code = 'CONTEXT_AMBIGUOUS'", (sub["job_id"],)).fetchone()[0], \
        "without a profile the terminal site is unknown, so the visit key cannot be built"
    assert _messages(conn, sub["job_id"])[0]["partner_state"] == "passed"


def test_message_lifecycle_duplicates_replacements_cancellations_and_ordering():
    root = _root()
    conn = _ws(root)
    prof = _profile(conn)
    c1, c2 = _bic("MSKU", 1), _bic("MSKU", 2)
    original = edi_interchange([edi_message("5", "DOC1", "9", [c1])])
    base = _run_msg(conn, root, original, profile=prof, filename="orig.edi")
    reconcile.publish(conn, base["generation_id"], "t")
    applied = _messages(conn, base["job_id"])[0]
    assert applied["lifecycle_resolution"] == "applied_scoped" and applied["applied_at"]
    # duplicate: same message again
    dup = _run_msg(conn, root, original, profile=prof, filename="dup.edi", baseline=base["generation_id"])
    s = review.summary(conn, dup["job_id"])
    assert s["findings"].get("MESSAGE_DUPLICATE") == 1 and "MESSAGE" not in reconcile.effects(conn, dup["generation_id"])["effects"]["blocked"]
    pub = reconcile.publish(conn, dup["generation_id"], "t")
    assert pub["applied"]["messages"]["duplicates"] == 1
    assert _messages(conn, dup["job_id"])[0]["lifecycle_resolution"] == "superseded"
    assert _messages(conn, base["job_id"])[0]["lifecycle_resolution"] == "applied_scoped", "no second effect"
    # same revision key, different payload
    conflict = edi_interchange([edi_message("5", "DOC1", "9", [c1, c2])])
    cj = _run_msg(conn, root, conflict, profile=prof, filename="conflict.edi", baseline=dup["generation_id"])
    assert review.summary(conn, cj["job_id"])["findings"].get("MESSAGE_ID_CONFLICT") == 1
    assert reconcile.effects(conn, cj["generation_id"])["effects"]["blocked"] == ["MESSAGE"]
    reconcile.abandon(conn, cj["generation_id"], "t")
    # replacement with a newer message reference supersedes the original
    repl = edi_interchange([edi_message("7", "DOC1", "5", [c1, c2])])
    rj = _run_msg(conn, root, repl, profile=prof, filename="repl.edi", baseline=dup["generation_id"])
    m = _messages(conn, rj["job_id"])[0]
    assert m["function"] == "replacement" and m["lifecycle_resolution"] == "staged" and "replacement of" in m["resolution_detail"]
    pub = reconcile.publish(conn, rj["generation_id"], "t")
    assert pub["applied"]["messages"] == {"applied": 1, "superseded": 1, "cancelled": 0, "duplicates": 0, "held": 0}
    assert _messages(conn, base["job_id"])[0]["lifecycle_resolution"] == "superseded"
    assert _messages(conn, base["job_id"])[0]["superseded_by"] == m["id"]
    # an older revision arriving later is retained, not applied
    old = edi_interchange([edi_message("6", "DOC1", "5", [c1])])
    oj = _run_msg(conn, root, old, profile=prof, filename="old.edi", baseline=rj["generation_id"])
    s = review.summary(conn, oj["job_id"])
    assert s["findings"].get("REVISION_OUT_OF_ORDER") == 1
    m = _messages(conn, oj["job_id"])[0]
    assert m["lifecycle_resolution"] == "held_older_revision"
    pub = reconcile.publish(conn, oj["generation_id"], "t")
    assert pub["applied"]["messages"]["held"] == 1 and pub["applied"]["messages"]["applied"] == 0
    assert _messages(conn, rj["job_id"])[0]["lifecycle_resolution"] == "applied_scoped", "current version unchanged"
    # change without any applied predecessor is held and blocks
    change = edi_interchange([edi_message("8", "DOC9", "4", [c2])])
    chj = _run_msg(conn, root, change, profile=prof, filename="change.edi", baseline=oj["generation_id"])
    assert review.summary(conn, chj["job_id"])["findings"].get("PREDECESSOR_UNRESOLVED") == 1
    assert _messages(conn, chj["job_id"])[0]["lifecycle_resolution"] == "held_predecessor_unresolved"
    try:
        reconcile.publish(conn, chj["generation_id"], "t")
        assert False
    except reconcile.PublishError as exc:
        assert exc.code == "PUBLICATION_BLOCKED" and "MESSAGE" in exc.detail
    reconcile.abandon(conn, chj["generation_id"], "t")
    # cancellation cancels the current version
    cancel = edi_interchange([edi_message("9", "DOC1", "1", [])])
    cj2 = _run_msg(conn, root, cancel, profile=prof, filename="cancel.edi", baseline=oj["generation_id"])
    pub = reconcile.publish(conn, cj2["generation_id"], "t")
    assert pub["applied"]["messages"]["cancelled"] == 1
    assert _messages(conn, rj["job_id"])[0]["lifecycle_resolution"] == "cancelled"
    # an original for an already applied business key: conflict by default, replace when the profile says so
    again = edi_interchange([edi_message("10", "DOC1", "9", [c1])])
    aj = _run_msg(conn, root, again, profile=prof, filename="again.edi", baseline=cj2["generation_id"])
    assert review.summary(conn, aj["job_id"])["findings"].get("MESSAGE_ID_CONFLICT") == 1
    reconcile.abandon(conn, aj["generation_id"], "t")
    prof2 = _profile(conn, rules={**BAPLIE_RULES, "original_on_existing": "replace"})
    aj2 = _run_msg(conn, root, again, profile=prof2, filename="again2.edi", baseline=cj2["generation_id"])
    assert "MESSAGE_ID_CONFLICT" not in review.summary(conn, aj2["job_id"])["findings"]
    pub = reconcile.publish(conn, aj2["generation_id"], "t")
    assert pub["applied"]["messages"]["applied"] == 1 and pub["applied"]["messages"]["superseded"] == 1
    docs = [m for m in _messages(conn, aj2["job_id"])]
    assert not contracts.validate_entity("message_transaction", _msg_doc(conn, docs[0]["id"]))


def _msg_doc(conn, mid):
    from workspace import views
    return views.message_doc(conn.execute("SELECT * FROM message_transactions WHERE id = ?", (mid,)).fetchone())


def test_message_jobs_resume_after_crash_without_duplicate_rows():
    root = _root()
    conn = _ws(root)
    prof = _profile(conn)
    msgs = [edi_message(str(i + 1), f"DOC{i + 1}", "9", [_bic("MSKU", i * 50 + k) for k in range(50)]) for i in range(6)]
    text = edi_interchange(msgs)
    path = _write(root, "many.edi", text.encode())
    sid = ingest.register_source(conn, root, "ws", "many.edi", path)
    sub = runner.submit(conn, workspace_id="ws", source_file_id=sid, intent_id=None, options={}, profile_id=prof["id"])
    try:
        runner.run_once(conn, root, wid="w1", batch_records=40, fail_after_records=120, lease_seconds=0.2)
        assert False
    except ingest._SimulatedCrash:
        pass
    import time
    time.sleep(0.25)
    r = runner.run_once(conn, root, wid="w2", batch_records=40)
    assert r["state"] == "awaiting_review"
    assert conn.execute("SELECT COUNT(*) FROM observations WHERE job_id = ?", (sub["job_id"],)).fetchone()[0] == 300
    assert conn.execute("SELECT COUNT(*) FROM observation_context WHERE job_id = ?", (sub["job_id"],)).fetchone()[0] == 300
    assert conn.execute("SELECT COUNT(*) FROM message_transactions WHERE job_id = ?", (sub["job_id"],)).fetchone()[0] == 6
    assert conn.execute("SELECT COUNT(*) FROM movements WHERE job_id = ?", (sub["job_id"],)).fetchone()[0] == 6
    assert conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0] == 1, "six messages, one vessel visit"
    assert conn.execute("SELECT COUNT(*) FROM events WHERE job_id = ?", (sub["job_id"],)).fetchone()[0] == 12
    assert all(m["identifier_count"] == 50 for m in _messages(conn, sub["job_id"]))


# --------------------------------------------------------------------------- #
# 3. Mapping contracts on profile-bound delimited feeds
# --------------------------------------------------------------------------- #

CONTRACT = {"fields": [{"name": "container", "identity": "container", "required": True, "type": "identifier"},
                       {"name": "remark", "identity": "remark", "required": False, "type": "text"}],
            "has_header": True, "positional": False, "extension_policy": "preserve", "required_fields": ["container"]}


def test_mapping_contract_governs_profile_bound_csv():
    root = _root()
    conn = _ws(root)
    prof = profiles.create_profile(conn, "ws", "tester", name="fleet-csv", mapping_contract=CONTRACT, message_family="CSV")
    assert prof["mapping_contract"]["required_fields"] == ["container"]
    rows = "".join(f"r{i},{_bic('MSKU', i)}\r\n" for i in range(20))
    # reordered header with an extension column: continues
    sub = _run_full(conn, root, "ws", "reorder.csv", ("remark,container,extra\r\n" + "".join(
        f"r{i},{_bic('MSKU', i)},x\r\n" for i in range(20))).encode(), mode="full_snapshot", declared=20, columns=(),
        options={"profile_id": prof["id"]}) if False else None
    path = _write(root, "reorder.csv", ("remark,container,extra\r\n" + "".join(f"r{i},{_bic('MSKU', i)},x\r\n" for i in range(20))).encode())
    sid = ingest.register_source(conn, root, "ws", "reorder.csv", path)
    iid, _ = ingest.create_intent(conn, "ws", sid, mode="full_snapshot", declared_record_count=20)
    sub = runner.submit(conn, workspace_id="ws", source_file_id=sid, intent_id=iid, options={}, profile_id=prof["id"])
    assert runner.run_once(conn, root)["state"] == "awaiting_review"
    s = review.summary(conn, sub["job_id"])
    assert s["findings"].get("MAPPING_REORDER_HARMLESS") == 1 and s["findings"].get("MAPPING_EXTENSION_PRESERVED") == 1, s["findings"]
    assert s["counters"]["status_valid"] == 20 and reconcile.effects(conn, sub["generation_id"])["state"] == "publishable"
    ck = jobq.load_checkpoint(conn, sub["job_id"])
    assert ck["mapping"]["fingerprint"] and ck["mapping"]["validation"]["violations"] == 0
    # missing required column blocks the transformation
    path = _write(root, "missing.csv", ("remark,box\r\n" + rows).encode())
    sid = ingest.register_source(conn, root, "ws", "missing.csv", path)
    iid, _ = ingest.create_intent(conn, "ws", sid, mode="full_snapshot", declared_record_count=20)
    sub = runner.submit(conn, workspace_id="ws", source_file_id=sid, intent_id=iid, options={}, profile_id=prof["id"])
    assert runner.run_once(conn, root)["state"] == "awaiting_review"
    s = review.summary(conn, sub["job_id"])
    assert s["findings"].get("MAPPING_DRIFT") == 1 and s["counters"].get("records_total", 0) == 0
    assert "MAPPING" in reconcile.effects(conn, sub["generation_id"])["effects"]["blocked"]
    # late violations: a layout change after sampling blocks; isolated bad rows do not
    late = "container,remark\r\n" + "".join(f"{_bic('MSKU', i)},r{i}\r\n" for i in range(10)) + "".join(f"garbage{i},r\r\n" for i in range(10))
    path = _write(root, "late.csv", late.encode())
    sid = ingest.register_source(conn, root, "ws", "late.csv", path)
    sub = runner.submit(conn, workspace_id="ws", source_file_id=sid, intent_id=None, options={}, profile_id=prof["id"])
    assert runner.run_once(conn, root)["state"] == "awaiting_review"
    s = review.summary(conn, sub["job_id"])
    assert s["findings"].get("MAPPING_VIOLATION_LATE") == 1 and "layout change" in conn.execute(
        "SELECT detail FROM findings WHERE job_id = ? AND code = 'MAPPING_VIOLATION_LATE'", (sub["job_id"],)).fetchone()[0]
    isolated = "container,remark\r\n" + "".join(f"{_bic('MSKU', i)},r{i}\r\n" for i in range(30)) + "bad1,r\r\nbad2,r\r\n"
    path = _write(root, "isolated.csv", isolated.encode())
    sid = ingest.register_source(conn, root, "ws", "isolated.csv", path)
    sub = runner.submit(conn, workspace_id="ws", source_file_id=sid, intent_id=None, options={}, profile_id=prof["id"])
    assert runner.run_once(conn, root)["state"] == "awaiting_review"
    s = review.summary(conn, sub["job_id"])
    assert s["findings"].get("MAPPING_VIOLATION_LATE") == 2 and s["counters"]["status_invalid_structure"] == 2
    assert s["findings"].get("PROFILE_UNVERIFIED") == 1


# --------------------------------------------------------------------------- #
# 4. X12 envelopes
# --------------------------------------------------------------------------- #

def x12_interchange(sets, *, sender="TERM", receiver="RAIL", groups=1):
    isa = f"ISA*00*          *00*          *ZZ*{sender:<15}*ZZ*{receiver:<15}*260916*1200*U*00401*000000001*0*P*:"
    segs = [isa, f"GS*IO*{sender}*{receiver}*20260916*1200*1*X*004010"]
    for s in sets:
        segs += s
    segs += [f"GE*{len(sets)}*1", f"IEA*{groups}*000000001"]
    return "~\n".join(segs) + "~\n"


def x12_set(control, function, n7s, *, se_delta=0):
    segs = [f"ST*322*{control}", f"BGN*{function}*REF{control}*20260916"]
    for initial, number, check in n7s:
        segs.append("*".join(["N7", initial, number] + [""] * 8 + ["L"] + [""] * 6 + [check] + [""] * 3 + ["22G1"]))
        segs.append("G62*86*20260916*1*1200")
    segs.append(f"SE*{len(segs) + 1 + se_delta}*{control}")
    return segs


def test_x12_envelopes_and_lifecycle():
    root = _root()
    conn = _ws(root)
    prof = profiles.create_profile(conn, "ws", "tester", name="x12-rail", message_family="X12_322",
                                   rules={"family": "X12_322", "required_segments": ["BGN"], "expected_sender": "TERM"})
    text = x12_interchange([x12_set("0001", "00", [("MSCU", "123456", "0"), ("TCLU", "456789", "7")]),
                            x12_set("0002", "05", [("HLBU", "112233", "2")])])
    sub = _run_msg(conn, root, text, profile=prof, filename="rail.edi")
    ms = _messages(conn, sub["job_id"])
    assert [m["message_type"] for m in ms] == ["322", "322"] and ms[0]["sender"] == "TERM" and ms[0]["sender_validated"] == 1
    assert ms[0]["function"] == "original" and ms[1]["function"] == "replacement"
    assert ms[1]["lifecycle_resolution"] == "held_predecessor_unresolved"
    assert ms[0]["identifier_count"] == 2 and ms[0]["syntax_state"] == "passed"
    events = conn.execute("SELECT event_type, classifier, precision, raw FROM events WHERE job_id = ? AND message_no = 1",
                          (sub["job_id"],)).fetchall()
    assert [tuple(e) for e in events] == [("pickup", "actual", "minute", "202609161200")] * 2
    ctx = conn.execute("SELECT full_empty, size_type FROM observation_context WHERE job_id = ? ORDER BY ordinal", (sub["job_id"],)).fetchall()
    assert [tuple(c) for c in ctx] == [("full", "22G1")] * 3
    s = review.summary(conn, sub["job_id"])
    assert s["findings"].get("PREDECESSOR_UNRESOLVED") == 1 and s["counters"]["status_corrected"] == 1
    bad = x12_interchange([x12_set("0001", "00", [("MSCU", "123456", "0")], se_delta=1)], groups=2)
    sub = _run_msg(conn, root, bad, profile=prof, filename="rail-bad.edi")
    s = review.summary(conn, sub["job_id"])
    assert s["findings"].get("MESSAGE_SYNTAX_INVALID") == 2, s["findings"]


# --------------------------------------------------------------------------- #
# 5. Deliveries, receiver feedback, linked repair
# --------------------------------------------------------------------------- #

def _exported_job(conn, root, prof, filename="bay.edi", ref="1"):
    wrong = _bic("MSKU", 7)[:-1] + str((int(_bic("MSKU", 7)[-1]) + 1) % 10)
    text = edi_interchange([edi_message(ref, "DOC" + ref, "9", [_bic("MSKU", 1), wrong])])
    sub = _run_msg(conn, root, text, profile=prof, filename=filename, mode=None)
    apr = review.decide(conn, sub["job_id"], {"status": "corrected"}, "approved", "r")
    art = export_mod.build_artifact(conn, root, sub["job_id"], apr["id"], "r", idempotency_key=filename)
    return sub, art


def test_deliveries_feedback_correlation_and_repair():
    root = _root()
    conn = _ws(root)
    prof = _profile(conn)
    sub, art = _exported_job(conn, root, prof)
    try:
        feedback_mod.record_delivery(conn, "ws", art["id"], destination="sftp://line", state="delivery_confirmed",
                                     idempotency_key="d1", actor="ops")
        assert False
    except feedback_mod.FeedbackError as exc:
        assert exc.code == "EVIDENCE_REQUIRED"
    d = feedback_mod.record_delivery(conn, "ws", art["id"], destination="sftp://line", state="delivery_confirmed",
                                     idempotency_key="d1", actor="ops", control_reference="ICR1",
                                     outcome_evidence="sftp log 2026-09-17 12:00 put bay.CORRECTED.edi 200 OK")
    assert d["state"] == "delivery_confirmed" and d["replayed"] is False
    assert feedback_mod.record_delivery(conn, "ws", art["id"], destination="sftp://line", state="delivery_confirmed",
                                        idempotency_key="d1", actor="ops", outcome_evidence="x")["replayed"] is True
    unknown = feedback_mod.record_delivery(conn, "ws", art["id"], destination="sftp://line", state="outcome_unknown",
                                           idempotency_key="d2", actor="ops")
    assert review.summary(conn, sub["job_id"])["findings"].get("DELIVERY_OUTCOME_UNKNOWN") == 1
    from workspace import views
    assert not contracts.validate_entity("delivery_attempt", views.delivery_doc(unknown))
    # CONTRL acceptance: exact correlation to the message and the delivery attempt
    contrl = ("UNB+UNOA:2+LINE+TERMINAL+260917:0900+ACK1'UNH+1+CONTRL:D:3:UN'UCI+ICR1+TERMINAL+LINE+7'"
              "UCM+1+BAPLIE:D:95B:UN+7'UNT+4+1'UNZ+1+ACK1'")
    fb = feedback_mod.ingest_feedback(conn, root, "ws", "ops", origin="manual_upload", text=contrl)
    assert fb["correlation"]["state"] == "exact" and fb["correlation"]["attempt_id"] == d["id"]
    assert fb["technical_ack_state"] == "accepted" and fb["business_processing_state"] == "unknown"
    assert fb["response_kind"] == "CONTRL" and fb["replayed"] is False
    assert feedback_mod.ingest_feedback(conn, root, "ws", "ops", origin="manual_upload", text=contrl)["replayed"] is True
    assert not contracts.validate_entity("receiver_feedback", fb), contracts.validate_entity("receiver_feedback", fb)
    try:
        feedback_mod.start_repair(conn, root, "ws", fb["id"], "ops")
        assert False
    except feedback_mod.FeedbackError as exc:
        assert exc.code == "NOT_A_REJECTION"
    # CONTRL rejection with a syntax error code the profile does not define
    reject = ("UNB+UNOA:2+LINE+TERMINAL+260917:0901+ACK2'UNH+1+CONTRL:D:3:UN'UCI+ICR1+TERMINAL+LINE+4'"
              "UCM+1+BAPLIE:D:95B:UN+4+99'UCS+5+12'UNT+5+1'UNZ+1+ACK2'")
    fb2 = feedback_mod.ingest_feedback(conn, root, "ws", "ops", origin="manual_upload", text=reject)
    assert fb2["technical_ack_state"] == "rejected" and fb2["correlation"]["state"] == "exact"
    assert review.summary(conn, sub["job_id"])["findings"].get("FEEDBACK_CODE_UNKNOWN") is None, "4 is a defined action code"
    rep = feedback_mod.start_repair(conn, root, "ws", fb2["id"], "ops")
    assert rep["previous_artifact_id"] == art["id"] and rep["replayed"] is False
    assert feedback_mod.start_repair(conn, root, "ws", fb2["id"], "ops")["replayed"] is True
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (rep["job_id"],)).fetchone()
    assert job["repair_of_feedback_id"] == fb2["id"] and job["profile_id"] == prof["id"]
    assert runner.run_once(conn, root)["state"] == "awaiting_review"
    s = review.summary(conn, rep["job_id"])
    assert s["counters"]["status_valid"] == 2 and s["options"]["repair"]["feedback_id"] == fb2["id"]
    art2 = export_mod.build_artifact(conn, root, rep["job_id"], None, "r", idempotency_key="repair")
    assert art2["manifest"]["repair_of_feedback_id"] == fb2["id"]
    assert views.artifact_doc(conn.execute("SELECT * FROM export_artifacts WHERE id = ?", (art2["id"],)).fetchone())["lineage"]["repair_of_feedback_id"] == fb2["id"]
    # APERAK with an unknown error code; unmatched and ambiguous correlations
    aperak = ("UNB+UNOA:2+LINE+TERMINAL+260917:0902+ACK3'UNH+1+APERAK:D:95B:UN'BGM+962+ERR1+27'RFF+ACW:1'ERC+ZZ9'"
              "FTX+AAO+++unknown stow position'UNT+6+1'UNZ+1+ACK3'")
    fb3 = feedback_mod.ingest_feedback(conn, root, "ws", "ops", origin="manual_upload", text=aperak)
    assert fb3["business_processing_state"] == "rejected" and fb3["correlation"]["state"] == "exact"
    assert any(not i["known"] for i in fb3["items"])
    assert review.summary(conn, sub["job_id"])["findings"].get("FEEDBACK_CODE_UNKNOWN") == 1
    nomatch = feedback_mod.ingest_feedback(conn, root, "ws", "ops", origin="manual_upload",
                                           text=contrl.replace("UCM+1+", "UCM+42+").replace("ICR1", "ICR9"))
    assert nomatch["correlation"]["state"] == "unmatched" and nomatch["finding"] == "FEEDBACK_UNMATCHED"
    _sub2, art2b = _exported_job(conn, root, prof, filename="bay2.edi", ref="1")
    still = feedback_mod.ingest_feedback(conn, root, "ws", "ops", origin="manual_upload",
                                         text=contrl.replace("ACK1", "ACK4").replace("0900", "0905"))
    assert still["correlation"]["state"] == "exact", "only the first job's artifact was delivered"
    feedback_mod.record_delivery(conn, "ws", art2b["id"], destination="sftp://line", state="delivery_confirmed",
                                 idempotency_key="d3", actor="ops", outcome_evidence="sftp log put bay2 200 OK")
    amb = feedback_mod.ingest_feedback(conn, root, "ws", "ops", origin="manual_upload",
                                       text=contrl.replace("ACK1", "ACK5").replace("0900", "0906"))
    assert amb["correlation"]["state"] == "ambiguous", "two delivered jobs carry message reference 1"
    try:
        feedback_mod.ingest_feedback(conn, root, "ws", "ops", origin="manual_outcome", manual={"message_refs": ["1"]})
        assert False
    except feedback_mod.FeedbackError as exc:
        assert exc.code == "DECISION_BY_REQUIRED"
    manual = feedback_mod.ingest_feedback(conn, root, "ws", "ops", origin="manual_outcome", decision_by="ops",
                                          manual={"message_refs": ["DOC1"], "technical_ack_state": "accepted",
                                                  "business_processing_state": "accepted"})
    assert manual["origin"] == "manual_outcome" and manual["correlation"]["decision_by"] == "ops"
    x997 = "ISA*00*          *00*          *ZZ*RAIL*ZZ*TERM*260917*0900*U*00401*000000009*0*P*:~GS*FA*RAIL*TERM*20260917*0900*9*X*004010~ST*997*0001~AK1*IO*1~AK2*322*0001~AK5*R*5~AK9*R*1*1*0~SE*6*0001~GE*1*9~IEA*1*000000009~"
    parsed = feedback_mod.parse_feedback_text(x997)
    assert parsed["kind"] == "997" and parsed["technical_ack_state"] == "rejected" and parsed["refs"] == ["0001"]


# --------------------------------------------------------------------------- #
# 6. Linked edits and connected inspection
# --------------------------------------------------------------------------- #

def test_linked_xml_occurrences_are_decided_together():
    root = _root()
    conn = _ws(root)
    bad = _bic("MSKU", 5)[:-1] + str((int(_bic("MSKU", 5)[-1]) + 1) % 10)
    xml = f'<snx><unit id="{bad}" unique-key="{bad}"/><container eqid="{_bic("MSKU", 6)}" type="22G1"/></snx>'
    sub = _run_full(conn, root, "ws", "linked.xml", xml.encode(), mode=None)
    partial = review.decide(conn, sub["job_id"], {"status": "corrected", "ordinal_to": 0}, "approved", "r", expected_count=1)
    try:
        export_mod.build_artifact(conn, root, sub["job_id"], partial["id"], "r", idempotency_key="p")
        assert False
    except export_mod.ExportError as exc:
        assert exc.code == "LINKED_EDIT_PRECONDITION_FAILED"
    assert conn.execute("SELECT state FROM export_artifacts").fetchone() is None
    rest = review.decide(conn, sub["job_id"], {"status": "corrected"}, "approved", "r", expected_count=1)
    try:
        export_mod.build_artifact(conn, root, sub["job_id"], rest["id"], "r", idempotency_key="p2")
        assert False, "an approval covering only the second occurrence is still partial"
    except export_mod.ExportError as exc:
        assert exc.code == "LINKED_EDIT_PRECONDITION_FAILED"
    both = review.decide(conn, sub["job_id"], {"status": "corrected", "decision": "approved"}, "approved", "r", expected_count=2)
    art = export_mod.build_artifact(conn, root, sub["job_id"], both["id"], "r", idempotency_key="full")
    out = open(export_mod.artifact_file(conn, art["id"], "corrected"), encoding="utf-8").read()
    assert out.count(_bic("MSKU", 5)) == 2 and bad not in out


def test_inspection_route_links_units_to_observations():
    if not HAVE_FASTAPI:
        return
    root = _root()
    client = _client(root)
    ws = "/v1/workspaces/acme"
    wrong = _bic("MSKU", 7)[:-1] + str((int(_bic("MSKU", 7)[-1]) + 1) % 10)
    text = edi_interchange([edi_message("1", "DOC1", "9", [(_bic("MSKU", 1), "22G1", "5"), (wrong, "45G1", "4")])])
    prof = client.post(f"{ws}/profiles", json={"name": "baplie", "message_family": "BAPLIE", "terminal_site": "USLAX",
                                              "rules": BAPLIE_RULES}).json()
    assert prof["version"] == 1
    assert client.post(f"{ws}/profiles", json={"name": "baplie", "rules": {"sequence": "wrong"}}).status_code == 422
    data = text.encode()
    up = client.post(f"{ws}/uploads", json={"filename": "bay.edi", "size": len(data)}).json()["upload_id"]
    client.put(f"{ws}/uploads/{up}?offset=0", content=data)
    sid = client.post(f"{ws}/uploads/{up}/complete", json={}).json()["source_file_id"]
    r = client.post(f"{ws}/jobs", json={"source_file_id": sid, "profile_id": prof["id"]})
    job = r.json()["job_id"]
    assert client.post(f"{ws}/jobs", json={"source_file_id": sid, "profile_id": "prof_nope"}).status_code == 404
    assert client.post(f"{ws}/jobs/{job}/run").json()["state"] == "awaiting_review"
    insp = client.get(f"{ws}/jobs/{job}/inspection").json()
    assert insp["kind"] == "vessel" and insp["svg_by_bay"] and len(insp["units"]) == 2
    by_id = {u["container_id"]: u for u in insp["units"]}
    assert by_id[wrong]["observations"][0]["status"] == "corrected" and by_id[_bic("MSKU", 1)]["observations"][0]["status"] == "valid"
    assert insp["findings"].get("PROFILE_UNVERIFIED") == 1
    msgs = client.get(f"{ws}/jobs/{job}/messages").json()["items"]
    assert len(msgs) == 1 and not contracts.validate_entity("message_transaction", msgs[0])
    events = client.get(f"{ws}/jobs/{job}/events").json()["items"]
    assert len(events) == 2 and all(not contracts.validate_entity("event_assertion", e) for e in events)
    visits = client.get(f"{ws}/visits").json()["items"]
    assert len(visits) == 1 and not contracts.validate_entity("terminal_visit", visits[0])
    assert all(not contracts.validate_entity("movement", m) for m in visits[0]["movements"])
    layers = client.get(f"{ws}/jobs/{job}/observations/1/layers").json()
    assert {l["layer"] for l in layers["layers"]} == set(contracts.ENUMS["validation_layer"])
    assert layers["context"]["stow_position"] == "00020102"
    fx = client.post(f"{ws}/profiles/{prof['id']}/verify-fixtures")
    assert fx.status_code == 409 and fx.json()["detail"]["code"] == "NO_FIXTURES"
    p2 = client.post(f"{ws}/profiles", json={"name": "baplie", "message_family": "BAPLIE", "terminal_site": "USLAX",
                                            "rules": BAPLIE_RULES, "acceptance_fixtures": [sid]}).json()
    assert client.post(f"{ws}/profiles/{p2['id']}/verify-fixtures").json()["state"] == "fixture_tested"
    assert client.post(f"{ws}/profiles/{p2['id']}/verification", json={"state": "production_enabled", "note": "x"}).status_code == 422
    ok = client.post(f"{ws}/profiles/{p2['id']}/verification", json={"state": "production_enabled",
                                                                       "note": "enabled after partner sign-off ticket 99"})
    assert ok.json()["verification_state"] == "production_enabled"
    assert not contracts.validate_entity("feed_profile", client.get(f"{ws}/profiles/{p2['id']}").json())
    assert [p["version"] for p in client.get(f"{ws}/profiles").json()["items"]] == [1, 2]
    apr = client.post(f"{ws}/jobs/{job}/decisions", json={"filter": {"status": "corrected"}, "decision": "approved"}).json()
    art = client.post(f"{ws}/jobs/{job}/exports", json={"approval_id": apr["id"], "idempotency_key": "e"}).json()
    d = client.post(f"{ws}/artifacts/{art['id']}/deliveries", json={"destination": "sftp://line", "state": "outcome_unknown",
                                                                     "idempotency_key": "d1"})
    assert d.status_code == 201 and d.json()["state"] == "outcome_unknown"
    assert client.get(f"{ws}/artifacts/{art['id']}/deliveries").json()["items"][0]["id"] == d.json()["id"]
    contrl = ("UNB+UNOA:2+LINE+TERMINAL+260917:0900+ACK1'UNH+1+CONTRL:D:3:UN'UCI+ICR1+TERMINAL+LINE+4'"
              "UCM+1+BAPLIE:D:95B:UN+4'UNT+4+1'UNZ+1+ACK1'")
    fb = client.post(f"{ws}/feedback", json={"origin": "manual_upload", "text": contrl})
    assert fb.status_code == 201 and fb.json()["technical_ack_state"] == "rejected"
    rep = client.post(f"{ws}/feedback/{fb.json()['id']}/repair")
    assert rep.status_code == 202 and rep.json()["previous_artifact_id"] == art["id"]
    assert client.get(f"{ws}/feedback").json()["items"][0]["repair_job_id"] == rep.json()["job_id"]
    assert client.get(f"{ws}/jobs/{rep.json()['job_id']}").json()["repair_of_feedback_id"] == fb.json()["id"]


def test_workspace_pages_are_served_only_behind_the_admin_gate():
    if not HAVE_FASTAPI:
        return
    import importlib
    import tempfile
    from fastapi.testclient import TestClient
    ui_dir = tempfile.mkdtemp(prefix="ws_ui_")
    T._TMP.append(ui_dir)
    os.makedirs(os.path.join(ui_dir, "assets"))
    with open(os.path.join(ui_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write("<!doctype html><title>Workspace</title>")
    with open(os.path.join(ui_dir, "job.html"), "w", encoding="utf-8") as fh:
        fh.write("<!doctype html><title>Job</title>")
    with open(os.path.join(ui_dir, "assets", "app.js"), "w", encoding="utf-8") as fh:
        fh.write("console.log(1)")
    os.environ["CHECKDIGIT_WORKSPACE_UI"] = ui_dir
    os.environ["CHECKDIGIT_WORKSPACE_DIR"] = os.path.join(ui_dir, "data")
    os.environ.pop("CHECKDIGIT_ADMIN_AUTH", None)
    import api as service_api
    service_api = importlib.reload(service_api)
    client = TestClient(service_api.app)
    assert client.get("/workspace/").status_code == 401, "pages need the admin session like the API"
    assert client.get("/workspace/job").status_code == 401
    assert client.get("/v1/workspaces/acme").status_code == 401
    service_api.app.dependency_overrides[service_api.admin_required] = lambda: "admin@example.test"
    try:
        r = client.get("/workspace/")
        assert r.status_code == 200 and "Workspace" in r.text
        assert client.get("/workspace/job").json if False else client.get("/workspace/job").status_code == 200
        assert client.get("/workspace/assets/app.js").status_code == 200
        assert client.get("/workspace/profiles").status_code == 404, "not built in this fixture"
        assert client.get("/workspace/../api.py").status_code in (404, 400)
        assert client.get("/v1/workspaces/acme").status_code == 200
    finally:
        service_api.app.dependency_overrides.clear()


if __name__ == "__main__":
    tests = [v for kname, v in sorted(globals().items()) if kname.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    for d in T._TMP:
        shutil.rmtree(d, ignore_errors=True)
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
