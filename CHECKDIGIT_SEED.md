# CHECKDIGIT — Project Seed

Single-file knowledge base for **CHECKDIGIT** (thecheckdigit.com). Drop this into the
repo root. A fresh engineer — or a fresh AI session — reading only this file plus the
source tree has everything needed to continue the build correctly: what it is, the
exact algorithms, the architecture, the schema, the API, the auth design, the deploy
model, the tests, the operating principles, and the open items.

Companion artifact: `checkdigit-full.tar.gz` (the complete verified source tree, 105
files; the `__pycache__` bytecode cache is intentionally excluded from the archive).
Baseline any new session by running `python3 run_acceptance.py` — green means the tree
matches this document.

---

## 1. What CHECKDIGIT is

A self-hosted web app that ingests a shipping/terminal data file, finds every
equipment identifier in it, recomputes each check digit with the algorithm that
identifier's **own standard** defines, and splices the corrected digit back **in
place** — never re-serializing the file — then shows a before/after diff, logs the
run, and (opt-in) enriches the result.

- **Public product domain:** `thecheckdigit.com` (static marketing/docs site) and
  `app.thecheckdigit.com` (the app: SPA + API + admin).
- `jayde.io` is the owner's **separate personal portfolio** — never reference it in
  this project's configs.
- **Operator/admin:** `jayde.cork@gmail.com` (Google sign-in allow-list).
- **Why it exists:** wrong check digits in inbound EDI (keying errors, gate-OCR
  misreads, legacy handoffs) get messages and boxes rejected downstream (TOS, AMS,
  carrier systems), turning into manual exception handling. The fix is mechanical;
  the danger is tooling that re-serializes files or applies the wrong algorithm.
  CHECKDIGIT does neither.

## 2. Non-negotiable principles

1. **Byte-splice, never re-serialize.** `substitution.apply_edits` is the ONLY code
   that mutates file content; every edit is offset-verified (`SubstitutionError` on
   mismatch). Output differs from input only at corrected check-digit characters.
2. **Route per standard, never cross-apply.** ISO 6346 mod-11 is never applied to a
   UIC number and vice versa. Ambiguous type ⇒ FLAG, don't guess.
3. **Context gates correction.** A failing digit is CORRECTED only in a field
   *declared* to carry an equipment ID (EDIFACT `EQD+CN`, X12 `N7`, SNX equipment
   attributes, recognized columns) or under explicit trust. Free-text hits ⇒ FLAGGED.
4. **Fail loud.** Missing config/deps/paths abort with explicit messages. The
   failure mode of misconfiguration is CLOSED/STOPPED, never silently degraded or
   silently open.
5. **No client IP is ever stored.** The audit schema has no IP column, by design.
6. **Enrichment is opt-in and dark by default.** No external call until a source is
   explicitly configured; every source is access-tier classified.
7. **Stdlib core.** The correction core (kernel, locators/correctors, dispatcher,
   db, service, substitution) uses only the Python standard library. Third-party
   deps are quarantined to the feature that needs them (`httpx` → enrichment,
   `PyJWT[crypto]` → admin auth) and lazy-imported.
8. **Verify, don't assume.** Read actual artifacts before writing; never invent an
   algorithm, endpoint, segment position, library, or URL; mark unknowns OPEN.

## 3. The algorithms — complete

### 3.1 ISO 6346 (maritime containers) — mod-11

Number anatomy: `OWNER(3 letters) + CATEGORY(1 letter: U/J/Z) + SERIAL(6 digits) +
CHECK(1 digit)` = 11 characters.

**Character values.** Digits `0–9` are themselves. Letters are numbered from A=10
upward, **skipping every multiple of 11** (11, 22, 33) so no value collides with
the modulus:

```
A=10  B=12  C=13  D=14  E=15  F=16  G=17  H=18  I=19  J=20  K=21
L=23  M=24  N=25  O=26  P=27  Q=28  R=29  S=30  T=31  U=32
V=34  W=35  X=36  Y=37  Z=38          (skipped: 11, 22, 33)
```

**Weights.** Position i (1st…10th) is worth 2^(i−1): `1 2 4 8 16 32 64 128 256 512`.

**Check digit** = (Σ value×weight over the first 10 chars) mod 11, with the special
case: **remainder 10 → check digit 0**. Because 0 then represents two remainders,
the standard's guidance is to avoid issuing serials whose remainder is 10; such
numbers still validate as 0 and the kernel marks the remainder-ten case explicitly.

**Worked example — `CSQU3054383`:**
```
C=13×1=13  S=30×2=60  Q=28×4=112  U=32×8=256  3×16=48
0×32=0    5×64=320   4×128=512   3×256=768   8×512=4096
sum = 6185       6185 mod 11 = 3  (6185 = 562×11 + 3)   → check digit 3  ✓
```
Remainder-ten example: `APLU100000` → remainder 10 → check digit **0**
(`APLU1000000` is valid).

**Error detection, honestly:** almost every single-character error and most adjacent
transpositions change the sum mod 11. Known blind spot: two characters whose
*values* differ by a multiple of 11 contribute identically — e.g. digit `1`
(value 1) ↔ letter `B` (value 12) — so that substitution passes.

### 3.2 ILU / EN 13044 (intermodal loading units)

Same arithmetic as ISO 6346, with its **own category letters (A/B/D/E/K)**.
**⚠ OPEN:** the algorithm-equivalence assumption has NOT been confirmed against the
normative EN 13044-1 text (paywalled). The kernel flags this in its docstring;
close it against the real standard before removing the caveat.

### 3.3 UIC wagon numbers

12 digits: 11-digit body + 1 check via **Luhn mod-10** (double alternate digits
from the right, sum digits of products, check = (10 − sum mod 10) mod 10).
Kernel-verified example: body `21812471217` → check **3**.

### 3.4 Size/type codes (`22G1`, `45R1`, …)

Decoded for context/visualization only. **Never a correction target.**

## 4. Architecture — four layers, never entangled

```
transport   per-format locators + correctors (find IDs at byte offsets; write back)
   ↓
decision    equipment_checkdigit kernel — pure, no I/O, no policy side effects
   ↓
mutation    substitution.apply_edits — the ONLY writer; verified byte splice
   ↓
persistence db.py (SQLite)      +      enrichment (opt-in, OFF the correction path)
```

**Key modules & entry points** (verified from source):
- `equipment_checkdigit.py` — `iso6346_value`, `iso6346_check_digit(body10)`,
  `uic_check_digit(body11)`, `normalize`, `correct_identifier(...)`;
  enums `IdentifierType`, `FieldContext`, `Status`; dataclass `CorrectionResult`.
- `substitution.py` — `Edit`, `apply_edits(text, edits)`, `changed_indices`,
  `SubstitutionError`.
- `dispatcher.py` — content-based `detect_format(text)` / `detect_bytes`,
  `correct(text, owner_policy, trust, ...)`, `correct_with_hint(format_hint=...)`.
- Format modules: `edifact_*`, `x12_*`, `snx_*` (namespace-agnostic structural
  detection; neutral ns `urn:tos:container-xml`), `csv_locator`, `fixedwidth_*`,
  `txt_*`, `xlsx_locator`/`xlsx_corrector` (rebuild preserves member bytes).
- `service.py` — `process_upload(conn, content, filename, ...)`: the write path;
  size cap `MAX_BYTES` (5 MB), audit logging, counters.
- `policy.py` — `Policy`, `plan_for_tokens`: strict/lenient owner policy plus a
  persistent deny→allow→per-prefix rule layer, threaded through one choke point.
- `db.py` — schema + `connect(path, create_schema=...)`, `recompute_counters()`.
- `api.py` — FastAPI surface (routes below), CORS, admin mount gate, docs toggle,
  static SPA mount at `/`.
- `auth.py` — Google OIDC admin auth (see §6).
- `enrichment.py` — `OwnerRegistry`, tier-classified `SourceInfo` adapters
  (BoxTech, Maersk, CMA CGM, DCSA events, Terminal49, …), all `wired=False` until
  configured; raw returns archived in `enrichment_fetches`.
- `nearmiss.py` — OSA-distance suggestions for invalid IDs.
- `watch_folder.py` — headless SFTP-inbox processor (env-tunable, see §10).
- `cf_access.py` — optional Cloudflare Access JWT guard (unused in the current
  public model).
- `checkdigit_app.jsx` — the single-file SPA (see §8).
- `site/` — the 3-page public website (see §8).

## 5. API surface (exact)

| Route | Method | Access |
|---|---|---|
| `/correct` | POST | **open — writes an `ingestion_events` row every run** |
| `/correct/batch` | POST | open (also logs) |
| `/visualize` | POST | open (operates only on the submitted file) |
| `/check/{token}` | GET | open (stateless calculator) |
| `/health` | GET | open |
| `/` | GET | open (SPA when `CHECKDIGIT_STATIC` set; JSON stub otherwise) |
| `/insights` | GET | **admin** |
| `/events` · `/events/{id}/containers.csv` | GET | admin |
| `/containers` · `/containers/{eqid}` | GET | admin |
| `/policy` | GET + PUT | admin |
| `/sources` · `/portwatch/{iso3}` | GET | admin |
| `/admin/auth/login` → Google · `/admin/auth/callback` · `/admin/logout` (POST) · `/admin/whoami` | — | mounted only when auth enabled |

Admin routes carry `dependencies=[Depends(admin_required)]`. Swagger/ReDoc/OpenAPI
are env-gated (`CHECKDIGIT_DOCS`, default on; **0 in prod**).

## 6. Admin auth (Google OIDC) — design

- Authorization Code flow; endpoints read from Google's **discovery document**
  (`accounts.google.com/.well-known/openid-configuration`) — never hardcoded.
- Callback validation chain, each step fail-loud: signed **state** cookie (CSRF,
  SameSite=**Lax** so it survives the redirect) → server-to-server code exchange →
  **RS256 id_token verification** against Google's JWKS (`PyJWT[crypto]`,
  `PyJWKClient`) → `aud`/`iss`/`exp` → **nonce** → `email_verified` →
  **allow-list**. Only then is a session minted.
- Session: HMAC-signed stateless cookie (`cd_admin`, 12 h TTL). The **allow-list is
  re-checked on every request**, so de-listing kills live sessions instantly.
- Scopes `openid email profile`, `access_type=online` — **no Google refresh token
  is ever requested or stored**; the only credential is our own cookie.
- Mount gate: `CHECKDIGIT_ADMIN_AUTH=google` mounts auth; unset ⇒ admin routes deny
  (401) and no sign-in exists. Enabled-but-misconfigured (missing var, **empty
  allow-list**) ⇒ **refuses to boot**. The missing-config outcome is always CLOSED.
- **The allow-list is the boundary** — a validly-signed cookie for an unlisted
  email is still 401; hiding SPA tabs is cosmetic.
- One-time Google Cloud Console setup: OAuth consent screen → External → scopes
  `openid`, `…userinfo.email`, `…userinfo.profile` → add `jayde.cork@gmail.com`
  as **test user**, stay in **Testing** (no verification needed). Credentials →
  OAuth client ID → **Web application** → Authorized redirect URIs (byte-exact):
  `https://app.thecheckdigit.com/admin/auth/callback` and
  `http://127.0.0.1:8000/admin/auth/callback` for local.
- **Footguns:** never put a blanket Cloudflare Access policy on the app hostname
  (blocks uploaders) nor on `/admin/*` / the callback (breaks the OAuth redirect).
  `CHECKDIGIT_COOKIE_SECURE=1` only behind HTTPS — set 0 for local http or the
  cookie won't store. Redirect URI must byte-match the registration.

## 7. Database schema (verbatim from `db.py`)

```sql
CREATE TABLE IF NOT EXISTS containers (
    eqid            TEXT PRIMARY KEY,     -- canonical (corrected) container number
    owner           TEXT,                 -- 3-letter owner prefix; joins owners(prefix)
    category        TEXT,
    id_type         TEXT,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    times_seen      INTEGER NOT NULL DEFAULT 0,  -- occurrence-weighted
    times_corrected INTEGER NOT NULL DEFAULT 0,  -- event-weighted (+1 per file fixed)
    times_flagged   INTEGER NOT NULL DEFAULT 0   -- event-weighted
);
CREATE TABLE IF NOT EXISTS ingestion_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT NOT NULL,
    filename         TEXT,
    content_type     TEXT,
    detected_format  TEXT,
    file_size        INTEGER,
    user_agent       TEXT,                -- stored; client IP intentionally NOT stored
    owner_policy     TEXT,
    status           TEXT NOT NULL,       -- processed | rejected
    reason           TEXT,
    total_containers INTEGER,
    corrected        INTEGER,
    flagged          INTEGER,
    valid            INTEGER,
    invalid          INTEGER,
    empty_id         INTEGER
);
CREATE TABLE IF NOT EXISTS event_containers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id       INTEGER NOT NULL REFERENCES ingestion_events(id),
    eqid           TEXT NOT NULL REFERENCES containers(eqid),
    as_found       TEXT,
    role           TEXT,                  -- valid | corrected | flagged | invalid_structure
    printed_check  TEXT,
    computed_check TEXT,
    occurrences    INTEGER
);
CREATE INDEX IF NOT EXISTS ix_evc_eqid  ON event_containers(eqid);
CREATE INDEX IF NOT EXISTS ix_evc_event ON event_containers(event_id);
CREATE INDEX IF NOT EXISTS ix_evt_ts    ON ingestion_events(ts);
CREATE TABLE IF NOT EXISTS container_enrichment (
    eqid TEXT PRIMARY KEY REFERENCES containers(eqid),
    owner_name TEXT, owner_city TEXT, owner_country TEXT,
    source TEXT, details TEXT, enriched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS owners (
    prefix TEXT PRIMARY KEY, name TEXT, city TEXT, country TEXT,
    source TEXT, first_seen TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources   (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS vessels   (id INTEGER PRIMARY KEY AUTOINCREMENT, imo TEXT, name TEXT, vessel_key TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS locations (unlocode TEXT PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS equipment_types (iso_code TEXT PRIMARY KEY, description TEXT);
CREATE TABLE IF NOT EXISTS enrichment_fetches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    eqid TEXT NOT NULL REFERENCES containers(eqid),
    source_id INTEGER NOT NULL REFERENCES sources(id),
    fetched_at TEXT NOT NULL,
    payload TEXT NOT NULL                 -- full raw JSON of the return
);
CREATE INDEX IF NOT EXISTS ix_fetch_eqid ON enrichment_fetches(eqid);
CREATE INDEX IF NOT EXISTS ix_fetch_src  ON enrichment_fetches(source_id, fetched_at);
```
Design notes: the three `containers` counters are documented denormalizations —
`event_containers` is the auditable truth and `recompute_counters()` proves they
agree. `containers.owner → owners.prefix` is a soft join enforced by the write path.

## 8. Frontends

**SPA (`checkdigit_app.jsx`, esbuild/Vite → `static/`, served at `/`):**
- Views: Correct (public) · Dashboard/History/Containers (admin) · Docs.
- Four-state auth machine `admin ∈ {null(checking), "offline"(no backend),
  false(signed-out), {email}}` from a mount probe of `/admin/whoami`
  (network error ⇒ offline sample-mode preserved; 401 ⇒ sign-in offered).
- `signIn()` is a **top-level navigation** to `/admin/auth/login` (OAuth needs
  full-page redirects, not fetch). `apiGet` intercepts 401/403 ⇒ drop to
  signed-out + bounce to Correct. Runs standalone on embedded fixtures.
- Its calculator's pure math sits in `/* CALC-PURE-BEGIN … END */` markers,
  verified against the Python kernel by the acceptance suite.

**Site (`site/`, static, Caddy-served at the apex; every page self-contained,
zero external requests, opens from `file://`):**
- `index.html` — landing: live segmented ISO 6346 validator (the signature),
  anatomy strip, 4-step pipeline, coverage, trust cards, info callout, links.
- `help.html` — quick start, verdict meanings (VALID/CORRECTED/FLAGGED/INVALID in
  the app's semantic colors), formats, public-vs-admin, privacy (honest CDN-edge
  note), self-hosting, FAQ.
- `check-digit.html` — the full calculation, interactive: letter table **generated
  from the same `T` the code computes with**, weights strip, live per-character
  breakdown (value×weight=product, sum, mod 11, remainder-10 rendered inline),
  the honest blind-spot callout.
- Design tokens (shared with the SPA): bg `#0E1418`, panel `#141C23`, raised
  `#1A242E`, line `#293743`, text `#E7EEF4`, muted `#90A0B0`, brand/corrected
  amber `#F3A93B`, valid `#52B98A`, flagged `#5B9DF0`, invalid `#E5604F`;
  monospace-forward type (container IDs are stenciled fixed-width).
- **CALC-PURE convention:** any page embedding the algorithm wraps the pure part
  in the markers; acceptance check A14 proves each block equals
  `equipment_checkdigit.iso6346_check_digit` on kernel-generated vectors.

## 9. Deployment (public model)

**Topology — two hostnames, one tunnel, no blanket Access:**
```
visitor ──HTTPS──▶ Cloudflare edge ──▶ Tunnel (outbound-only; CGNAT-proof)
                                        │
                          cloudflared ─▶ caddy:80  (Host-switched, Caddyfile.public-site)
                                          ├─ thecheckdigit.com      ─▶ ./site  (file_server, try_files clean URLs)
                                          └─ app.thecheckdigit.com  ─▶ app:8000 (5 MB cap; admin gated in-app)
```
- Compose (`docker-compose.public.yml`): services `app, caddy, cloudflared,
  litestream`. **No host ports published** — the only ingress is the tunnel.
  Caddy mounts `Caddyfile.public-site` + `./site`. App env sets the full admin-auth
  block, `CHECKDIGIT_COOKIE_SECURE=1`, `CHECKDIGIT_DOCS=0`, and derives
  `CHECKDIGIT_OAUTH_REDIRECT_URI` from `${PUBLIC_ORIGIN}`.
- Local/full compose (`docker-compose.yml`): `app, worker, sftp, litestream`
  (SFTP inbox + watch-folder worker).
- Litestream continuously replicates the SQLite DB.
- Zero-Trust setup: add BOTH public hostnames to the tunnel → `http://caddy:80`.
- Cloudflare terminates TLS at its edge (it can see uploads in plaintext) — for
  sensitive/operational data use the **Tailscale** alternative in
  `DEPLOY_PUBLIC.md` §4 instead of a public tunnel.
- Context constraint: AT&T residential / CGNAT — outbound tunnels only; never rely
  on port forwarding.

**Environment variables (complete):**

| Var | Purpose |
|---|---|
| `CHECKDIGIT_DB` | SQLite path |
| `CHECKDIGIT_STATIC` | built-SPA dir served at `/` |
| `CHECKDIGIT_CORS_ORIGINS` | CORS allow-list (same-origin model; keep credential-less) |
| `CHECKDIGIT_OWNER_REGISTRY` | BIC register CSV (fail-loud if set-but-missing) |
| `CHECKDIGIT_POLICY_FILE` | persisted policy JSON |
| `CHECKDIGIT_DOCS` | `0` disables Swagger/ReDoc/OpenAPI (prod) |
| `CHECKDIGIT_ADMIN_AUTH` | `google` mounts admin auth (unset ⇒ admin closed) |
| `CHECKDIGIT_GOOGLE_CLIENT_ID` / `_SECRET` | OAuth client (Web application) |
| `CHECKDIGIT_ADMIN_EMAILS` | comma allow-list (empty ⇒ refuse to boot) |
| `CHECKDIGIT_SESSION_SECRET` | ≥48 random bytes (`secrets.token_urlsafe(48)`) |
| `CHECKDIGIT_OAUTH_REDIRECT_URI` | byte-match Google registration (compose derives it) |
| `CHECKDIGIT_COOKIE_SECURE` | `1` behind HTTPS; `0` for local http |
| `PUBLIC_ORIGIN` | `https://app.thecheckdigit.com` |
| `CLOUDFLARED_TOKEN` | tunnel token (**rotate any token that ever appeared in a screenshot/session**) |
| `CHECKDIGIT_WATCH_ROOT` / `_TRUST` / `_OWNER_POLICY` / `_INTERVAL` / `_STABLE_SCANS` / `_MIN_AGE` | watch-folder inbox tuning |

## 10. Tooling & scripts

| Script | Purpose / flags |
|---|---|
| `bootstrap.sh` | fresh Debian/Ubuntu/Mint → ready in one command. Installs `python3 python3-venv python3-pip ca-certificates curl` (+`nodejs npm`), verifies the venv module imports, optional `--docker` (official get.docker.com), scaffolds `.env`, hands off to `install.sh --no-run`. Flags: `--run --verify --docker --no-node`. Does NOT fetch source. |
| `install.sh` | venv + deps + SPA build (+ run). Flags: `--no-spa --no-run --verify`. Hardened venv guard: checks `.venv/bin/activate`, retries `--copies` on symlink-restricted mounts, fails loud with the `python3-venv` hint. |
| `start-local.sh` | lean run path (same hardened guard). |
| `build-spa.sh` | esbuild the JSX → `static/`. |
| `smoke-test.sh` | verifies a **running** deployment over HTTP: open surface answers (`/health`, `/check`, `POST /correct → event_id`), admin routes 401, prod: `/admin/whoami` 401 + login **302s to accounts.google.com** + `/docs`/`/openapi.json` 404; optional `SITE=` landing check. `--dev` for a local box. Exit 0 only if all pass. |

## 11. Testing & verification map

`python3 run_acceptance.py` — the network-less, FastAPI-optional sweep. Checks:
`R1-R3/EDIFACT`, `R1/X12`, `R1-R5/SNX`, `R1-R4/xlsx`, `R1/txt`, `R2` (routing:
ISO vs UIC, cross-application forbidden), `R6-R8`, `SCOPE`, `A1`…`A8` (A8: all 14
API routes present in source; **no IP read anywhere**), `A4/A9` (SPA markers incl.
sign-in), `A10` (deploy artifacts + admin-auth env on the public compose, DOCS off,
COOKIE secure, redirect derived), `A11` (XXE rejected; 5 MB cap audited), `A12`
(every module imports cleanly; api/cf_access AST-parsed), `A13/auth` + `A13/http`
(the access boundary; HTTP half self-skips without FastAPI), `A14` (all `site/*.html`
self-contained, cross-linked, zero external deps; every CALC-PURE block ==
kernel on 10 ISO 6346 vectors), and `A15` (harness portability: the sweep writes
scratch only under `tempfile.gettempdir()`, never a hardcoded `/tmp`, and `site/*.html`
globs are separator-normalized — so `run_acceptance.py` is green on Windows as well as
POSIX; a self-scan across the harness files locks it against regression). `A2` proves
the SPA calculator == kernel via
node vectors (note: those vectors are deliberately messy — lowercase/spaces —
and multi-scheme, including UIC).

`run_pass26.py` — standalone access-boundary pass (mints its own session; never
calls Google): 9 read routes 401 without a session, 200 with, **valid-signature
unlisted-email cookie still 401**, `/correct` logs exactly one event without auth.
Import-safe (env setup in `_setup_env()` at run time). Requires FastAPI+httpx.

`install.sh --verify` runs unit tests + passes 8–26 + the sweep. After deploy:
`./smoke-test.sh https://app.thecheckdigit.com` with
`SITE=https://thecheckdigit.com`.

## 12. Security posture (summary)

Public write path (logged), admin read path behind Google allow-list enforced
server-side; Swagger off in prod; XXE-hardened XML parsing; 5 MB body cap (Caddy
+ app, rejections audited); trusted-proxy-only forwarded headers
(`--forwarded-allow-ips` = Caddy's IP); no published host ports; stdlib core;
no client IP; enrichment dark; secrets only via env/`.env` (never committed).

## 13. Operational lessons (earned, keep them)

- Venv guard must check `.venv/bin/activate`, not the directory; a half-built venv
  cascades silently otherwise. `--copies` self-heals symlink-restricted mounts.
- `apt --fix-missing` silently skips packages — always `apt update` first.
- Cloudflare Tunnel 404 traps: wrong Service scheme (`https` vs `http`), connector
  not reloading routes added post-startup, missing CNAME.
- Blanket Cloudflare Access on the hostname blocks uploaders AND breaks the OAuth
  callback — the in-app 401 is the boundary.
- ISO 6346 owner prefix ≠ shipment operator (leasing prefixes ride on carrier
  moves). bic-code.org is the authoritative free owner source; aggregators aren't.
- N4 internal line-operator values are instance-specific — never portable.
- **Volatile workspaces lose; artifacts win.** A sandbox/live-USB reset wiped a
  working tree mid-build; full recovery came from the presented output copies +
  the original tar. Keep `checkdigit-full.tar.gz` current; re-tar after changes.

## 14. Current state

**Done and verified (offline):** kernel + routing + all format parsers; verified
byte-splice; policy engine; near-miss; SQLite warehouse + dossier + insights;
SPA incl. auth machine; Google admin auth (B–F passes); public two-hostname deploy
config; 3-page site with pinned widgets; bootstrap/install/start-local hardening;
smoke-test (mock-validated in prod/dev/negative modes); full acceptance sweep green.

**First live run — done locally (2026-08-28):** uvicorn serves the API; the engine
corrected/flagged real SNX/BAPLIE/X12/text uploads with the byte-splice intact (12 of
11,626 bytes changed on the SNX file, length preserved); the Google-gated admin
dashboard returns data only to an allow-listed session and 401s otherwise, and
`/admin/auth/login` 302s to Google with a well-formed authorize URL. This run surfaced
and fixed a real bug: under `from __future__ import annotations`, the `request: Request`
annotation on auth.py's dependencies was an unresolvable string (`Request` was imported
only locally), so every admin route returned **422 instead of 401** — and a validly
signed-in admin could never enter. Fixed with a guarded module-level
`from fastapi import Request`; `run_pass26` and A13/http now execute live, green.

**⚠ OPEN — remaining:**
1. **Public internet deploy** — the local live run above is done; what remains is public
   exposure: Docker + a real Google OAuth client + a Cloudflare tunnel on the two
   hostnames. Sequence: `./bootstrap.sh --verify` → Google Console (§6) → `.env` (rotate
   tunnel token) → `./build-spa.sh` (needs Node) → `docker compose -f
   docker-compose.public.yml up -d --build` → two tunnel hostnames →
   `SITE=https://thecheckdigit.com ./smoke-test.sh https://app.thecheckdigit.com` → sign
   in as the admin. Stop and flag security/legal before exposing anything publicly.
2. **EN 13044/ILU normative confirmation** (§3.2).

**Parked scope / known ceilings (flagged, deliberate):** replace
`owner_registry.seed.csv` (observational) with the real BIC register before
trusting attribution; BAPLIE deep checks (VGM/reefer setpoint/DG) await real
samples; rail/truck well-tier + front/rear geometry is inferred (drawn dashed);
near-miss suggestions recomputed per run (not persisted); rebuilt XLSX preserves
member bytes, not ZIP timestamps; SQLite is single-host (Postgres if scaling).

## 15. Working agreement (how to build on this)

Incremental passes with explicit confirmation; "leave no code behind" = complete
artifacts, not sketches. Read the actual files before editing — never edit from an
assumed structure. Every change lands with verification (extend the acceptance
sweep or a `run_passN`; the sweep must stay green in a network-less sandbox).
State assumptions in one line; classify any external data source by access tier
before implying access; distinguish verified fact from recommendation; skimmable
output — tables and fenced code where earned. Keep transport/decision/mutation/
persistence/enrichment separate. Before touching real operational data or
exposing anything publicly, stop and flag security/legal implications.

## 16. Session-critical file manifest

Beyond the original baseline, these files were added/changed and are canonical:
`auth.py`*, `api.py`, `checkdigit_app.jsx`, `run_pass26.py`*, `run_acceptance.py`,
`requirements.txt`, `install.sh`, `start-local.sh`, `bootstrap.sh`*,
`smoke-test.sh`*, `docker-compose.public.yml`, `.env.example`,
`DEPLOY_PUBLIC.md`, `Caddyfile.public-site`*, `site/index.html`*,
`site/help.html`*, `site/check-digit.html`*, and this file. (* = new.)

— End of seed. Baseline check: `python3 run_acceptance.py` → `ACCEPTANCE PASS`.
