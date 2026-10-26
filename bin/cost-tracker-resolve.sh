#!/usr/bin/env sh
# cost-tracker-resolve.sh — resolve where this plugin lives at runtime.
#
# The plugin's own binaries and its installers need to know where the INSTALLED
# copy is, so a plugin update takes effect without re-wiring and a branch switch
# in a workspace checkout changes nothing live.
#
# RESOLUTION ORDER (the live-wiring plan's §Spec 1, decided with the user):
#   (a) $COST_TRACKER_PLUGIN — explicit override (developer or sysadmin pin).
#       It must contain .claude-plugin/plugin.json; when it is set but invalid
#       the script exits 1 NAMING THE VAR, so a typo fails loudly instead of
#       silently resolving somewhere else.
#   (b) the installPath that $CLAUDE_PLUGINS_DIR/installed_plugins.json records
#       for cost-tracker@haiggoh — the user chose this BEFORE the highest cache
#       dir, so the statusline and the hooks always run the same version. A
#       missing, unreadable, malformed or stale registry is not an error: it
#       falls through to (c).
#   (c) the highest semver dir under $CLAUDE_PLUGINS_DIR/cache/haiggoh/cost-tracker
#       that holds a plugin.json.
#   (d) nothing found => exit 1 with one stderr line.
# A workspace checkout is reachable ONLY through (a).
#
# Usage: cost-tracker-resolve.sh [--which] [--help]
#   --which   print the resolved plugin dir (also the default action)
#   --help    print this header and exit 0
# An unrecognised argument exits 2 with a usage line before any work.
#
# Output: exactly one absolute path on stdout, nothing on stderr, exit 0 — or
# exit 1 with one stderr line. Never creates or modifies anything. A stdlib-only
# python3 reads the registry JSON; nothing else is required.
set -e
[ -n "${COST_TRACKER_DEBUG:-}" ] && set -x

PLUGINS_DIR="${CLAUDE_PLUGINS_DIR:-$HOME/.claude/plugins}"
NAME="cost-tracker@haiggoh"

_usage() { awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"; }

case "${1:-}" in
    -h|--help) _usage; exit 0 ;;
    --which)   shift ;;
    "")        ;;
    *) printf 'cost-tracker-resolve: unknown argument: %s\n' "$1" >&2
       _usage >&2; exit 2 ;;
esac
# A second argument is not part of the contract; refuse it rather than ignore it.
[ $# -gt 0 ] && { printf 'cost-tracker-resolve: unexpected argument: %s\n' "$1" >&2; _usage >&2; exit 2; }

# Numeric version comparison: returns 0 if $1 >= $2.
# Handles N.M, N.M.P and N.M.P.Q via dot-split, padding with zeros.
_dotted_to_int() {
    printf '%s\n' "$1" | awk -F. '{printf "%05d%05d%05d%05d\n", $1+0, $2+0, $3+0, $4+0}'
}

_d_ge() {
    _a=$(_dotted_to_int "${1##*/}")
    _b=$(_dotted_to_int "${2##*/}")
    [ "$_a" -ge "$_b" ]
}

# --- (a) explicit override ----------------------------------------------------
if [ -n "${COST_TRACKER_PLUGIN:-}" ]; then
    if [ -f "$COST_TRACKER_PLUGIN/.claude-plugin/plugin.json" ]; then
        printf '%s\n' "${COST_TRACKER_PLUGIN%/}"
        exit 0
    fi
    printf 'cost-tracker-resolve: COST_TRACKER_PLUGIN=%s is set but is not a plugin dir (no .claude-plugin/plugin.json)\n' \
        "$COST_TRACKER_PLUGIN" >&2
    exit 1
fi

# --- (b) the installPath Claude Code's plugin manager recorded ----------------
# The registry is tool-owned state: we only ever READ it. A malformed or missing
# file simply means "no recorded install", which is step (c)'s business.
REGISTRY="$PLUGINS_DIR/installed_plugins.json"
if [ -f "$REGISTRY" ]; then
    if FROM_REGISTRY="$(_REG="$REGISTRY" _NAME="$NAME" python3 -c '
import json, os, sys
try:
    with open(os.environ["_REG"]) as f:
        data = json.load(f)
except Exception:
    sys.exit(1)                      # unreadable or malformed: not an error here
entries = None
if isinstance(data, dict):
    plugins = data.get("plugins")
    if isinstance(plugins, dict):
        entries = plugins.get(os.environ["_NAME"])
if not isinstance(entries, list):
    sys.exit(1)
for entry in entries:
    if not isinstance(entry, dict):
        continue
    path = entry.get("installPath")
    if not isinstance(path, str) or not path:
        continue
    if os.path.isfile(os.path.join(path, ".claude-plugin", "plugin.json")):
        print(path)                 # the first USABLE entry wins
        sys.exit(0)
sys.exit(1)                         # nothing usable recorded
' 2>/dev/null)"; then
        if [ -n "$FROM_REGISTRY" ]; then
            printf '%s\n' "${FROM_REGISTRY%/}"
            exit 0
        fi
    fi
fi

# --- (c) the highest semver dir in the plugin cache ---------------------------
CACHE="$PLUGINS_DIR/cache/haiggoh/cost-tracker"
if [ -d "$CACHE" ]; then
    hit=""
    for d in "$CACHE"/*/; do
        [ -f "$d/.claude-plugin/plugin.json" ] || continue
        if [ -z "$hit" ] || _d_ge "$d" "$hit"; then hit="$d"; fi
    done
    if [ -n "$hit" ]; then
        # Strip the trailing slash so callers get a clean path.
        printf '%s\n' "${hit%/}"
        exit 0
    fi
fi

# --- (d) nothing found --------------------------------------------------------
printf 'cost-tracker-resolve: no installed cost-tracker found (tried COST_TRACKER_PLUGIN, %s, and %s)\n' \
    "$REGISTRY" "$CACHE" >&2
exit 1
