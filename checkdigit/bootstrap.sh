#!/usr/bin/env bash
# bootstrap.sh — take a fresh Debian / Ubuntu / Linux Mint box to a ready-to-run
# CHECKDIGIT in one command. Installs the SYSTEM packages install.sh assumes, then
# hands off to ./install.sh. Run it from the project root (where install.sh lives):
#
#   ./bootstrap.sh                 # system deps + Python venv/deps + SPA build (no server start)
#   ./bootstrap.sh --run           # ...and start the local server when done
#   ./bootstrap.sh --verify        # ...and run the full test suite during setup
#   ./bootstrap.sh --docker        # also install Docker + compose (for the public stack)
#   ./bootstrap.sh --no-node       # skip Node + the SPA build (JSON API only)
#
# Idempotent: safe to re-run. Fail-loud: a missing prerequisite stops the script
# with an explicit message rather than limping on. It does NOT fetch the source —
# unpack the tarball (or clone) first, then run this from inside it.
set -euo pipefail
cd "$(dirname "$0")"

RUN=0; VERIFY=0; DOCKER=0; WANT_NODE=1
for arg in "$@"; do
  case "$arg" in
    --run)     RUN=1 ;;
    --verify)  VERIFY=1 ;;
    --docker)  DOCKER=1 ;;
    --no-node) WANT_NODE=0 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say(){ printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die(){ echo "ERROR: $*" >&2; exit 1; }

# 0) sanity: are we actually in the project root? ---------------------------
for f in install.sh requirements.txt api.py; do
  [ -f "$f" ] || die "missing '$f' — run bootstrap.sh from the CHECKDIGIT project root (unpack the tarball or clone the repo first)."
done

# 1) require apt (Debian / Ubuntu / Mint) -----------------------------------
command -v apt-get >/dev/null 2>&1 || die "this bootstrap targets Debian/Ubuntu/Mint (apt). On another distro, install python3-venv + pip (and optionally node + docker) yourself, then run ./install.sh."

# privilege escalation for apt / docker
if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  command -v sudo >/dev/null 2>&1 || die "need root or sudo to install system packages."
  SUDO="sudo"
fi

# 2) base system packages ---------------------------------------------------
say "installing base system packages (apt)"
$SUDO apt-get update
# Python interpreter + the venv module (a SEPARATE package on Debian/Ubuntu, the
# usual missing piece) + pip. curl/ca-certificates are needed for the Docker path.
PKGS="python3 python3-venv python3-pip ca-certificates curl"
[ "$WANT_NODE" -eq 1 ] && PKGS="$PKGS nodejs npm"
# shellcheck disable=SC2086
$SUDO apt-get install -y $PKGS

# the venv module must actually import (the classic Debian gotcha) -- fail loud
python3 -c 'import venv' 2>/dev/null || die "python3 venv module still unavailable after install. Try: $SUDO apt-get install python3-venv"

if [ "$WANT_NODE" -eq 1 ] && command -v node >/dev/null 2>&1; then
  NODE_MAJOR="$(node -v | sed 's/^v//; s/\..*//')"
  if [ "${NODE_MAJOR:-0}" -lt 18 ]; then
    echo "    NOTE: Node $(node -v) is older than 18; the SPA build may fail."
    echo "    Install a current Node (nvm: https://github.com/nvm-sh/nvm, or NodeSource) and re-run,"
    echo "    or use --no-node to ship the JSON API without the web UI."
  fi
fi

# 3) optional Docker (official installer; opt-in) ---------------------------
if [ "$DOCKER" -eq 1 ]; then
  if command -v docker >/dev/null 2>&1; then
    say "Docker already present ($(docker --version))"
  else
    say "installing Docker via the official get.docker.com script"
    curl -fsSL https://get.docker.com | $SUDO sh \
      || die "Docker install failed. Install it manually from https://docs.docker.com/engine/install/ then re-run with --docker."
    if [ -n "$SUDO" ]; then
      $SUDO usermod -aG docker "$USER" || true
      echo "    added '$USER' to the docker group — log out and back in for it to take effect."
    fi
  fi
fi

# 4) scaffold .env ----------------------------------------------------------
if [ ! -f .env ] && [ -f .env.example ]; then
  say "scaffolding .env from .env.example (edit it before any public deploy)"
  cp .env.example .env
fi

# 5) build the app environment (venv + Python deps + SPA) -------------------
say "building the app environment (./install.sh --no-run)"
INSTALL_ARGS="--no-run"
[ "$VERIFY"    -eq 1 ] && INSTALL_ARGS="$INSTALL_ARGS --verify"
[ "$WANT_NODE" -eq 0 ] && INSTALL_ARGS="$INSTALL_ARGS --no-spa"
# shellcheck disable=SC2086
bash ./install.sh $INSTALL_ARGS

# 6) done / next steps ------------------------------------------------------
say "bootstrap complete"
cat <<'EOF'
  CHECKDIGIT is set up. To start it:

    Local (no Docker):   ./start-local.sh            -> http://127.0.0.1:8000
    Public (Docker):     edit .env, then
                         docker compose -f docker-compose.public.yml up -d --build

  Landing page: site/index.html  (Caddy serves it at thecheckdigit.com in production).
  Before public deploy: set the Google OAuth + PUBLIC_ORIGIN values in .env
  (see .env.example, TIER 2b) and rotate any old tunnel token.
EOF

if [ "$RUN" -eq 1 ]; then
  say "starting the local server (Ctrl-C to stop)"
  exec bash ./start-local.sh
fi
