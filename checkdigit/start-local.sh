#!/usr/bin/env bash
# Fastest path to a running instance on a laptop WITHOUT Docker.
# Creates a venv, installs deps, runs the API. Ctrl-C to stop.
#   ./start-local.sh            -> http://127.0.0.1:8000  (and /docs)
# Honors a .env if present (export the vars before running uvicorn).
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" -c 'import sys; assert sys.version_info[:2] >= (3,10)' 2>/dev/null; then
  echo "Need Python 3.10+; found: $($PYTHON -V 2>&1). Install a newer python3." >&2
  exit 1
fi

# Guard on the ACTIVATE SCRIPT, not the directory: a half-built .venv must be
# rebuilt, never silently sourced. Self-heal symlink-restricted mounts with --copies.
if [ ! -f .venv/bin/activate ]; then
  echo "==> creating virtualenv (.venv)"
  "$PYTHON" -m venv .venv || true
  if [ ! -f .venv/bin/activate ]; then
    echo "    no activate script after first attempt; retrying with --copies" >&2
    rm -rf .venv
    "$PYTHON" -m venv --copies .venv || true
  fi
fi
if [ ! -f .venv/bin/activate ]; then
  echo "ERROR: virtualenv creation failed -- .venv/bin/activate is missing." >&2
  echo "  On Debian/Ubuntu the venv module ships separately:  sudo apt-get install python3-venv" >&2
  echo "  Then re-run." >&2
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> installing dependencies"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

if [ -f .env ]; then
  echo "==> loading .env"
  set -a; # shellcheck disable=SC1091
  source .env; set +a
fi

HOST="${HOST:-127.0.0.1}"; PORT="${PORT:-8000}"
echo "==> starting CHECKDIGIT on http://${HOST}:${PORT}  (Ctrl-C to stop)"
exec uvicorn api:app --host "$HOST" --port "$PORT"
