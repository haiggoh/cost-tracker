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
is()  { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "$3" "$2"; fi; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/home/.claude/scripts" "$TMP/home/.claude/cost-ledger"
# The PRIOR state is what an older release left: a SYMLINK into a version dir. That
# is wiring this plugin made, so it is replaced (and backed up) — as opposed to a
# plain file, which is someone else's and is refused. The fixture below stands in
# for the old install, and its target is the file the backup assertions read back.
PRIOR="$TMP/prior-install/bin"
mkdir -p "$PRIOR"
printf '#!/bin/sh\necho OLD_LIVE_COPY\n' > "$PRIOR/budget-tally.py"
chmod 600 "$PRIOR/budget-tally.py"
ln -s "$PRIOR/budget-tally.py" "$TMP/home/.claude/scripts/budget-tally.py"
# The exact 2026-09-23 break: statusLine calls the RENDERER, bypassing capture.
printf '%s\n' '{"model":"x","statusLine":{"type":"command","command":"sh /somewhere/statusline-render.sh","padding":0}}' \
    > "$TMP/home/.claude/settings.json"
chmod 600 "$TMP/home/.claude/settings.json"
# The statusLine target is the VERSIONLESS scripts path: the entry points there are
# shims that resolve per call, so settings.json never names a version directory.
WANT="sh $TMP/home/.claude/scripts/cost-ledger-capture.sh"

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
   "$([ -L "$TMP/home/.claude/scripts/budget-tally.py" ] && echo symlink || echo plainfile)" "symlink"

# --- dry run must change NOTHING ---------------------------------------------
HOME="$TMP/home" bash "$PLUGIN/install/wire-statusline.sh" --dry-run > "$TMP/dry.out" 2>&1
is "dry run exits 0" "$?" "0"
is "dry run names the bypassed statusLine" "$(grep -c 'BYPASSED' "$TMP/dry.out")" "1"
is "dry run leaves settings.json alone" \
   "$(jq -r .statusLine.command "$TMP/home/.claude/settings.json")" "sh /somewhere/statusline-render.sh"
is "dry run leaves the prior symlink alone" \
   "$([ -L "$TMP/home/.claude/scripts/budget-tally.py" ] && echo symlink || echo plainfile)" "symlink"
is "dry run says DRY RUN" \
   "$(grep -c 'DRY RUN' "$TMP/dry.out")" "1"
is "dry run creates no backup dir" \
   "$(ls -d "$TMP/home/.claude/backups"/cost-tracker-wire-* 2>/dev/null | wc -l | tr -d ' ')" "0"

# --- no arguments = DO the work (not help, not a dry run) ---------------------
HOME="$TMP/home" bash "$PLUGIN/install/wire-statusline.sh" > "$TMP/apply.out" 2>&1
is "a bare run exits 0" "$?" "0"
is "a bare run repoints statusLine at the versionless scripts path" \
   "$(jq -r .statusLine.command "$TMP/home/.claude/settings.json")" "$WANT"
is "other statusLine keys survive" "$(jq -r .statusLine.padding "$TMP/home/.claude/settings.json")" "0"
is "unrelated settings survive" "$(jq -r .model "$TMP/home/.claude/settings.json")" "x"
is "settings.json keeps mode 600" "$(ls -l "$TMP/home/.claude/settings.json" | cut -c1-10)" "-rw-------"
for f in cost-ledger-capture.sh statusline-render.sh budget-tally.py cost-tracker; do
    is "apply writes a SHIM (not a symlink) for $f" \
       "$([ -L "$TMP/home/.claude/scripts/$f" ] && echo symlink || echo regular)" "regular"
    is "the $f shim carries the haiggoh-shim header" \
       "$(head -n 3 "$TMP/home/.claude/scripts/$f" | grep -c '^# haiggoh-shim v1 plugin=cost-tracker target=bin/')" "1"
    is "the $f shim is executable" \
       "$([ -x "$TMP/home/.claude/scripts/$f" ] && echo yes || echo no)" "yes"
done
BK="$(find "$TMP/home/.claude/backups" -maxdepth 1 -name 'cost-tracker-wire-*' 2>/dev/null | head -1)"
is "apply backs up the prior symlink's target" \
   "$(grep -c 'echo OLD_LIVE_COPY' "$BK/budget-tally.py" 2>/dev/null)" "1"
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
cp "$PLUGIN/install/wire-statusline.sh" "$PLUGIN/install/shim-template.sh" "$FAKE/install/"
cp "$PLUGIN/bin/statusline-render.sh" "$PLUGIN/bin/budget-tally.py" "$FAKE/bin/"
# a capture script that renders NOTHING is exactly the failure that must never be
# left wired: a blank status line with no error.
printf '#!/bin/sh\ncat > /dev/null\nexit 0\n' > "$FAKE/bin/cost-ledger-capture.sh"
chmod +x "$FAKE/bin/cost-ledger-capture.sh"
mkdir -p "$TMP/home2/.claude/scripts" "$TMP/prior2/bin"
# The prior state is a SYMLINK into a version dir: wiring this plugin made, so it is
# replaced rather than refused. A plain file here would be FOREIGN and the installer
# would (correctly) refuse before ever reaching the verification this case is about.
printf 'echo PRIOR\n' > "$TMP/prior2/bin/cost-ledger-capture.sh"
ln -s "$TMP/prior2/bin/cost-ledger-capture.sh" "$TMP/home2/.claude/scripts/cost-ledger-capture.sh"
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
cp "$PLUGIN/install/wire-statusline.sh" "$PLUGIN/install/shim-template.sh" "$FAKE2/install/"
cp "$PLUGIN/bin/statusline-render.sh" "$PLUGIN/bin/budget-tally.py" "$FAKE2/bin/"
printf '#!/bin/sh\ncat > /dev/null\necho looks-fine\n' > "$FAKE2/bin/cost-ledger-capture.sh"
mkdir -p "$TMP/home4/.claude"
echo '{"statusLine":{"type":"command","command":"sh /prior.sh"}}' > "$TMP/home4/.claude/settings.json"
HOME="$TMP/home4" bash "$FAKE2/install/wire-statusline.sh" > "$TMP/rb2.out" 2>&1
is "renders-but-captures-nothing fails verification" "$([ $? -ne 0 ] && echo nonzero || echo zero)" "nonzero"
is "and says the ledger was not written" "$(grep -c 'ledger written=no' "$TMP/rb2.out")" "1"
is "and restores settings.json" "$(jq -r .statusLine.command "$TMP/home4/.claude/settings.json")" "sh /prior.sh"

# ---------------------------------------------------------------------------
printf '\n== shims, not symlinks: an update lands with no re-wiring ==\n'

# THE DISCRIMINANT. A symlink froze the version at install time: the entry point
# kept exec'ing the old copy until somebody re-ran the installer by hand. A shim
# resolves on every call, so pointing the registry at a NEW version is the whole
# update. This is the case a versioned-path installer fails.
SHOME="$TMP/shim"; mkdir -p "$SHOME/.claude/scripts" "$SHOME/.claude/cost-ledger"
PDIR="$TMP/plugins"
mkver() { # mkver <version>
    mkdir -p "$PDIR/cache/haiggoh/cost-tracker/$1/bin" "$PDIR/cache/haiggoh/cost-tracker/$1/.claude-plugin" \
             "$PDIR/cache/haiggoh/cost-tracker/$1/install"
    printf '{"name":"cost-tracker","version":"%s"}\n' "$1" \
        > "$PDIR/cache/haiggoh/cost-tracker/$1/.claude-plugin/plugin.json"
    # An installed copy ships its own installer, its own template, and the REAL bin/
    # set: the installer's post-wire verification runs the capture wrapper, which
    # resolves its renderer relative to itself, so a partial bin/ would fail
    # verification for the wrong reason.
    cp "$PLUGIN/install/shim-template.sh" "$PDIR/cache/haiggoh/cost-tracker/$1/install/"
    cp "$PLUGIN/bin/"* "$PDIR/cache/haiggoh/cost-tracker/$1/bin/"
    chmod +x "$PDIR/cache/haiggoh/cost-tracker/$1/bin/"*
}

# WHICH copy did the shim resolve to? --which prints the target path, and the version
# is in that path — no need to execute anything, so the real bin/ can stay real.
shim_which() { HOME="$SHOME" CLAUDE_PLUGINS_DIR="$PDIR" /bin/sh "$SHOME/.claude/scripts/budget-tally.py" --which; }
mkver 0.18.4
mkver 0.19.0
write_registry() { printf '%s' '{"plugins":{"cost-tracker@haiggoh":[{"scope":"user","installPath":"'"$1"'"}]}}' \
    > "$PDIR/installed_plugins.json"; }
write_registry "$PDIR/cache/haiggoh/cost-tracker/0.18.4"

HOME="$SHOME" CLAUDE_PLUGINS_DIR="$PDIR" bash "$PLUGIN/install/wire-statusline.sh" > "$TMP/shim.out" 2>&1
is "shim install exits 0" "$?" "0"
is "statusLine points at the VERSIONLESS scripts path" \
   "$(jq -r .statusLine.command "$SHOME/.claude/settings.json")" "sh $SHOME/.claude/scripts/cost-ledger-capture.sh"
for f in cost-ledger-capture.sh statusline-render.sh budget-tally.py cost-tracker; do
    is "$f is a shim, not a symlink" \
       "$([ -L "$SHOME/.claude/scripts/$f" ] && echo symlink || echo regular)" "regular"
    is "the $f shim carries the haiggoh-shim header" \
       "$(head -n 3 "$SHOME/.claude/scripts/$f" | grep -c '^# haiggoh-shim v1 plugin=cost-tracker target=bin/')" "1"
    is "the $f shim is executable" \
       "$([ -x "$SHOME/.claude/scripts/$f" ] && echo yes || echo no)" "yes"
done
is "--which prints the resolved target" \
   "$(HOME="$SHOME" CLAUDE_PLUGINS_DIR="$PDIR" /bin/sh "$SHOME/.claude/scripts/budget-tally.py" --which)" \
   "$PDIR/cache/haiggoh/cost-tracker/0.18.4/bin/budget-tally.py"

# THE UPDATE: repoint the registry at 0.19.0 and re-run nothing. The shim must now
# resolve to the new version — the whole reason it exists.
write_registry "$PDIR/cache/haiggoh/cost-tracker/0.19.0"
is "the installed shim now resolves to the NEW version without re-wiring" \
   "$(shim_which)" "$PDIR/cache/haiggoh/cost-tracker/0.19.0/bin/budget-tally.py"

# A stale registry entry (the dir is gone) falls through to the highest cache dir.
write_registry "$PDIR/cache/haiggoh/cost-tracker/0.17.0-does-not-exist"
is "a stale installPath falls through to the highest cache dir" \
   "$(shim_which)" "$PDIR/cache/haiggoh/cost-tracker/0.19.0/bin/budget-tally.py"

# --- the override routes to a checkout -----------------------------------------
CO="$TMP/checkout"; mkdir -p "$CO/bin" "$CO/.claude-plugin"
printf '{"name":"cost-tracker","version":"dev"}\n' > "$CO/.claude-plugin/plugin.json"
printf '#!/bin/sh\necho checkout\n' > "$CO/bin/budget-tally.py"; chmod +x "$CO/bin/budget-tally.py"
is "the override routes the shim to the checkout" \
   "$(HOME="$SHOME" CLAUDE_PLUGINS_DIR="$PDIR" COST_TRACKER_PLUGIN="$CO" /bin/sh "$SHOME/.claude/scripts/budget-tally.py" 2>/dev/null)" "checkout"
is "an invalid override fails loudly through the shim" \
   "$(HOME="$SHOME" CLAUDE_PLUGINS_DIR="$PDIR" COST_TRACKER_PLUGIN="$TMP/nope" /bin/sh "$SHOME/.claude/scripts/budget-tally.py" 2>&1 >/dev/null | grep -c 'COST_TRACKER_PLUGIN')" "1"

# --- idempotence, backups, and the foreign-file refusal -------------------------
HOME="$SHOME" CLAUDE_PLUGINS_DIR="$PDIR" bash "$PLUGIN/install/wire-statusline.sh" > "$TMP/rerun.out" 2>&1
is "a re-run exits 0" "$?" "0"
is "a re-run reports nothing to do" "$(grep -c 'Nothing to do' "$TMP/rerun.out")" "1"
is "a re-run writes no NEW backup" \
   "$(find "$SHOME/.claude/backups" -maxdepth 1 -name 'cost-tracker-wire-*' 2>/dev/null | wc -l | tr -d ' ')" "1"

# a pre-existing SYMLINK at the path is backed up, then replaced by the shim
SLINK="$TMP/prior-link"; mkdir -p "$SLINK/.claude/scripts" "$SLINK/target/bin"
printf '#!/bin/sh\necho OLD\n' > "$SLINK/target/bin/budget-tally.py"
ln -s "$SLINK/target/bin/budget-tally.py" "$SLINK/.claude/scripts/budget-tally.py"
HOME="$SLINK" CLAUDE_PLUGINS_DIR="$PDIR" bash "$PLUGIN/install/wire-statusline.sh" > /dev/null 2>&1
is "a replaced symlink exits 0" "$?" "0"
SBK="$(find "$SLINK/.claude/backups" -maxdepth 1 -name 'cost-tracker-wire-*' 2>/dev/null | head -1)"
is "the replaced symlink's target is backed up" \
   "$(grep -c 'echo OLD' "$SBK/budget-tally.py" 2>/dev/null | awk '{print ($1>0)?"yes":"no"}')" "yes"
is "and the path now carries a shim" \
   "$([ -L "$SLINK/.claude/scripts/budget-tally.py" ] && echo symlink || echo regular)" "regular"

# a FOREIGN regular file at the path is refused unless --force
FHOME="$TMP/foreign"; mkdir -p "$FHOME/.claude/scripts"
printf 'echo mine\n' > "$FHOME/.claude/scripts/budget-tally.py"
HOME="$FHOME" CLAUDE_PLUGINS_DIR="$PDIR" bash "$PLUGIN/install/wire-statusline.sh" > "$TMP/foreign.out" 2>&1
is "a foreign file is refused (exit 1)" "$?" "1"
is "the refusal names the path and the way out" "$(grep -c '^REFUSING: scripts/' "$TMP/foreign.out")" "1"
is "the refusal offers --force" "$(grep -c -- '--force' "$TMP/foreign.out")" "1"
is "the foreign file is left untouched" "$(cat "$FHOME/.claude/scripts/budget-tally.py")" "echo mine"
HOME="$FHOME" CLAUDE_PLUGINS_DIR="$PDIR" bash "$PLUGIN/install/wire-statusline.sh" --force > /dev/null 2>&1
is "--force replaces it" "$?" "0"
is "and the path now carries a shim" \
   "$([ -L "$FHOME/.claude/scripts/budget-tally.py" ] && echo symlink || echo regular)" "regular"

# --- a dry run writes nothing at all --------------------------------------------
DHOME="$TMP/dryshim"; mkdir -p "$DHOME/.claude/scripts" "$DHOME/.claude"
printf '%s\n' '{"statusLine":{"command":"sh /prior.sh"}}' > "$DHOME/.claude/settings.json"
BEFORE="$(cat "$DHOME/.claude/settings.json")$(find "$DHOME/.claude/scripts" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l | tr -d ' ')"
HOME="$DHOME" CLAUDE_PLUGINS_DIR="$PDIR" bash "$PLUGIN/install/wire-statusline.sh" --dry-run > "$TMP/dryshim.out" 2>&1
AFTER="$(cat "$DHOME/.claude/settings.json")$(find "$DHOME/.claude/scripts" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l | tr -d ' ')"
is "dry run writes nothing" "$BEFORE" "$AFTER"

echo
printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
