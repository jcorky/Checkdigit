# Running CHECKDIGIT on an i5 laptop (homelab)

This is the one runbook for putting CHECKDIGIT on a single 4-core i5 laptop and
(optionally) serving it at your domain. The workload is tiny — a correction is
sub-second single-core string work, and 10 users sit far below 1 request/second,
so this hardware has ~50–100× headroom. The only laptop-specific work is making
the OS treat it as a server (don't sleep on lid close) and putting data somewhere
durable.

Assumed OS: **Ubuntu Server 24.04 (or Debian 12), 64-bit.** The full stack
(Caddy, atmoz/sftp, cloudflared, litestream) is Linux + Docker. On Windows/macOS
only the bare-Python path (Path A) works as written.

---

## Decide your path

- **Path A — bare Python.** Fastest. No Docker. Just the API on localhost. Best
  for "run it and use it on this machine / LAN." ~2 minutes.
- **Path B — Docker Compose (local).** Full stack: API + SFTP watch-folder worker
  + Litestream backup, auto-restart on boot. Still private (localhost/LAN).
- **Path C — Docker Compose (public).** Path B + Cloudflare Tunnel so
  `thecheckdigit.com` reaches it, with Cloudflare Access as the login. No port
  forwarding, works behind AT&T CGNAT.

You can start at A to sanity-check, then move to B/C — same code, same DB.

---

## 0. Lay the files down

```bash
sudo mkdir -p /opt/checkdigit && sudo chown "$USER" /opt/checkdigit
# unpack the delivered archive into it:
tar -xzf checkdigit.tar.gz -C /opt/checkdigit --strip-components=1
cd /opt/checkdigit
cp .env.example .env          # edit later as you add tiers
```

Put the **real BIC register** where `.env` points (`CHECKDIGIT_OWNER_REGISTRY`).
The shipped `owner_registry.seed.csv` is a 5-row sample, not the register.

---

## Path A — bare Python (no Docker)

```bash
./start-local.sh
# → http://127.0.0.1:8000   API docs at /docs
```

That script makes a venv, installs `requirements.txt`, loads `.env`, and runs
uvicorn. To verify the engine itself (no network needed):

```bash
source .venv/bin/activate
python run_acceptance.py      # every requirement, live
# 22/22, 7/7, passes 8–16 also runnable: for p in 8..16: python run_pass$p.py
```

Reach it from other machines on your LAN: run with `HOST=0.0.0.0 ./start-local.sh`
and browse `http://<laptop-LAN-ip>:8000`. (For the public internet, use Path C —
don't port-forward this directly.)

To keep it running after you close the terminal, use the systemd unit in §"Make
it a service" below (point it at uvicorn instead of compose).

---

## Path B — Docker Compose, local

```bash
# install Docker once
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER" && newgrp docker

# build the SPA into static/ (do this on any machine with Node, copy dist/* here)
#   npm create vite@latest  ->  drop in checkdigit_app.jsx  ->  npm run build
#   cp -r dist/* /opt/checkdigit/static/
# (optional — the API serves fine without the SPA; /docs still works)

docker compose up -d
docker compose logs -f app          # watch it boot
curl -s http://localhost:8000/health
```

`docker-compose.yml` runs four services (app, worker, sftp, litestream), all
multi-arch, all `restart: unless-stopped`. SQLite WAL is what lets app + worker
share one DB on the same host — never split them across machines.

**Before any real SFTP partner:** open `docker-compose.yml`, replace
`CHANGE_ME_BEFORE_USE` in the `sftp` service with key auth (mount an
`authorized_keys`, drop the password). The password form is LAN testing only. If
you don't need SFTP at all, comment out the `sftp` and `worker` services.

---

## Path C — Docker Compose, public (thecheckdigit.com)

One-time DNS + tunnel:

1. Cloudflare → add site `thecheckdigit.com` (Free) → copy the two nameservers.
2. GoDaddy → nameservers → paste them. (Registration stays at GoDaddy; verify MX
   if anything emails from the domain.)
3. Zero Trust → Networks → Tunnels → create (token mode) → copy token →
   `CLOUDFLARED_TOKEN=...` in `.env`.
4. Tunnel → Public hostname → `thecheckdigit.com` → service `http://caddy:80`
   (add `www` the same way).
5. Access → Applications → Self-hosted on `thecheckdigit.com` → policy = your
   email. **This is your login** — the app has no built-in auth.

Bring it up:

```bash
docker compose -f docker-compose.public.yml up -d
```

Browse `https://thecheckdigit.com` → Access login → SPA. TLS terminates at
Cloudflare's edge; nothing is forwarded on the AT&T gateway. (Caveat: Cloudflare
sees request plaintext. For maximally sensitive manifests, prefer Tailscale-only
with no public domain — Path B reachable over the tailnet.)

---

## Make it a server, not a laptop (do this for B/C, and A-as-service)

**1. Never suspend on lid close** — the #1 way laptop servers die:

```bash
sudo sed -i 's/^#\?HandleLidSwitch=.*/HandleLidSwitch=ignore/' /etc/systemd/logind.conf
sudo sed -i 's/^#\?HandleLidSwitchExternalPower=.*/HandleLidSwitchExternalPower=ignore/' /etc/systemd/logind.conf
sudo systemctl restart systemd-logind
```

**2. Disable system sleep entirely:**

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

**3. Prefer Ethernet.** On Wi-Fi, disable power-saving so it doesn't drop:
`sudo iw dev wlan0 set power_save off` (make it persist via NetworkManager).

**4. Docker on boot:** `sudo systemctl enable docker` — then `restart:
unless-stopped` brings the stack back after reboot automatically.

**5. The battery is a free UPS.** It rides through power blips that would drop a
desktop. Leave it plugged in; the battery covers the gaps.

**6. Auto-updates (optional but wise):**
`sudo apt install unattended-upgrades`.

### Bare-Python as a service (Path A without a terminal)

```ini
# /etc/systemd/system/checkdigit.service
[Unit]
Description=CHECKDIGIT
After=network-online.target
[Service]
WorkingDirectory=/opt/checkdigit
EnvironmentFile=/opt/checkdigit/.env
ExecStart=/opt/checkdigit/.venv/bin/uvicorn api:app --host 0.0.0.0 --port 8000
Restart=always
User=%i
[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now checkdigit
```

---

## Storage & backup

A laptop SSD is fine for the DB; just configure Litestream (you built it in) so a
disk failure isn't fatal — edit `litestream.yml` with an S3/B2 bucket + creds.
Your 5 TB HDDs make good local replica/archive targets too: a USB-attached HDD
mounted at `/mnt/backup`, pointed at by Litestream and the watch-folder
`processed/` archive.

---

## Configuration recap (what to actually fill in)

Open `.env` and add tiers as you need them — it's fully commented:

- **Always:** `CHECKDIGIT_DB`, `CHECKDIGIT_OWNER_REGISTRY` (real register).
- **Public:** `CLOUDFLARED_TOKEN` (+ the Cloudflare/GoDaddy steps above).
- **Enrichment (each optional, your own free keys):** `BOXTECH_*`,
  `MAERSK_CONSUMER_KEY`, `CMACGM_API_KEY`, `HAPAG_BASE_URL`+auth, `ZIM_*`.
- **SFTP:** replace the compose password with keys; tune `CHECKDIGIT_WATCH_*`.

The correction engine — all five formats — needs **zero** configuration. Start
there, confirm `/health` and a sample upload, then layer on the rest.

---

## Verify you're done

```bash
curl -s http://localhost:8000/health                       # {"status":"ok",...}
curl -F "text=MSKU7351773 recheck" http://localhost:8000/correct   # corrects → MSKU7351770
curl -s http://localhost:8000/sources | head               # the access-tier registry
# then in a browser: upload a sample EDI/SNX, check History + a container dossier
```
