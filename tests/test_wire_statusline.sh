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

echo "wire-statusline.sh"

# --- dry run must change NOTHING ---------------------------------------------
HOME="$TMP/home" bash "$PLUGIN/install/wire-statusline.sh" > "$TMP/dry.out" 2>&1
is "dry run exits 0" "$?" "0"
is "dry run leaves the live copy a plain file" \
   "$([ -L "$TMP/home/.claude/scripts/budget-tally.py" ] && echo symlink || echo plainfile)" "plainfile"
is "dry run says DRY RUN" \
   "$(grep -c 'DRY RUN' "$TMP/dry.out")" "1"
is "dry run creates no backup dir" \
   "$(ls -d "$TMP/home/.claude/backups"/cost-tracker-wire-* 2>/dev/null | wc -l | tr -d ' ')" "0"

# --- apply -------------------------------------------------------------------
HOME="$TMP/home" bash "$PLUGIN/install/wire-statusline.sh" --apply > "$TMP/apply.out" 2>&1
is "apply exits 0" "$?" "0"
for f in cost-ledger-capture.sh statusline-render.sh budget-tally.py; do
    is "apply symlinks $f into the plugin" \
       "$(readlink "$TMP/home/.claude/scripts/$f")" "$PLUGIN/bin/$f"
done
BK="$(ls -d "$TMP/home/.claude/backups"/cost-tracker-wire-* 2>/dev/null | head -1)"
is "apply backs up the pre-existing live copy" "$(cat "$BK/budget-tally.py" 2>/dev/null)" "echo OLD_LIVE_COPY"
is "the backup keeps the original mode (600, not the umask default)" \
   "$(ls -l "$BK/budget-tally.py" | cut -c1-10)" "-rw-------"
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
is "a second apply is a no-op" "$(grep -c 'Nothing to do' "$TMP/again.out")" "1"
is "a second apply creates no second backup" \
   "$(ls -d "$TMP/home/.claude/backups"/cost-tracker-wire-* | wc -l | tr -d ' ')" "1"

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
HOME="$TMP/home2" bash "$FAKE/install/wire-statusline.sh" --apply > "$TMP/rb.out" 2>&1
is "a non-rendering chain exits non-zero" "$([ $? -ne 0 ] && echo nonzero || echo zero)" "nonzero"
is "rollback is announced" "$(grep -c 'ROLLING BACK' "$TMP/rb.out")" "1"
is "the prior live copy is restored, not left as a symlink" \
   "$(cat "$TMP/home2/.claude/scripts/cost-ledger-capture.sh")" "echo PRIOR"
is "a file that did not exist before is removed again" \
   "$([ -e "$TMP/home2/.claude/scripts/statusline-render.sh" ] && echo present || echo absent)" "absent"

echo
printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
