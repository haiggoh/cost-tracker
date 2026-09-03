#!/usr/bin/env bash
# wire-statusline.sh — point THIS machine's statusline chain at the plugin's copies.
#
# WHY A SCRIPT AND NOT A PLUGIN HOOK: a Claude Code session may have exactly ONE
# status line, so a plugin that claimed it would fight whatever the user already
# runs. This plugin therefore ships the wrapper PATTERN and the machine wires it
# in — deliberately, once, visibly.
#
# WHAT IT CHANGES: three files under ~/.claude/scripts/ become symlinks into this
# plugin. settings.json is NOT touched: its statusLine already points at
# cost-ledger-capture.sh, and after wiring that path resolves here.
#
#   ~/.claude/scripts/cost-ledger-capture.sh -> <plugin>/bin/cost-ledger-capture.sh
#   ~/.claude/scripts/statusline-render.sh   -> <plugin>/bin/statusline-render.sh
#   ~/.claude/scripts/budget-tally.py        -> <plugin>/bin/budget-tally.py
#
# SAFETY: dry-run unless --apply. Every replaced file is copied to a timestamped
# backup dir FIRST, with its mode preserved. After wiring, the chain is exercised
# with a sample statusLine payload; if it stops rendering, every change is rolled
# back before the script exits non-zero. A silent half-wired statusline is the one
# outcome worse than not wiring at all.
set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN="$(cd -P "$HERE/.." && pwd)"
SCRIPTS="$HOME/.claude/scripts"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$HOME/.claude/backups/cost-tracker-wire-$STAMP"

APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

FILES="cost-ledger-capture.sh statusline-render.sh budget-tally.py"

say() { printf '%s\n' "$*"; }

# --- differences worth knowing about before overwriting anything --------------
say "plugin:  $PLUGIN"
say "scripts: $SCRIPTS"
say ""
NEED=0
for f in $FILES; do
    live="$SCRIPTS/$f"
    mine="$PLUGIN/bin/$f"
    if [ -L "$live" ] && [ "$(readlink "$live")" = "$mine" ]; then
        say "  already wired   $f"
        continue
    fi
    NEED=1
    if [ ! -e "$live" ]; then
        say "  MISSING live    $f (will be created as a symlink)"
    elif cmp -s "$live" "$mine"; then
        say "  identical       $f (plain file -> symlink, no content change)"
    else
        say "  DIFFERS         $f — the live copy is not what this plugin ships:"
        diff -u "$live" "$mine" | sed -n '1,12p' | sed 's/^/      /'
        say "      (full diff: diff -u $live $mine)"
    fi
done

if [ "$NEED" = "0" ]; then
    say ""
    say "Nothing to do — the chain already resolves into this plugin."
    exit 0
fi

if [ "$APPLY" = "0" ]; then
    say ""
    say "DRY RUN. Re-run with --apply to back up the live copies to"
    say "  $BACKUP"
    say "and replace them with symlinks into the plugin."
    exit 0
fi

# --- apply --------------------------------------------------------------------
mkdir -p "$BACKUP" || { say "cannot create $BACKUP"; exit 1; }
CHANGED=""
for f in $FILES; do
    live="$SCRIPTS/$f"
    mine="$PLUGIN/bin/$f"
    [ -L "$live" ] && [ "$(readlink "$live")" = "$mine" ] && continue
    if [ -e "$live" ] || [ -L "$live" ]; then
        # -p keeps mode and timestamps: one of these is a 600 file on some setups,
        # and a backup that widens permissions is its own incident.
        cp -p "$live" "$BACKUP/$f" || { say "backup failed for $f"; exit 1; }
    fi
    ln -sfn "$mine" "$live" || { say "symlink failed for $f"; exit 1; }
    CHANGED="$CHANGED $f"
    say "  wired  $f"
done

# --- verify by OUTCOME, not by absence of error -------------------------------
SAMPLE='{"session_id":"wire-check-0000-0000-0000-000000000000","cost":{"total_cost_usd":0},'
SAMPLE="$SAMPLE"'"model":{"display_name":"WireCheck"},"workspace":{"current_dir":"'"$HOME"'"}}'
OUT="$(printf '%s' "$SAMPLE" | sh "$SCRIPTS/cost-ledger-capture.sh" 2>/dev/null)"
RC=$?
if [ "$RC" != "0" ] || [ -z "$OUT" ]; then
    say ""
    say "VERIFY FAILED (rc=$RC, output empty=$([ -z "$OUT" ] && echo yes || echo no)) — ROLLING BACK."
    for f in $CHANGED; do
        if [ -e "$BACKUP/$f" ]; then
            rm -f "$SCRIPTS/$f" && cp -p "$BACKUP/$f" "$SCRIPTS/$f" && say "  restored $f"
        else
            rm -f "$SCRIPTS/$f" && say "  removed  $f (did not exist before)"
        fi
    done
    exit 1
fi
# The check session wrote a real ledger entry; it is not a session, so drop it.
rm -f "$HOME/.claude/cost-ledger/wire-check-0000-0000-0000-000000000000"

say ""
say "VERIFIED — the chain renders:"
printf '%s\n' "$OUT" | sed 's/^/    /'
say ""
say "Backup of the previous copies: $BACKUP"
say "settings.json was not touched; its statusLine path now resolves into the plugin."
say ""
say "Optional: export COST_TRACKER_CAP_USD=40 in your shell profile so reports print a"
say "denominator. Without it, spend is shown with no cap rather than an invented one."
