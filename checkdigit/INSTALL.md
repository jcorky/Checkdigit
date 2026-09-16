# Installing CHECKDIGIT

Three ways to run it, fastest first. Pick one. All assume you've unpacked the
release and are sitting in its directory:

```bash
tar -xzf checkdigit.tar.gz
cd checkdigit
```

The **correction engine has zero third-party dependencies** — the only Python
packages are FastAPI/uvicorn to serve it (plus `python-multipart` for uploads,
and `httpx` only if you turn on live enrichment). The web GUI is optional: the
API runs without it and exposes every function over HTTP.

---

## Path A — One command, no Docker (recommended for a homelab)

Needs **Python 3.10+**. Node 18+ is optional (only to build the GUI).

```bash
./install.sh
```

That's it. The script creates a virtualenv, installs the Python deps, builds the
web UI if Node is present, and starts the server:

```
http://127.0.0.1:8000/        ← the web app (if built)
http://127.0.0.1:8000/docs    ← interactive API docs (always)
```

Stop with **Ctrl-C**. Re-running `./install.sh` is safe and fast (it reuses the
venv and only rebuilds the UI if the source changed).

Useful variants:

```bash
HOST=0.0.0.0 PORT=8080 ./install.sh    # bind on the LAN / a different port
./install.sh --no-spa                  # API only, skip the web UI build
./install.sh --no-run                  # set up but don't start (run ./start-local.sh later)
./install.sh --verify                  # run the full test suite, then start
PYTHON=python3.12 ./install.sh         # pick a specific interpreter
```

If Node isn't installed you'll see a note and the API still starts — `/` returns
a JSON index instead of the GUI. Install Node 18+ and re-run to get the UI.

---

## Path B — Docker Compose (full stack: API + SFTP intake + backups)

Needs **Docker** and the **compose** plugin. This brings up four services: the
API, the SFTP watch-folder worker, an OpenSSH SFTP endpoint, and continuous
SQLite backup (Litestream).

```bash
# 1) (optional) build the web UI so the container serves it at "/"
./build-spa.sh           # needs Node 18+; produces ./static
#    then uncomment the "COPY static/ ./static/" line in the Dockerfile
#    (or bind-mount ./static into the app container)

# 2) IMPORTANT — change the SFTP password before first use
#    edit docker-compose.yml: replace CHANGE_ME_BEFORE_USE
#    (for anything real, switch to key auth — see DEPLOY.md)

# 3) launch
docker compose up -d --build

# 4) check it
curl http://127.0.0.1:8000/health
docker compose logs -f app
```

The app listens on `127.0.0.1:8000` and SFTP on `127.0.0.1:2222` by default
(local only). For a public URL, see **Path D**. Tear down with
`docker compose down` (add `-v` to also wipe the data/backups volumes).

> **Before exposing anything publicly**, read the security notes in `DEPLOY.md`
> and `DEPLOY_PUBLIC.md` — SFTP password→keys, CORS origin, proxy `forwarded-allow-ips`,
> and body-size limits all matter.

---

## Path C — Manual (full control, no scripts)

```bash
# 1) Python env + deps
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt

# 2) (optional) build the GUI
./build-spa.sh                     # or skip; API serves JSON at "/" without it

# 3) run
uvicorn api:app --host 127.0.0.1 --port 8000
#    behind a proxy, add:  --proxy-headers --forwarded-allow-ips=<proxy-ip>
```

Verify the engine itself with no network needed:

```bash
python3 test_equipment_checkdigit.py     # 22 kernel tests
python3 test_substitution.py             # 7 byte-splice tests
for p in 8 9 10 11 12 13 14 15 16 17 20 21 22 23 24 25; do python3 run_pass$p.py; done
python3 run_acceptance.py                # every requirement → a live assertion
```

---

## Path D — Public exposure on residential internet (AT&T / CGNAT)

The homelab target sits behind CGNAT with a dynamic IP, so **don't** port-forward.
Use a tunnel. The bundle ships the config for the Cloudflare route:

- `docker-compose.public.yml` — adds Caddy (automatic HTTPS) + a `cloudflared` tunnel
- `Caddyfile` — reverse-proxy + TLS
- `DEPLOY_PUBLIC.md` — the full runbook: creating the tunnel, the GoDaddy/Cloudflare
  DNS delegation, locking CORS to your real origin, and the AT&T-gateway tradeoffs

Tailscale is the simpler alternative if you only need *your* devices to reach it
(no public URL): install Tailscale on the host and hit it over the tailnet.

---

## Configuration (environment variables)

All optional; sensible defaults out of the box.

| Variable | Default | What it does |
|---|---|---|
| `CHECKDIGIT_DB` | `checkdigit.db` | SQLite file path (a volume in Docker) |
| `CHECKDIGIT_STATIC` | `static` | Directory the built SPA is served from at `/` |
| `CHECKDIGIT_OWNER_REGISTRY` | _(unset)_ | Path to a BIC owner-code CSV; enables owner corroboration. Ships with `owner_registry.seed.csv` as an illustrative starter — **replace with the real BIC register** for production |
| `CHECKDIGIT_POLICY_FILE` | _(unset)_ | Path to a JSON policy file; persists `PUT /policy` changes |
| `CHECKDIGIT_CORS_ORIGINS` | `*` | Comma-separated allowed origins. **Lock to your real origin** in production |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Bind address/port (install.sh / start-local.sh) |

Live carrier enrichment is **off by default** and each source is enabled by its
own env var (your free API key). See `.env.example` for the full list and
`/sources` on the running app for each source's access tier. Copy `.env.example`
to `.env` and the launch scripts will load it.

---

## After install — what to do next

- Open `http://<host>:<port>/` and try the **Correct** view. With no backend
  data yet, the sample chips (SNX XML, X12, Vessel BAPLIE) show the full result
  UI including the stowage diagram.
- Drop a real BAPLIE / X12 / COPINO file to see the stowage visualizers on your
  own data.
- Replace `owner_registry.seed.csv` with the real BIC register download.
- If you'll take partner drops over SFTP, switch the SFTP password to key auth
  (`DEPLOY.md`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Need Python 3.10+` | Install a newer `python3`, or point `PYTHON=` at one |
| `/` returns JSON, not the GUI | The SPA isn't built — run `./build-spa.sh` (needs Node 18+) |
| `python-multipart` error on upload | `pip install -r requirements.txt` (it's listed; the venv may be stale) |
| `database is locked` under load | Already mitigated (WAL + busy_timeout); if it persists you're likely on a network filesystem — SQLite must be on a **local** disk |
| SFTP login refused | You didn't change `CHANGE_ME_BEFORE_USE`, or uid 1001 can't write the volume — see `DEPLOY.md` |
| Docker: UI missing in container | Uncomment `COPY static/ ./static/` in the Dockerfile and rebuild, or bind-mount `./static` |

For deeper operational detail see `RUN_ON_LAPTOP.md` (laptop-as-server gotchas:
lid-switch, sleep), `DEPLOY.md` (compose, SFTP hardening, concurrency/multi-worker),
and `DEPLOY_PUBLIC.md` (the public-URL runbook).
