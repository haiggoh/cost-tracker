#!/usr/bin/env bash
# Tests for install.sh. Everything runs against a TEMP HOME.
#
# The central case replays what `joyia agent --setup` leaves behind (statusLine =
# `sh ~/.claude/joyia-statusline.sh`, a regenerated joyia-statusline.sh) and asserts a
# single bare run of install.sh restores capture — the recipe that took two sessions
# by hand on 2026-09-23.
set -uo pipefail
export LC_ALL=C

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN="$(cd -P "$HERE/.." && pwd)"

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n     expected: %s\n     actual:   %s\n' "$1" "$2" "$3"; }
is()  { [ "$2" = "$3" ] && ok "$1" || bad "$1" "$3" "$2"; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
H="$TMP/home"
mkdir -p "$H/.claude/scripts" "$H/.claude/cost-ledger" "$H/.local/bin" "$TMP/other"
# --- the post-`joyia agent --setup` state ------------------------------------
printf '#!/bin/sh\ncat >/dev/null; echo JOYIA_RENDER\n' > "$H/.claude/joyia-statusline.sh"
printf '%s\n' '{"model":"sonnet[1m]","statusLine":{"type":"command","command":"sh '"$H"'/.claude/joyia-statusline.sh","padding":0}}' \
    > "$H/.claude/settings.json"
chmod 600 "$H/.claude/settings.json"
# another tool owns the bare name (the agy-cost-tracker case)
printf '#!/bin/sh\necho OTHER\n' > "$TMP/other/cost-tracker"; chmod +x "$TMP/other/cost-tracker"
ln -s "$TMP/other/cost-tracker" "$H/.local/bin/cost-tracker"

echo "install.sh"

HOME="$H" bash "$PLUGIN/install.sh" --help > "$TMP/help.out" 2>&1
is "--help exits 0" "$?" "0"
is "--help prints usage" "$(grep -c '^Usage: install.sh' "$TMP/help.out")" "1"
is "--help changes nothing" "$(readlink "$H/.local/bin/cost-tracker")" "$TMP/other/cost-tracker"

HOME="$H" bash "$PLUGIN/install.sh" --nope > /dev/null 2>&1
is "an unknown flag exits 2" "$?" "2"

HOME="$H" bash "$PLUGIN/install.sh" --dry-run > "$TMP/dry.out" 2>&1
is "dry run exits 0" "$?" "0"
is "dry run names the joyia statusLine as FOREIGN" "$(grep -c 'FOREIGN  ' "$TMP/dry.out")" "1"
is "dry run names the foreign PATH link" "$(grep -c 'FOREIGN link' "$TMP/dry.out")" "1"
is "dry run leaves settings.json alone" \
   "$(jq -r .statusLine.command "$H/.claude/settings.json")" "sh $H/.claude/joyia-statusline.sh"
is "dry run leaves the PATH link alone" "$(readlink "$H/.local/bin/cost-tracker")" "$TMP/other/cost-tracker"

HOME="$H" bash "$PLUGIN/install.sh" > "$TMP/run.out" 2>&1
is "a bare run exits 0" "$?" "0"
is "statusLine now calls the plugin's capture wrapper" \
   "$(jq -r .statusLine.command "$H/.claude/settings.json")" "sh $PLUGIN/bin/cost-ledger-capture.sh"
is "settings.json stays 600" "$(ls -l "$H/.claude/settings.json" | cut -c1-10)" "-rw-------"
is "unrelated settings survive" "$(jq -r .model "$H/.claude/settings.json")" "sonnet[1m]"
is "joyia-statusline.sh is left untouched" "$(sed -n 2p "$H/.claude/joyia-statusline.sh")" "cat >/dev/null; echo JOYIA_RENDER"
is "cost-tracker on PATH is this plugin" "$(readlink "$H/.local/bin/cost-tracker")" "$PLUGIN/bin/cost-tracker"
BK="$(ls -d "$H/.claude/backups"/cost-tracker-path-* 2>/dev/null | head -1)"
is "the foreign PATH link is backed up as a link" "$(readlink "$BK/cost-tracker" 2>/dev/null)" "$TMP/other/cost-tracker"

# the status line now CAPTURES, which is the thing the joyia state broke
OUT="$(printf '%s' '{"session_id":"inst-1","cost":{"total_cost_usd":1.5},"model":{"display_name":"M"},"workspace":{"current_dir":"/tmp"}}' \
    | HOME="$H" COST_TRACKER_STATUSLINE=0 sh -c "$(jq -r .statusLine.command "$H/.claude/settings.json")" 2>/dev/null)"
is "a render through the new statusLine writes the ledger" "$(cut -d' ' -f2 "$H/.claude/cost-ledger/inst-1" 2>/dev/null)" "1.5"
is "and renders with the plugin's renderer, not joyia's" "$(printf '%s' "$OUT" | grep -c JOYIA_RENDER)" "0"

HOME="$H" bash "$PLUGIN/install.sh" > "$TMP/again.out" 2>&1
is "a second run exits 0" "$?" "0"
is "a second run reports the PATH link as already linked" "$(grep -c 'already linked' "$TMP/again.out")" "1"
is "a second run changes no settings" "$(grep -c 'Nothing to do' "$TMP/again.out")" "1"

echo
printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
