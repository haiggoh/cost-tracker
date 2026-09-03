#!/usr/bin/env bash
# Tests for bin/statusline-render.sh — the CONTRACT this renderer was migrated under.
#
# The delimiter test is the one that matters most and is the reason this file exists.
# The renderer extracts its fields in a single jq pass joined by a delimiter and reads
# them with one `read`. When that delimiter was a TAB, a missing field silently shifted
# every later field LEFT — an absent .effort.level pushed the DIRECTORY into the effort
# slot — because tab is an IFS *whitespace* character and `read` collapses runs of it.
# \037 (ASCII US) is not whitespace, so empty fields survive. That bug was hit in real
# use; this asserts it stays fixed.
set -uo pipefail
export LC_ALL=C

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RENDER="$HERE/../bin/statusline-render.sh"

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n     expected: %s\n     actual:   %s\n' "$1" "$2" "$3"; }
is()  { [ "$2" = "$3" ] && ok "$1" || bad "$1" "$3" "$2"; }
has() { case "$2" in *"$3"*) ok "$1" ;; *) bad "$1" "to contain '$3'" "$2" ;; esac; }
hasnt() { case "$2" in *"$3"*) bad "$1" "NOT to contain '$3'" "$2" ;; *) ok "$1" ;; esac; }

# Strip ANSI so assertions test content, not colour codes.
render() { printf '%s' "$1" | sh "$RENDER" 2>/dev/null | sed $'s/\033\[[0-9;]*m//g'; }

echo "statusline-render.sh"

FULL='{"model":{"display_name":"Opus 5 (1M)"},"effort":{"level":"high"},
 "workspace":{"current_dir":"/tmp/some-project-dir"},
 "context_window":{"total_input_tokens":48123,"used_percentage":12.4},
 "cost":{"total_cost_usd":3.14159,"total_lines_added":7,"total_lines_removed":2},
 "rate_limits":{"five_hour":{"used_percentage":31.7},"seven_day":{"used_percentage":8.2}}}'
OUT="$(render "$FULL")"
L1="$(printf '%s' "$OUT" | sed -n 1p)"
L2="$(printf '%s' "$OUT" | sed -n 2p)"
has "line 1 carries the model"        "$L1" "Opus 5 (1M)"
has "line 1 carries the effort level" "$L1" "high"
has "line 1 carries absolute ctx tokens and percent" "$L1" "ctx 48123 12%"
has "line 1 carries the session-lifetime cost at 2dp" "$L1" '$3.14'
has "line 1 carries the 5h rate limit"  "$L1" "5h 32%"
has "line 1 carries the 7d rate limit"  "$L1" "7d 8%"
has "line 2 carries the dir basename"   "$L2" "some-project-dir"
has "line 2 carries the diff counts"    "$L2" "+7"
# Variable-length names stay OFF line 1 so a long project name cannot wrap it.
hasnt "line 1 does not carry the directory" "$L1" "some-project-dir"

# --- THE DELIMITER REGRESSION -------------------------------------------------
# .effort.level absent. With a tab delimiter the next non-empty field (the dir)
# slid into EFFORT and was printed on line 1.
NOEFFORT='{"model":{"display_name":"Opus 5"},
 "workspace":{"current_dir":"/tmp/leaky-dir-name"},
 "context_window":{"total_input_tokens":100,"used_percentage":1},
 "cost":{"total_cost_usd":1}}'
OUT="$(render "$NOEFFORT")"
L1="$(printf '%s' "$OUT" | sed -n 1p)"
L2="$(printf '%s' "$OUT" | sed -n 2p)"
hasnt "a MISSING effort level does not shift the dir onto line 1" "$L1" "leaky-dir-name"
has   "the dir is still on line 2 where it belongs"               "$L2" "leaky-dir-name"
has   "ctx did not shift either"                                  "$L1" "ctx 100 1%"
has   "cost did not shift either"                                 "$L1" '$1.00'

# --- absent fields are DROPPED, never printed as null or 0 -------------------
MINIMAL='{"model":{"display_name":"Opus 5"},"workspace":{"current_dir":"/tmp/x"}}'
OUT="$(render "$MINIMAL")"
hasnt "a missing cost is not rendered as \$0.00" "$OUT" '$0.00'
hasnt "nothing is rendered as the string null"   "$OUT" "null"
hasnt "absent rate limits are dropped (gateway has none)" "$OUT" "5h"
has   "the model still renders"                  "$OUT" "Opus 5"

# --- ALWAYS exits 0: a renderer bug must never blank the status line ---------
for payload in '' 'not json at all' '{' '{"model":null}' '[]' '{"cost":{"total_cost_usd":"abc"}}'; do
    printf '%s' "$payload" | sh "$RENDER" > /dev/null 2>&1
    rc=$?
    is "exits 0 on payload: ${payload:-（empty）}" "$rc" "0"
done
OUT="$(render '{}')"
is "an empty object still renders a line" "$([ -n "$OUT" ] && echo nonempty || echo empty)" "nonempty"

echo
printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
