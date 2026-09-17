"""
test_workspace_phase_e.py
=========================
Phase E regression tests: authorized connections, transmission with outcome
tracking and the resend policy, inbound automation (feeds and acknowledgments
from an inbox), reference snapshots feeding the prefix layer, and enrichment
requests with authorization, budgets and storage policies.

Run:  python3 test_workspace_phase_e.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contracts                                                                       # noqa: E402
import test_workspace as T                                                             # noqa: E402
import test_workspace_phase_d as D                                                     # noqa: E402
from test_workspace import HAVE_FASTAPI, _bic, _client, _root, _write, _ws            # noqa: E402
from workspace import (automation, connections as conn_mod, context as ctx_mod, export as export_mod,  # noqa: E402
                       feedback as feedback_mod, ingest, profiles, reconcile, references, review, runner, transmit)


def _production_profile(conn, root):
    prof = D._profile(conn)
    good = D.edi_interchange([D.edi_message("1", "DOC1", "9", [_bic("MSKU", 1)])])
    sid = ingest.register_source(conn, root, "ws", "fixture.edi", _write(root, "fixture.edi", good.encode()))
    prof = D._profile(conn, acceptance_fixtures=[sid])
    profiles.verify_fixtures(conn, root, prof["id"], "tester")
    profiles.set_verification(conn, prof["id"], "production_enabled", "partner sign-off ticket 4711 on 2026-09-10", "admin")
    return profiles.get_profile(conn, prof["id"])


def _ready_artifact(conn, root, prof, ref="7"):
    wrong = _bic("MSKU", 7)[:-1] + str((int(_bic("MSKU", 7)[-1]) + 1) % 10)
    text = D.edi_interchange([D.edi_message(ref, "DOC" + ref, "9", [_bic("MSKU", 1), wrong])], icref=f"ICR{ref}")
    sub = D._run_msg(conn, root, text, profile=prof, filename=f"bay{ref}.edi", mode=None)
    apr = review.decide(conn, sub["job_id"], {"status": "corrected"}, "approved", "r")
    art = export_mod.build_artifact(conn, root, sub["job_id"], apr["id"], "r", idempotency_key=f"art{ref}")
    return sub, apr, art


def _drop_folder(root, name="partner"):
    base = os.path.join(root, name)
    for sub in ("out", "in"):
        os.makedirs(os.path.join(base, sub), exist_ok=True)
    return base


# --------------------------------------------------------------------------- #
# 1. Connections and transmission
# --------------------------------------------------------------------------- #

def test_connection_lifecycle_gates_transmission():
    root = _root()
    conn = _ws(root)
    prof = _production_profile(conn, root)
    base = _drop_folder(root)
    for bad in ({"kind": "ftp"}, {"direction": "sideways"}, {"config": {"path": base, "password": "x"}},
                {"config": {"nope": 1}}, {"duplicate_handling": "maybe"}, {"config": {"path": base, "intent": {"mode": "magic"}}}):
        kw = {"name": "line-out", "kind": "filesystem", "direction": "outbound", "config": {"path": base, "outbox": "out"}}
        kw.update(bad)
        try:
            conn_mod.create_connection(conn, "ws", "admin", **kw)
            assert False, bad
        except conn_mod.ConnectionError_:
            pass
    con = conn_mod.create_connection(conn, "ws", "admin", name="line-out", kind="filesystem", direction="outbound",
                                     config={"path": base, "outbox": "out"}, partner="LINE", profile_id=prof["id"])
    assert con["state"] == "draft" and con["can_transmit"] is False
    _sub, apr, art = _ready_artifact(conn, root, prof)
    for step, code in ((lambda: transmit.send_artifact(conn, root, "ws", art["id"], con["id"], "admin", idempotency_key="k1"), "CONNECTION_NOT_ENABLED"),
                       (lambda: conn_mod.enable(conn, con["id"], "admin"), "NOT_AUTHORIZED")):
        try:
            step()
            assert False, code
        except (transmit.TransmitError, conn_mod.ConnectionError_) as exc:
            assert exc.code == code, (exc.code, code)
    try:
        conn_mod.authorize(conn, con["id"], "admin", "ok")
        assert False
    except conn_mod.ConnectionError_ as exc:
        assert exc.code == "EVIDENCE_REQUIRED"
    con = conn_mod.authorize(conn, con["id"], "admin", "customer authorization: contract C-2026-17, ticket 99")
    assert con["state"] == "authorized"
    try:
        conn_mod.enable(conn, con["id"], "admin")
        assert False
    except conn_mod.ConnectionError_ as exc:
        assert exc.code == "NOT_VERIFIED"
    v = conn_mod.verify(conn, con["id"], "admin")
    assert v["ok"] is True and v["connection"]["state"] == "verified" and v["checked"] == "write-and-delete probe"
    assert not os.listdir(os.path.join(base, "out")), "the probe file was removed"
    # an outbound connection needs a production-enabled profile
    draft_prof = D._profile(conn, name="other")
    con2 = conn_mod.create_connection(conn, "ws", "admin", name="line-out-2", kind="filesystem", direction="outbound",
                                      config={"path": base, "outbox": "out"}, profile_id=draft_prof["id"])
    conn_mod.authorize(conn, con2["id"], "admin", "customer authorization: contract C-2026-17")
    conn_mod.verify(conn, con2["id"], "admin")
    try:
        conn_mod.enable(conn, con2["id"], "admin")
        assert False
    except conn_mod.ConnectionError_ as exc:
        assert exc.code == "PROFILE_UNVERIFIED"
    con = conn_mod.enable(conn, con["id"], "admin")
    assert con["state"] == "enabled" and con["can_transmit"] is True
    # transmission needs the transmission authorization scope on the approval
    try:
        transmit.send_artifact(conn, root, "ws", art["id"], con["id"], "admin", idempotency_key="k1")
        assert False
    except transmit.TransmitError as exc:
        assert exc.code == "TRANSMISSION_NOT_AUTHORIZED"
    try:
        transmit.authorize_transmission(conn, apr["id"], "admin", "x")
        assert False
    except transmit.TransmitError as exc:
        assert exc.code == "EVIDENCE_REQUIRED"
    doc = transmit.authorize_transmission(conn, apr["id"], "admin", "transmission authorized by ops lead, ticket 100")
    assert doc["authorization_scope"] == "transmission"
    assert not contracts.validate_entity("approval", doc)
    out = transmit.send_artifact(conn, root, "ws", art["id"], con["id"], "admin", idempotency_key="k1")
    assert out["state"] == "delivery_confirmed" and out["origin"] == "connector" and out["control_reference"] == "ICR7"
    sent = os.path.join(base, "out", "bay7.CORRECTED.edi")
    assert os.path.exists(sent) and out["receipt"]["sha256"] == art["sha256"]
    assert hashlib.sha256(open(sent, "rb").read()).hexdigest() == art["sha256"]
    assert not contracts.validate_entity("delivery_attempt", out), contracts.validate_entity("delivery_attempt", out)
    replay = transmit.send_artifact(conn, root, "ws", art["id"], con["id"], "admin", idempotency_key="k1")
    assert replay["replayed"] is True and len(os.listdir(os.path.join(base, "out"))) == 1
    # a second send with a new key is refused by the transport (file exists) and recorded as failed
    again = transmit.send_artifact(conn, root, "ws", art["id"], con["id"], "admin", idempotency_key="k2")
    assert again["state"] == "failed" and "already exists" in again["error"]
    # the job's profile must match the connection's
    other_prof = _production_profile(conn, root)
    _s2, apr2, art2 = _ready_artifact(conn, root, other_prof, ref="8")
    transmit.authorize_transmission(conn, apr2["id"], "admin", "transmission authorized, ticket 101")
    try:
        transmit.send_artifact(conn, root, "ws", art2["id"], con["id"], "admin", idempotency_key="k3")
        assert False
    except transmit.TransmitError as exc:
        assert exc.code == "PROFILE_MISMATCH"
    conn_mod.disable(conn, con["id"], "admin", "partner migration")
    try:
        transmit.send_artifact(conn, root, "ws", art["id"], con["id"], "admin", idempotency_key="k4")
        assert False
    except transmit.TransmitError as exc:
        assert exc.code == "CONNECTION_NOT_ENABLED"


class _TimeoutTransport:
    def send(self, local_path, remote_name):
        raise TimeoutError("no response in 30 s")


class _StuckTransport:
    def __init__(self, delay):
        self.delay = delay

    def send(self, local_path, remote_name):
        raise RuntimeError("simulated worker death")


def test_outcome_unknown_and_resend_policy():
    root = _root()
    conn = _ws(root)
    prof = _production_profile(conn, root)
    base = _drop_folder(root)
    con = conn_mod.create_connection(conn, "ws", "admin", name="line-out", kind="filesystem", direction="outbound",
                                     config={"path": base, "outbox": "out"}, profile_id=prof["id"])
    conn_mod.authorize(conn, con["id"], "admin", "customer authorization: contract C-2026-17")
    conn_mod.verify(conn, con["id"], "admin")
    conn_mod.enable(conn, con["id"], "admin")
    sub, apr, art = _ready_artifact(conn, root, prof)
    transmit.authorize_transmission(conn, apr["id"], "admin", "transmission authorized, ticket 100")
    unknown = transmit.send_artifact(conn, root, "ws", art["id"], con["id"], "admin", idempotency_key="t1", transport=_TimeoutTransport())
    assert unknown["state"] == "outcome_unknown" and "timeout" in unknown["error"]
    assert review.summary(conn, sub["job_id"])["findings"].get("DELIVERY_OUTCOME_UNKNOWN") == 1
    try:
        transmit.resend(conn, root, "ws", unknown["id"], "admin", idempotency_key="r1")
        assert False
    except transmit.TransmitError as exc:
        assert exc.code == "RESEND_NOT_SAFE" and "unknown" in exc.detail
    resent = transmit.resend(conn, root, "ws", unknown["id"], "admin", idempotency_key="r1",
                             note="receiver confirmed by phone that nothing arrived; resend approved by ops lead")
    assert resent["state"] == "delivery_confirmed" and resent["resend_of_id"] == unknown["id"] and ".resend-" in resent["receipt"]["path"]
    # a receiver known to reject duplicates allows the resend without a note
    con2 = conn_mod.create_connection(conn, "ws", "admin", name="line-dedup", kind="filesystem", direction="outbound",
                                      config={"path": base, "outbox": "out"}, profile_id=prof["id"], duplicate_handling="rejects_duplicates")
    conn_mod.authorize(conn, con2["id"], "admin", "customer authorization: contract C-2026-17")
    conn_mod.verify(conn, con2["id"], "admin")
    conn_mod.enable(conn, con2["id"], "admin")
    unknown2 = transmit.send_artifact(conn, root, "ws", art["id"], con2["id"], "admin", idempotency_key="t2", transport=_TimeoutTransport())
    resent2 = transmit.resend(conn, root, "ws", unknown2["id"], "admin", idempotency_key="r2")
    assert resent2["state"] == "delivery_confirmed"
    # a confirmed delivery is never resent; a failed one may be
    try:
        transmit.resend(conn, root, "ws", resent2["id"], "admin", idempotency_key="r3")
        assert False
    except transmit.TransmitError as exc:
        assert exc.code == "NOT_RESENDABLE"
    # a worker that dies mid-send leaves `sending`; recovery marks it outcome_unknown after the lease
    stuck = None
    try:
        transmit.send_artifact(conn, root, "ws", art["id"], con2["id"], "admin", idempotency_key="t3", transport=_StuckTransport(0))
    except Exception:  # noqa: BLE001
        pass
    stuck = conn.execute("SELECT id, state FROM delivery_attempts WHERE idempotency_key = 't3'").fetchone()
    assert stuck["state"] == "failed", "an exception inside the transport is a recorded failure"
    conn.execute("UPDATE delivery_attempts SET state = 'sending', lease_until = ? WHERE id = ?", (time.time() - 1, stuck["id"]))
    recovered = transmit.recover_stale(conn, "ws")
    assert recovered == [stuck["id"]]
    assert conn.execute("SELECT state FROM delivery_attempts WHERE id = ?", (stuck["id"],)).fetchone()[0] == "outcome_unknown"
    assert transmit.recover_stale(conn, "ws") == []


# --------------------------------------------------------------------------- #
# 2. Inbound automation
# --------------------------------------------------------------------------- #

def test_inbox_automation_registers_feeds_and_correlates_acknowledgments():
    root = _root()
    conn = _ws(root)
    prof = _production_profile(conn, root)
    base = _drop_folder(root)
    inbound = conn_mod.create_connection(conn, "ws", "admin", name="line-in", kind="filesystem", direction="inbound",
                                         config={"path": base, "inbox": "in", "intent": {"mode": "incremental"}}, profile_id=prof["id"])
    try:
        automation.poll_connection(conn, root, inbound["id"])
        assert False
    except conn_mod.ConnectionError_ as exc:
        assert exc.code == "CONNECTION_NOT_ENABLED"
    conn_mod.authorize(conn, inbound["id"], "admin", "customer authorization: contract C-2026-17")
    v = conn_mod.verify(conn, inbound["id"], "admin")
    assert v["ok"] and v["checked"] == "listing"
    conn_mod.enable(conn, inbound["id"], "admin")
    wrong = _bic("MSKU", 7)[:-1] + str((int(_bic("MSKU", 7)[-1]) + 1) % 10)
    feed = D.edi_interchange([D.edi_message("1", "DOC1", "9", [_bic("MSKU", 1), wrong])], icref="ICR1")
    with open(os.path.join(base, "in", "bay.edi"), "w", encoding="utf-8") as fh:
        fh.write(feed)
    with open(os.path.join(base, "in", "notes.txt"), "w", encoding="utf-8") as fh:
        fh.write("plain text mentioning MSKU9070323\n")
    out = automation.tick(conn, root, "ws")
    poll = out["polls"][0]
    assert poll["new"] == 2 and len(poll["jobs"]) == 2 and poll["feedback"] == [], poll
    assert not os.listdir(os.path.join(base, "in")) and sorted(os.listdir(os.path.join(base, "processed"))) == ["bay.edi", "notes.txt"]
    rows = automation.inbox_page(conn, "ws")
    assert [r["state"] for r in rows] == ["job_created", "job_created"]
    job_id = [r for r in rows if r["filename"] == "bay.edi"][0]["job_id"]
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert job["state"] == "awaiting_review" and job["profile_id"] == prof["id"] and job["import_intent_id"]
    assert conn.execute("SELECT mode FROM import_intents WHERE id = ?", (job["import_intent_id"],)).fetchone()[0] == "incremental"
    assert review.summary(conn, job_id)["counters"]["status_corrected"] == 1
    # the same file again is remembered by hash and not registered twice
    with open(os.path.join(base, "in", "bay-again.edi"), "w", encoding="utf-8") as fh:
        fh.write(feed)
    out = automation.tick(conn, root, "ws")
    assert out["polls"][0]["new"] == 0 and out["polls"][0]["seen"] == 1
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2, "only the two inbox jobs exist"
    # export, transmit (outbound connection), then a CONTRL rejection arrives in the inbox
    outbound = conn_mod.create_connection(conn, "ws", "admin", name="line-out", kind="filesystem", direction="outbound",
                                          config={"path": base, "outbox": "out"}, profile_id=prof["id"])
    conn_mod.authorize(conn, outbound["id"], "admin", "customer authorization: contract C-2026-17")
    conn_mod.verify(conn, outbound["id"], "admin")
    conn_mod.enable(conn, outbound["id"], "admin")
    apr = review.decide(conn, job_id, {"status": "corrected"}, "approved", "r")
    transmit.authorize_transmission(conn, apr["id"], "admin", "transmission authorized, ticket 100")
    art = export_mod.build_artifact(conn, root, job_id, apr["id"], "r", idempotency_key="a")
    sent = transmit.send_artifact(conn, root, "ws", art["id"], outbound["id"], "admin", idempotency_key="s")
    assert sent["state"] == "delivery_confirmed" and sent["control_reference"] == "ICR1"
    contrl = ("UNB+UNOA:2+LINE+TERMINAL+260917:0901+ACK2'UNH+1+CONTRL:D:3:UN'UCI+ICR1+TERMINAL+LINE+4'"
              "UCM+1+BAPLIE:D:95B:UN+4'UNT+4+1'UNZ+1+ACK2'")
    with open(os.path.join(base, "in", "ack.edi"), "w", encoding="utf-8") as fh:
        fh.write(contrl)
    out = automation.tick(conn, root, "ws")
    poll = out["polls"][0]
    assert len(poll["feedback"]) == 1 and len(poll["repairs"]) == 1, poll
    fb = feedback_mod.feedback_doc(conn, poll["feedback"][0])
    assert fb["origin"] == "connector" and fb["correlation"]["state"] == "exact" and fb["correlation"]["attempt_id"] == sent["id"]
    assert fb["technical_ack_state"] == "rejected" and fb["repair_job_id"] == poll["repairs"][0]
    repair = conn.execute("SELECT repair_of_feedback_id, state FROM jobs WHERE id = ?", (fb["repair_job_id"],)).fetchone()
    assert repair["repair_of_feedback_id"] == fb["id"]
    inbox = {r["filename"]: r for r in automation.inbox_page(conn, "ws")}
    assert inbox["ack.edi"]["state"] == "feedback_recorded" and "repair=" in inbox["ack.edi"]["detail"]
    # a broken file is recorded as failed and does not stop the inbox
    with open(os.path.join(base, "in", "bad.zip"), "wb") as fh:
        fh.write(b"PK\x03\x04" + b"\x00" * 40)
    out = automation.tick(conn, root, "ws")
    inbox = {r["filename"]: r for r in automation.inbox_page(conn, "ws")}
    assert inbox["bad.zip"]["state"] in ("job_created", "failed")
    assert conn.execute("SELECT COUNT(*) FROM audit_events WHERE action = 'connection.poll'").fetchone()[0] == 4


def test_sftp_and_https_transports_report_their_limits():
    root = _root()
    conn = _ws(root)
    prof = _production_profile(conn, root)
    con = conn_mod.create_connection(conn, "ws", "admin", name="sftp-line", kind="sftp", direction="outbound",
                                     config={"host": "sftp.example.test", "username": "cd", "remote_dir": "/in", "password_env": "LINE_SFTP_PASSWORD"},
                                     profile_id=prof["id"])
    try:
        conn_mod.verify(conn, con["id"], "admin")
        assert False
    except conn_mod.ConnectionError_ as exc:
        assert exc.code == "NOT_AUTHORIZED", "network transports are not contacted before authorization"
    conn_mod.authorize(conn, con["id"], "admin", "customer authorization: contract C-2026-17")
    v = conn_mod.verify(conn, con["id"], "admin")
    assert v["ok"] is False and "paramiko" in v["unavailable"]
    assert v["connection"]["state"] == "authorized"
    try:
        conn_mod.create_connection(conn, "ws", "admin", name="http-line", kind="https", direction="outbound",
                                   config={"url": "http://partner.example.test/inbound"})
        assert False
    except conn_mod.ConnectionError_ as exc:
        assert exc.code == "BAD_CONFIG"
    try:
        conn_mod.create_connection(conn, "ws", "admin", name="http-line", kind="https", direction="outbound",
                                   config={"url": "https://partner.example.test/inbound", "auth_header_env": "Bearer abc def"})
        assert False
    except conn_mod.ConnectionError_ as exc:
        assert exc.code == "SECRET_IN_CONFIG"


# --------------------------------------------------------------------------- #
# 3. Reference snapshots and enrichment requests
# --------------------------------------------------------------------------- #

REGISTER = "code,company,city,country\nMSK,A.P. Moller-Maersk,Copenhagen,DK\nHLC,Hapag-Lloyd,Hamburg,DE\n"


def test_owner_register_snapshot_feeds_the_prefix_layer():
    root = _root()
    conn = _ws(root)
    sub = T._run_full(conn, root, "ws", "f.csv", T._fleet_csv(3) + f"9,{_bic('TCLU', 1)},x\r\n".encode(), mode=None)
    jobrow = conn.execute("SELECT * FROM jobs WHERE id = ?", (sub["job_id"],)).fetchone()
    obs = conn.execute("SELECT * FROM observations WHERE job_id = ? AND ordinal = 0", (sub["job_id"],)).fetchone()
    before = {l["layer"]: l for l in ctx_mod.layers_for(conn, jobrow, obs, None)}
    assert before["prefix_registration"]["state"] == "not_checked"
    for bad in ({"version": "", "license_note": "BIC terms accepted 2026-09-01"}, {"version": "2026-09", "license_note": "x"},
                {"version": "2026-09", "license_note": "BIC terms accepted 2026-09-01", "text": "a,b\n1,2\n"}):
        kw = {"text": REGISTER, "version": "2026-09", "license_note": "BIC register download 2026-09-01, internal use"}
        kw.update(bad)
        try:
            references.import_owner_register(conn, root, "ws", "admin", **kw)
            assert False, bad
        except references.ReferenceError:
            pass
    snap = references.import_owner_register(conn, root, "ws", "admin", text=REGISTER, version="2026-09",
                                            license_note="BIC register download 2026-09-01, internal use only")
    assert snap["row_count"] == 2 and not contracts.validate_entity("reference_snapshot", snap)
    after = {l["layer"]: l for l in ctx_mod.layers_for(conn, jobrow, obs, None)}
    assert after["prefix_registration"]["state"] == "passed" and "Maersk" in after["prefix_registration"]["detail"]
    assert after["prefix_registration"]["rule_version"] == "2026-09"
    obs2 = conn.execute("SELECT * FROM observations WHERE job_id = ? AND ordinal = 3", (sub["job_id"],)).fetchone()
    layers2 = {l["layer"]: l for l in ctx_mod.layers_for(conn, jobrow, obs2, None)}
    assert layers2["prefix_registration"]["state"] == "failed" and "TCL" in layers2["prefix_registration"]["detail"]
    newer = references.import_owner_register(conn, root, "ws", "admin", text=REGISTER + "TCL,Triton,Bermuda,BM\n", version="2026-10",
                                             license_note="BIC register download 2026-10-01, internal use only")
    assert conn.execute("SELECT COUNT(*) FROM owner_register WHERE workspace_id = 'ws'").fetchone()[0] == 3
    layers3 = {l["layer"]: l for l in ctx_mod.layers_for(conn, jobrow, obs2, None)}
    assert layers3["prefix_registration"]["state"] == "passed" and layers3["prefix_registration"]["rule_version"] == "2026-10"
    assert [s["version"] for s in references.list_snapshots(conn, "ws")] == ["2026-09", "2026-10"] and newer["id"] != snap["id"]


def test_enrichment_requests_need_authorization_and_respect_budget_and_policy():
    root = _root()
    conn = _ws(root)
    sub = T._run_full(conn, root, "ws", "f.csv", T._fleet_csv(10), mode=None)
    try:
        references.create_request(conn, "ws", "analyst", provider="boxtech", purpose="owner and size for the fleet", fields=[],
                                  scope={"job_id": sub["job_id"]}, estimated_requests=10, budget={})
        assert False
    except references.ReferenceError as exc:
        assert exc.code == "BAD_REQUEST"
    req = references.create_request(conn, "ws", "analyst", provider="boxtech", purpose="owner and size for the fleet",
                                    fields=["owner_name", "size_type", "internal_note"], scope={"job_id": sub["job_id"]},
                                    estimated_requests=10, budget={"max_requests": 6})
    assert req["status"] == "planned" and not contracts.validate_entity("enrichment_request", req), contracts.validate_entity("enrichment_request", req)
    calls = []

    def fake_boxtech(eqid):
        calls.append(eqid)
        return {"owner_name": f"Owner of {eqid[:3]}", "size_type": "22G1", "internal_note": "not shown", "source": "boxtech"}
    providers = {"boxtech": fake_boxtech}
    try:
        references.run_request(conn, req["id"], "admin", providers=providers)
        assert False
    except references.ReferenceError as exc:
        assert exc.code == "NOT_AUTHORIZED"
    assert calls == [], "no provider is called before authorization"
    references.authorize_request(conn, req["id"], "admin", "BoxTech terms accepted by ops lead; spend approved ticket 55")
    out = references.run_request(conn, req["id"], "admin", providers=providers)
    assert out["status"] == "paused" and out["results"]["requests_made"] == 6 and len(calls) == 6, out["results"]
    results = references.results_for(conn, req["id"])
    assert len(results) == 6 and set(results[0]["fields"]) == {"owner_name", "size_type", "source"}, "internal_note is not retained under the BoxTech policy"
    again = references.run_request(conn, req["id"], "admin", providers=providers)
    assert again["status"] == "paused" and len(calls) == 6, "an exhausted budget makes no further calls"
    with references_budget(conn, req["id"], 20):
        done = references.run_request(conn, req["id"], "admin", providers=providers)
    assert done["status"] == "completed" and len(calls) == 10 and done["results"]["enriched"] == 10
    unconfigured = references.create_request(conn, "ws", "analyst", provider="maersk", purpose="events", fields=[],
                                             scope={"identifiers": [_bic("MSKU", 1)]}, estimated_requests=1, budget={"max_requests": 1})
    references.authorize_request(conn, unconfigured["id"], "admin", "approved by ops lead ticket 56")
    failed = references.run_request(conn, unconfigured["id"], "admin")
    assert failed["status"] == "failed" and "not configured" in failed["results"]["error"]


class references_budget:
    def __init__(self, conn, request_id, max_requests):
        self.conn, self.request_id, self.max_requests = conn, request_id, max_requests

    def __enter__(self):
        self.conn.execute("UPDATE enrichment_requests SET budget_json = ? WHERE id = ?", (json.dumps({"max_requests": self.max_requests}), self.request_id))

    def __exit__(self, *a):
        return False


# --------------------------------------------------------------------------- #
# 4. API
# --------------------------------------------------------------------------- #

def test_connection_and_enrichment_routes():
    if not HAVE_FASTAPI:
        return
    root = _root()
    client = _client(root)
    ws = "/v1/workspaces/acme"
    base = _drop_folder(root)
    good = D.edi_interchange([D.edi_message("1", "DOC1", "9", [_bic("MSKU", 1)])]).encode()
    up = client.post(f"{ws}/uploads", json={"filename": "fixture.edi", "size": len(good)}).json()["upload_id"]
    client.put(f"{ws}/uploads/{up}?offset=0", content=good)
    sid = client.post(f"{ws}/uploads/{up}/complete", json={}).json()["source_file_id"]
    prof = client.post(f"{ws}/profiles", json={"name": "baplie", "message_family": "BAPLIE", "terminal_site": "USLAX",
                                              "rules": D.BAPLIE_RULES, "acceptance_fixtures": [sid]}).json()
    client.post(f"{ws}/profiles/{prof['id']}/verify-fixtures")
    client.post(f"{ws}/profiles/{prof['id']}/verification", json={"state": "production_enabled", "note": "partner sign-off ticket 4711"})
    r = client.post(f"{ws}/connections", json={"name": "line-out", "kind": "filesystem", "direction": "outbound",
                                               "config": {"path": base, "outbox": "out"}, "profile_id": prof["id"]})
    assert r.status_code == 201, r.text
    con = r.json()
    assert client.post(f"{ws}/connections", json={"name": "x", "kind": "https", "direction": "outbound",
                                                  "config": {"url": "https://p.example.test", "token": "abc"}}).status_code == 422
    assert client.post(f"{ws}/connections/{con['id']}/enable").status_code == 409
    assert client.post(f"{ws}/connections/{con['id']}/authorize", json={"note": "short"}).status_code == 422
    assert client.post(f"{ws}/connections/{con['id']}/authorize", json={"note": "customer authorization contract C-2026-17"}).json()["state"] == "authorized"
    assert client.post(f"{ws}/connections/{con['id']}/verify").json()["ok"] is True
    assert client.post(f"{ws}/connections/{con['id']}/enable").json()["state"] == "enabled"
    job = client.post(f"{ws}/jobs", json={"source_file_id": sid, "profile_id": prof["id"]}).json()["job_id"]
    client.post(f"{ws}/jobs/{job}/run")
    art = client.post(f"{ws}/jobs/{job}/exports", json={"approval_id": None, "idempotency_key": "e"}).json()
    r = client.post(f"{ws}/artifacts/{art['id']}/transmit", json={"connection_id": con["id"], "idempotency_key": "t"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "TRANSMISSION_NOT_AUTHORIZED"
    items = client.get(f"{ws}/connections").json()["items"]
    assert [i["name"] for i in items] == ["line-out"] and items[0]["can_transmit"] is True
    assert client.get(f"{ws}/transmissions").json()["items"] == []
    inbound = client.post(f"{ws}/connections", json={"name": "line-in", "kind": "filesystem", "direction": "inbound",
                                                     "config": {"path": base, "inbox": "in"}, "profile_id": prof["id"]}).json()
    client.post(f"{ws}/connections/{inbound['id']}/authorize", json={"note": "customer authorization contract C-2026-17"})
    client.post(f"{ws}/connections/{inbound['id']}/verify")
    client.post(f"{ws}/connections/{inbound['id']}/enable")
    with open(os.path.join(base, "in", "feed.edi"), "wb") as fh:
        fh.write(good.replace(b"DOC1", b"DOC2"))
    tick = client.post(f"{ws}/automation/tick").json()
    assert tick["polls"][0]["new"] == 1 and len(tick["polls"][0]["jobs"]) == 1
    assert client.get(f"{ws}/inbox").json()["items"][0]["state"] == "job_created"
    r = client.post(f"{ws}/reference/owner-register", json={"text": REGISTER, "version": "2026-09",
                                                             "license_note": "BIC register download 2026-09-01, internal use only"})
    assert r.status_code == 201 and client.get(f"{ws}/reference").json()["owner_codes"] == 2
    r = client.post(f"{ws}/enrichment", json={"provider": "boxtech", "purpose": "owners", "scope": {"job_id": job},
                                              "estimated_requests": 1, "budget": {"max_requests": 1}})
    assert r.status_code == 201
    req = r.json()
    assert client.post(f"{ws}/enrichment/{req['id']}/run").status_code == 409
    client.post(f"{ws}/enrichment/{req['id']}/authorize", json={"note": "BoxTech terms accepted, spend approved ticket 55"})
    ran = client.post(f"{ws}/enrichment/{req['id']}/run").json()
    assert ran["status"] == "failed" and "not configured" in ran["results"]["error"], "no credentials in this environment"
    listing = client.get(f"{ws}/enrichment").json()
    assert listing["providers_configured"] == [] and listing["items"][0]["id"] == req["id"]


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
