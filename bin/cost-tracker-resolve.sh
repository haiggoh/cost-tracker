#!/usr/bin/env sh
# cost-tracker-resolve.sh — resolve where this plugin lives at runtime.
#
# Purpose: the plugin's own binaries need to know where the installed copy is
# so they can point statusLine (via install.sh) at the right place, and run-tests
# can locate the installed scripts. The path is NOT fixed at write-time: the user
# may have cloned the repo somewhere that isn't ~/ClaudeWorkspace/cost-tracker,
# and a fresh install comes from the plugin cache which also has no stable
# absolute path (the version subdirectory changes with every release).
#
# Resolution order:
#   1. $COST_TRACKER_PLUGIN — explicit override (developer use, or a sysadmin
#      who keeps plugins under a fixed tree). Resolved as-is, must be absolute.
#   2. $CLAUDE_PLUGIN_ROOT/plugin-name/version/ — the canonical plugin cache
#      layout used by Claude Code's plugin manager. $CLAUDE_PLUGIN_ROOT is NOT
#      guaranteed to be set (it is an env var from the user's shell, not the
#      harness), so we fall back to ~/.claude/plugins/cache/haiggoh/cost-tracker.
#   3. The directory containing THIS SCRIPT (the source checkout). Falls through
#      to the install script's "$ROOT" style detection, which is the developer
#      workflow (bash ~/ClaudeWorkspace/cost-tracker/install.sh from the repo).
#
# Output: prints exactly one absolute path to stdout (the resolved PLUGIN_DIR)
# and exits 0. Prints nothing on stderr. Never creates or modifies anything.
#
# This is NOT sourced by the binaries — it is a standalone helper that the
# install scripts and tests call. The binaries that need the plugin dir
# (cost-ledger-capture.sh, statusline-render.sh) resolve it inline so they
# do not depend on the helper being on PATH.
set -e
[ -n "${COST_TRACKER_DEBUG:-}" ] && set -x

resolve() {
    # 1. Explicit override
    if [ -n "${COST_TRACKER_PLUGIN:-}" ]; then
        if [ -f "$COST_TRACKER_PLUGIN/.claude-plugin/plugin.json" ]; then
            printf '%s\n' "$COST_TRACKER_PLUGIN"
            return 0
        fi
        # Invalid override — die loudly so the user notices before anything breaks.
        printf 'cost-tracker-resolve: COST_TRACKER_PLUGIN=%s exists but is not a plugin dir (no .claude-plugin/plugin.json)\n' \
            "$COST_TRACKER_PLUGIN" >&2
        return 1
    fi

    # 2. Plugin cache
    _cache="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/plugins/cache}/haiggoh/cost-tracker"
    if [ -d "$_cache" ]; then
        # Pick the highest-versioned subdirectory (0.8.0 beats 0.7.9). The
        # plugin cache keeps all versions, so we need the one the user actually
        # runs — the newest, which is what `claude plugin update` installs.
        _hit=""
        for _d in "$_cache"/*/; do
            [ -f "$_d/.claude-plugin/plugin.json" ] || continue
            if [ -z "$_hit" ] || _d_ge "$_d" "$_hit"; then
                _hit="$_d"
            fi
        done
        if [ -n "$_hit" ]; then
            # Strip trailing slash so callers get a clean path.
            printf '%s\n' "${_hit%/}"
            return 0
        fi
    fi

    # 3. The directory containing this script — the developer checkout path.
    _here="$(cd -P "$(dirname "$0")" && pwd)"
    _here="$(cd -P "$_here/.." && pwd)"
    if [ -f "$_here/.claude-plugin/plugin.json" ]; then
        printf '%s\n' "$_here"
        return 0
    fi

    # Fallback: the caller should never reach here in practice — the source
    # checkout always has .claude-plugin/plugin.json. But return the script dir
    # so callers can still construct paths from it.
    printf '%s\n' "$_here"
}

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

resolve
