# CHECKDIGIT — public exposure (deploy pass)

## 0. Read this before you expose anything

This app ingests **container numbers and the surrounding booking/origin data** in
your EDI/SNX files and writes corrected copies. That corpus is the thing to think
about before putting it on the public internet.

**The unavoidable fact about Cloudflare Tunnel:** Cloudflare terminates TLS at its
edge. Your visitor's HTTPS is decrypted *by Cloudflare* so it can route and apply
Access; the hop from there to your house is re-encrypted over the tunnel, but
**Cloudflare (the company) sees your uploads and the corrected output in
plaintext.** For synthetic test data or a hobby instance, that's fine. For real
operational terminal data, it's a genuine consideration (third-party data
handling, jurisdiction, their ToS).

Two supported paths, chosen by **whether you need *public* access** and **how
sensitive the data is**:

| | Cloudflare Tunnel + Access | Tailscale |
|---|---|---|
| Reachability | Public URL, anyone you allow | Private — only *your* tailnet devices |
| Auth | Cloudflare Access (email/SSO) at the edge | Your tailnet identity (device-level) |
| Who can see plaintext | **Cloudflare can** (TLS terminates at edge) | **No third party** (WireGuard end-to-end; DERP relays carry only ciphertext) |
| Port forwarding / static IP | None needed (outbound only) | None needed (outbound only) |
| Setup | Domain on Cloudflare + dashboard | `tailscale up` on the host |

**Recommendation:** if the only consumer is you and a couple of devices — which is
the homelab norm — **use Tailscale** (§4). It's less moving parts and nothing
outside your control sees the files. Use **Cloudflare Tunnel + Access** (§2–3)
only when you genuinely need a public, shareable URL, and keep the data synthetic
or non-sensitive while it rides through Cloudflare.

Both options share the property that matters on AT&T residential: **outbound-only,
so you never touch port forwarding, the BGW gateway's NAT/passthrough quirks, your
dynamic IPv4, or CGNAT** (§5).

---

## 0.5 This deployment: public app + in-app admin sign-in

CHECKDIGIT now ships its **own** admin login (Google sign-in, allow-listed). For the
"users upload; only the admin sees history" use case, the Cloudflare-Access-in-front
model described in §1–3 is **not** used. Instead:

- The **upload/correct app is public** — anyone with the URL submits a file and gets
  it corrected. No edge login.
- The **dashboard, history, container registry, and policy** are gated by the in-app
  Google sign-in, enforced **server-side** (`admin_required`). The SPA also hides those
  tabs until you sign in; a hand-crafted `GET /insights` still returns 401.
- **Do not** add a blanket Cloudflare Access policy to this hostname — it forces every
  uploader to log in at the edge. And **do not** put Access on `/admin/*` or
  `/admin/auth/callback` — it intercepts the Google OAuth redirect and breaks sign-in.

So in §2–3, do the tunnel and public-hostname steps, but **skip "Protect it with
Access" and the optional Access-JWT origin check (§3.4)** — those describe the older
"whole app private behind Access" posture. Want the *entire* app private instead? Use
Tailscale (§4); it's simpler than Access for that. Google OAuth setup is in **§3A**.

---

## 0.6 Two hostnames: the site and the app

thecheckdigit.com is served as two public hostnames through the same tunnel, with
Caddy switching on the `Host` header (`Caddyfile.public-site`):

| Hostname | Serves | Caddy |
|---|---|---|
| `thecheckdigit.com` | the static landing page (`./site`) | `file_server` |
| `app.thecheckdigit.com` | the FastAPI app — SPA, API, admin | `reverse_proxy app:8000` |

To wire it: in the Zero Trust tunnel add **both** public hostnames, each pointing at
the same service `http://caddy:80`. `docker-compose.public.yml` already mounts
`./site` into Caddy and uses `Caddyfile.public-site`. Set
`PUBLIC_ORIGIN=https://app.thecheckdigit.com` in `.env` (the OAuth redirect URI is
derived from it — register `https://app.thecheckdigit.com/admin/auth/callback` in
Google). The landing page is static and needs no auth; the admin gate lives only on
the app host.

---

## 1. Topology (Cloudflare path)

```
visitor ──HTTPS──▶ Cloudflare edge ──▶ Tunnel   (no blanket Access — see §0.5)
                   (terminates TLS)      │
                                         ▼  (outbound from your house)
                              cloudflared ─▶ caddy:80
                                               ├─ thecheckdigit.com      ─▶ ./site   (static landing)
                                               └─ app.thecheckdigit.com  ─▶ app:8000 (FastAPI; SQLite+Litestream)
                                                  (5 MB cap, headers; admin gated in-app)
```
No host ports are published. The app trusts only Caddy's internal IP for
`X-Forwarded-*` (never `*`), and stores **no client IP** regardless.

---

## 2. Cloudflare setup (one time)

Prerequisites: a free Cloudflare account and **a domain managed by Cloudflare**
(any domain you own, moved to Cloudflare's nameservers). Cloudflare Zero Trust's
free tier (up to 50 users) is sufficient. Dashboard wording shifts over time;
these are the current sections.

1. **Create the tunnel.** Zero Trust dashboard → **Networks → Tunnels → Create a
   tunnel → Cloudflared**. Name it (e.g. `homelab`). On the install screen, copy
   the **token** from the `docker run … --token <TOKEN>` command — that token is
   the only secret you need; put it in `.env` as `CLOUDFLARED_TOKEN`.
2. **Add a public hostname** (still in the tunnel config): hostname =
   `checkdigit.<your-domain>`, service = **`http://caddy:80`**. (cloudflared
   resolves `caddy` because it shares the docker network.)
3. **Protect it with Access.** Zero Trust → **Access → Applications → Add an
   application → Self-hosted**. Application domain = the same
   `checkdigit.<your-domain>`. Add a policy: Action **Allow**, rule
   **Emails == `you@example.com`** (a short allow-list). For the login method,
   **One-time PIN** works with no IdP setup (Cloudflare emails a code). Note the
   application's **AUD tag** if you plan to enable origin JWT verification (§3.4).

---

## 3. Run it

### 3.1 Secrets
Create `.env` next to `docker-compose.public.yml` (do **not** commit it):
```bash
CLOUDFLARED_TOKEN=eyJ...        # from step 1
PUBLIC_ORIGIN=https://checkdigit.example.com
```

### 3.2 (Recommended) build the SPA in for same-origin serving
Build the React SPA (Vite/CRA) and drop its output in `./static/`, then uncomment
the `COPY static/ ./static/` line in the `Dockerfile`. The app serves the SPA at
`/`, so it's same-origin with the API — **no CORS, and the SPA's *Service URL*
field stays blank.** Browser calls to `/correct` then ride the Access session
cookie automatically (§3.3).

### 3.3 Up
```bash
docker compose -f docker-compose.public.yml up -d --build
```
- Visit `https://checkdigit.<your-domain>` → Cloudflare Access prompts for login →
  the SPA loads. Because it's same-origin behind Access, its `fetch` calls to
  `/correct` carry the Access cookie with no extra work.
- **Programmatic / curl access** (no browser, so no cookie) needs a **service
  token**: Zero Trust → **Access → Service Auth → Create Service Token**, then add
  an Access policy that allows that token. Call with its headers:
  ```bash
  curl -F file=@4Containers_snx_Example.xml \
       -H "CF-Access-Client-Id: <id>.access" \
       -H "CF-Access-Client-Secret: <secret>" \
       "https://checkdigit.example.com/correct?owner_policy=strict"
  ```

### 3.4 (Optional) verify the Access JWT at the origin
Belt-and-suspenders — the tunnel already makes the origin unreachable except via
Access. To make the app itself reject anything that didn't transit Access, install
`pyjwt[crypto]`, set `CF_ACCESS_TEAM` and `CF_ACCESS_AUD`, and add
`dependencies=[Depends(require_cf_access)]` to the protected routes. See
`cf_access.py`.

---

## 3A. Admin sign-in (Google) — for the public-app model

The dashboard/history/registry/policy are gated by an in-app Google sign-in. Set it up once:

1. **Google Cloud Console → APIs & Services → OAuth consent screen** → User type
   **External**; add scopes `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile`;
   add `jayde.cork@gmail.com` as a **test user**; leave in **Testing** (only that user can
   sign in, and these scopes need no Google verification).
2. **Credentials → Create credentials → OAuth client ID → Web application.** Authorized
   redirect URI (exact, byte-for-byte): `https://app.thecheckdigit.com/admin/auth/callback`. Also add
   `http://127.0.0.1:8000/admin/auth/callback` if you test locally. Copy the Client ID and secret.
3. Fill `.env` (see **TIER 2b** in `.env.example`): `CHECKDIGIT_ADMIN_AUTH=google`,
   `CHECKDIGIT_GOOGLE_CLIENT_ID` / `_SECRET`, `CHECKDIGIT_ADMIN_EMAILS=jayde.cork@gmail.com`,
   and a `CHECKDIGIT_SESSION_SECRET` from
   `python3 -c "import secrets;print(secrets.token_urlsafe(48))"`. The public compose derives
   `CHECKDIGIT_OAUTH_REDIRECT_URI` from `PUBLIC_ORIGIN` and sets `CHECKDIGIT_COOKIE_SECURE=1`
   and `CHECKDIGIT_DOCS=0` for you.

Notes:
- **Fail-loud:** with `CHECKDIGIT_ADMIN_AUTH=google` set, the app refuses to boot if any value
  is missing or the allow-list is empty — it never leaves the dashboard open.
- **The allow-list is the boundary.** Even if the OAuth app is published so any Google account
  can complete sign-in, the server rejects every email except the allow-list, server-side.
- **No Access on the callback.** If a Cloudflare Access policy covers `/admin/auth/callback`,
  Google's redirect hits an Access wall and sign-in fails. Keep this hostname Access-free.
- **Cookies:** `CHECKDIGIT_COOKIE_SECURE=1` in prod (HTTPS at the edge). For local `http`
  testing set `0`, or the Secure session cookie won't be stored.
- We request `openid email profile` with `access_type=online`, so **no Google refresh token**
  is stored — login only; the session is our own HMAC-signed cookie.

---

## 4. Alternative: Tailscale (recommended for sensitive/private use)

No public URL, no third party in the TLS path. Reachable only from devices on your
tailnet.

1. Install Tailscale on the homelab host; `sudo tailscale up`.
2. Run the **local** compose (the LAN one), which binds the app on the host:
   ```bash
   docker compose up -d --build      # docker-compose.yml, publishes 127.0.0.1:8000
   ```
   Reach it from any tailnet device at `http://<host>:8000` (MagicDNS) — but first
   make it listen where the tailnet can see it (next step).
3. **HTTPS on the tailnet, privately**, via Tailscale Serve (provisions a cert for
   your MagicDNS name; reachable only inside your tailnet):
   ```bash
   sudo tailscale serve --bg 8000        # serves your local :8000 over HTTPS on the tailnet
   sudo tailscale serve status
   ```
   Then browse `https://<host>.<your-tailnet>.ts.net`.
4. **Do not** use `tailscale funnel` for this app — Funnel exposes the service
   publicly, defeating the privacy reason for choosing Tailscale. Use **Serve**
   (private) only.

Traffic between your devices is WireGuard end-to-end encrypted; Tailscale's
coordination and DERP relay servers only ever see encrypted packets, so the
container data is never visible to a third party.

---

## 5. AT&T residential reality

- **CGNAT / dynamic IP / gateway:** irrelevant here. Both options are
  outbound-only, so there is **nothing to port-forward**, no IP-Passthrough/NAT
  juggling on the BGW gateway, and no breakage when your IPv4 rotates. This is the
  whole reason to prefer a tunnel over opening ports on a residential line.
- **Terms of service:** AT&T's residential terms restrict running public
  "servers." An outbound tunnel isn't an inbound server and is practically
  invisible to AT&T, but the ToS still nominally applies — your call. Tailscale
  (private, no public listener) is the cleaner posture on that axis too.

---

## 6. Security recap (what this configuration enforces)

- **No exposed origin.** No host ports in `docker-compose.public.yml`; the only
  ingress is the outbound tunnel. Finding your IP buys nothing.
- **Admin auth is in-app and server-side.** The dashboard/history/registry/policy
  require a Google sign-in on an allow-list (`admin_required`); the upload/correct
  app is intentionally public. A request without a valid admin session gets 401 on
  those routes regardless of how it reached the origin. (For a *fully* private app,
  Cloudflare Access or your tailnet can additionally gate everything — §0.5/§4.)
- **Swagger/OpenAPI off in prod** (`CHECKDIGIT_DOCS=0`) so the admin route shapes
  aren't advertised.
- **Body cap at Caddy (5 MB)** rejects oversize uploads with 413 before the app
  reads them — the real DoS guard. Keep it equal to `service.MAX_BYTES`.
- **No client IP stored**, ever (audit log keeps user-agent, filename, type,
  format, size, timestamp, counts).
- **XML is XXE-hardened** (DOCTYPE/ENTITY rejected on the SNX path).
- **Secrets via `.env`**, never committed; pin `cloudflared` and `litestream` to
  release tags rather than `latest` for reproducibility.
- **Cloudflare sees plaintext** (§0). If that's not acceptable for your data, you
  are on the Tailscale path.
