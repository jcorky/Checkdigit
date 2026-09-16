#!/usr/bin/env bash
# smoke-test.sh — verify a RUNNING CHECKDIGIT deployment over HTTP. run_acceptance.py
# tests the code in-process; this tests the live, SERVED stack: the public surface
# answers, the admin surface is locked over the wire, and prod hardening (Swagger
# off, OAuth wired) is actually in place.
#
#   ./smoke-test.sh https://app.thecheckdigit.com         # the public app (prod checks)
#   SITE=https://thecheckdigit.com ./smoke-test.sh https://app.thecheckdigit.com
#   ./smoke-test.sh --dev http://127.0.0.1:8000           # a local dev box (docs ON, no OAuth)
#   ./smoke-test.sh                                        # defaults to --dev on 127.0.0.1:8000
#
# Runs every check (does not abort on first failure) and exits 0 only if all pass.
# Needs curl. NOTE: it POSTs one /correct, so it writes one audit event.
set -uo pipefail

BASE=""; DEV=0
for arg in "$@"; do
  case "$arg" in
    --dev) DEV=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    http://*|https://*) BASE="${arg%/}" ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done
if [ -z "$BASE" ]; then BASE="http://127.0.0.1:8000"; DEV=1; fi   # default = local dev box
command -v curl >/dev/null 2>&1 || { echo "ERROR: curl is required." >&2; exit 1; }

PASS=0; FAIL=0
g="\033[32m"; r="\033[31m"; d="\033[2m"; z="\033[0m"
code(){ curl -s -o /dev/null -w '%{http_code}'    --max-time 12 "$@"; }
redir(){ curl -s -o /dev/null -w '%{redirect_url}' --max-time 12 "$@"; }
check(){ local l="$1" e="$2" a="$3"
  if [ "$a" = "$e" ]; then PASS=$((PASS+1)); printf "  ${g}PASS${z} %-42s ${d}%s${z}\n" "$l" "$a"
  else FAIL=$((FAIL+1)); printf "  ${r}FAIL${z} %-42s expected ${e}, got ${r}%s${z}\n" "$l" "$a"; fi; }

printf "CHECKDIGIT smoke test  ->  %s   ${d}(%s mode)${z}\n" "$BASE" "$([ "$DEV" -eq 1 ] && echo dev || echo prod)"

# --- open surface (no auth) ------------------------------------------------
check "GET /health"                   200 "$(code "$BASE/health")"
check "GET /check (valid number)"     200 "$(code "$BASE/check/CSQU3054383")"
if curl -s --max-time 12 -X POST -F 'text=CSQU3054380 smoke' "$BASE/correct" | grep -q '"event_id"'; then
  check "POST /correct -> event_id"   ok ok
else
  check "POST /correct -> event_id"   ok missing
fi

# --- admin read surface is locked (holds whether auth is configured or stubbed) ---
check "GET /insights   (locked)"      401 "$(code "$BASE/insights")"
check "GET /events     (locked)"      401 "$(code "$BASE/events")"
check "GET /containers (locked)"      401 "$(code "$BASE/containers")"

if [ "$DEV" -eq 1 ]; then
  check "GET /docs (dev: ON)"          200 "$(code "$BASE/docs")"
else
  # --- prod: admin auth wired + Swagger off --------------------------------
  check "GET /admin/whoami (no sess)"  401 "$(code "$BASE/admin/whoami")"
  case "$(redir "$BASE/admin/auth/login")" in
    *accounts.google.com*) check "GET /admin/auth/login -> Google" ok ok ;;
    *)                     check "GET /admin/auth/login -> Google" ok "no" ;;
  esac
  check "GET /docs (prod: OFF)"         404 "$(code "$BASE/docs")"
  check "GET /openapi.json (prod: OFF)" 404 "$(code "$BASE/openapi.json")"
fi

# --- optional landing page (set SITE=https://thecheckdigit.com) -------------
if [ -n "${SITE:-}" ]; then
  S="${SITE%/}"
  check "GET landing /"                200 "$(code "$S/")"
  if curl -s --max-time 12 "$S/" | grep -q "CHECKDIGIT"; then
    check "landing mentions CHECKDIGIT" ok ok
  else
    check "landing mentions CHECKDIGIT" ok no
  fi
fi

echo
if [ "$FAIL" -eq 0 ]; then
  printf "${g}SMOKE TEST PASS${z}: %d checks against %s.\n" "$PASS" "$BASE"; exit 0
else
  printf "${r}SMOKE TEST FAIL${z}: %d passed, %d failed.\n" "$PASS" "$FAIL"; exit 1
fi
