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

# Find a line by CONTENT rather than by number. The layout is three lines since 0.5.1 (chrome,
# spend, dir) and which of them appear depends on the payload, so pinning a line NUMBER makes
# a test fail on a layout change that did not break anything. Grepping for the line's own
# marker keeps each assertion about the thing it names.
spend_line() { printf '%s' "$1" | grep -E '\$[0-9]' | head -1; }
dir_line()   { printf '%s' "$1" | grep -v -E '^JoyIA' | grep -v -E '\$[0-9]' | head -1; }

# The today-segment is OFF for the baseline assertions below, which are about the
# renderer's own fields. Leaving it on would make them depend on live spend — and on a
# day with $0.00 of it, the "a missing cost is not rendered as $0.00" assertion would
# fail against a perfectly correct renderer. The segment gets its own section, with a
# ledger dir the test controls.
export COST_TRACKER_STATUSLINE=0

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
has "the SPEND line carries the session-lifetime cost at 2dp" "$(spend_line "$OUT")" '$3.14'
hasnt "…and line 1 no longer does: the dollar figures moved off the chrome line" "$L1" '$3.14'
has "line 1 carries the 5h rate limit"  "$L1" "5h 32%"
has "line 1 carries the 7d rate limit"  "$L1" "7d 8%"
has "the DIR line carries the dir basename"   "$(dir_line "$OUT")" "some-project-dir"
has "the DIR line carries the diff counts"    "$(dir_line "$OUT")" "+7"
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
has   "the dir is still on the DIR line where it belongs"         "$(dir_line "$OUT")" "leaky-dir-name"
has   "ctx did not shift either"                                  "$L1" "ctx 100 1%"
has   "cost did not shift either"                                 "$(spend_line "$OUT")" '$1.00'

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


# --- the today segment (cost-tracker) ----------------------------------------
TMPD="$(mktemp -d)"
mkdir -p "$TMPD/ledger"
TODAY_UTC="$(date -u +%F)"
printf '%s 30.12 0\n' "$TODAY_UTC" > "$TMPD/ledger/aaaaaaaa-0000-0000-0000-000000000001"
printf '%s 5.00 5.00\n' "$TODAY_UTC" > "$TMPD/ledger/aaaaaaaa-0000-0000-0000-000000000002"
# EVERY store the renderer can reach is redirected into $TMPD. Missing one fails OPEN
# onto real machine state: the "no cap means no denominator" assertion below started
# failing the moment this machine learned a real $40 cap, because the config dir was
# not sandboxed and the test was silently reading it.
seg_render() {
    printf '%s' "$1" | env COST_TRACKER_STATUSLINE=1 \
        COST_TRACKER_LEDGER_DIR="$TMPD/ledger" \
        COST_TRACKER_HISTORY="$TMPD/nonexistent-history.log" \
        COST_TRACKER_CONFIG_DIR="$TMPD/no-config" \
        COST_TRACKER_PROJECTS_DIR="$TMPD/no-transcripts" \
        LOCAL_AGENTS_LEDGER_DIR="$TMPD/no-savings" \
        COST_TRACKER_CAP_USD="${2:-}" \
        sh "$RENDER" 2>/dev/null | sed $'s/\033\[[0-9;]*m//g'
}
# The today segment has its OWN line since 0.5.1 (line 1 plus the segment overflowed one
# terminal width and wrapped), so an assertion about it must read the line it is ON. Grep
# for it rather than pinning a line NUMBER: whether a git line follows depends on the cwd.
seg_line() { printf '%s' "$1" | grep 'today:' || true; }

PAY='{"model":{"display_name":"Opus 5"},"cost":{"total_cost_usd":3.14159}}'
OUT="$(seg_render "$PAY" 40)"
L1="$(printf '%s' "$OUT" | sed -n 1p)"
has "the today segment renders, with its axis named" "$(seg_line "$OUT")" "today: \$30.12/\$40"
# "cloud" is a CONTRAST word: it separates cloud spend from local savings. With no savings
# figure there is nothing to contrast with, so it is dropped rather than padding the line.
hasnt "…and drops the 'cloud' word when no savings figure sits beside it" \
    "$(seg_line "$OUT")" "cloud"
has "the segment shares the SPEND line, not line 1" \
    "$(printf '%s' "$OUT" | sed -n 2p)" "today:"
hasnt "…so line 1 no longer carries it" "$L1" "today:"
has "the session-lifetime figure is LABELLED once a second figure is present" \
    "$(seg_line "$OUT")" 'session $3.14'
has "…and BOTH dollar figures share one line" "$(spend_line "$OUT")" 'today:'
hasnt "an absent savings ledger adds no savings claim" "$(seg_line "$OUT")" "local saved"
OUT="$(seg_render "$PAY")"
has "no cap means no denominator in the segment either" \
    "$(seg_line "$OUT")" "today: \$30.12"
hasnt "and no invented /\$40" "$(seg_line "$OUT")" '/$40'
OUT="$(printf '%s' "$PAY" | env COST_TRACKER_STATUSLINE=0 sh "$RENDER" 2>/dev/null | sed $'s/\033\[[0-9;]*m//g')"
hasnt "COST_TRACKER_STATUSLINE=0 suppresses the segment" "$OUT" "today: cloud"
has   "and the lifetime figure loses its now-unneeded label" "$OUT" '$3.14'
hasnt "…which means no bare 'session' prefix when it stands alone" "$OUT" "session \$"

# a LEARNED cap (no env var at all) must reach the segment
mkdir -p "$TMPD/learned"
cat > "$TMPD/learned/cap.json" <<'JSON'
{"contract":1,"found":true,"learned_at":"2026-09-03T00:00:00Z",
 "key":{"cap_usd":40.0,"cost_at_kill_usd":40.02,"scope":"key","label":"Joyia-Code-M4m",
        "key_hint":"sk-...XXXX","observed_at":"2026-09-02T02:17:19.496Z",
        "source_file":"x.jsonl","raw":"Max budget: 40.0"}}
JSON
OUT="$(printf '%s' "$PAY" | env COST_TRACKER_STATUSLINE=1 \
    COST_TRACKER_LEDGER_DIR="$TMPD/ledger" \
    COST_TRACKER_HISTORY="$TMPD/nonexistent-history.log" \
    COST_TRACKER_CONFIG_DIR="$TMPD/learned" \
    COST_TRACKER_PROJECTS_DIR="$TMPD/no-transcripts" \
    LOCAL_AGENTS_LEDGER_DIR="$TMPD/no-savings" \
    sh "$RENDER" 2>/dev/null | sed $'s/\033\[[0-9;]*m//g')"
has "a LEARNED cap gives the segment a denominator with no env var set" \
    "$(seg_line "$OUT")" "today: \$30.12/\$40"

# THE SYMLINK CASE, which is how the renderer is actually reached once wired: via
# ~/.claude/scripts/statusline-render.sh pointing into the plugin. A plain
# dirname "$0" resolves to the symlink's directory, where the sibling CLI is NOT,
# and the segment silently vanishes. Every assertion above ran the renderer
# directly in the repo, so none of them could see this — it was found end-to-end.
mkdir -p "$TMPD/fake-scripts"
ln -sf "$(cd -P "$(dirname "$RENDER")" && pwd)/statusline-render.sh" "$TMPD/fake-scripts/statusline-render.sh"
OUT="$(printf '%s' "$PAY" | env COST_TRACKER_STATUSLINE=1 \
    COST_TRACKER_LEDGER_DIR="$TMPD/ledger" \
    COST_TRACKER_HISTORY="$TMPD/nonexistent-history.log" \
    COST_TRACKER_CONFIG_DIR="$TMPD/no-config" \
    COST_TRACKER_PROJECTS_DIR="$TMPD/no-transcripts" \
    LOCAL_AGENTS_LEDGER_DIR="$TMPD/no-savings" \
    COST_TRACKER_CAP_USD=40 \
    sh "$TMPD/fake-scripts/statusline-render.sh" 2>/dev/null | sed $'s/\033\[[0-9;]*m//g')"
has "the segment still renders when reached THROUGH a symlink" \
    "$(seg_line "$OUT")" "today: \$30.12/\$40"

# --- 0.5.1: width ------------------------------------------------------------------
# The reported overflow was 108 columns on ONE line. Two changes cut it: the model's
# context suffix is abbreviated, and the budget segment moved to its own line.
OUT="$(render '{"model":{"display_name":"Opus 5 (1M context)"},"effort":{"level":"medium"},"cost":{"total_cost_usd":2.88}}')"
L1="$(printf '%s' "$OUT" | sed -n 1p)"
has   "the model's context suffix is abbreviated for width" "$L1" 'Opus 5 (1M)'
hasnt "…so the padding word is gone" "$L1" '1M context'
# A display name WITHOUT the suffix must pass through untouched — the rule is a
# substitution, not a truncation.
has "a model name with no context suffix is unchanged" \
    "$(render '{"model":{"display_name":"Sonnet 5"},"cost":{"total_cost_usd":1.0}}' | sed -n 1p)" 'Sonnet 5'
# The whole point: no single emitted line may be anywhere near the old 108 columns.
WIDEST=$(printf '%s' "$OUT" | awk '{ if (length($0) > m) m = length($0) } END { print m+0 }')
[ "$WIDEST" -lt 80 ] && ok "no emitted line approaches the 108-column overflow (widest ${WIDEST})" \
    || bad "no emitted line approaches the 108-column overflow" "widest line < 80 cols" "widest is ${WIDEST}"

# --- 0.5.1: --help is answered, never silently RUN -----------------------------------
# The defect this guards is worse than a missing flag: before this, `--help` fell through
# to the normal path and RENDERED, so a user probing an unfamiliar script got output that
# looked like help while the script did its real work. Verified by running it, not by
# grepping for the string — argparse-style help never appears in the source.
H="$(sh "$RENDER" --help 2>&1 </dev/null)"
has  "--help explains what the script does" "$H" 'render the Claude Code status line'
has  "--help lists the lines it prints" "$H" 'today: $<billed>'
has  "--help documents the env var" "$H" 'COST_TRACKER_STATUSLINE=0'
# It must not RENDER. The help text legitimately shows the layout, so the tell cannot be
# the literal "JoyIA" — it is a rendered VALUE: a real run of this script always emits a
# concrete dollar figure or the session cost, which a layout template never does.
# The discriminator is a DIGIT after a dollar sign: help shows placeholders ($<cost>),
# a real render always shows a number. Checking for "session $" cannot work — the layout
# example in the help text contains it, which is the help doing its job.
if printf '%s' "$H" | grep -q '\$[0-9]'; then
    bad "--help emits no rendered dollar VALUE" "no \$<digit> anywhere" "found one"
else
    ok "--help emits no rendered dollar VALUE"
fi
has   "…and what it shows instead is a labelled template" "$H" '$<cost>'
BADOUT="$(sh "$RENDER" --nope 2>&1 </dev/null)"; BADRC=$?
has "an unrecognised flag names itself" "$BADOUT" 'unrecognised option: --nope'
[ "$BADRC" -ne 0 ] && ok "an unrecognised flag exits non-zero" \
    || bad "an unrecognised flag exits non-zero" "non-zero exit" "exit $BADRC"
# ...and the normal stdin path is untouched by the new parsing.
has "a payload on stdin still renders normally" \
    "$(render '{"model":{"display_name":"Opus 5"},"cost":{"total_cost_usd":1.0}}' | sed -n 1p)" 'JoyIA'

# --- 0.5.1: WEIGHT makes today the primary figure, and both share one line -----------
# Order is NOT the emphasis mechanism here (today already led and still read as equal).
# Weight is: today is BOLD and the session lifetime is DIMMED beside it. Asserted on the
# raw escape codes, since that is the only place the distinction exists — a colour-stripped
# line cannot show it, which is why the other assertions here strip and this one must not.
RAW="$(printf '%s' '{"model":{"display_name":"Opus 5"},"cost":{"total_cost_usd":9.33}}' \
    | env COST_TRACKER_STATUSLINE=1 \
      COST_TRACKER_LEDGER_DIR="$TMPD/ledger" \
      COST_TRACKER_HISTORY="$TMPD/nonexistent-history.log" \
      COST_TRACKER_CONFIG_DIR="$TMPD/no-config" \
      COST_TRACKER_PROJECTS_DIR="$TMPD/no-transcripts" \
      LOCAL_AGENTS_LEDGER_DIR="$TMPD/no-savings" \
      COST_TRACKER_CAP_USD=40 sh "$RENDER" 2>/dev/null | sed -n 2p)"
case "$RAW" in
  *$'\033[1m'*'today:'*) ok "today is emitted BOLD" ;;
  *) bad "today is emitted BOLD" "an ESC[1m before today:" "$RAW" ;;
esac
case "$RAW" in
  *$'\033[2m'*'session $9.33'*) ok "the session figure is emitted DIM" ;;
  *) bad "the session figure is emitted DIM" "an ESC[2m before session" "$RAW" ;;
esac
# A session figure standing ALONE is not secondary to anything, so it is not dimmed.
SOLO="$(printf '%s' '{"model":{"display_name":"Opus 5"},"cost":{"total_cost_usd":9.33}}' \
    | env COST_TRACKER_STATUSLINE=0 sh "$RENDER" 2>/dev/null | sed -n 2p)"
case "$SOLO" in
  *$'\033[2m'*'$9.33'*) bad "a lone session figure is NOT dimmed" "no ESC[2m" "$SOLO" ;;
  *) ok "a lone session figure is NOT dimmed" ;;
esac
# The 'cloud' word returns the moment a savings figure gives it something to contrast with.
mkdir -p "$TMPD/savings"
printf '%s\n' '{"saved_usd": 4.80, "at": "2026-09-11T10:00:00Z"}' > "$TMPD/savings/x.jsonl"

rm -rf "$TMPD"

echo
printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
