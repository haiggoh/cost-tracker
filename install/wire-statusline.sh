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
#   2. four files under ~/.claude/scripts/ become shims into this plugin. The
#      statusline no longer goes through them, but the budget-tally hooks and older
#      manual invocations still do, so they stay wired:
#        ~/.claude/scripts/cost-ledger-capture.sh -> <plugin>/bin/cost-ledger-capture.sh
#        ~/.claude/scripts/statusline-render.sh   -> <plugin>/bin/statusline-render.sh
#        ~/.claude/scripts/budget-tally.py        -> <plugin>/bin/budget-tally.py
#        ~/.claude/scripts/cost-tracker           -> <plugin>/bin/cost-tracker
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
  --force      replace a FOREIGN file at a scripts/ path (one this plugin did not
               write). Without it such a file is refused, not overwritten.
  -h, --help   show this help and exit

Environment:
  HOME         everything is resolved under $HOME/.claude (settings.json,
               scripts/, cost-ledger/, backups/)
  COST_TRACKER_PLUGIN  force this directory as the plugin root (developer
                       override; must contain .claude-plugin/plugin.json).
                       Skips the cache search and uses this path directly.

Exit status: 0 wired (or already wired, or dry run), 1 wiring or verification
failed (changes rolled back), 2 bad usage.
EOF
}

APPLY=1
FORCE=0
for arg in "$@"; do
    case "$arg" in
        -h|--help) usage; exit 0 ;;
        --dry-run|-n) APPLY=0 ;;
        --apply) APPLY=1 ;;
        --force) FORCE=1 ;;
        *) printf 'wire-statusline.sh: unknown argument: %s\n' "$arg" >&2
           printf 'usage: wire-statusline.sh [--dry-run] [--force] [--help]\n' >&2
           exit 2 ;;
    esac
done

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Resolve where this plugin is installed, in the resolver's order:
#   1. $COST_TRACKER_PLUGIN override (developer/sysadmin pin)
#   2. the installPath recorded in installed_plugins.json
#   3. the highest-versioned entry in the plugin cache (no hardcoded version)
# Default to THIS checkout first: the resolver exits 1 when none of those resolve
# (a fresh clone has no registry entry), and an unset $PLUGIN under `set -u` would
# kill this script before it could wire anything.
PLUGIN="$(cd -P "$HERE/.." && pwd)"
RESOLVER="$HERE/../bin/cost-tracker-resolve.sh"
if [ -x "$RESOLVER" ]; then
    RESOLVED="$(bash "$RESOLVER" 2>/dev/null)" && PLUGIN="$RESOLVED"
fi
SCRIPTS="$HOME/.claude/scripts"
SETTINGS="$HOME/.claude/settings.json"
SETTINGS_LOCAL="$HOME/.claude/settings.local.json"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$HOME/.claude/backups/cost-tracker-wire-$STAMP"
# The statusLine target is the VERSIONLESS scripts path, never a version dir: the
# entry points there are shims that resolve their plugin dir per call, so a plugin
# update lands without re-wiring and settings.json never names a stale version.
WANT_CMD="sh $SCRIPTS/cost-ledger-capture.sh"
# Claude Code runs the command through a shell, so a $HOME with a space (or any shell
# metacharacter) splits the path and the statusLine exits 127. Quote only then: for
# every ordinary path the command stays byte-identical, so no existing install is
# reported as changed.
case "$SCRIPTS" in
    *[!A-Za-z0-9_./-]*) WANT_CMD="sh \"$SCRIPTS/cost-ledger-capture.sh\"" ;;
esac

FILES="cost-ledger-capture.sh statusline-render.sh budget-tally.py cost-tracker"
SHIM_TEMPLATE="$PLUGIN/install/shim-template.sh"

# Render this file's shim body for one target. The header line is how a later run
# recognises its own work, so it must stay exactly this shape.
render_shim() {
    /usr/bin/sed -e 's|@PLUGIN@|cost-tracker|g' \
                 -e 's|@OVERRIDE@|COST_TRACKER_PLUGIN|g' \
                 -e "s|@REL@|bin/$1|g" "$SHIM_TEMPLATE"
}
is_our_shim() {  # $1 = the live path
    # The header is the SECOND line: line 1 is the shebang, which must stay there
    # for the shim to be executable.
    [ -f "$1" ] && head -n 3 "$1" 2>/dev/null | /usr/bin/grep -q '^# haiggoh-shim v1 plugin=cost-tracker target='
}

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

# --- 1b. hooks that run a shim through python3 ---------------------------------
# budget-tally.py is a #!/bin/sh SHIM, not a python file. Before the shims it was a
# symlink to python, and users registered it as `python3 ~/.claude/scripts/budget-
# tally.py` in their own Stop/SessionStart hooks. Replacing the symlink without
# touching those hooks turns every turn's end into a python SyntaxError on shell code
# (2026-10-26) — non-blocking, so it looks like the session stopped on an error while
# the budget tally silently never runs. The shim is executable and execs its python
# target itself, so the repair is only to drop the `python3 ` prefix.
#
# Matches only an invocation of THIS scripts dir's budget-tally.py (as ~, $HOME,
# ${HOME} or the absolute path), at the start of a command or after ; & |. A python3
# hook on another script, or a command that merely mentions budget-tally, is not ours.
# The path may be QUOTED ("…" or '…'): a user whose $HOME holds a space had no other
# way to write a working hook, so the quotes are part of the path and are kept as-is.
SCRIPTS_RE="$(printf '%s' "$SCRIPTS" | /usr/bin/sed 's/[][\.*^$(){}?+|/]/\\&/g')"
SHIM_RE='(?:~/\.claude/scripts|\$HOME/\.claude/scripts|\$\{HOME\}/\.claude/scripts|'"$SCRIPTS_RE"')/budget-tally\.py'
SHIM_TOKEN_RE='(?:"'"$SHIM_RE"'"|'"'$SHIM_RE'"'|'"$SHIM_RE"')'
HOOK_RE='(?<pre>^|[;&|]\s*)python3?\s+(?<path>'"$SHIM_TOKEN_RE"')(?=\s|$|[;&|])'
# "<event><TAB><command>" per broken hook in file $1; nothing when the file is absent,
# unreadable as JSON, or has none.
broken_hooks() {
    [ -f "$1" ] || return 0
    jq -r --arg re "$HOOK_RE" '(.hooks // {}) | to_entries[]? | .key as $ev
        | (.value // [])[]? | (.hooks // [])[]? | (.command // empty)
        | select(type == "string" and test($re)) | "\($ev)\t\(.)"' "$1" 2>/dev/null
}
NEED_HOOKS=0
HOOKS_FOUND="$(broken_hooks "$SETTINGS")"
if [ -n "$HOOKS_FOUND" ]; then
    NEED_HOOKS=1
    while IFS="$(printf '\t')" read -r ev cmd; do
        say "  BROKEN HOOK     settings.json ($ev): $cmd"
    done <<EOF
$HOOKS_FOUND
EOF
    say "                  python3 cannot run the shim (it is /bin/sh) — the hook errors on every"
    say "                  run. -> drop the python3 prefix; the shim execs python itself"
else
    say "  already wired   settings.json hooks (no python3-prefixed shim calls)"
fi
# settings.local.json outranks settings.json and is the user's override layer:
# reported, never edited — the same policy as its statusLine above.
LOCAL_HOOKS="$(broken_hooks "$SETTINGS_LOCAL")"
if [ -n "$LOCAL_HOOKS" ]; then
    while IFS="$(printf '\t')" read -r ev cmd; do
        say "  BROKEN HOOK     settings.local.json ($ev): $cmd"
    done <<EOF
$LOCAL_HOOKS
EOF
    say "                  (not edited — drop the python3 prefix yourself)"
fi

# --- 2. the ~/.claude/scripts shims --------------------------------------------
# A shim is a generated file, not a symlink: it resolves its plugin dir per call, so
# it keeps working across a plugin update. A pre-existing symlink is a wiring this
# plugin (or an older version of it) made, so it is replaced and backed up like any
# other change; a plain file is not, and is refused unless --force says otherwise.
NEED_LINKS=0
FOREIGN=""
for f in $FILES; do
    live="$SCRIPTS/$f"
    mine="$PLUGIN/bin/$f"
    if is_our_shim "$live"; then
        say "  already wired   scripts/$f (shim)"
        continue
    fi
    NEED_LINKS=1
    if [ -L "$live" ]; then
        say "  REPLACE symlink scripts/$f -> $(readlink "$live")"
    elif [ -e "$live" ]; then
        say "  FOREIGN         scripts/$f is a plain file this plugin did not write:"
        diff -u "$live" "$mine" | sed -n '1,8p' | sed 's/^/      /'
        say "      (full diff: diff -u $live $mine)"
        FOREIGN="$FOREIGN $f"
    else
        say "  MISSING live    scripts/$f (will be created as a shim)"
    fi
done

if [ -n "$FOREIGN" ] && [ "$FORCE" != "1" ]; then
    say ""
    say "REFUSING: scripts/ has files this plugin did not write:$FOREIGN"
    say "Overwriting a file someone else owns is not this script's call to make."
    say "Re-run with --force to back each one up (timestamped) and replace it with a"
    say "shim, or move them aside first."
    exit 1
fi

if [ "$NEED_SETTINGS" = "0" ] && [ "$NEED_LINKS" = "0" ] && [ "$NEED_HOOKS" = "0" ]; then
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
        # Already our shim for this exact target: nothing to write.
        is_our_shim "$live" && continue
        if [ -e "$live" ] || [ -L "$live" ]; then
            # -p keeps mode and timestamps: one of these is a 600 file on some setups,
            # and a backup that widens permissions is its own incident.
            cp -p "$live" "$BACKUP/$f" || { say "backup failed for $f"; rollback; exit 1; }
        fi
        # Write the shim, do not symlink it: a symlink would freeze the version this
        # entry point runs, which is the whole failure the shim exists to prevent.
        render_shim "$f" > "$live.new" || { say "could not render the shim for $f"; rm -f "$live.new"; rollback; exit 1; }
        chmod 755 "$live.new"
        mv "$live.new" "$live" || { say "could not install the shim for $f"; rm -f "$live.new"; rollback; exit 1; }
        CHANGED="$CHANGED $f"
        say "  wired  scripts/$f (shim)"
    done
fi

if [ "$NEED_SETTINGS" = "1" ] || [ "$NEED_HOOKS" = "1" ]; then
    if [ -f "$SETTINGS" ]; then
        SETTINGS_EXISTED=1
        cp -p "$SETTINGS" "$BACKUP/settings.json" || { say "backup failed for settings.json"; rollback; exit 1; }
        SRC="$SETTINGS"
    else
        SRC=/dev/null
    fi
    # Two independent edits to one document, applied as two stages so each is skipped
    # when it has nothing to do. Stage 1 keeps any other statusLine keys (padding,
    # type) the user already has. Stage 2 rewrites only the matched prefix of only the
    # matched hook commands; every sibling key (timeout, statusMessage, matcher) and
    # every other hook is carried through untouched.
    NEW="$( { [ "$SRC" = /dev/null ] && echo '{}' || cat "$SRC"; } \
        | if [ "$NEED_SETTINGS" = "1" ]; then
              jq --arg cmd "$WANT_CMD" '.statusLine = ((.statusLine // {}) + {type: "command", command: $cmd})'
          else cat; fi \
        | if [ "$NEED_HOOKS" = "1" ]; then
              jq --arg re "$HOOK_RE" '.hooks |= with_entries(.value |= map(
                  if (.hooks | type) == "array" then .hooks |= map(
                      if (.command | type) == "string"
                      then .command |= gsub($re; "\(.pre)\(.path)") else . end)
                  else . end))'
          else cat; fi)" \
        || { say "could not compute the new settings.json"; rollback; exit 1; }
    # cat-into-place, not mv: mv installs a NEW inode at the default umask and would
    # widen a 600 settings.json to 644.
    [ "$SETTINGS_EXISTED" = "0" ] && ( umask 077; : > "$SETTINGS" )
    printf '%s\n' "$NEW" > "$SETTINGS" || { say "could not write settings.json"; rollback; exit 1; }
    SETTINGS_CHANGED=1
    [ "$NEED_SETTINGS" = "1" ] && say "  wired  settings.json statusLine"
    [ "$NEED_HOOKS" = "1" ] && say "  repaired  settings.json hooks (dropped the python3 prefix from the shim calls)"
fi

# --- verify by OUTCOME, not by absence of error -------------------------------
# Run the command settings.json NOW holds — the thing Claude Code will actually run —
# and require BOTH a rendered line AND a ledger entry. Output alone is exactly what the
# 2026-09-23 break produced.
SENTINEL="wire-check-0000-0000-0000-000000000000"
SAMPLE='{"session_id":"'"$SENTINEL"'","cost":{"total_cost_usd":0},'
SAMPLE="$SAMPLE"'"model":{"display_name":"WireCheck"},"workspace":{"current_dir":"'"$HOME"'"}}'
RUN_CMD="$(jq -r '.statusLine.command // empty' "$SETTINGS" 2>/dev/null)"
# The shim resolves per call, so the verification runs it exactly as Claude Code
# will — with the plugin this installer resolved handed to it as the override. That
# keeps the check meaningful on a machine whose registry is missing or malformed
# (where an unseeded shim would correctly find nothing and the check would fail for
# the wrong reason). The shim's own registry resolution is pinned by its own tests.
OUT="$(printf '%s' "$SAMPLE" | COST_TRACKER_PLUGIN="$PLUGIN" sh -c "$RUN_CMD" 2>/dev/null)"
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

# Hooks: the same outcome rule. "No python3 prefix left" is only the absence of the
# known-bad shape, so ALSO run each budget-tally hook's executable through the shim's
# own harmless query (--which) and require it to resolve to a real file. The hook's
# real arguments are not replayed: the tally hook writes state, a verification must not.
HOOKS_OK=yes
HOOKS_WHY=""
if [ -n "$(broken_hooks "$SETTINGS")" ]; then
    HOOKS_OK=no; HOOKS_WHY="a python3-prefixed shim call remains"
fi
# One shim path per LINE, taken with the same token regex the repair used — never by
# splitting the command on spaces, which cut a spaced $HOME in half and skipped it.
while IFS= read -r hc; do
    [ -n "$hc" ] || continue
    case "$hc" in \"*\"|\'*\') hc="${hc#?}"; hc="${hc%?}" ;; esac
    case "$hc" in
        "~/"*) hc="$HOME/${hc#"~/"}" ;;
        '$HOME/'*) hc="$HOME/${hc#'$HOME/'}" ;;
        '${HOME}/'*) hc="$HOME/${hc#'${HOME}/'}" ;;
    esac
    WHICH="$(COST_TRACKER_PLUGIN="$PLUGIN" "$hc" --which 2>/dev/null)"
    if [ ! -x "$hc" ] || [ -z "$WHICH" ] || [ ! -f "$WHICH" ]; then
        HOOKS_OK=no; HOOKS_WHY="$hc does not resolve to a runnable plugin target"
    fi
done <<EOF
$(jq -r --arg re "$SHIM_TOKEN_RE" '(.hooks // {}) | .[]? | .[]? | (.hooks // [])[]? | (.command // empty)
        | select(type == "string") | match($re; "g") | .string' "$SETTINGS" 2>/dev/null)
EOF

if [ "$RC" != "0" ] || [ -z "$OUT" ] || [ "$CAPTURED" != "yes" ] || [ "$HOOKS_OK" != "yes" ]; then
    say ""
    say "VERIFY FAILED (rc=$RC, output empty=$([ -z "$OUT" ] && echo yes || echo no), ledger written=$CAPTURED, hooks ok=$HOOKS_OK${HOOKS_WHY:+: $HOOKS_WHY}) — ROLLING BACK."
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
