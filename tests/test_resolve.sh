#!/usr/bin/env bash
# Tests for bin/cost-tracker-resolve.sh. Everything runs in an isolated env.
#
# The resolution order is the live-wiring plan's §Spec 1 (decided with the user):
#   (a) $COST_TRACKER_PLUGIN override — must contain .claude-plugin/plugin.json
#   (b) the installPath recorded in $CLAUDE_PLUGINS_DIR/installed_plugins.json
#       for cost-tracker@haiggoh — the user chose this BEFORE the highest cache
#       dir so statusline and hooks always run the same version
#   (c) the highest semver dir under $CLAUDE_PLUGINS_DIR/cache/haiggoh/cost-tracker
#   (d) nothing found => exit 1 with one stderr line
# A workspace checkout is reachable ONLY through (a).
#
# CLAUDE_PLUGINS_DIR (default $HOME/.claude/plugins) is the test seam: every case
# points it at a fixture tree, so the real ~/.claude/plugins is never consulted.
set -uo pipefail
export LC_ALL=C

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN="$(cd -P "$HERE/.." && pwd)"
RESOLVER="$PLUGIN/bin/cost-tracker-resolve.sh"

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n     expected: %s\n     actual:   %s\n' "$1" "$2" "$3"; }
is()  { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "$3" "$2"; fi; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
PDIR="$TMP/plugins"                       # the seam: a fake $HOME/.claude/plugins
CACHE="$PDIR/cache/haiggoh/cost-tracker"

# --- fixtures -----------------------------------------------------------------
_make_plugin() {
    mkdir -p "$1/.claude-plugin"
    printf '%s\n' '{"name":"cost-tracker","version":"0.9.0"}' > "$1/.claude-plugin/plugin.json"
}
_make_registry() {
    printf '%s' "$1" > "$PDIR/installed_plugins.json"
    # A malformed fixture would silently exercise the wrong code path (the resolver
    # treats malformed as "fall through"), so validate every fixture that claims to
    # be a registry. The `{not json` case is the deliberate exception.
    case "$1" in
        '{"plugins"'*)
            python3 -m json.tool "$PDIR/installed_plugins.json" >/dev/null 2>&1 ||
                printf 'FIXTURE ERROR: not valid JSON: %s\n' "$1" >&2 ;;
    esac
}
_run() { COST_TRACKER_PLUGIN="" CLAUDE_PLUGINS_DIR="$PDIR" bash "$RESOLVER" "$@"; }

_make_plugin "$CACHE/0.19.0"
_make_plugin "$CACHE/0.18.4"
{ echo "DIAG-A after fixtures:"; ls -A "$CACHE"; } >&2

echo "cost-tracker-resolve.sh"

# --- --help / --which interface ----------------------------------------------
_run --help > "$TMP/out" 2>"$TMP/err"; is "--help exits 0" "$?" "0"
is "--help prints the usage line to stdout" "$(grep -c '^Usage:' "$TMP/out")" "1"
is "--help prints nothing on stderr" "$(cat "$TMP/err")" ""

_run --which > "$TMP/out" 2>"$TMP/err"
is "--which prints one path" "$(wc -l < "$TMP/out" | tr -d ' ')" "1"

# --- (c) the cache path, when no registry exists ------------------------------
_run > "$TMP/out" 2>"$TMP/err"
is "no registry: highest cache dir wins" "$(cat "$TMP/out")" "$CACHE/0.19.0"
is "cache resolution prints nothing on stderr" "$(cat "$TMP/err")" ""

# --- (b) THE DISCRIMINANT: the registry's installPath beats a higher cache ----
_make_registry '{"plugins":{"cost-tracker@haiggoh":[{"scope":"user","installPath":"'"$CACHE/0.18.4"'"}]}}'
_run > "$TMP/out" 2>"$TMP/err"
is "installpath_wins_over_highest" "$(cat "$TMP/out")" "$CACHE/0.18.4"

# --- (b) a stale installPath (the dir is gone) falls through to (c) ------------
_make_registry '{"plugins":{"cost-tracker@haiggoh":[{"scope":"user","installPath":"'"$CACHE/0.17.0"'"}]}}'
_run > "$TMP/out" 2>"$TMP/err"
is "stale_installpath_falls_back" "$(cat "$TMP/out")" "$CACHE/0.19.0"

# --- (b) a malformed registry is not an error: it falls through to (c) ---------
_make_registry '{not json'
_run > "$TMP/out" 2>"$TMP/err"
is "malformed_registry_falls_back" "$(cat "$TMP/out")" "$CACHE/0.19.0"
is "malformed registry still exits 0" "$?" "0"
is "malformed registry: stdout is only the path" "$(wc -l < "$TMP/out" | tr -d ' ')" "1"

# --- (b) an entry without .claude-plugin/plugin.json is skipped ----------------
_make_registry '{"plugins":{"cost-tracker@haiggoh":[{"scope":"user","installPath":"'"$TMP/not-a-plugin"'"}]}}'
_run > "$TMP/out" 2>"$TMP/err"
is "installpath without plugin.json is skipped" "$(cat "$TMP/out")" "$CACHE/0.19.0"

# --- (b) several scopes: the first USABLE entry wins ---------------------------
_make_registry '{"plugins":{"cost-tracker@haiggoh":[{"scope":"local","installPath":"'"$TMP/gone"'"},{"scope":"user","installPath":"'"$CACHE/0.18.4"'"},{"scope":"project","installPath":"'"$CACHE/0.19.0"'"}]}}'
_run > "$TMP/out" 2>"$TMP/err"
is "first usable entry among several scopes" "$(cat "$TMP/out")" "$CACHE/0.18.4"

# --- (a) the override beats every other source --------------------------------
_make_plugin "$TMP/override"
COST_TRACKER_PLUGIN="$TMP/override" CLAUDE_PLUGINS_DIR="$PDIR" bash "$RESOLVER" > "$TMP/out" 2>"$TMP/err"
is "override_wins" "$(cat "$TMP/out")" "$TMP/override"
is "override resolution prints nothing on stderr" "$(cat "$TMP/err")" ""

# --- (a) an invalid override fails loudly, naming the var ----------------------
COST_TRACKER_PLUGIN="$TMP/nonexistent" CLAUDE_PLUGINS_DIR="$PDIR" bash "$RESOLVER" > "$TMP/out" 2>"$TMP/err"
is "invalid_override_fails_loudly exit" "$?" "1"
is "invalid override print nothing on stdout" "$(cat "$TMP/out")" ""
is "invalid override names the var" "$(grep -c 'COST_TRACKER_PLUGIN' "$TMP/err")" "1"

# --- (d) nothing found at all -------------------------------------------------
rm -rf "$PDIR"
mkdir -p "$PDIR"
_run > "$TMP/out" 2>"$TMP/err"
is "nothing_found_fails exit" "$?" "1"
is "nothing found prints nothing on stdout" "$(cat "$TMP/out")" ""
is "nothing found prints one stderr line" "$(wc -l < "$TMP/err" | tr -d ' ')" "1"

# --- the resolved path is clean and stable -------------------------------------
_make_plugin "$CACHE/0.19.0"
_make_registry '{"plugins":{"cost-tracker@haiggoh":[{"scope":"user","installPath":"'"$CACHE/0.19.0"'"}]}}'
_run > "$TMP/r1" 2>/dev/null; _run > "$TMP/r2" 2>/dev/null
is "idempotent across runs" "$(cat "$TMP/r1")" "$(cat "$TMP/r2")"
_run 2>/dev/null | grep -q '/$'
is "resolved path has no trailing slash" "$?" "1"

echo
printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
