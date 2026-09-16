"""
cf_access.py  (OPTIONAL — defense-in-depth)
===========================================
Verify the Cloudflare Access JWT (the `Cf-Access-Jwt-Assertion` header) at the
origin, so the app itself rejects any request that did not pass through Access.

With Cloudflare Tunnel the origin is already unreachable except via the tunnel
(which Access gates), so this is belt-and-suspenders. Enable it only if you want
the extra guarantee, or if you ever expose the origin by another path.

Requires:   pip install "pyjwt[crypto]"
Configure (both required to enforce):
    CF_ACCESS_TEAM   your-team      # the <team> in https://<team>.cloudflareaccess.com
    CF_ACCESS_AUD    <AUD tag>      # Application Audience (AUD) tag of the Access app
Wire into the protected routes, e.g. in api.py:
    from cf_access import require_cf_access
    @app.post("/correct", dependencies=[Depends(require_cf_access)])

Cloudflare signs Access JWTs with RS256 and publishes the signing keys at
  https://<team>.cloudflareaccess.com/cdn-cgi/access/certs
Confirm the team domain and AUD in your Zero Trust dashboard.
"""
import os

from fastapi import Header, HTTPException

_TEAM = os.environ.get("CF_ACCESS_TEAM")
_AUD = os.environ.get("CF_ACCESS_AUD")
_verify = None


def _build_verifier():
    # Imported lazily so the app runs without PyJWT when this guard is unused.
    import jwt
    from jwt import PyJWKClient

    issuer = f"https://{_TEAM}.cloudflareaccess.com"
    jwks = PyJWKClient(f"{issuer}/cdn-cgi/access/certs")

    def verify(token: str):
        key = jwks.get_signing_key_from_jwt(token).key
        return jwt.decode(token, key, algorithms=["RS256"], audience=_AUD, issuer=issuer)

    return verify


def require_cf_access(cf_access_jwt_assertion: str = Header(default="")):
    """FastAPI dependency: 403 unless a valid Access assertion is present."""
    if not (_TEAM and _AUD):
        raise HTTPException(status_code=500,
                            detail="CF_ACCESS_TEAM / CF_ACCESS_AUD are not configured")
    if not cf_access_jwt_assertion:
        raise HTTPException(status_code=403, detail="missing Cloudflare Access assertion")
    global _verify
    if _verify is None:
        _verify = _build_verifier()
    try:
        _verify(cf_access_jwt_assertion)
    except Exception as exc:                       # invalid signature / aud / issuer / expiry
        raise HTTPException(status_code=403, detail=f"invalid Access assertion: {exc}")
