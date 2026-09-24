#!/usr/bin/env bash
# Tests for install/wire-statusline.sh. Everything runs against a TEMP HOME: the
# script is entirely $HOME-relative, so nothing here can touch the real chain.
#
# The rollback test copies the plugin to a temp dir and BREAKS the copy's capture
# script, because "it rolls back on failure" is only worth claiming if a failure has
# actually been produced and the rollback observed.
set -uo pipefail
export LC_ALL=C

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN="$(cd -P "$HERE/.." && pwd)"

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n     expected: %s\n     actual:   %s\n' "$1" "$2" "$3"; }
is()  { [ "$2" = "$3" ] && ok "$1" || bad "$1" "$3" "$2"; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/home/.claude/scripts" "$TMP/home/.claude/cost-ledger"
echo 'echo OLD_LIVE_COPY' > "$TMP/home/.claude/scripts/budget-tally.py"
chmod 600 "$TMP/home/.claude/scripts/budget-tally.py"
# The exact 2026-09-23 break: statusLine calls the RENDERER, bypassing capture.
printf '%s\n' '{"model":"x","statusLine":{"type":"command","command":"sh /somewhere/statusline-render.sh","padding":0}}' \
    > "$TMP/home/.claude/settings.json"
chmod 600 "$TMP/home/.claude/settings.json"
WANT="sh $PLUGIN/bin/cost-ledger-capture.sh"

echo "wire-statusline.sh"

# --- --help and bad arguments must do NO work --------------------------------
HOME="$TMP/home" bash "$PLUGIN/install/wire-statusline.sh" --help > "$TMP/help.out" 2>&1
is "--help exits 0" "$?" "0"
is "--help prints usage" "$(grep -c '^Usage: wire-statusline.sh' "$TMP/help.out")" "1"
is "--help documents --dry-run" "$(grep -c -- '--dry-run' "$TMP/help.out" | awk '{print ($1>0)?"yes":"no"}')" "yes"
is "--help writes nothing" "$(jq -r .statusLine.command "$TMP/home/.claude/settings.json")" "sh /somewhere/statusline-render.sh"
HOME="$TMP/home" bash "$PLUGIN/install/wire-statusline.sh" --bogus > "$TMP/bogus.out" 2>&1
is "an unknown flag exits 2" "$?" "2"
is "an unknown flag writes nothing" \
   "$([ -L "$TMP/home/.claude/scripts/budget-tally.py" ] && echo symlink || echo plainfile)" "plainfile"

# --- dry run must change NOTHING ---------------------------------------------
HOME="$TMP/home" bash "$PLUGIN/install/wire-statusline.sh" --dry-run > "$TMP/dry.out" 2>&1
is "dry run exits 0" "$?" "0"
is "dry run names the bypassed statusLine" "$(grep -c 'BYPASSED' "$TMP/dry.out")" "1"
is "dry run leaves settings.json alone" \
   "$(jq -r .statusLine.command "$TMP/home/.claude/settings.json")" "sh /somewhere/statusline-render.sh"
is "dry run leaves the live copy a plain file" \
   "$([ -L "$TMP/home/.claude/scripts/budget-tally.py" ] && echo symlink || echo plainfile)" "plainfile"
is "dry run says DRY RUN" \
   "$(grep -c 'DRY RUN' "$TMP/dry.out")" "1"
is "dry run creates no backup dir" \
   "$(ls -d "$TMP/home/.claude/backups"/cost-tracker-wire-* 2>/dev/null | wc -l | tr -d ' ')" "0"

# --- no arguments = DO the work (not help, not a dry run) ---------------------
HOME="$TMP/home" bash "$PLUGIN/install/wire-statusline.sh" > "$TMP/apply.out" 2>&1
is "a bare run exits 0" "$?" "0"
is "a bare run repoints statusLine straight at the plugin's capture wrapper" \
   "$(jq -r .statusLine.command "$TMP/home/.claude/settings.json")" "$WANT"
is "other statusLine keys survive" "$(jq -r .statusLine.padding "$TMP/home/.claude/settings.json")" "0"
is "unrelated settings survive" "$(jq -r .model "$TMP/home/.claude/settings.json")" "x"
is "settings.json keeps mode 600" "$(ls -l "$TMP/home/.claude/settings.json" | cut -c1-10)" "-rw-------"
for f in cost-ledger-capture.sh statusline-render.sh budget-tally.py; do
    is "apply symlinks $f into the plugin" \
       "$(readlink "$TMP/home/.claude/scripts/$f")" "$PLUGIN/bin/$f"
done
BK="$(ls -d "$TMP/home/.claude/backups"/cost-tracker-wire-* 2>/dev/null | head -1)"
is "apply backs up the pre-existing live copy" "$(cat "$BK/budget-tally.py" 2>/dev/null)" "echo OLD_LIVE_COPY"
is "the backup keeps the original mode (600, not the umask default)" \
   "$(ls -l "$BK/budget-tally.py" | cut -c1-10)" "-rw-------"
is "settings.json is backed up before the edit" \
   "$(jq -r .statusLine.command "$BK/settings.json" 2>/dev/null)" "sh /somewhere/statusline-render.sh"
is "apply reports VERIFIED" "$(grep -c 'VERIFIED' "$TMP/apply.out")" "1"

# --- the verification render must not leave a fictional session behind -------
is "no sentinel ledger file remains" \
   "$(ls "$TMP/home/.claude/cost-ledger" | grep -c wire-check)" "0"
# grep -c prints its count AND exits 1 when the count is zero, so a `|| echo 0`
# fallback appends a SECOND line and the comparison fails against a correct result.
ROWS="$(grep -c wire-check "$TMP/home/.claude/cost-ledger-history.log" 2>/dev/null)"
is "no sentinel history row remains" "${ROWS:-0}" "0"
COUNT="$(COST_TRACKER_LEDGER_DIR="$TMP/home/.claude/cost-ledger" \
         COST_TRACKER_HISTORY="$TMP/home/.claude/cost-ledger-history.log" \
         COST_TRACKER_SAVINGS_CMD=/bin/false \
         python3 "$PLUGIN/bin/cost-tracker" report --json | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["sessions"]))')"
is "a report over the wired store shows no sessions at all" "$COUNT" "0"

# --- idempotence -------------------------------------------------------------
HOME="$TMP/home" bash "$PLUGIN/install/wire-statusline.sh" --apply > "$TMP/again.out" 2>&1
is "a second run (--apply kept for compatibility) is a no-op" "$(grep -c 'Nothing to do' "$TMP/again.out")" "1"
is "a second apply creates no second backup" \
   "$(ls -d "$TMP/home/.claude/backups"/cost-tracker-wire-* | wc -l | tr -d ' ')" "1"

# --- a FOREIGN rewrite after wiring is detected and repaired -----------------
# Symlinks intact, settings pointed elsewhere: the old script said "already wired" here.
jq '.statusLine.command = "sh /x/joyia-statusline.sh"' "$TMP/home/.claude/settings.json" > "$TMP/s.json" \
    && cat "$TMP/s.json" > "$TMP/home/.claude/settings.json"
HOME="$TMP/home" bash "$PLUGIN/install/wire-statusline.sh" --dry-run > "$TMP/foreign.out" 2>&1
is "intact symlinks + foreign statusLine is NOT reported as wired" "$(grep -c 'Nothing to do' "$TMP/foreign.out")" "0"
is "the foreign statusLine is named" "$(grep -c 'FOREIGN' "$TMP/foreign.out")" "1"
HOME="$TMP/home" bash "$PLUGIN/install/wire-statusline.sh" > /dev/null 2>&1
is "a re-run repairs it" "$(jq -r .statusLine.command "$TMP/home/.claude/settings.json")" "$WANT"

# --- settings.local.json outranks settings.json: warn, never edit ------------
echo '{"statusLine":{"type":"command","command":"sh /override.sh"}}' > "$TMP/home/.claude/settings.local.json"
HOME="$TMP/home" bash "$PLUGIN/install/wire-statusline.sh" --dry-run > "$TMP/local.out" 2>&1
is "a settings.local.json statusLine override is warned about" "$(grep -c 'OVERRIDES' "$TMP/local.out")" "1"
is "settings.local.json is not edited" "$(jq -r .statusLine.command "$TMP/home/.claude/settings.local.json")" "sh /override.sh"
rm -f "$TMP/home/.claude/settings.local.json"

# --- the capture wrapper renders through its SIBLING, not ~/.claude/scripts ---
mkdir -p "$TMP/home3/.claude/scripts"
printf '#!/bin/sh\necho WRONG_RENDERER\n' > "$TMP/home3/.claude/scripts/statusline-render.sh"
OUT3="$(printf '%s' '{"session_id":"s3","cost":{"total_cost_usd":0},"model":{"display_name":"M"},"workspace":{"current_dir":"/tmp"}}' \
    | HOME="$TMP/home3" COST_TRACKER_STATUSLINE=0 sh "$PLUGIN/bin/cost-ledger-capture.sh" 2>/dev/null)"
is "capture uses the renderer next to it" "$(printf '%s' "$OUT3" | grep -c WRONG_RENDERER)" "0"
is "capture still writes its ledger entry" "$([ -f "$TMP/home3/.claude/cost-ledger/s3" ] && echo yes || echo no)" "yes"

# --- rollback on a chain that does not render --------------------------------
FAKE="$TMP/fakeplugin"
mkdir -p "$FAKE/bin" "$FAKE/install"
cp "$PLUGIN/install/wire-statusline.sh" "$FAKE/install/"
cp "$PLUGIN/bin/statusline-render.sh" "$PLUGIN/bin/budget-tally.py" "$FAKE/bin/"
# a capture script that renders NOTHING is exactly the failure that must never be
# left wired: a blank status line with no error.
printf '#!/bin/sh\ncat > /dev/null\nexit 0\n' > "$FAKE/bin/cost-ledger-capture.sh"
chmod +x "$FAKE/bin/cost-ledger-capture.sh"
mkdir -p "$TMP/home2/.claude/scripts"
echo 'echo PRIOR' > "$TMP/home2/.claude/scripts/cost-ledger-capture.sh"
echo '{"statusLine":{"type":"command","command":"sh /prior.sh"}}' > "$TMP/home2/.claude/settings.json"
HOME="$TMP/home2" bash "$FAKE/install/wire-statusline.sh" > "$TMP/rb.out" 2>&1
is "a non-rendering chain exits non-zero" "$([ $? -ne 0 ] && echo nonzero || echo zero)" "nonzero"
is "rollback is announced" "$(grep -c 'ROLLING BACK' "$TMP/rb.out")" "1"
is "the prior live copy is restored, not left as a symlink" \
   "$(cat "$TMP/home2/.claude/scripts/cost-ledger-capture.sh")" "echo PRIOR"
is "a file that did not exist before is removed again" \
   "$([ -e "$TMP/home2/.claude/scripts/statusline-render.sh" ] && echo present || echo absent)" "absent"
is "settings.json is restored on rollback" "$(jq -r .statusLine.command "$TMP/home2/.claude/settings.json")" "sh /prior.sh"

# A chain that RENDERS but does not CAPTURE is the 2026-09-23 failure; it must not verify.
FAKE2="$TMP/fakeplugin2"
mkdir -p "$FAKE2/bin" "$FAKE2/install"
cp "$PLUGIN/install/wire-statusline.sh" "$FAKE2/install/"
cp "$PLUGIN/bin/statusline-render.sh" "$PLUGIN/bin/budget-tally.py" "$FAKE2/bin/"
printf '#!/bin/sh\ncat > /dev/null\necho looks-fine\n' > "$FAKE2/bin/cost-ledger-capture.sh"
mkdir -p "$TMP/home4/.claude"
echo '{"statusLine":{"type":"command","command":"sh /prior.sh"}}' > "$TMP/home4/.claude/settings.json"
HOME="$TMP/home4" bash "$FAKE2/install/wire-statusline.sh" > "$TMP/rb2.out" 2>&1
is "renders-but-captures-nothing fails verification" "$([ $? -ne 0 ] && echo nonzero || echo zero)" "nonzero"
is "and says the ledger was not written" "$(grep -c 'ledger written=no' "$TMP/rb2.out")" "1"
is "and restores settings.json" "$(jq -r .statusLine.command "$TMP/home4/.claude/settings.json")" "sh /prior.sh"

echo
printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
