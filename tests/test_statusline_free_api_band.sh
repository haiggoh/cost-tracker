#!/usr/bin/env bash
# tests/test_statusline_free_api_band.sh — 0.25.1 Task 1 (free-agents 0.67.0 plan):
# the statusline classifies free_api by the PROXY BAND — a loopback URL whose port is in
# [LA_REMOTE_PROXY_PORT_MIN, LA_REMOTE_PROXY_PORT_MAX] (session env, fallback the shipped
# default 4141-4190) — not by the old literal 414[1-9]|415[01]. Runs the REAL
# bin/statusline-render.sh down its legacy endpoint-detection path (the stub resolver
# answers "unknown", same pin as test_statusline_render.sh).
set -uo pipefail
unset CLAUDE_CODE_USE_VERTEX CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_FOUNDRY \
      ANTHROPIC_VERTEX_PROJECT_ID CLOUD_ML_REGION CLAUDE_CODE_SKIP_VERTEX_AUTH
unset LA_REMOTE_PROXY_PORT_MIN LA_REMOTE_PROXY_PORT_MAX   # default-band cases need them unset
export LC_ALL=C

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RENDER="$HERE/../bin/statusline-render.sh"

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n     expected: %s\n     actual:   %s\n' "$1" "$2" "$3"; }
is()  { [ "$2" = "$3" ] && ok "$1" || bad "$1" "$3" "$2"; }

# Pin the identity resolver to "unknown" so the renderer uses the legacy ANTHROPIC_BASE_URL
# path under test (same pin as test_statusline_render.sh — a real resolver would answer with
# the ambient live session instead).
STUB_BIN="$(mktemp -d)"
printf '%s\n' '#!/bin/sh' 'printf "{\"schema_version\":1,\"session_kind\":\"unknown\",\"unknown_fallback\":true}"' \
  > "$STUB_BIN/la-session-identity.sh"
chmod +x "$STUB_BIN/la-session-identity.sh"
export COST_FREE_AGENTS_BIN="$STUB_BIN"

TMPD="$(mktemp -d)"; LED="$TMPD/ledger"; mkdir -p "$LED"
PAY='{"model":{"display_name":"Opus 5"},"workspace":{"current_dir":"/tmp"},"session_id":"sess-x"}'
frender() {  # frender <url> [VAR=val ...] -> rendered output, ANSI stripped
  local url="$1"; shift
  printf '%s' "$PAY" | env COST_TRACKER_STATUSLINE=1 \
    COST_TRACKER_LEDGER_DIR="$LED" COST_TRACKER_HISTORY="$TMPD/none.log" \
    COST_TRACKER_REMEMBER_DIRS="$TMPD/remember" COST_TRACKER_SG_LOG="$TMPD/sg/log.txt" \
    ANTHROPIC_BASE_URL="$url" "$@" sh "$RENDER" 2>/dev/null | sed $'s/\033\[[0-9;]*m//g'
}
spend_line() { printf '%s' "$1" | grep -E '\$[0-9]' | head -1; }
kind() {  # kind <url> [VAR=val ...] -> free_api | local | other
  local out; out="$(spend_line "$(frender "$@")")"
  case "$out" in
    *"free api session"*) echo free_api ;;
    *"local session"*)    echo local ;;
    *)                    echo other ;;
  esac
}

echo "statusline free_api band"

is "localhost:4160 (inside the widened band) is free_api"      "$(kind http://localhost:4160)" "free_api"
is "localhost:4141 (old band edge) stays free_api"             "$(kind http://localhost:4141)" "free_api"
is "localhost:4151 (old band far edge) stays free_api"         "$(kind http://localhost:4151)" "free_api"
is "127.0.0.1:4190/ (new band edge, trailing slash) is free_api" "$(kind http://127.0.0.1:4190/)" "free_api"
is "localhost:4160 with a path is free_api"                    "$(kind http://localhost:4160/v1)" "free_api"
is "localhost:4191 (outside the band) is NOT free_api"         "$(kind http://localhost:4191)" "other"
is "https://llmgw.example:4160 (gateway host on a band port) is never free_api" "$(kind https://llmgw.example:4160)" "other"
is "localhost:8005 stays LOCAL (arm order: local wins)"       "$(kind http://localhost:8005)" "local"
is "localhost:8005 stays LOCAL even when the band covers it"   "$(kind http://localhost:8005 LA_REMOTE_PROXY_PORT_MIN=8000 LA_REMOTE_PROXY_PORT_MAX=8010)" "local"
is "env override MIN=5000 MAX=5010: localhost:5005 is free_api" "$(kind http://localhost:5005 LA_REMOTE_PROXY_PORT_MIN=5000 LA_REMOTE_PROXY_PORT_MAX=5010)" "free_api"
is "env override MIN=5000 MAX=5010: localhost:4141 is NOT free_api" "$(kind http://localhost:4141 LA_REMOTE_PROXY_PORT_MIN=5000 LA_REMOTE_PROXY_PORT_MAX=5010)" "other"

printf '%s checks, %s failed\n' "$((PASS+FAIL))" "$FAIL"
[ "$FAIL" -eq 0 ]
