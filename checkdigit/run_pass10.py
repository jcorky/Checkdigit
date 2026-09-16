#!/usr/bin/env python3
"""
run_pass10.py -- external-enrichment wiring test (NO network, NO httpx required).

Every HTTP call is intercepted by monkeypatching the enricher's _get_json/_post_json
helpers, so this exercises the request shape, response->dict mapping, the
EnrichmentService merge, env-driven enabling, error isolation, and SQLite
persistence of the new `details` column -- all offline. Fail-loud asserts.
"""
import datetime
import os
import tempfile

import db
import enrichment as E


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# ── sample payloads (subset of real provider responses) ────────────────────
BOXTECH_CONTAINER = {                       # shape from docs.bic-boxtech.org examples
    "bic_code": "NNCU", "container_number": "NNCU1000180",
    "code_holder": "CANADIAN ROYALTIES INC", "current_operator": None,
    "detail_st": "20G1", "group_st": "20GP", "manufacture_date": "2000-10-01",
}
DCSA_EVENTS = {                             # shape from Hapag-Lloyd T&T docs
    "events": [
        {"eventType": "EQUIPMENT", "eventDateTime": "2019-11-11T23:11:00Z",
         "equipmentEventTypeCode": "LOAD", "equipmentReference": "HLCU4585116",
         "ISOEquipmentCode": "22GP", "eventLocation": {"UNLocationCode": "FRPAR"},
         "transportCall": {"vessel": {"vesselName": "King of the Seas", "vesselIMONumber": "9321483"}}},
        {"eventType": "TRANSPORT", "eventDateTime": "2019-11-09T08:00:00Z",
         "transportEventTypeCode": "DEPA", "eventLocation": {"UNLocationCode": "CNSHA"}},
    ]
}


def _patch(enr, *, get=None, post=None, calls=None):
    """Replace the HTTP helpers on an enricher instance, recording call URLs/params."""
    if calls is None:
        calls = {}
    if get is not None:
        def _get(url, *, headers=None, params=None, auth=None):
            calls["get_url"] = url
            calls["get_params"] = params
            return get
        enr._get_json = _get
    if post is not None:
        def _post(url, *, headers=None, json_body=None, auth=None):
            calls["post_url"] = url
            calls["post_body"] = json_body
            return post
        enr._post_json = _post
    return calls


def test_boxtech():
    enr = E.BoxTechEnricher("user", "pass")            # enabled (creds present)
    assert enr.enabled
    calls = _patch(enr, post={"accessToken": "TESTTOKEN"}, get=BOXTECH_CONTAINER)
    out = enr.enrich("GLDU5334260")
    assert calls["post_url"].endswith("/oauth/token"), calls["post_url"]
    assert calls["get_url"].endswith("/container/GLDU5334260"), calls["get_url"]
    assert out["owner_name"] == "CANADIAN ROYALTIES INC"
    assert out["size_type"] == "20G1" and out["group_type"] == "20GP"
    assert out["manufacture_date"] == "2000-10-01"
    assert out["source"] == "bic-boxtech"
    assert "operator" not in out                        # None was filtered out
    print("  boxtech: token+container fetched, mapped ->", out)


def test_dcsa_latest_event():
    enr = E.DcsaEventsEnricher("hapag-lloyd", "https://gw.example/hl",
                               auth_header="Authorization", auth_value="Bearer X")
    assert enr.enabled
    calls = _patch(enr, get=DCSA_EVENTS)
    out = enr.enrich("HLCU4585116")
    assert calls["get_url"] == "https://gw.example/hl/events", calls["get_url"]
    assert calls["get_params"] == {"equipmentReference": "HLCU4585116"}
    assert out["vessel_name"] == "King of the Seas"     # picked the LATER event
    assert out["last_location"] == "FRPAR"
    assert out["last_event_code"] == "LOAD"
    assert out["source"] == "hapag-lloyd"
    print("  dcsa: latest event chosen, mapped ->", out)


def test_maersk_prefix_filter():
    enr = E.MaerskEnricher("ckey", prefixes=["MAE", "MSK"])
    hit = {"called": False}

    def _get(url, *, headers=None, params=None, auth=None):
        hit["called"] = True
        return DCSA_EVENTS
    enr._get_json = _get
    assert enr.enrich("HLCU1234567") == {} and hit["called"] is False    # filtered, no call
    out = enr.enrich("MAEU1234560")
    assert hit["called"] and out["vessel_name"] == "King of the Seas"
    print("  maersk: prefix filter skips non-Maersk boxes, queries Maersk ones")


def test_service_merge():
    reg = E.OwnerRegistry.from_rows([{"code": "APL", "company": "APL Co", "country": "SG"}])
    bt = E.BoxTechEnricher("u", "p"); _patch(bt, post={"accessToken": "T"}, get=BOXTECH_CONTAINER)
    dc = E.DcsaEventsEnricher("hapag-lloyd", "https://x/hl"); _patch(dc, get=DCSA_EVENTS)
    svc = E.EnrichmentService(registry=reg, extra=[bt, dc])
    assert svc.has_external
    merged = svc.enrich("APLU1234567")
    # external owner (BoxTech, authoritative) overrides registry owner
    assert merged["owner_name"] == "CANADIAN ROYALTIES INC"
    assert merged["size_type"] == "20G1"
    assert merged["vessel_name"] == "King of the Seas"
    for s in ("bic-register", "bic-boxtech", "hapag-lloyd"):
        assert s in merged["source"], merged["source"]
    print("  service.enrich: registry + boxtech + dcsa merged ->", merged["source"])
    return merged


def test_error_isolation():
    class Boom(E._HttpEnricher):
        name = "boom"
        def __init__(self, exc): super().__init__(); self._exc = exc
        @property
        def enabled(self): return True
        def enrich(self, eqid): raise self._exc

    good = E.DcsaEventsEnricher("ok", "https://x/ok"); _patch(good, get=DCSA_EVENTS)
    # transient/network error on one source is skipped; the good source still returns
    svc = E.EnrichmentService(extra=[Boom(RuntimeError("connection reset")), good])
    out = svc.enrich_external("HLCU4585116")
    assert out.get("vessel_name") == "King of the Seas", out
    # a configuration error (EnricherError) propagates loudly
    svc2 = E.EnrichmentService(extra=[Boom(E.EnricherError("missing httpx"))])
    try:
        svc2.enrich_external("HLCU4585116")
        raise AssertionError("EnricherError should have propagated")
    except E.EnricherError:
        pass
    print("  isolation: network error skipped, EnricherError propagated")


def test_persistence(merged):
    path = os.path.join(tempfile.mkdtemp(), "p10.db")
    conn = db.connect(path, create_schema=True)
    try:
        eqid = "APLU1234567"
        conn.execute("INSERT INTO containers (eqid, first_seen, last_seen, times_seen) "
                     "VALUES (?,?,?,1)", (eqid, _now(), _now()))
        conn.commit()
        db.upsert_enrichment(conn, eqid, merged)
        rec = db.enrichment_for(conn, eqid)
        assert rec["owner_name"] == "CANADIAN ROYALTIES INC"
        assert rec["details"]["size_type"] == "20G1"
        assert rec["details"]["vessel_name"] == "King of the Seas"
        print("  persist: owner column + details JSON round-tripped")

        # COALESCE: an owner-less external update must NOT wipe owner_name
        db.upsert_enrichment(conn, eqid, {"size_type": "45G1", "vessel_name": "New Ship",
                                          "source": "maersk"})
        rec2 = db.enrichment_for(conn, eqid)
        assert rec2["owner_name"] == "CANADIAN ROYALTIES INC", "owner_name was wiped!"
        assert rec2["details"]["size_type"] == "45G1"
        print("  persist: COALESCE preserved owner_name across owner-less update")
    finally:
        conn.close()


def test_build_from_env():
    none = E.EnrichmentService.build_from_env(env={})
    assert none.has_external is False
    cfg = E.EnrichmentService.build_from_env(env={
        "BOXTECH_USERNAME": "u", "BOXTECH_PASSWORD": "p",
        "MAERSK_CONSUMER_KEY": "k",
        "HAPAG_BASE_URL": "https://gw/hl", "HAPAG_BEARER": "tok",
    })
    names = sorted(e.name for e in cfg.extra)
    assert names == ["bic-boxtech", "hapag-lloyd", "maersk"], names
    assert cfg.has_external
    print("  build_from_env: enabled", names, "from env credentials")


def test_cma_cgm_and_portwatch():
    DCSA = {"events": [{"eventType": "EQUIPMENT", "eventDateTime": "2026-02-01T10:00:00Z",
        "equipmentEventTypeCode": "LOAD", "equipmentReference": "CMAU1234567",
        "eventLocation": {"UNLocationCode": "FRLEH"},
        "transportCall": {"vessel": {"vesselName": "CMA CGM TEST", "vesselIMONumber": "9839179"}}}]}
    cma = E.CmaCgmEnricher("KEY123")
    calls = _patch(cma, get=DCSA)
    out = cma.enrich("CMAU1234567")
    assert calls["get_url"].endswith("/operation/trackandtrace/v1/events"), calls["get_url"]
    assert out["vessel_name"] == "CMA CGM TEST" and out["source"] == "cma-cgm"
    # build_from_env: dedicated CMA adapter, generic for hapag/zim
    svc = E.EnrichmentService.build_from_env(env={
        "CMACGM_API_KEY": "k", "HAPAG_BASE_URL": "https://gw/hl", "HAPAG_BEARER": "t"})
    names = sorted(e.name for e in svc.extra)
    assert names == ["cma-cgm", "hapag-lloyd"], names
    assert isinstance(next(e for e in svc.extra if e.name == "cma-cgm"), E.CmaCgmEnricher)
    # generic fallback only when no dedicated key
    svc2 = E.EnrichmentService.build_from_env(env={"CMACGM_BASE_URL": "https://gw/cma"})
    assert isinstance(next(e for e in svc2.extra if e.name == "cma-cgm"), E.DcsaEventsEnricher)
    print("  cma-cgm: dedicated adapter (KeyId, verified endpoint) + generic fallback")

    pw = E.PortWatchClient()
    cap = {}
    def _get(url, *, headers=None, params=None, auth=None):
        cap.update(url=url, params=params)
        return {"features": [{"attributes": {"portname": "Los Angeles", "ISO3": "USA",
                                             "portcalls_container": 42}}]}
    pw._get_json = _get
    rows = pw.latest_for_country("usa", limit=5)
    assert cap["url"].endswith("/FeatureServer/0/query") and cap["params"]["where"] == "ISO3='USA'"
    assert rows[0]["portcalls_container"] == 42
    try:
        pw.latest_for_country("United States")
        raise AssertionError("ISO3 validation should reject")
    except E.EnricherError:
        pass
    print("  portwatch: no-account FeatureServer query, ISO3-validated, port-level (not container)")


def main():
    print("Pass 10 -- external enrichment (mocked, offline)")
    test_boxtech()
    test_dcsa_latest_event()
    test_maersk_prefix_filter()
    merged = test_service_merge()
    test_error_isolation()
    test_persistence(merged)
    test_build_from_env()
    test_cma_cgm_and_portwatch()
    print("\nPASS: external enrichment wiring verified end-to-end without network.")


if __name__ == "__main__":
    main()
