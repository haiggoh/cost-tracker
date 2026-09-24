#!/usr/bin/env bash
# wire-statusline.sh — point THIS machine's statusline chain at this plugin checkout.
#
# WHY A SCRIPT AND NOT A PLUGIN HOOK: a Claude Code session may have exactly ONE
# status line, so a plugin that claimed it would fight whatever the user already
# runs. This plugin therefore ships the wrapper PATTERN and the machine wires it
# in — deliberately, visibly, and re-runnably.
#
# WHAT IT CHANGES:
#   1. ~/.claude/settings.json  statusLine.command -> sh <plugin>/bin/cost-ledger-capture.sh
#      DIRECTLY, with no ~/.claude/scripts hop in between. (0.7.8) The earlier design
#      left settings.json alone and relied on a symlink at the old path — so when an
#      overwrite and its "revert" (2026-09-23) pointed statusLine at the RENDERER, the
#      display still looked healthy while nothing was captured, and this script said
#      "already wired" because it only ever looked at the symlinks. The settings target
#      is now the first thing checked, not something assumed.
#   2. three files under ~/.claude/scripts/ become symlinks into this plugin. The
#      statusline no longer goes through them, but the budget-tally hooks and older
#      manual invocations still do, so they stay wired:
#        ~/.claude/scripts/cost-ledger-capture.sh -> <plugin>/bin/cost-ledger-capture.sh
#        ~/.claude/scripts/statusline-render.sh   -> <plugin>/bin/statusline-render.sh
#        ~/.claude/scripts/budget-tally.py        -> <plugin>/bin/budget-tally.py
#
# SAFETY: every replaced file is copied to a timestamped backup dir FIRST, with its
# mode preserved; settings.json is rewritten IN PLACE (cat into the same inode) so a
# 600 file stays 600. After wiring, the configured statusLine command is exercised
# with a sample payload; if it stops rendering or stops capturing, every change is
# rolled back before the script exits non-zero. A silent half-wired statusline is the
# one outcome worse than not wiring at all.
set -uo pipefail

usage() {
    cat <<'EOF'
Usage: wire-statusline.sh [--dry-run] [--help]

Wire Claude Code's statusLine to this cost-tracker checkout: point
~/.claude/settings.json statusLine.command straight at
<plugin>/bin/cost-ledger-capture.sh, and symlink the three ~/.claude/scripts
helpers into the plugin. Then verify the chain renders AND writes a ledger
entry, rolling everything back if it does not.

With no arguments it CHECKS and FIXES (backups first). Safe to re-run: when
everything already points here it changes nothing.

Options:
  --dry-run    report what is wrong and what would change; write nothing
  --apply      accepted for compatibility; same as no arguments
  -h, --help   show this help and exit

Environment:
  HOME         everything is resolved under $HOME/.claude (settings.json,
               scripts/, cost-ledger/, backups/)

Exit status: 0 wired (or already wired, or dry run), 1 wiring or verification
failed (changes rolled back), 2 bad usage.
EOF
}

APPLY=1
for arg in "$@"; do
    case "$arg" in
        -h|--help) usage; exit 0 ;;
        --dry-run|-n) APPLY=0 ;;
        --apply) APPLY=1 ;;
        *) printf 'wire-statusline.sh: unknown argument: %s\n' "$arg" >&2
           printf 'usage: wire-statusline.sh [--dry-run] [--help]\n' >&2
           exit 2 ;;
    esac
done

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN="$(cd -P "$HERE/.." && pwd)"
SCRIPTS="$HOME/.claude/scripts"
SETTINGS="$HOME/.claude/settings.json"
SETTINGS_LOCAL="$HOME/.claude/settings.local.json"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$HOME/.claude/backups/cost-tracker-wire-$STAMP"
WANT_CMD="sh $PLUGIN/bin/cost-ledger-capture.sh"

FILES="cost-ledger-capture.sh statusline-render.sh budget-tally.py"

say() { printf '%s\n' "$*"; }

say "plugin:   $PLUGIN"
say "settings: $SETTINGS"
say "scripts:  $SCRIPTS"
case "$PLUGIN" in
    */.claude/plugins/cache/*)
        say ""
        say "  WARNING  this copy lives in the plugin CACHE, which the next plugin update"
        say "           replaces. Run it from the source checkout so the wiring survives." ;;
esac
say ""

if ! command -v jq >/dev/null 2>&1; then
    say "jq is required to read and edit settings.json"
    exit 1
fi

# --- 1. the settings.json statusLine target ------------------------------------
NEED_SETTINGS=0
CUR_CMD=""
if [ -f "$SETTINGS" ]; then
    CUR_CMD="$(jq -r '.statusLine.command // empty' "$SETTINGS" 2>/dev/null)" || {
        say "  UNREADABLE settings.json is not valid JSON — refusing to touch it"
        exit 1
    }
fi
if [ "$CUR_CMD" = "$WANT_CMD" ]; then
    say "  already wired   settings.json statusLine"
else
    NEED_SETTINGS=1
    case "$CUR_CMD" in
        "")  say "  MISSING         settings.json has no statusLine.command" ;;
        *statusline-render.sh*)
             say "  BYPASSED        settings.json statusLine calls the RENDERER directly:"
             say "                    $CUR_CMD"
             say "                  it renders, but NOTHING is captured, so every today/saved"
             say "                  figure built from the ledger reads \$0" ;;
        *cost-ledger-capture.sh*)
             say "  INDIRECT        settings.json statusLine goes through another path:"
             say "                    $CUR_CMD" ;;
        *)   say "  FOREIGN         settings.json statusLine is not the capture wrapper:"
             say "                    $CUR_CMD"
             say "                  (e.g. regenerated by another tool) — nothing is captured" ;;
    esac
    say "                  -> $WANT_CMD"
fi
# settings.local.json OUTRANKS settings.json, so a statusLine there silently wins.
# Reported, never edited: it is the user's override layer.
if [ -f "$SETTINGS_LOCAL" ]; then
    LOCAL_CMD="$(jq -r '.statusLine.command // empty' "$SETTINGS_LOCAL" 2>/dev/null)"
    if [ -n "$LOCAL_CMD" ] && [ "$LOCAL_CMD" != "$WANT_CMD" ]; then
        say "  WARNING         settings.local.json sets its own statusLine, which OVERRIDES"
        say "                  settings.json: $LOCAL_CMD"
        say "                  (not edited — remove or repoint it yourself)"
    fi
fi

# --- 2. the ~/.claude/scripts symlinks ------------------------------------------
NEED_LINKS=0
for f in $FILES; do
    live="$SCRIPTS/$f"
    mine="$PLUGIN/bin/$f"
    if [ -L "$live" ] && [ "$(readlink "$live")" = "$mine" ]; then
        say "  already wired   scripts/$f"
        continue
    fi
    NEED_LINKS=1
    if [ ! -e "$live" ]; then
        say "  MISSING live    scripts/$f (will be created as a symlink)"
    elif cmp -s "$live" "$mine"; then
        say "  identical       scripts/$f (plain file -> symlink, no content change)"
    else
        say "  DIFFERS         scripts/$f — the live copy is not what this plugin ships:"
        diff -u "$live" "$mine" | sed -n '1,12p' | sed 's/^/      /'
        say "      (full diff: diff -u $live $mine)"
    fi
done

if [ "$NEED_SETTINGS" = "0" ] && [ "$NEED_LINKS" = "0" ]; then
    say ""
    say "Nothing to do — the chain already resolves into this plugin."
    exit 0
fi

if [ "$APPLY" = "0" ]; then
    say ""
    say "DRY RUN — nothing written. Run without --dry-run to back up to"
    say "  $BACKUP"
    say "and apply the changes above."
    exit 0
fi

# --- apply --------------------------------------------------------------------
mkdir -p "$BACKUP" "$SCRIPTS" || { say "cannot create $BACKUP"; exit 1; }
CHANGED=""
SETTINGS_CHANGED=0
SETTINGS_EXISTED=0

rollback() {
    for f in $CHANGED; do
        if [ -e "$BACKUP/$f" ]; then
            rm -f "$SCRIPTS/$f" && cp -p "$BACKUP/$f" "$SCRIPTS/$f" && say "  restored scripts/$f"
        else
            rm -f "$SCRIPTS/$f" && say "  removed  scripts/$f (did not exist before)"
        fi
    done
    if [ "$SETTINGS_CHANGED" = "1" ]; then
        if [ "$SETTINGS_EXISTED" = "1" ]; then
            cat "$BACKUP/settings.json" > "$SETTINGS" && say "  restored settings.json"
        else
            rm -f "$SETTINGS" && say "  removed  settings.json (did not exist before)"
        fi
    fi
}

if [ "$NEED_LINKS" = "1" ]; then
    for f in $FILES; do
        live="$SCRIPTS/$f"
        mine="$PLUGIN/bin/$f"
        [ -L "$live" ] && [ "$(readlink "$live")" = "$mine" ] && continue
        if [ -e "$live" ] || [ -L "$live" ]; then
            # -p keeps mode and timestamps: one of these is a 600 file on some setups,
            # and a backup that widens permissions is its own incident.
            cp -p "$live" "$BACKUP/$f" || { say "backup failed for $f"; rollback; exit 1; }
        fi
        ln -sfn "$mine" "$live" || { say "symlink failed for $f"; rollback; exit 1; }
        CHANGED="$CHANGED $f"
        say "  wired  scripts/$f"
    done
fi

if [ "$NEED_SETTINGS" = "1" ]; then
    if [ -f "$SETTINGS" ]; then
        SETTINGS_EXISTED=1
        cp -p "$SETTINGS" "$BACKUP/settings.json" || { say "backup failed for settings.json"; rollback; exit 1; }
        SRC="$SETTINGS"
    else
        SRC=/dev/null
    fi
    # Keep any other statusLine keys (padding, type) the user already has.
    NEW="$( { [ "$SRC" = /dev/null ] && echo '{}' || cat "$SRC"; } \
        | jq --arg cmd "$WANT_CMD" '.statusLine = ((.statusLine // {}) + {type: "command", command: $cmd})')" \
        || { say "could not compute the new settings.json"; rollback; exit 1; }
    # cat-into-place, not mv: mv installs a NEW inode at the default umask and would
    # widen a 600 settings.json to 644.
    [ "$SETTINGS_EXISTED" = "0" ] && ( umask 077; : > "$SETTINGS" )
    printf '%s\n' "$NEW" > "$SETTINGS" || { say "could not write settings.json"; rollback; exit 1; }
    SETTINGS_CHANGED=1
    say "  wired  settings.json statusLine"
fi

# --- verify by OUTCOME, not by absence of error -------------------------------
# Run the command settings.json NOW holds — the thing Claude Code will actually run —
# and require BOTH a rendered line AND a ledger entry. Output alone is exactly what the
# 2026-09-23 break produced.
SENTINEL="wire-check-0000-0000-0000-000000000000"
SAMPLE='{"session_id":"'"$SENTINEL"'","cost":{"total_cost_usd":0},'
SAMPLE="$SAMPLE"'"model":{"display_name":"WireCheck"},"workspace":{"current_dir":"'"$HOME"'"}}'
RUN_CMD="$(jq -r '.statusLine.command // empty' "$SETTINGS" 2>/dev/null)"
OUT="$(printf '%s' "$SAMPLE" | sh -c "$RUN_CMD" 2>/dev/null)"
RC=$?
CAPTURED=no
[ -f "$HOME/.claude/cost-ledger/$SENTINEL" ] && CAPTURED=yes

# The verification render went through the REAL capture path, so it wrote a real
# ledger entry AND a real history row under a synthetic session id. Neither is a
# session, and leaving them behind puts a "wire-check" row in every future report —
# which is precisely the kind of fictional entry this plugin quarantines other tools
# for. Remove both, pass or fail. The history log is append-only by policy, so it is
# edited here only to delete a line this script itself just wrote, with the mode kept.
rm -f "$HOME/.claude/cost-ledger/$SENTINEL"
HIST="$HOME/.claude/cost-ledger-history.log"
if [ -f "$HIST" ] && grep -q "$SENTINEL" "$HIST"; then
    # NOT `if grep -v ...; then`: grep exits 1 when it prints NOTHING, which is exactly
    # the fresh-install case where the sentinel is the only row in a brand-new history
    # file. Gate on the temp file, which grep creates either way.
    grep -v "$SENTINEL" "$HIST" > "$HIST.wiretmp" 2>/dev/null
    if [ -f "$HIST.wiretmp" ]; then
        cat "$HIST.wiretmp" > "$HIST" && say "  cleaned the verification row out of the history log"
    fi
    rm -f "$HIST.wiretmp"
fi

if [ "$RC" != "0" ] || [ -z "$OUT" ] || [ "$CAPTURED" != "yes" ]; then
    say ""
    say "VERIFY FAILED (rc=$RC, output empty=$([ -z "$OUT" ] && echo yes || echo no), ledger written=$CAPTURED) — ROLLING BACK."
    rollback
    exit 1
fi

say ""
say "VERIFIED — the statusLine command renders and writes the ledger:"
printf '%s\n' "$OUT" | sed 's/^/    /'
say ""
say "Backup of everything replaced: $BACKUP"
say "Running sessions pick up the new statusLine on their next render."
say ""
say "If another tool rewrites statusLine again, re-run this script: it detects that and"
say "repoints it. Suppress the today segment with COST_TRACKER_STATUSLINE=0."
