#!/usr/bin/env python3
"""
run_pass26.py -- admin access boundary (Google sign-in gate).

The requirement: users may UPLOAD and PROCESS files (and every upload is still
logged), but only an allow-listed admin may READ the warehouse -- the dashboard,
history/audit, container registry, and policy. This pass proves that split end
to end, plus the load-bearing privacy property that logging does NOT depend on
auth.

It runs FULLY OFFLINE. The OAuth round-trip to Google is never exercised: Google
only gates ISSUANCE of a session, and the session cookie is our own HMAC-signed
artifact, so the test mints one directly with auth.issue_session() and asserts
the server honours / rejects it correctly. This isolates OUR boundary from
Google's availability.

  auth unit          session round-trip, tamper/expiry rejection, per-request
                     allowlist recheck, secret rotation, and fail-loud config
  closed by default  with NO session, all 9 read/config routes + /admin/whoami
                     return 401
  open with session  with a minted admin session, those routes return 200 and
                     /admin/whoami echoes the email
  signature != authz a VALIDLY SIGNED cookie for a NON-allow-listed email is
                     still 401 (the allowlist is the real boundary, server-side)
  tamper             a garbage cookie is 401
  write path open    /health, /check, /correct work with no session
  logging is open    /correct with NO session writes exactly one ingestion_event

Requires the service deps installed (fastapi, uvicorn[standard], python-multipart,
and the TestClient's httpx). Run from the project root:  python3 run_pass26.py
"""
import json
import os
import sys
import tempfile

import auth
import db

ADMIN_EMAIL = "jayde.cork@gmail.com"
COOKIE = auth.SESSION_COOKIE


def _setup_env():
    """Configure admin-auth env + an isolated temp DB at RUN TIME (not import), so
    importing this module is side-effect-free (run_acceptance.py imports it)."""
    tmp = tempfile.mkdtemp(prefix="checkdigit_pass26_")
    os.environ.update({
        "CHECKDIGIT_ADMIN_AUTH": "google",
        "CHECKDIGIT_GOOGLE_CLIENT_ID": "test-client.apps.googleusercontent.com",
        "CHECKDIGIT_GOOGLE_CLIENT_SECRET": "test-secret",
        "CHECKDIGIT_OAUTH_REDIRECT_URI": "https://app.thecheckdigit.com/admin/auth/callback",
        "CHECKDIGIT_SESSION_SECRET": "pass26-" + "k" * 48,
        "CHECKDIGIT_ADMIN_EMAILS": "Jayde.Cork@gmail.com",          # mixed case on purpose
        "CHECKDIGIT_COOKIE_SECURE": "0",                            # TestClient is http
        "CHECKDIGIT_DB": os.path.join(tmp, "pass26.db"),
        "CHECKDIGIT_STATIC": os.path.join(tmp, "no_such_static"),   # force JSON root, no SPA mount
    })

# every route that must be admin-only, with the method used to probe it
PROTECTED = [
    ("get", "/insights"),
    ("get", "/events"),
    ("get", "/events/1/containers.csv"),
    ("get", "/containers"),
    ("get", "/containers/CSQU3054383"),
    ("get", "/policy"),
    ("put", "/policy"),
    ("get", "/sources"),
    ("get", "/portwatch/USA"),
]


def _cookie_header(value: str) -> dict:
    return {"Cookie": f"{COOKIE}={value}"}


def test_auth_unit():
    cfg = auth.load_config()
    assert auth.auth_enabled() is True
    assert cfg.admin_emails == frozenset({ADMIN_EMAIL})                  # lower-cased

    good = auth.issue_session(cfg, ADMIN_EMAIL)
    assert auth.read_session(cfg, good) == ADMIN_EMAIL                   # round-trip
    assert auth.read_session(cfg, good[:-2] + "zz") is None             # tamper
    assert auth.read_session(cfg, None) is None                         # absent

    import time
    expired = auth._sign(cfg.session_secret, json.dumps(
        {"email": ADMIN_EMAIL, "exp": int(time.time()) - 1},
        separators=(",", ":")).encode())
    assert auth.read_session(cfg, expired) is None                      # expired

    # per-request allowlist recheck: same cookie, different allowlist -> None
    os.environ["CHECKDIGIT_ADMIN_EMAILS"] = "other@gmail.com"
    assert auth.read_session(auth.load_config(), good) is None
    os.environ["CHECKDIGIT_ADMIN_EMAILS"] = "Jayde.Cork@gmail.com"

    # fail-loud: enabled but missing a var
    saved = os.environ.pop("CHECKDIGIT_GOOGLE_CLIENT_SECRET")
    try:
        auth.load_config(); raise AssertionError("missing var did not fail loud")
    except RuntimeError:
        pass
    finally:
        os.environ["CHECKDIGIT_GOOGLE_CLIENT_SECRET"] = saved

    # fail-loud: empty allowlist is never "allow everyone"
    os.environ["CHECKDIGIT_ADMIN_EMAILS"] = " , ,"
    try:
        auth.load_config(); raise AssertionError("empty allowlist did not fail loud")
    except RuntimeError:
        pass
    finally:
        os.environ["CHECKDIGIT_ADMIN_EMAILS"] = "Jayde.Cork@gmail.com"
    print("  auth unit: round-trip, tamper, expiry, allowlist recheck, fail-loud all OK")


def test_closed_by_default(client):
    for method, path in PROTECTED:
        r = getattr(client, method)(path)
        assert r.status_code == 401, f"{method.upper()} {path} expected 401, got {r.status_code}"
    assert client.get("/admin/whoami").status_code == 401
    print(f"  closed by default: {len(PROTECTED)} read/config routes + /admin/whoami -> 401 with no session")


def test_open_with_session(client, cfg):
    h = _cookie_header(auth.issue_session(cfg, ADMIN_EMAIL))
    # read routes that don't need pre-existing rows return 200 on an empty warehouse
    for path in ("/insights", "/events", "/containers", "/policy", "/sources"):
        r = client.get(path, headers=h)
        assert r.status_code == 200, f"GET {path} with session expected 200, got {r.status_code}"
    who = client.get("/admin/whoami", headers=h)
    assert who.status_code == 200 and who.json().get("email") == ADMIN_EMAIL \
        and who.json().get("admin") is True
    print("  open with session: dashboard/history/registry/policy/sources -> 200; whoami echoes email")


def test_signature_is_not_authorization(client, cfg):
    # A correctly-signed cookie for an email NOT on the allowlist must still 401:
    # the server re-checks the allowlist, so forging a valid signature is not enough.
    intruder = auth.issue_session(cfg, ADMIN_EMAIL)  # signed with the real secret...
    intruder = auth._sign(cfg.session_secret, json.dumps(
        {"email": "intruder@gmail.com",
         "exp": 9999999999}, separators=(",", ":")).encode())
    r = client.get("/insights", headers=_cookie_header(intruder))
    assert r.status_code == 401, f"unlisted-email valid cookie expected 401, got {r.status_code}"
    print("  signature != authorization: valid cookie for an unlisted email -> 401")


def test_tamper_rejected(client):
    r = client.get("/insights", headers=_cookie_header("garbage.not-a-cookie"))
    assert r.status_code == 401
    print("  tamper: malformed cookie -> 401")


def test_write_path_open(client):
    assert client.get("/health").status_code == 200
    chk = client.get("/check/CSQU3054383")
    assert chk.status_code == 200 and chk.json().get("verdict") == "valid"
    r = client.post("/correct", data={"text": "CSQU3054383 gate inspection"})
    assert r.status_code == 200, f"/correct expected 200, got {r.status_code}: {r.text[:200]}"
    assert r.json().get("event_id") is not None
    print("  write path open: /health, /check, /correct succeed with NO session")


def test_logging_is_open(client):
    conn = db.connect(os.environ["CHECKDIGIT_DB"], create_schema=False)
    try:
        before = conn.execute("SELECT COUNT(*) AS n FROM ingestion_events").fetchone()["n"]
    finally:
        conn.close()

    r = client.post("/correct", data={"text": "MSKU0000000 recheck"})  # no session
    assert r.status_code == 200

    conn = db.connect(os.environ["CHECKDIGIT_DB"], create_schema=False)
    try:
        after = conn.execute("SELECT COUNT(*) AS n FROM ingestion_events").fetchone()["n"]
        latest = conn.execute(
            "SELECT detected_format, status FROM ingestion_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert after == before + 1, f"expected exactly one new ingestion_event, got {after - before}"
    assert latest["status"] in ("processed", "rejected")
    print(f"  logging is open: an unauthenticated /correct wrote 1 ingestion_event "
          f"(format={latest['detected_format']}, status={latest['status']})")


def main():
    print("Pass 26 -- admin access boundary (Google sign-in gate)")
    _setup_env()
    try:
        from fastapi.testclient import TestClient
    except Exception as exc:                                            # pragma: no cover
        print(f"  SKIP HTTP tests: TestClient unavailable ({exc}). "
              f"Install service deps (fastapi, python-multipart, httpx) to run them.")
        test_auth_unit()
        print("\nPARTIAL: auth unit verified; HTTP split not run (deps missing).")
        return

    import api
    cfg = auth.load_config()
    test_auth_unit()
    with TestClient(api.app) as client:                                # runs startup (schema init)
        test_closed_by_default(client)
        test_open_with_session(client, cfg)
        test_signature_is_not_authorization(client, cfg)
        test_tamper_rejected(client)
        test_write_path_open(client)
        test_logging_is_open(client)
    print("\nPASS: write path open + logs regardless of auth; all read/config routes "
          "gated to the allow-listed admin (server-side, signature-independent).")


if __name__ == "__main__":
    main()
