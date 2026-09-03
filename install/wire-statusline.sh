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
# The verification render went through the REAL capture path, so it wrote a real
# ledger entry AND a real history row under a synthetic session id. Neither is a
# session, and leaving them behind puts a "wire-check" row in every future report —
# which is precisely the kind of fictional entry this plugin quarantines other tools
# for. Remove both. The history log is append-only by policy, not by accident, so it
# is edited here only to delete a line this script itself just wrote, in place, with
# the mode preserved.
SENTINEL="wire-check-0000-0000-0000-000000000000"
rm -f "$HOME/.claude/cost-ledger/$SENTINEL"
HIST="$HOME/.claude/cost-ledger-history.log"
if [ -f "$HIST" ] && grep -q "$SENTINEL" "$HIST"; then
    # NOT `if grep -v ...; then`: grep exits 1 when it prints NOTHING, which is exactly
    # the fresh-install case where the sentinel is the only row in a brand-new history
    # file. Gating on the exit code silently skipped the cleanup precisely there, and a
    # machine with thousands of existing rows would never have shown it. Gate on the
    # temp file, which grep creates either way.
    grep -v "$SENTINEL" "$HIST" > "$HIST.wiretmp" 2>/dev/null
    if [ -f "$HIST.wiretmp" ]; then
        # cat-into-place rather than mv: mv would install a NEW inode at the default
        # umask and silently widen the mode of a file that may be 600.
        cat "$HIST.wiretmp" > "$HIST" && say "  cleaned the verification row out of the history log"
    fi
    rm -f "$HIST.wiretmp"
fi

say ""
say "VERIFIED — the chain renders:"
printf '%s\n' "$OUT" | sed 's/^/    /'
say ""
say "Backup of the previous copies: $BACKUP"
say "settings.json was not touched; its statusLine path now resolves into the plugin."
say ""
say "The status line now carries a today: cloud \$X segment next to the session-lifetime"
say "figure, each naming its own axis. Suppress it with COST_TRACKER_STATUSLINE=0."
say ""
say "The daily cap is LEARNED from the gateway's own refusal message, so no configuration"
say "is normally needed \u2014 run 'cost-tracker cap' to see it and which refusal it came from,"
say "or 'cost-tracker cap --learn' after the cap changes. COST_TRACKER_CAP_USD still"
say "overrides it. With neither, spend prints without a denominator rather than an"
say "invented one."
