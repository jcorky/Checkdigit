# CHECKDIGIT — running the service (pass 8b)

The HTTP layer (`api.py`) is a thin FastAPI wrapper over the already-tested
service core. Everything under it (`equipment_checkdigit`, the four
`*_locator`/`*_corrector` modules, `dispatcher`, `db`, `service`,
`correction_report`, `substitution`) is pure standard library and has unit/round-
trip tests; only the HTTP glue is new.

## Files
- `api.py` — FastAPI routes
- `requirements.txt` — fastapi, uvicorn, **python-multipart** (needed for uploads)
- `Dockerfile`, `docker-compose.yml`, `litestream.yml`
- service core: `service.py`, `dispatcher.py`, `db.py`, `correction_report.py`,
  `substitution.py`, `equipment_checkdigit.py`, `*_locator.py`, `*_corrector.py`

## Endpoints
| Method | Path | Notes |
|---|---|---|
| POST | `/correct?owner_policy=strict\|lenient&trust=false` | multipart `file`; returns `report.to_dict()` |
| GET | `/events?limit=&offset=` | ingestion / audit log (newest first) |
| GET | `/containers?owner=&limit=&offset=` | container registry |
| GET | `/containers/{eqid}` | one container + event history |
| GET | `/health` | liveness |
| — | `/docs` | auto OpenAPI UI |

`POST /correct` returns:
```json
{ "status": "processed", "detected_format": "snx",
  "filename": "...", "event_id": 12, "report": { ...CorrectionReport... } }
```
Rejections return HTTP `413` (too large) or `415` (unsupported format) with a
`detail` message, and are still recorded in the audit log.

## Run locally (no Docker)
```bash
pip install -r requirements.txt
uvicorn api:app --reload --port 8000           # http://127.0.0.1:8000/docs
# quick check:
curl -F file=@4Containers_snx_Example.xml "http://127.0.0.1:8000/correct?owner_policy=strict"
```
The DB defaults to `./checkdigit.db` (override with `CHECKDIGIT_DB`).

## Run with Docker (app + Litestream backup)
```bash
# PROXY_IP is the address uvicorn trusts for X-Forwarded-* (your proxy). Never "*".
PROXY_IP=127.0.0.1 docker compose up --build
```
- `data` volume holds the SQLite DB; the `litestream` sidecar replicates its WAL
  to the `backups` volume (put that on a separate disk, or switch to an S3/B2
  replica in `litestream.yml` for offsite durability).
- Restore after loss: `litestream restore -config /etc/litestream.yml /data/checkdigit.db`.

## Wiring the SPA
Two options:
1. **Same origin (simplest):** build the SPA (e.g. Vite) and copy its `dist/`
   into `./static/`. FastAPI serves it at `/`, so the SPA's *Service URL* field
   can stay blank and no CORS is needed.
2. **Separate dev server:** run the SPA on its own port and set the SPA's
   *Service URL* to the API origin; set `CHECKDIGIT_CORS_ORIGINS` to that origin.

## Security posture (recap)
- Uploads: size-capped in the service (audited on reject). **Also set a body-size
  limit at the proxy** (Caddy `request_body { max_size 5MB }`) — that is the real
  DoS guard, since the app reads the body before checking.
- XML is parsed only through the XXE-hardened SNX path (DOCTYPE/ENTITY rejected).
- The audit log stores user-agent, filename, type, format, size, timestamp, and
  counts — **never the client IP**.
- No application auth yet. Do **not** expose this directly; the public-exposure
  pass adds Cloudflare Tunnel + Cloudflare Access (and re-flags that Cloudflare
  terminates TLS at its edge). Until then, keep it on the LAN / behind Tailscale.

## SFTP watch-folder (headless ingestion)

EDI moves over SFTP, not browsers. The pattern here is **delegation**: the
`sftp` service (`atmoz/sftp`, a thin wrapper over OpenSSH — pin its tag) owns
the transport and chroots the partner user into a shared volume; the `worker`
service watches that volume's `inbox/` and runs each completed file through the
identical pipeline as a web upload (detect → correct → audit → corroborate →
near-miss). No SFTP code was written, on purpose.

Flow: partner drops `loadlist.xlsx` into `inbox/` → worker waits until the size
is stable across polls **and** the file is a few seconds old (half-written
uploads are the classic corruption source; `.part`/`.filepart` temp names are
ignored outright) → `outbox/loadlist.corrected.xlsx` + `outbox/loadlist.report.csv`
appear for the partner to fetch back → the original archives to `processed/`
timestamped. Rejects and crashes quarantine to `failed/` with a `.reason.txt`;
the loop never dies on one bad file. Everything lands in the same audit DB
(user-agent `checkdigit-watch-folder/1`), so drops show up in the History view
alongside uploads. Corrected text is re-encoded with the codec it was decoded
with — a latin-1 EDIFACT comes back byte-identical except the spliced digits.

Hardening before any real partner touches it:
- **Replace the password** in the compose `command` with key auth: mount
  `./keys/partner.pub:/home/edi/.ssh/keys/partner.pub:ro` and drop the password
  field (`edi::1001::inbox,...`). The password form is LAN testing only.
- **Exposure on AT&T residential:** default stays `127.0.0.1:2222` + Tailscale
  (partner joins your tailnet or you share the node). Cloudflare Tunnel does
  *not* transparently proxy raw SFTP — the partner side would need
  `cloudflared access tcp`, which most counterparties won't run. If a partner
  needs plain internet SFTP, a high-numbered port forward on the AT&T gateway
  to 2222 with key-only auth + fail2ban is the pragmatic exception to the
  tunnel-first rule — flagged as such.
- `--once` exists for cron-style runs; `CHECKDIGIT_WATCH_TRUST=1` only if drops
  are declared container lists. Remote-SFTP *polling* (pulling from a partner's
  server) is a different topology — `paramiko` is the named library if you ever
  need it; deliberately not built here because it's unverifiable in this
  environment.

---

## Concurrency & multiple simultaneous sessions

The service is built to take concurrent load — many users uploading at once,
plus the SFTP watcher, all sharing one SQLite database. What makes that safe:

**Already on, no action needed:**
- **WAL journal** — concurrent readers never block each other or the single
  writer. Set on every connection; also what Litestream needs.
- **`busy_timeout=5000`** — the key fix. A write that meets a momentarily-held
  lock now *waits up to 5s* for it instead of failing instantly with
  `database is locked` (SQLite's default is 0 = fail immediately). Verified: a
  blocked writer waits then succeeds rather than erroring.
- **`synchronous=NORMAL`** — the WAL-recommended durability level: safe across
  app/OS crashes, faster than FULL, narrower lock windows. (A hard power loss can
  lose the last few WAL frames, never the database.)
- **Per-request connection** — each HTTP request gets its own SQLite connection
  on its own threadpool thread (the correct sync-FastAPI pattern); connections are
  never shared between requests.
- **`db.execute_write(...)`** — a small retry wrapper around writes for the rare
  case where a WAL checkpoint outlasts the busy_timeout; real (non-lock) errors
  still propagate immediately (fail-loud).
- **Atomic policy swaps** — `PUT /policy` swaps the in-memory rule layer under a
  lock, so a concurrent `/correct` never reads a half-updated policy.
- **Race-safe schema init** — multiple worker processes opening a fresh DB at the
  same moment all converge (every DDL is `IF NOT EXISTS`; init retries on the WAL
  setup window). Verified with 12 concurrent fresh opens.

**Scaling the API process (optional):**
A single uvicorn worker already serves many simultaneous requests concurrently
via its threadpool — for a homelab that is usually enough, and it keeps all
writes funnelling through one process (the gentlest case for SQLite). If you need
more CPU parallelism:

```bash
# Multiple worker processes (each its own threadpool). All workers share the
# one WAL database safely thanks to busy_timeout + the retry wrapper.
uvicorn api:app --host 0.0.0.0 --port 8000 --workers 4 \
  --proxy-headers --forwarded-allow-ips=127.0.0.1
```

Guidance:
- **Start with 1 worker.** Add workers only if you see a CPU-bound bottleneck.
  Container check-digit correction is light; the DB is the shared resource, and
  more writer processes mean more lock contention, not linear throughput.
- **Rule of thumb:** workers ≈ CPU cores, but for this SQLite-backed workload
  2–4 is plenty even on a bigger box. The i5 laptop target runs comfortably on 1.
- **One machine.** SQLite is single-host. Do **not** put the DB on a network
  share (NFS/SMB) and point multiple machines at it — WAL locking is unreliable
  over network filesystems. Multi-host scale-out would mean swapping SQLite for
  Postgres, which is out of scope for the homelab design.
- **Body-size limit at the proxy** (`Caddy request_body max_size`) is still the
  real DoS guard under load — the in-app `MAX_BYTES` cap rejects + audits, but the
  proxy stops oversized bodies before they reach the app. See above.
- **Litestream** keeps replicating continuously under concurrent load; no change.

This was load-tested (`run_pass25.py`): 16 parallel uploads through the real
pipeline all audit correctly with consistent counters (no lost updates); 6 OS
processes × 120 writes each hit zero lock errors; concurrent readers + a policy
writer never see a torn read.
