#!/usr/bin/env bash
# install.sh — one command to install and launch CHECKDIGIT on a single host
# WITHOUT Docker. Idempotent: safe to re-run. Ctrl-C stops the server.
#
#   ./install.sh                 # venv + deps + (SPA if Node present) + run
#   ./install.sh --no-spa        # skip the SPA build even if Node is present
#   ./install.sh --no-run        # set everything up but don't start the server
#   ./install.sh --verify        # run the test suite after install, then run
#
# Env knobs (all optional):
#   HOST=0.0.0.0 PORT=8080 ./install.sh        # bind address/port (default 127.0.0.1:8000)
#   PYTHON=python3.12 ./install.sh             # pick a specific interpreter
#   CHECKDIGIT_OWNER_REGISTRY=/path/bic.csv ./install.sh   # enable owner corroboration
set -euo pipefail
cd "$(dirname "$0")"

NO_SPA=0; NO_RUN=0; VERIFY=0
for arg in "$@"; do
  case "$arg" in
    --no-spa) NO_SPA=1 ;;
    --no-run) NO_RUN=1 ;;
    --verify) VERIFY=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

# 1) Python 3.10+ -----------------------------------------------------------
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "ERROR: '$PYTHON' not found. Install Python 3.10+ first." >&2
  exit 1
fi
if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)'; then
  echo "ERROR: Python 3.10+ required; found $("$PYTHON" -V 2>&1)." >&2
  exit 1
fi
echo "==> Python: $("$PYTHON" -V 2>&1)"

# 2) virtualenv + deps ------------------------------------------------------
# Guard on the ACTIVATE SCRIPT, not just the directory. A half-built .venv (dir
# present but bin/activate missing -- interrupted run, missing python3-venv, or a
# symlink-restricted mount) must be rebuilt, never silently sourced into a cryptic
# "No such file or directory".
if [ ! -f .venv/bin/activate ]; then
  echo "==> creating virtualenv (.venv)"
  "$PYTHON" -m venv .venv || true
  if [ ! -f .venv/bin/activate ]; then
    # Common on overlay / external-drive filesystems: venv's default symlinks are
    # disallowed. Rebuild with copied binaries before giving up.
    echo "    no activate script after first attempt; retrying with --copies" >&2
    rm -rf .venv
    "$PYTHON" -m venv --copies .venv || true
  fi
fi
# Fail loud rather than proceed to `source` a venv that was never created.
if [ ! -f .venv/bin/activate ]; then
  echo "ERROR: virtualenv creation failed -- .venv/bin/activate is missing." >&2
  echo "  On Debian/Ubuntu the venv module ships SEPARATELY from the interpreter:" >&2
  echo "      sudo apt-get install python3-venv      # or the versioned python3.X-venv" >&2
  echo "  Then re-run ./install.sh" >&2
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "==> installing Python dependencies"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# 3) optional .env ----------------------------------------------------------
if [ -f .env ]; then
  echo "==> loading .env"
  set -a; # shellcheck disable=SC1091
  source .env; set +a
fi

# 4) optional SPA build -----------------------------------------------------
if [ "$NO_SPA" -eq 0 ] && command -v node >/dev/null 2>&1; then
  if [ ! -d static ] || [ checkdigit_app.jsx -nt static ]; then
    echo "==> building the web UI (Node detected)"
    bash ./build-spa.sh || {
      echo "    SPA build failed — continuing with the JSON API only." >&2
    }
  else
    echo "==> web UI already built (static/ is up to date)"
  fi
else
  if [ "$NO_SPA" -eq 1 ]; then
    echo "==> skipping SPA build (--no-spa)"
  else
    echo "==> Node not found: skipping the web UI. The API still runs and serves"
    echo "    JSON at '/'. Install Node 18+ and re-run to get the GUI."
  fi
fi

# 4b) landing page ----------------------------------------------------------
# The marketing site is a separate static artifact served by Caddy at the apex in
# production (thecheckdigit.com), NOT by this local app server. Confirm it's here.
if [ -f site/index.html ]; then
  echo "==> landing page present: site/index.html"
  echo "    (Caddy serves it at thecheckdigit.com in production; open the file to preview)"
fi

# 5) optional verification --------------------------------------------------
if [ "$VERIFY" -eq 1 ]; then
  echo "==> running the test suite (network-less; core is stdlib)"
  python3 test_equipment_checkdigit.py
  python3 test_substitution.py
  for p in 8 9 10 11 12 13 14 15 16 17 20 21 22 23 24 25 26; do python3 "run_pass$p.py" >/dev/null; done
  python3 run_acceptance.py
  echo "    all tests passed."
fi

# 6) run --------------------------------------------------------------------
if [ "$NO_RUN" -eq 1 ]; then
  echo "==> setup complete (--no-run). Start later with:  ./start-local.sh"
  exit 0
fi
HOST="${HOST:-127.0.0.1}"; PORT="${PORT:-8000}"
echo
echo "==> CHECKDIGIT is starting on  http://${HOST}:${PORT}"
echo "    GUI (if built):  http://${HOST}:${PORT}/"
echo "    API docs:        http://${HOST}:${PORT}/docs"
echo "    Stop:            Ctrl-C"
echo
exec uvicorn api:app --host "$HOST" --port "$PORT"
