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
mkdir -p "$H/.claude/scripts" "$H/.claude/cost-ledger" "$H/.local/bin" "$TMP/other" \
         "$H/.claude/plugins/cache/haiggoh/cost-tracker/0.19.0" \
         "$H/.claude/plugins/cache/haiggoh/cost-tracker/0.19.0/.claude-plugin"
# The fixture HOME must look like a machine where this plugin IS installed: with no
# registry the scripts/ shim finds nothing and the statusline renders BLANK, which is
# the disaster mode the plan's Review Focus #2 names — not a wiring defect.
printf '%s' \
    '{"plugins":{"cost-tracker@haiggoh":[{"scope":"user","installPath":"'"$H/.claude/plugins/cache/haiggoh/cost-tracker/0.19.0"'"}]}}' \
    > "$H/.claude/plugins/installed_plugins.json"
printf '%s\n' '{"name":"cost-tracker","version":"0.19.0"}' \
    > "$H/.claude/plugins/cache/haiggoh/cost-tracker/0.19.0/.claude-plugin/plugin.json"
# The installPath names that copy, so the shim execs FROM it and install.sh runs ITS
# installer: it must carry the real bin/ (the capture wrapper resolves its renderer
# relative to itself), the shim template, and the installer itself — exactly what a
# release archive contains.
CT="$H/.claude/plugins/cache/haiggoh/cost-tracker/0.19.0"
mkdir -p "$CT/bin" "$CT/install"
for f in cost-ledger-capture.sh statusline-render.sh budget-tally.py cost-tracker; do
    cp "$PLUGIN/bin/$f" "$CT/bin/"
done
cp "$PLUGIN/install/shim-template.sh" "$PLUGIN/install/wire-statusline.sh" "$CT/install/"
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
is "statusLine now calls the capture wrapper through the scripts shim" \
   "$(jq -r .statusLine.command "$H/.claude/settings.json")" "sh $H/.claude/scripts/cost-ledger-capture.sh"
is "settings.json stays 600" "$(ls -l "$H/.claude/settings.json" | cut -c1-10)" "-rw-------"
is "unrelated settings survive" "$(jq -r .model "$H/.claude/settings.json")" "sonnet[1m]"
is "joyia-statusline.sh is left untouched" "$(sed -n 2p "$H/.claude/joyia-statusline.sh")" "cat >/dev/null; echo JOYIA_RENDER"
# The PATH entry resolves through the registry to the INSTALLED copy, not to the
# checkout the installer happened to run from: that is what makes a branch switch in
# a workspace checkout change nothing live.
is "cost-tracker on PATH is the resolved installed copy" \
   "$(readlink "$H/.local/bin/cost-tracker")" "$CT/bin/cost-tracker"
BK="$(ls -d "$H/.claude/backups"/cost-tracker-path-* 2>/dev/null | head -1)"
is "the foreign PATH link is backed up as a link" "$(readlink "$BK/cost-tracker" 2>/dev/null)" "$TMP/other/cost-tracker"

# the status line now CAPTURES, which is the thing the joyia state broke
#
# The lane is PINNED. cost-ledger-capture.sh derives its lane from the environment it inherits
# (correctly — it runs inside Claude Code's own process, so the endpoint is authoritative), which
# meant this assertion depended on whoever ran it: on a gateway box the ledger line is
# `<date> <cum> <baseline>` and field 2 is the cost, while on a free lane the writer zeroes cum
# and puts the avoided cost in field 4. Unpinned, this suite passed on gateway machines and
# failed on free-lane ones (measured 2026-10-23, cost-tracker 0.16.0) — the same shape as the
# inherited-ANTHROPIC_BASE_URL trap tests/conftest.py documents for the Python suite.
RENDER_CMD="$(jq -r .statusLine.command "$H/.claude/settings.json")"
# Every -u BEFORE the first VAR=: env stops parsing options at the first assignment, so a
# trailing `-u X` is taken as the COMMAND and the render silently never runs (this bit us while
# writing this fix — the array in its natural reading order did exactly that).
GW_ENV=(-u CLAUDE_CODE_USE_BEDROCK -u CLAUDE_CODE_USE_VERTEX -u CLAUDE_CODE_USE_FOUNDRY
        HOME="$H" COST_TRACKER_STATUSLINE=0 ANTHROPIC_BASE_URL="https://gw.example.test")
OUT="$(printf '%s' '{"session_id":"inst-1","cost":{"total_cost_usd":1.5},"model":{"display_name":"M"},"workspace":{"current_dir":"/tmp"}}' \
    | env "${GW_ENV[@]}" sh -c "$RENDER_CMD" 2>/dev/null)"
is "a render through the new statusLine writes the ledger" \
   "$(cut -d' ' -f2 "$H/.claude/cost-ledger/inst-1" 2>/dev/null)" "1.5"
is "and renders with the plugin's renderer, not joyia's" "$(printf '%s' "$OUT" | grep -c JOYIA_RENDER)" "0"

# The OTHER lane, asserted rather than assumed: a free render zeroes cum and records the avoided
# cost in ledger field 4 (0.14.0's phantom axis). Keeps the writer's real contract visible in
# the suite, and guards the field the unpinned test used to read by accident.
printf '%s' '{"session_id":"inst-2","cost":{"total_cost_usd":1.5},"model":{"display_name":"M"},"workspace":{"current_dir":"/tmp"}}' \
    | HOME="$H" COST_TRACKER_STATUSLINE=0 ANTHROPIC_BASE_URL="http://127.0.0.1:4143" \
      sh -c "$RENDER_CMD" >/dev/null 2>&1
is "a free-lane render zeroes cum and books the phantom instead" \
   "$(cut -d' ' -f2 "$H/.claude/cost-ledger/inst-2" 2>/dev/null)" "0"
is "phantom lands in field 4" \
   "$(cut -d' ' -f4 "$H/.claude/cost-ledger/inst-2" 2>/dev/null)" "1.5"

HOME="$H" bash "$PLUGIN/install.sh" > "$TMP/again.out" 2>&1
is "a second run exits 0" "$?" "0"
is "a second run reports the PATH link as already linked" "$(grep -c 'already linked' "$TMP/again.out")" "1"
is "a second run changes no settings" "$(grep -c 'Nothing to do' "$TMP/again.out")" "1"

echo
printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
