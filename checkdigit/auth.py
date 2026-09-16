"""
auth.py
=======
Admin authentication for CHECKDIGIT via Google Sign-In (OpenID Connect,
Authorization Code flow), gated to an explicit email allowlist.

BOUNDARY MODEL (locked):
  * The WRITE path (/correct, /correct/batch, /visualize, /check, /health) stays
    OPEN and keeps writing the audit log. Auth changes WHO CAN READ the warehouse,
    not what gets logged on a user's upload.
  * The READ path (/insights, /events*, /containers*, /policy*, /sources,
    /portwatch*) is admin-only, guarded by the `admin_required` dependency.

THE REAL BOUNDARY is the server-side allowlist check on the *verified* Google
email -- NOT the SPA hiding tabs, and NOT Google's own test-user gating (that is
defence in depth). Every email not on CHECKDIGIT_ADMIN_EMAILS is refused with
403 and given no session, even after Google has authenticated it.

DEPENDENCY ISOLATION: this is the ONLY module that needs PyJWT[crypto] (RS256
id_token signature verification against Google's JWKS). It is imported lazily,
inside the verify path, so importing this module for config checks never hard-
requires the dependency. The correction core stays pure stdlib; like httpx for
enrichment, this third-party dep is quarantined here.

FAIL LOUD:
  * Admin auth only mounts when CHECKDIGIT_ADMIN_AUTH=google. If it is unset,
    admin features are simply OFF (unreachable) -- never silently OPEN.
  * When mounted, a missing client id/secret/redirect/session-secret, or an
    EMPTY allowlist, raises and the server refuses to boot. An empty allowlist
    is never interpreted as "allow everyone".

We request only `openid email profile`, and use access_type=online: NO Google
refresh token is requested or stored. This is a login (authN), not ongoing API
access. The only persisted credential is our own HMAC-signed session cookie.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

try:  # Module-level so the stringized `Request` annotation on the admin/callback/whoami
    from fastapi import Request  # FastAPI dependencies resolves under this module's
except ImportError:  # `from __future__ import annotations`. Guarded so auth.py still
    Request = None  # type: ignore  # imports for config checks (and A12) without FastAPI.

# Google's OIDC discovery document. Endpoints (authorization/token/jwks/userinfo)
# are READ FROM this document at runtime, never hardcoded -- so a Google endpoint
# change cannot silently break or, worse, be wrong in our source.
DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
SCOPE = "openid email profile"

STATE_COOKIE = "cd_oauth_state"      # short-lived, carries signed {state, nonce}
SESSION_COOKIE = "cd_admin"          # HMAC-signed admin session
SESSION_TTL_SECONDS = 12 * 3600      # 12h; re-auth with Google after expiry
STATE_TTL_SECONDS = 600              # 10 min to complete the round trip


# --------------------------------------------------------------------------- #
# 1. Config (fail-loud)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    session_secret: bytes
    admin_emails: frozenset           # lower-cased
    cookie_secure: bool


def auth_enabled() -> bool:
    """True only when the operator has explicitly turned admin auth on."""
    return os.environ.get("CHECKDIGIT_ADMIN_AUTH", "").strip().lower() == "google"


def load_config() -> AuthConfig:
    """Read + validate admin-auth config from the environment. Fail loud."""
    cid = os.environ.get("CHECKDIGIT_GOOGLE_CLIENT_ID", "").strip()
    csec = os.environ.get("CHECKDIGIT_GOOGLE_CLIENT_SECRET", "").strip()
    redirect = os.environ.get("CHECKDIGIT_OAUTH_REDIRECT_URI", "").strip()
    sess = os.environ.get("CHECKDIGIT_SESSION_SECRET", "").strip()
    emails_raw = os.environ.get("CHECKDIGIT_ADMIN_EMAILS", "").strip()
    secure = os.environ.get("CHECKDIGIT_COOKIE_SECURE", "1").strip().lower() \
        not in ("0", "false", "no", "")

    missing = [name for name, val in (
        ("CHECKDIGIT_GOOGLE_CLIENT_ID", cid),
        ("CHECKDIGIT_GOOGLE_CLIENT_SECRET", csec),
        ("CHECKDIGIT_OAUTH_REDIRECT_URI", redirect),
        ("CHECKDIGIT_SESSION_SECRET", sess),
        ("CHECKDIGIT_ADMIN_EMAILS", emails_raw),
    ) if not val]
    if missing:
        raise RuntimeError(
            "CHECKDIGIT_ADMIN_AUTH=google but these env vars are missing: "
            + ", ".join(missing)
            + ". Refusing to start -- the dashboard must never be left open. "
              "See .env.example."
        )

    emails = frozenset(e.strip().lower() for e in emails_raw.split(",") if e.strip())
    if not emails:
        raise RuntimeError(
            "CHECKDIGIT_ADMIN_EMAILS resolved to an EMPTY allowlist. An empty "
            "allowlist is never read as 'allow everyone' -- refusing to start."
        )
    if len(sess) < 16:
        raise RuntimeError(
            "CHECKDIGIT_SESSION_SECRET is too short; use >=32 random bytes, e.g. "
            "python3 -c \"import secrets;print(secrets.token_urlsafe(48))\""
        )

    return AuthConfig(
        client_id=cid,
        client_secret=csec,
        redirect_uri=redirect,
        session_secret=sess.encode("utf-8"),
        admin_emails=emails,
        cookie_secure=secure,
    )


# --------------------------------------------------------------------------- #
# 2. OIDC discovery (fetched + cached; never hardcoded)
# --------------------------------------------------------------------------- #
_discovery_cache: Optional[dict] = None


def _http_get_json(url: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed Google/discovery URL)
        return json.loads(resp.read().decode("utf-8"))


def discovery() -> dict:
    global _discovery_cache
    if _discovery_cache is None:
        _discovery_cache = _http_get_json(DISCOVERY_URL)
    return _discovery_cache


# --------------------------------------------------------------------------- #
# 3. HMAC-signed token helpers (stdlib; same primitive for state + session)
# --------------------------------------------------------------------------- #
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(secret: bytes, payload: bytes) -> str:
    sig = hmac.new(secret, payload, hashlib.sha256).digest()
    return f"{_b64e(payload)}.{_b64e(sig)}"


def _unsign(secret: bytes, token: Optional[str]) -> Optional[bytes]:
    """Return the verified payload bytes, or None if absent/tampered."""
    if not token or "." not in token:
        return None
    try:
        body_b64, sig_b64 = token.split(".", 1)
        payload = _b64d(body_b64)
        expected = hmac.new(secret, payload, hashlib.sha256).digest()
        if hmac.compare_digest(expected, _b64d(sig_b64)):
            return payload
    except Exception:
        return None
    return None


def issue_session(cfg: AuthConfig, email: str) -> str:
    payload = json.dumps(
        {"email": email, "exp": int(time.time()) + SESSION_TTL_SECONDS},
        separators=(",", ":"),
    ).encode()
    return _sign(cfg.session_secret, payload)


def read_session(cfg: AuthConfig, cookie: Optional[str]) -> Optional[str]:
    """
    Verify a session cookie and return the admin email, or None. The allowlist
    is RE-CHECKED here on every request, so de-listing an email invalidates its
    live sessions immediately.
    """
    payload = _unsign(cfg.session_secret, cookie)
    if payload is None:
        return None
    try:
        data = json.loads(payload)
    except Exception:
        return None
    if int(data.get("exp", 0)) < int(time.time()):
        return None
    email = str(data.get("email", "")).lower()
    return email if email in cfg.admin_emails else None


# --------------------------------------------------------------------------- #
# 4. Google round-trip pieces
# --------------------------------------------------------------------------- #
def _exchange_code(cfg: AuthConfig, code: str) -> dict:
    """Server-to-server authorization-code -> token exchange (TLS to Google)."""
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "redirect_uri": cfg.redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(
        discovery()["token_endpoint"], data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _verify_id_token(cfg: AuthConfig, id_token: str, expected_nonce: str) -> dict:
    """
    Verify the Google id_token: RS256 signature against the JWKS, plus aud, iss,
    exp/iat, and the OIDC nonce. Returns the claims. Raises on ANY failure.
    PyJWT[crypto] is imported here so the dependency is needed only when a real
    verification runs.
    """
    import jwt  # PyJWT[crypto]
    from jwt import PyJWKClient

    disc = discovery()
    signing_key = PyJWKClient(disc["jwks_uri"]).get_signing_key_from_jwt(id_token)
    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=cfg.client_id,
        issuer=disc["issuer"],
        leeway=30,  # small clock-skew tolerance
        options={"require": ["exp", "iat", "aud", "iss"]},
    )
    if not hmac.compare_digest(str(claims.get("nonce", "")), str(expected_nonce)):
        raise ValueError("OIDC nonce mismatch (possible replay).")
    return claims


# --------------------------------------------------------------------------- #
# 5. FastAPI surface (router + dependency). Built lazily so importing this
#    module does not require FastAPI to be installed.
# --------------------------------------------------------------------------- #
def build_router(cfg: AuthConfig):
    from fastapi import APIRouter, HTTPException, Request
    from fastapi.responses import JSONResponse, RedirectResponse

    router = APIRouter()

    def _set_cookie(resp, name: str, value: str, max_age: int) -> None:
        # SameSite=Lax is REQUIRED on the state cookie so it survives the
        # top-level redirect back from Google. Secure is gated by env so local
        # http://127.0.0.1 testing still stores the cookie.
        resp.set_cookie(name, value, max_age=max_age, httponly=True,
                        secure=cfg.cookie_secure, samesite="lax", path="/")

    @router.get("/admin/auth/login")
    def login():
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        bundle = _sign(cfg.session_secret, json.dumps(
            {"state": state, "nonce": nonce, "exp": int(time.time()) + STATE_TTL_SECONDS},
            separators=(",", ":")).encode())
        params = urllib.parse.urlencode({
            "client_id": cfg.client_id,
            "redirect_uri": cfg.redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "state": state,
            "nonce": nonce,
            "access_type": "online",        # no refresh token: login only
            "prompt": "select_account",
        })
        resp = RedirectResponse(
            discovery()["authorization_endpoint"] + "?" + params, status_code=302)
        _set_cookie(resp, STATE_COOKIE, bundle, STATE_TTL_SECONDS)
        return resp

    @router.get("/admin/auth/callback")
    def callback(request: Request, code: str = "", state: str = "", error: str = ""):
        if error:
            raise HTTPException(400, f"Google returned an error: {error}")

        bundle_raw = _unsign(cfg.session_secret, request.cookies.get(STATE_COOKIE))
        if not bundle_raw:
            raise HTTPException(400, "Missing or invalid OAuth state cookie.")
        bundle = json.loads(bundle_raw)
        if int(bundle.get("exp", 0)) < int(time.time()):
            raise HTTPException(400, "OAuth state expired; restart sign-in.")
        if not state or not hmac.compare_digest(state, str(bundle.get("state", ""))):
            raise HTTPException(400, "OAuth state mismatch (CSRF guard).")
        if not code:
            raise HTTPException(400, "No authorization code returned.")

        tok = _exchange_code(cfg, code)
        id_token = tok.get("id_token")
        if not id_token:
            raise HTTPException(502, "Google token response carried no id_token.")
        claims = _verify_id_token(cfg, id_token, str(bundle.get("nonce", "")))

        email = str(claims.get("email", "")).lower()
        if not claims.get("email_verified", False):
            raise HTTPException(403, "Google account email is not verified.")
        if email not in cfg.admin_emails:
            # THE REAL BOUNDARY: authenticated by Google, but not authorized here.
            raise HTTPException(403, f"{email} is not an authorized admin.")

        resp = RedirectResponse("/", status_code=302)
        _set_cookie(resp, SESSION_COOKIE, issue_session(cfg, email), SESSION_TTL_SECONDS)
        resp.delete_cookie(STATE_COOKIE, path="/")
        return resp

    @router.post("/admin/logout")
    def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    @router.get("/admin/whoami")
    def whoami(request: Request):
        email = read_session(cfg, request.cookies.get(SESSION_COOKIE))
        if not email:
            raise HTTPException(401, "Not signed in.")
        return {"admin": True, "email": email}

    return router


def make_admin_required(cfg: AuthConfig):
    """Return a FastAPI dependency that 401s unless a valid admin session is present."""
    from fastapi import HTTPException, Request

    def admin_required(request: Request) -> str:
        email = read_session(cfg, request.cookies.get(SESSION_COOKIE))
        if not email:
            raise HTTPException(status_code=401, detail="Admin sign-in required.")
        return email

    return admin_required
