"""
Regression tests for the Phase A defect repairs. Runs under pytest or as a
plain script: python3 test_phase_a.py

Each test names the defect it guards against. Tests that need FastAPI skip
themselves loudly when it is not installed.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import batch                      # noqa: E402
import db                         # noqa: E402
import dispatcher                 # noqa: E402
import enrichment                 # noqa: E402
import equipment_checkdigit as k  # noqa: E402
import limits                     # noqa: E402
import nearmiss                   # noqa: E402
import service                    # noqa: E402
import substitution               # noqa: E402
import txt_corrector              # noqa: E402
import xlsx_locator               # noqa: E402
from correction_report import (IDENTITY_MATH_CANDIDATE, IDENTITY_PRINTED_VALID,  # noqa: E402
                               IDENTITY_UNVERIFIED, OFFSET_KIND)

try:
    from fastapi.testclient import TestClient
    HAVE_FASTAPI = True
except ImportError:                                   # pragma: no cover
    HAVE_FASTAPI = False


def _db():
    path = os.path.join(tempfile.mkdtemp(), "phase_a.db")
    return db.connect(path, create_schema=True), path


# --------------------------------------------------------------------------- #
# 1. Review-only processing changes no source values
# --------------------------------------------------------------------------- #

CSV_BAD = "container,qty\nCSQU3054384,1\n"


def test_review_only_csv_is_not_rewritten():
    det, rep = dispatcher.correct_with_hint(
        CSV_BAD, format_hint="csv", parse_options={"columns": ["container"]}, trust=False)
    assert rep.summary()["flagged"] == 1 and rep.corrected_text == CSV_BAD
    conn, _ = _db()
    res = service.process_upload(conn, CSV_BAD.encode(), filename="a.csv", trust=False,
                                 format_hint="csv", parse_options={"columns": ["container"]})
    assert res["summary"]["corrected"] == 0 and res["summary"]["flagged"] == 1
    assert res["corrected_text"] == CSV_BAD, "review-only request must leave the file unchanged"
    res2 = service.process_upload(conn, CSV_BAD.encode(), filename="a.csv", trust=True,
                                  format_hint="csv", parse_options={"columns": ["container"]})
    assert res2["summary"]["corrected"] == 1 and "CSQU3054383" in res2["corrected_text"]
    conn.close()


# --------------------------------------------------------------------------- #
# 2. Raw observed value preserved; candidate distinct from identity
# --------------------------------------------------------------------------- #

X12 = ("ST*322*0001~\n"
       "N7*MSCU*123456****************0****22G1~\n"
       "N7*HLBU*112233~\n"
       "SE*3*0001~\n")


def test_as_found_is_raw_and_candidate_is_not_identity():
    conn, _ = _db()
    res = service.process_upload(conn, X12.encode(), filename="t.edi")
    by = {c.normalized: c for c in res["report"].containers}
    fixed = by["MSCU1234560"]
    assert fixed.as_found == "MSCU/123456", "raw split-field observation must be kept"
    assert fixed.candidate == "MSCU1234566" and fixed.canonical == "MSCU1234566"
    assert fixed.identity_basis == IDENTITY_MATH_CANDIDATE
    flagged = by["HLBU112233"]
    assert flagged.status == "flagged"
    assert flagged.canonical == "HLBU112233", "a flagged token keeps its observed form as key"
    assert flagged.candidate == "HLBU1122332", "the arithmetic proposal is still visible"
    assert flagged.identity_basis == IDENTITY_UNVERIFIED
    keys = {r["eqid"] for r in conn.execute("SELECT eqid FROM containers")}
    assert "HLBU1122332" not in keys, "an unapplied candidate must not be recorded as a container"
    assert "HLBU112233" in keys and "MSCU1234566" in keys
    row = conn.execute("SELECT identity_basis, normalized FROM event_containers WHERE eqid='MSCU1234566'").fetchone()
    assert row["identity_basis"] == IDENTITY_MATH_CANDIDATE and row["normalized"] == "MSCU1234560"
    valid = service.process_upload(conn, b"CSQU3054383", filename="v.txt", trust=True)["report"].containers[0]
    assert valid.identity_basis == IDENTITY_PRINTED_VALID and valid.candidate is None
    assert res["report"].offset_kind == OFFSET_KIND == "text_codepoint"
    conn.close()


# --------------------------------------------------------------------------- #
# 3. Offsets are code-point indices; multibyte text before an edited field
# --------------------------------------------------------------------------- #

def test_multibyte_prefix_offsets():
    text = "Ünïcödé ✓ 🚢 note APLU9192812 end\n"
    rep = txt_corrector.correct_txt(text, trust=True)
    assert rep.corrected_text == text.replace("APLU9192812", "APLU9192819")
    occ = rep.corrected[0].occurrences[0]
    assert text[occ.offset:occ.offset + 11] == "APLU9192812", "offset indexes the decoded text"
    assert substitution.byte_offset(text, occ.offset, "utf-8") > occ.offset
    assert substitution.utf16_index(text, occ.offset) == occ.offset + 1, "the ship emoji is two UTF-16 units"
    assert substitution.byte_offset("abc", 2, "utf-8") == 2 and substitution.utf16_index("abc", 2) == 2


# --------------------------------------------------------------------------- #
# 4. ASCII-only digits and letters (Python and TypeScript agree)
# --------------------------------------------------------------------------- #

def test_non_ascii_digits_are_invalid_structure():
    arabic = "MSKU١٢٣٤٥٦٧"
    assert k.correct_identifier(arabic, k.FieldContext.EQUIPMENT_ID).status is k.Status.INVALID_STRUCTURE
    assert k.explain(arabic)["ok"] is False
    assert k.explain("21812471217٣")["ok"] is False
    assert k.correct_x12_equipment("EMHÜ", "123456", "1").status is k.Status.INVALID_STRUCTURE
    assert k.correct_x12_equipment("EMHU", "12٣456", "").normalized == "EMHU012456",         "a non-ASCII digit inside N7-02 is dropped like any other non-digit, never converted"


# --------------------------------------------------------------------------- #
# 5. Bounded, indexed near-miss retrieval with provenance
# --------------------------------------------------------------------------- #

def _valid(body10: str) -> str:
    return body10 + str(k.iso6346_check_digit(body10))


def test_candidate_neighbors_bounded_and_complete_for_single_errors():
    conn, _ = _db()
    seen = [_valid(b) for b in ("MSKU123456", "MSKU123457", "MSKU129456", "MSKU123465",
                                "MSCU123456", "TCLU987654", "MSKU223456", "APLU919281")]
    service.process_upload(conn, ("\n".join(seen)).encode(), filename="seen.txt", trust=True)
    body = "MSKU123456"
    got = {e for e, _ in db.candidate_neighbors(conn, body)}
    for expected in ("MSKU123456", "MSKU123457", "MSKU129456", "MSKU123465", "MSCU123456", "MSKU223456"):
        assert _valid(expected) in got, expected
    assert _valid("TCLU987654") not in got and _valid("APLU919281") not in got
    full = [(e, 1) for e in seen]
    a = nearmiss.suggest(body, [(e, s) for e, s in db.candidate_neighbors(conn, body)], exclude="MSKU1234567", limit=10)
    b = nearmiss.suggest(body, full, exclude="MSKU1234567", limit=10)
    assert [x["eqid"] for x in a] == [x["eqid"] for x in b], "bounded retrieval matches the fleet scan here"
    out = service.near_misses_for(conn, "MSKU123456", exclude="MSKU1234560")
    assert out and out[0]["provenance"]["source"] == "workspace_history"
    assert db.candidate_neighbors(conn, "TOOSHORT") == []
    conn.close()


# --------------------------------------------------------------------------- #
# 6. Enrichment storage policies
# --------------------------------------------------------------------------- #

class _FakeEnricher(enrichment._HttpEnricher):
    def __init__(self, name):
        super().__init__()
        self.name = name

    @property
    def enabled(self):
        return True

    def enrich(self, eqid):
        self._last_payload = {"raw": "whole provider response", "secret_field": 1}
        return {"owner_name": "ACME", "size_type": "22G1", "manufacture_date": "2020-01",
                "extra": "x", "source": self.name}


def test_enrichment_storage_policy_defaults_and_optin():
    svc = enrichment.EnrichmentService(extra=[_FakeEnricher("maersk"), _FakeEnricher("boxtech")])
    svc._env = {}
    triples, merged = svc.enrich_detailed("MSKU1234565")
    by = {t[0]: t for t in triples}
    assert by["maersk"][2] is None, "full payloads are not warehoused by default"
    assert by["maersk"][1]["extra"] == "x", "default policy keeps every normalized field"
    assert by["boxtech"][2] is None
    assert set(by["boxtech"][1]) <= {"owner_name", "operator", "size_type", "group_type", "source"}
    assert "manufacture_date" not in by["boxtech"][1] and "extra" not in by["boxtech"][1]
    assert svc.storage_policy("boxtech").reuse_allowed is False
    svc._env = {"CHECKDIGIT_ENRICH_PERSIST_PAYLOADS": "maersk,boxtech"}
    triples, _ = svc.enrich_detailed("MSKU1234565")
    by = {t[0]: t for t in triples}
    assert by["maersk"][2] == {"raw": "whole provider response", "secret_field": 1}
    assert by["boxtech"][2] is None, "a provider with restrictive terms cannot be opted in by env"


# --------------------------------------------------------------------------- #
# 7. ZIP and XLSX expansion budgets, duplicate member paths, time budget
# --------------------------------------------------------------------------- #

def _zip(members, compression=zipfile.ZIP_DEFLATED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buf.getvalue()


def test_zip_budgets_reject_members_not_batches():
    conn, _ = _db()
    saved = (limits.ZIP_MAX_MEMBERS, limits.ZIP_MAX_MEMBER_BYTES, limits.ZIP_MAX_COMPRESSION_RATIO,
             limits.ZIP_MAX_EXPANDED_BYTES)
    try:
        limits.ZIP_MAX_MEMBERS = 3
        limits.ZIP_MAX_MEMBER_BYTES = 40_000
        limits.ZIP_MAX_COMPRESSION_RATIO = 50
        limits.ZIP_MAX_EXPANDED_BYTES = 60_000
        data = _zip([("a.txt", b"MSKU1234567\n"),
                     ("bomb.txt", b"0" * 30_000),                 # ratio > 50:1
                     ("big.txt", b"MSKU1234567 " * 4_000),        # declared > member cap
                     ("nested.zip", _zip([("x.txt", b"MSKU1234567")])),
                     ("fifth.txt", b"CSQU3054383\n")])
        res = batch.process_batch_zip(conn, data, trust=True)
        m = {x.source_name: x for x in res.members}
        assert m["a.txt"].status == "processed" and m["a.txt"].summary["corrected"] == 1
        assert m["bomb.txt"].status == "rejected" and "compression ratio" in m["bomb.txt"].reason
        assert m["big.txt"].status == "rejected" and "limit" in m["big.txt"].reason
        assert m["nested.zip"].status == "rejected" and "member limit" in m["nested.zip"].reason
        assert m["fifth.txt"].status == "rejected" and "member limit" in m["fifth.txt"].reason
        assert res.totals["files"] == 5 and res.totals["processed"] == 1
        manifest = json.loads(zipfile.ZipFile(io.BytesIO(res.zip_bytes)).read("manifest.json"))
        assert manifest["limits"]["members"] == 3
    finally:
        (limits.ZIP_MAX_MEMBERS, limits.ZIP_MAX_MEMBER_BYTES, limits.ZIP_MAX_COMPRESSION_RATIO,
         limits.ZIP_MAX_EXPANDED_BYTES) = saved
    # nested archives are never expanded: the per-file pipeline rejects them
    res = batch.process_batch_zip(conn, _zip([("inner.zip", _zip([("x.txt", b"MSKU1234567")]))]), trust=True)
    assert res.members[0].status == "rejected" and "Excel" in res.members[0].reason
    conn.close()


def test_zip_duplicate_paths_and_same_basenames_are_distinct_members():
    conn, _ = _db()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("dup.txt", b"MSKU1234567\n")
        zf.writestr("dup.txt", b"CSQU3054383\n")        # same path twice
        zf.writestr("sub/dup.txt", b"APLU9192812\n")    # same basename, other dir
    res = batch.process_batch_zip(conn, buf.getvalue(), trust=True)
    assert [m.name for m in res.members] == ["dup.txt", "dup(2).txt", "dup(3).txt"]
    assert [m.source_name for m in res.members] == ["dup.txt", "dup.txt", "sub/dup.txt"]
    assert sorted(m.summary["corrected"] for m in res.members) == [0, 1, 1]
    out = zipfile.ZipFile(io.BytesIO(res.zip_bytes))
    assert out.read("corrected/dup.corrected(2).txt") == b"CSQU3054383\n"
    conn.close()


def test_time_budget_stops_between_members():
    conn, _ = _db()
    data = _zip([("a.txt", b"MSKU1234567\n"), ("b.txt", b"MSKU1234567\n")])
    dl = limits.Deadline(seconds=-1)                    # already expired
    try:
        batch.process_batch_zip(conn, data, trust=True, deadline=dl)
        raise AssertionError("expired deadline must stop archive expansion")
    except limits.BudgetExceeded as exc:
        assert "time budget" in str(exc)
    conn.close()


def test_xlsx_budgets():
    saved = (limits.XLSX_MAX_MEMBER_BYTES, limits.XLSX_MAX_COMPRESSION_RATIO, limits.XLSX_MAX_MEMBERS)
    try:
        limits.XLSX_MAX_MEMBER_BYTES = 10_000
        bad = _zip([("xl/workbook.xml", b"<workbook/>"), ("xl/worksheets/sheet1.xml", b"<x>" + b"a" * 20_000 + b"</x>")])
        try:
            xlsx_locator.load_workbook(bad)
            raise AssertionError("declared member size past the cap must be refused")
        except xlsx_locator.XlsxError as exc:
            assert "limit" in str(exc)
        limits.XLSX_MAX_MEMBER_BYTES = saved[0]
        limits.XLSX_MAX_COMPRESSION_RATIO = 5
        bomb = _zip([("xl/workbook.xml", b"<workbook/>"), ("xl/sharedStrings.xml", b"0" * 50_000)])
        try:
            xlsx_locator.load_workbook(bomb)
            raise AssertionError("compression ratio past the cap must be refused")
        except xlsx_locator.XlsxError as exc:
            assert "compression ratio" in str(exc)
        limits.XLSX_MAX_COMPRESSION_RATIO = saved[1]
        limits.XLSX_MAX_MEMBERS = 1
        try:
            xlsx_locator.load_workbook(_zip([("xl/workbook.xml", b"<w/>"), ("b", b"x")]))
            raise AssertionError("member count past the cap must be refused")
        except xlsx_locator.XlsxError as exc:
            assert "members" in str(exc)
    finally:
        limits.XLSX_MAX_MEMBER_BYTES, limits.XLSX_MAX_COMPRESSION_RATIO, limits.XLSX_MAX_MEMBERS = saved
    ok = _zip([("xl/workbook.xml", b"<workbook/>"), ("xl/sharedStrings.xml", b"<sst><si><t>MSKU1234567</t></si></sst>")])
    parts = xlsx_locator.load_workbook(ok)
    assert parts.members["xl/sharedStrings.xml"].startswith(b"<sst>")


# --------------------------------------------------------------------------- #
# 8. HTTP layer: ingress limits, public /check privacy, contract, mapping options
# --------------------------------------------------------------------------- #

def _api(db_path):
    os.environ["CHECKDIGIT_DB"] = db_path
    os.environ.pop("CHECKDIGIT_ADMIN_AUTH", None)
    import importlib
    import api
    importlib.reload(api)
    return api, TestClient(api.app)


def test_public_check_never_uses_upload_history():
    if not HAVE_FASTAPI:
        raise AssertionError("FastAPI is required for the HTTP regression tests")
    conn, path = _db()
    service.process_upload(conn, b"seen CBHU6818017 valid", filename="s.txt", trust=True)
    api, client = _api(path)
    res = client.get("/check/CBHU6818013").json()
    assert res["verdict"] == "mismatch" and "near_misses" not in res
    assert res["scope"] == "public_arithmetic" and res["history_consulted"] is False
    assert client.get("/workspace/nearmiss/CBHU6818013").status_code == 401, "private candidates need workspace auth"
    assert "near_misses" not in client.get("/check/CBHU6818017").json()
    conn.close()


def test_correct_response_contract_includes_encoding_and_visualization():
    if not HAVE_FASTAPI:
        raise AssertionError("FastAPI is required for the HTTP regression tests")
    conn, path = _db()
    conn.close()
    api, client = _api(path)
    r = client.post("/correct?trust=true", files={"file": ("x.txt", b"MSKU1234567")})
    body = r.json()
    assert r.status_code == 200 and body["api_version"] == "2"
    assert body["encoding"] == "utf-8" and "visualization" in body and body["offset_kind"] == "text_codepoint"
    assert body["report"]["containers"][0]["as_found"] == "MSKU1234567"
    assert body["report"]["containers"][0]["identity_basis"] == "mathematical_candidate"
    latin = "EQD+CN+MSKU1234567+22G1'\xe9".encode("latin-1")
    r = client.post("/correct", files={"file": ("x.edi", b"UNB+UNOA:2+A+B+260101:1200+1'" + latin)})
    assert r.json()["encoding"] == "latin-1"


def test_upload_limits_enforced_per_route():
    if not HAVE_FASTAPI:
        raise AssertionError("FastAPI is required for the HTTP regression tests")
    conn, path = _db()
    conn.close()
    api, client = _api(path)
    too_big = b"A" * (limits.MAX_UPLOAD_BYTES + 1)
    assert client.post("/correct", files={"file": ("big.txt", too_big)}).status_code == 413
    # pasted text: the multipart form-part cap (1 MiB, HTTP 400 from the framework) is the
    # effective limit and is documented as limits.MAX_PASTE_BYTES
    assert client.post("/correct", data={"text": "A" * (limits.MAX_PASTE_BYTES + 1)}).status_code in (400, 413)
    assert client.post("/correct", data={"text": "MSKU1234567 " * 100}).status_code == 200
    assert client.post("/visualize", files={"file": ("big.txt", too_big)}).status_code == 413
    assert client.post("/correct/batch", files=[("files", ("big.txt", too_big))]).status_code == 413
    r = client.post("/correct/batch", files={"archive": ("a.zip", b"PK\x03\x04" + b"A" * limits.MAX_ARCHIVE_BYTES)})
    assert r.status_code == 413
    assert client.post("/correct", files={"file": ("ok.txt", b"MSKU1234567")}).status_code == 200


def test_ingress_middleware_rejects_before_handler():
    if not HAVE_FASTAPI:
        raise AssertionError("FastAPI is required for the HTTP regression tests")
    import api
    calls = []

    async def inner(scope, receive, send):
        calls.append("handler")
        while True:
            m = await receive()
            if not m.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = api.BodySizeLimitMiddleware(inner, max_bytes=100)

    async def run(headers, chunks):
        sent = []
        it = iter(chunks)

        async def receive():
            body, more = next(it)
            return {"type": "http.request", "body": body, "more_body": more}

        async def send(m):
            sent.append(m)
        await mw({"type": "http", "headers": headers, "method": "POST", "path": "/correct"}, receive, send)
        return sent

    # declared too large: rejected without reading a byte or calling the handler
    sent = asyncio.run(run([(b"content-length", b"1000")], [(b"x" * 1000, False)]))
    assert sent[0]["status"] == 413 and calls == []
    # chunked body that grows past the cap: cut off mid-stream, handler never answers
    sent = asyncio.run(run([], [(b"x" * 60, True), (b"x" * 60, True), (b"x" * 60, False)]))
    assert sent[0]["status"] == 413 and calls == ["handler"]
    calls.clear()
    sent = asyncio.run(run([(b"content-length", b"50")], [(b"x" * 50, False)]))
    assert sent[0]["status"] == 200 and calls == ["handler"]


def test_malformed_mapping_options_are_422_not_500():
    if not HAVE_FASTAPI:
        raise AssertionError("FastAPI is required for the HTTP regression tests")
    conn, path = _db()
    conn.close()
    api, client = _api(path)
    csv_file = {"file": ("x.csv", b"a,b\nMSKU1234567,1\n")}
    for query in ("format_hint=fixed&ranges=1-x", "format_hint=fixed&ranges=9-3", "format_hint=fixed",
                  "format_hint=csv", "format_hint=csv&columns=-1", "format_hint=fixed&ranges=0-4"):
        r = client.post(f"/correct?{query}", files=csv_file)
        assert r.status_code == 422, (query, r.status_code, r.text)
        assert r.json()["detail"], query
    r = client.post("/correct?format_hint=csv&columns=nosuch", files=csv_file)
    assert r.status_code == 415 and "not found in header" in r.json()["detail"]
    r = client.post("/correct?format_hint=csv&columns=a&trust=true", files=csv_file)
    assert r.status_code == 200 and r.json()["report"]["corrected"][0]["new"] == "MSKU1234565"


# --------------------------------------------------------------------------- #
# Plain-script runner (works without pytest installed)
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    tests = [v for kname, v in sorted(globals().items())
             if kname.startswith("test_") and callable(v)]
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
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
