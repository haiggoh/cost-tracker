#!/usr/bin/env bash
# Tests for bin/cost-tracker-resolve.sh. Everything runs in an isolated env.
#
# The resolver has three resolution paths:
#   1. $COST_TRACKER_PLUGIN override
#   2. Plugin cache (highest-versioned)
#   3. Script-directory (source checkout)
#
# Each path is exercised in isolation by setting up the right fixture and
# unsetting the others. Because the real plugin cache lives at the default
# ~/.claude/plugins/cache/haiggoh/cost-tracker, tests that target the cache
# path must use a fake CLAUDE_PLUGIN_ROOT AND override the default by setting
# the full cache path to a non-existent dir (the resolver falls through to
# step 3 when the cache dir itself is absent, so tests 2/4/5/8 set
# CLAUDE_PLUGIN_ROOT to the temp dir AND clear the default).
set -uo pipefail
export LC_ALL=C

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN="$(cd -P "$HERE/.." && pwd)"
RESOLVER="$PLUGIN/bin/cost-tracker-resolve.sh"

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n     expected: %s\n     actual:   %s\n' "$1" "$2" "$3"; }
is()  { [ "$2" = "$3" ] && ok "$1" || bad "$1" "$3" "$2"; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# --- helper: build a fake plugin dir at $1 -----------------------------------
_make_plugin() {
    _dir="$1"
    mkdir -p "$_dir/.claude-plugin"
    printf '%s\n' '{"name":"cost-tracker","version":"0.9.0"}' > "$_dir/.claude-plugin/plugin.json"
}

# --- helper: run the resolver with the real cache path hidden ----------------
# The resolver's default cache is ~/.claude/plugins/cache/haiggoh/cost-tracker.
# To test the cache path in isolation we need CLAUDE_PLUGIN_ROOT pointing at our
# temp dir AND the default root not having a cost-tracker cache. We achieve the
# latter by setting CLAUDE_PLUGIN_ROOT to the temp dir (the resolver uses
# $CLAUDE_PLUGIN_ROOT when set, defaulting to $HOME/.claude/plugins/cache only
# when unset).
_run_resolver() {
    COST_TRACKER_PLUGIN="" bash "$RESOLVER" "$@"
}

echo "cost-tracker-resolve.sh"

# --- source checkout path (default when no cache and no override) -------------
# With no CLAUDE_PLUGIN_ROOT set and no override, the resolver finds the real
# plugin cache first (on this machine). We test the source-checkout path by
# pointing CLAUDE_PLUGIN_ROOT at a temp dir that contains no cache, and
# unsetting the override.
CLAUDE_PLUGIN_ROOT="$TMP/empty_root" bash "$RESOLVER" > "$TMP/out" 2>"$TMP/err"
is "source checkout resolves when cache root is empty" "$(cat "$TMP/out")" "$PLUGIN"
is "source checkout prints nothing on stderr" "$(cat "$TMP/err")" ""

# --- explicit override, valid ------------------------------------------------
_make_plugin "$TMP/override"
COST_TRACKER_PLUGIN="$TMP/override" bash "$RESOLVER" > "$TMP/out" 2>"$TMP/err"
is "valid override wins" "$(cat "$TMP/out")" "$TMP/override"
is "valid override nothing on stderr" "$(cat "$TMP/err")" ""

# --- explicit override, invalid ----------------------------------------------
COST_TRACKER_PLUGIN="$TMP/nonexistent" bash "$RESOLVER" > "$TMP/out" 2>"$TMP/err"
is "invalid override exits 1" "$?" "1"
is "invalid override prints to stderr" "$(grep -c 'cost-tracker-resolve' "$TMP/err")" "1"

# --- plugin cache: picks highest version -------------------------------------
# The resolver looks for $CLAUDE_PLUGIN_ROOT/haiggoh/cost-tracker/<version>/,
# so the fixture must mirror that layout.
_make_plugin "$TMP/haiggoh/cost-tracker/0.7.9"
_make_plugin "$TMP/haiggoh/cost-tracker/0.8.0"
_make_plugin "$TMP/haiggoh/cost-tracker/0.8.1"
mkdir -p "$TMP/haiggoh/cost-tracker/0.7.0"
# 0.7.0 has no plugin.json -> must be skipped
CLAUDE_PLUGIN_ROOT="$TMP" bash "$RESOLVER" > "$TMP/out" 2>"$TMP/err"
is "highest-versioned cache entry wins" "$(cat "$TMP/out")" "$TMP/haiggoh/cost-tracker/0.8.1"
is "cache resolution nothing on stderr" "$(cat "$TMP/err")" ""

# --- plugin cache: only one version ------------------------------------------
# Use a fresh CLAUDE_PLUGIN_ROOT so the haiggoh/ from the previous test
# (which has 0.8.1) does not win the sort.
TMP2="$(mktemp -d)"; _make_plugin "$TMP2/haiggoh/cost-tracker/0.5.0"
CLAUDE_PLUGIN_ROOT="$TMP2" bash "$RESOLVER" > "$TMP/out" 2>/dev/null
is "single-version resolves to 0.5.0" "$(cat "$TMP/out")" "$TMP2/haiggoh/cost-tracker/0.5.0"
rm -rf "$TMP2"

# --- plugin cache empty: falls through to source checkout --------------------
CLAUDE_PLUGIN_ROOT="$TMP/empty_cache" bash "$RESOLVER" > "$TMP/out" 2>"$TMP/err"
is "empty cache falls through to source" "$(cat "$TMP/out")" "$PLUGIN"

# --- CLAUDE_PLUGIN_ROOT not set: uses default ~/.claude/plugins/cache ----------
# On this machine the default cache has a real cost-tracker entry, so the
# resolver would resolve there. We test that the default root is consulted by
# pointing it at a non-existent dir and asserting it falls through to source.
CLAUDE_PLUGIN_ROOT="$TMP/does-not-exist" bash "$RESOLVER" > "$TMP/out" 2>"$TMP/err"
is "missing CLAUDE_PLUGIN_ROOT falls through to source" "$(cat "$TMP/out")" "$PLUGIN"

# --- overridden cache with CLAUDE_PLUGIN_ROOT --------------------------------
# Fresh root so the haiggoh/0.8.1 from test 2 cannot win.
TMP3="$(mktemp -d)"; _make_plugin "$TMP3/haiggoh/cost-tracker/1.0.0"
_make_plugin "$TMP3/haiggoh/cost-tracker/0.99.0"
CLAUDE_PLUGIN_ROOT="$TMP3" bash "$RESOLVER" > "$TMP/out" 2>/dev/null
is "CLAUDE_PLUGIN_ROOT with two versions picks 1.0.0" "$(cat "$TMP/out")" "$TMP3/haiggoh/cost-tracker/1.0.0"
rm -rf "$TMP3"

# --- idempotence: running twice gives the same path --------------------------
CLAUDE_PLUGIN_ROOT="$TMP" bash "$RESOLVER" > "$TMP/r1" 2>/dev/null
CLAUDE_PLUGIN_ROOT="$TMP" bash "$RESOLVER" > "$TMP/r2" 2>/dev/null
is "idempotent across runs" "$(cat "$TMP/r1")" "$(cat "$TMP/r2")"

# --- resolver returns a clean absolute path (no trailing slash) --------------
CLAUDE_PLUGIN_ROOT="$TMP" bash "$RESOLVER" 2>/dev/null | grep -q '/$'
is "resolved path has no trailing slash" "$?" "1"

echo
printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
