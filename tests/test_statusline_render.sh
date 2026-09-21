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

# For cloud session tests, ensure resolver detects cloud by setting ANTHROPIC_BASE_URL
# to an Anthropic endpoint (the resolver keys off this).
export ANTHROPIC_BASE_URL="https://api.anthropic.com"

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
OUT="$(ANTHROPIC_BASE_URL=https://api.anthropic.com render "$NOEFFORT")"
L1="$(printf '%s' "$OUT" | sed -n 1p)"
L2="$(printf '%s' "$OUT" | sed -n 2p)"
hasnt "a MISSING effort level does not shift the dir onto line 1" "$L1" "leaky-dir-name"
has   "the dir is still on the DIR line where it belongs"         "$(dir_line "$OUT")" "leaky-dir-name"
has   "ctx did not shift either"                                  "$L1" "ctx 100 1%"
has   "cost did not shift either"                                 "$(spend_line "$OUT")" '$1.00'

# --- absent fields are DROPPED, never printed as null or 0 -------------------
MINIMAL='{"model":{"display_name":"Opus 5"},"workspace":{"current_dir":"/tmp/x"}}'
OUT="$(ANTHROPIC_BASE_URL=https://api.anthropic.com render "$MINIMAL")"
hasnt "a missing cost is not rendered as \$0.00" "$OUT" '$0.00'
hasnt "nothing is rendered as the string null"   "$OUT" "null"
hasnt "absent rate limits are dropped (gateway has none)" "$OUT" "5h"
has   "the model still renders"                  "$OUT" "Opus 5"

# --- ALWAYS exits 0: a renderer bug must never blank the status line ---------
for payload in '' 'not json at all' '{' '{"model":null}' '[]' '{"cost":{"total_cost_usd":"abc"}}'; do
    printf '%s' "$payload" | ANTHROPIC_BASE_URL=https://api.anthropic.com sh "$RENDER" > /dev/null 2>&1
    rc=$?
    is "exits 0 on payload: ${payload:-（empty）}" "$rc" "0"
done
OUT="$(ANTHROPIC_BASE_URL=https://api.anthropic.com render '{}')"
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
OUT="$(ANTHROPIC_BASE_URL=https://api.anthropic.com render '{"model":{"display_name":"Opus 5 (1M context)"},"effort":{"level":"medium"},"cost":{"total_cost_usd":2.88}}')"
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
H="$(ANTHROPIC_BASE_URL=https://api.anthropic.com sh "$RENDER" --help 2>&1 </dev/null)"
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
    "$(ANTHROPIC_BASE_URL=https://api.anthropic.com render '{"model":{"display_name":"Opus 5"},"cost":{"total_cost_usd":1.0}}' | sed -n 1p)" 'JoyIA'

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
      COST_TRACKER_CAP_USD=40 \
      ANTHROPIC_BASE_URL=https://api.anthropic.com sh "$RENDER" 2>/dev/null | sed -n 2p)"
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
    | env COST_TRACKER_STATUSLINE=0 \
      ANTHROPIC_BASE_URL=https://api.anthropic.com sh "$RENDER" 2>/dev/null | sed -n 2p)"
case "$SOLO" in
  *$'\033[2m'*'$9.33'*) bad "a lone session figure is NOT dimmed" "no ESC[2m" "$SOLO" ;;
  *) ok "a lone session figure is NOT dimmed" ;;
esac
# The 'cloud' word returns the moment a savings figure gives it something to contrast with.
mkdir -p "$TMPD/savings"
printf '%s\n' '{"saved_usd": 4.80, "at": "2026-09-11T10:00:00Z"}' > "$TMPD/savings/x.jsonl"

rm -rf "$TMPD"

# --- FREE sessions: local MLX and free-API lanes ------------------------------------
# A free session bills nothing, so showing it a cloud figure and a cap is a LIE about the
# axis. These assert the suppression, the two labels, and the phantom arithmetic. The lane
# is chosen from ANTHROPIC_BASE_URL — never CLAUDE_IS_LOCAL, which leaks into a later cloud
# session in the same shell and would mislabel it.
FTMP="$(mktemp -d)"; FLED="$FTMP/ledger"; mkdir -p "$FLED"
FTODAY="$(date -u +%F)"
# Field 4 is the phantom cumulative: what this free work WOULD have cost on the gateway.
printf '%s 0 0 12.5\n' "$FTODAY" > "$FLED/sess-x"
printf '%s 0 0 7.5\n'  "$FTODAY" > "$FLED/sess-y"
FPAY='{"model":{"display_name":"Opus 5"},"workspace":{"current_dir":"/tmp"},
 "cost":{"total_cost_usd":0.5},"session_id":"sess-x"}'
frender() {
  printf '%s' "$FPAY" | env COST_TRACKER_STATUSLINE=1 \
    COST_TRACKER_LEDGER_DIR="$FLED" COST_TRACKER_HISTORY="$FTMP/none.log" \
    ANTHROPIC_BASE_URL="$1" sh "$RENDER" 2>/dev/null | sed $'s/\033\[[0-9;]*m//g'
}
LOC="$(spend_line "$(frender http://localhost:8000)")"
FRE="$(spend_line "$(frender http://localhost:4141)")"
# A free session bills nothing, so showing it a cloud figure and a cap is a LIE about the
# axis. These assert the suppression, the two labels, and the phantom arithmetic. The lane
# is chosen from ANTHROPIC_BASE_URL — never CLAUDE_IS_LOCAL, which leaks into a later cloud
# session in the same shell and would mislabel it.
FTMP="$(mktemp -d)"; FLED="$FTMP/ledger"; mkdir -p "$FLED"
FTODAY="$(date -u +%F)"
# Field 4 is the phantom cumulative: what this free work WOULD have cost on the gateway.
printf '%s 0 0 12.5\n' "$FTODAY" > "$FLED/sess-x"
printf '%s 0 0 7.5\n'  "$FTODAY" > "$FLED/sess-y"
FPAY='{"model":{"display_name":"Opus 5"},"workspace":{"current_dir":"/tmp"},
 "cost":{"total_cost_usd":0.5},"session_id":"sess-x"}'
frender() {
  printf '%s' "$FPAY" | env COST_TRACKER_STATUSLINE=1 \
    COST_TRACKER_LEDGER_DIR="$FLED" COST_TRACKER_HISTORY="$FTMP/none.log" \
    ANTHROPIC_BASE_URL="$1" sh "$RENDER" 2>/dev/null | sed $'s/\033\[[0-9;]*m//g'
}
LOC="$(spend_line "$(frender http://localhost:8000)")"
FRE="$(spend_line "$(frender http://localhost:4141)")"

has "a LOCAL session is labelled as one"        "$LOC" "local session"
has "a FREE-API session is labelled distinctly" "$FRE" "free api session"
# The whole point: no cloud spend and no cap on a lane that cannot bill.
hasnt "a LOCAL session shows no cap"            "$LOC" "/\$40"
hasnt "a LOCAL session shows no gateway axis"   "$LOC" "gw"
hasnt "a FREE-API session shows no cap"         "$FRE" "/\$40"
# This session's own phantom comes from field 4 of ITS ledger line, not another session's.
has "the session figure is THIS session's phantom" "$LOC" 'saved $12.50'
# …and today is every free session's phantom together (12.5 + 7.5), not just this one's.
has "today totals every free session's phantom"    "$LOC" '$20.00 saved'
has "…and names free agents as the source"         "$LOC" "with free agents"

# A planted change must MOVE the figure — otherwise the two assertions above would also pass
# against a renderer that hardcoded them.
printf '%s 0 0 99.0\n' "$FTODAY" > "$FLED/sess-x"
MOVED="$(spend_line "$(frender http://localhost:8000)")"
has "the session figure tracks the ledger (planted 99.0)" "$MOVED" 'saved $99.00'
has "…and today tracks it too (99.0 + 7.5)"               "$MOVED" '$106.50 saved'

# A session with no ledger entry must read $0.00 rather than inheriting another session's.
NOENT="$(printf '%s' '{"model":{"display_name":"Opus 5"},"session_id":"sess-absent"}' \
  | env COST_TRACKER_STATUSLINE=1 COST_TRACKER_LEDGER_DIR="$FLED" \
    COST_TRACKER_HISTORY="$FTMP/none.log" ANTHROPIC_BASE_URL=http://localhost:8000 \
    MODEL_ALIAS=qwen38-27b-4bit sh "$RENDER" 2>/dev/null | sed $'s/\033\[[0-9;]*m//g')"
has "an unknown session claims no phantom" "$(spend_line "$NOENT")" 'saved $0.00'

# The PAYLOAD outranks the environment. CLAUDE_CODE_SESSION_ID is exported into every child of
# a session, so if the env won, a renderer drawing session B under session A would price A —
# which measured as $0.00 when A had no entry in the store being rendered.
ENVW="$(printf '%s' "$FPAY" | env COST_TRACKER_STATUSLINE=1 COST_TRACKER_LEDGER_DIR="$FLED" \
    COST_TRACKER_HISTORY="$FTMP/none.log" ANTHROPIC_BASE_URL=http://localhost:8000 \
    MODEL_ALIAS=qwen38-27b-4bit CLAUDE_CODE_SESSION_ID=sess-absent sh "$RENDER" 2>/dev/null | sed $'s/\033\[[0-9;]*m//g')"
has "the payload session_id beats CLAUDE_CODE_SESSION_ID" "$(spend_line "$ENVW")" 'saved $99.00'

# The lane is the ENDPOINT's business. A leaked CLAUDE_IS_LOCAL must not relabel a cloud session.
LEAK="$(printf '%s' "$FPAY" | env COST_TRACKER_STATUSLINE=0 CLAUDE_IS_LOCAL=1 \
    ANTHROPIC_BASE_URL=https://api.anthropic.com sh "$RENDER" 2>/dev/null | sed $'s/\033\[[0-9;]*m//g')"
hasnt "a leaked CLAUDE_IS_LOCAL does not relabel a cloud session" "$LEAK" "local session"
# An unrelated localhost port is not a free lane either.
OTHER="$(printf '%s' "$FPAY" | env COST_TRACKER_STATUSLINE=0 \
    ANTHROPIC_BASE_URL=http://localhost:9999 sh "$RENDER" 2>/dev/null | sed $'s/\033\[[0-9;]*m//g')"
hasnt "an unrelated localhost port is not treated as free" "$OTHER" "local session"

# The free line must survive the cost-tracker CLI being switched off: no unbound variable, no
# stray output, still exit 0. (CT_BIN is only defined inside that block.)
OFFOUT="$(printf '%s' "$FPAY" | env COST_TRACKER_STATUSLINE=0 \
    ANTHROPIC_BASE_URL=http://localhost:8000 MODEL_ALIAS=qwen38-27b-4bit sh "$RENDER" 2>&1)"
OFFRC=$?
is "a free session exits 0 with the CLI disabled" "$OFFRC" "0"
hasnt "…and emits no shell error" "$OFFOUT" "unbound"
hasnt "…and emits no not-found error" "$OFFOUT" "not found"
has "…and still reports a zero saving rather than nothing" \
    "$(spend_line "$OFFOUT")" 'saved $0.00'

# --- the free-API lane spans the launcher's WHOLE port scan range ---------------------
# remote-session.sh scans LA_REMOTE_PROXY_PORT_MIN..MAX (default 4141-4151) and takes the
# first FREE port, so a session launched while an earlier proxy is alive lands on 4142+.
# Matching only 4141 rendered those as CLOUD sessions -- a cap and a gateway figure on a
# session that bills nothing, the exact lie this lane exists to remove. A 4141-only
# fixture cannot catch that, so these probe NON-FIRST ports deliberately.
for port in 4142 4145 4151; do
    RANGED="$(spend_line "$(frender "http://127.0.0.1:$port")")"
    has "port $port renders as a free session, not a gateway one" "$RANGED" "free api session"
    hasnt "port $port shows no cap" "$RANGED" "/\$40"
done
FOUT="$(spend_line "$(frender http://127.0.0.1:4152)")"
has "port 4152 (outside the range) is still a cloud session" "$FOUT" "today:"
hasnt "…and is not claimed as free" "$FOUT" "free api session"

# --- line 1 names the model even when the payload omits display_name ------------------
# The harness does not always send model.display_name. Reading only that field rendered
# the literal word "Claude" with no model and no effort, on cloud sessions included.
IDONLY='{"model":{"id":"claude-opus-5[1m]"},"effort":{"level":"high"},
 "workspace":{"current_dir":"/tmp"},"cost":{"total_cost_usd":1.0},"session_id":"sess-x"}'
MOUT="$(printf '%s' "$IDONLY" | env COST_TRACKER_STATUSLINE=0 \
    COST_TRACKER_LEDGER_DIR="$FLED" COST_TRACKER_HISTORY="$FTMP/none.log" \
    sh "$RENDER" 2>/dev/null | sed $'s/\033\[[0-9;]*m//g' | head -1)"
has   "model.id is used when display_name is absent" "$MOUT" "claude-opus-5"
hasnt "…and the bare fallback word is not shown instead" "$MOUT" "Claude "
has   "…and the effort level still renders beside it"    "$MOUT" "high"

# display_name still wins when both are present.
BOTH='{"model":{"id":"claude-opus-5[1m]","display_name":"Opus 5 (1M context)"},
 "effort":{"level":"high"},"workspace":{"current_dir":"/tmp"},
 "cost":{"total_cost_usd":1.0},"session_id":"sess-x"}'
BOUT="$(printf '%s' "$BOTH" | env COST_TRACKER_STATUSLINE=0 \
    COST_TRACKER_LEDGER_DIR="$FLED" COST_TRACKER_HISTORY="$FTMP/none.log" \
    sh "$RENDER" 2>/dev/null | sed $'s/\033\[[0-9;]*m//g' | head -1)"
has   "display_name still takes precedence over id" "$BOUT" "Opus 5 (1M)"
hasnt "…and the raw id is not shown when a display name exists" "$BOUT" "claude-opus-5["

# --- free-agents helpers must be found WITHOUT relying on PATH ------------------
#
# THE BUG THIS GUARDS, and why it hid for days: the renderer located
# la-session-identity.sh / la-statusline-segment.sh / la-telemetry-token-rate.sh with
# `command -v`. Those live only in the free-agents PLUGIN CACHE bin, which is on the
# PATH of Claude Code's Bash tool but NOT on the plain PATH the CLI spawns a statusline
# with. So every hand-run test printed the real model name, RAM and tok/s, while the
# LIVE statusline silently omitted all three and showed the spoofed "Opus 5". A test that
# inherits the developer's PATH cannot see this -- so run under `env -i` with a minimal
# PATH, which is the only condition that reproduces it.
echo
echo "free-agents helper discovery (plain PATH)"

PTMP="$(mktemp -d)"
PLED="$PTMP/ledger"; mkdir -p "$PLED"

# A local session: kind comes from the loopback endpoint, model name from the resolver.
PPAYLOAD='{"model":{"display_name":"Opus 5","id":"claude-opus-5"},
 "workspace":{"current_dir":"/Users/x"},
 "context_window":{"total_input_tokens":"12345","used_percentage":"12"},
 "cost":{"total_cost_usd":1.5},"session_id":"sess-path"}'

# env -i drops PATH entirely, so give it ONLY the system dirs: jq lives in /usr/bin and
# must still be found, while the plugin bin deliberately must not be.
plain_render() {
    printf '%s' "$PPAYLOAD" | env -i HOME="$HOME" PATH=/usr/bin:/bin:/usr/sbin:/sbin \
        COST_TRACKER_STATUSLINE=0 COST_TRACKER_LEDGER_DIR="$PLED" \
        COST_TRACKER_HISTORY="$PTMP/none.log" \
        MODEL_ALIAS="qwen-3.6-operator" ANTHROPIC_BASE_URL="http://localhost:8003" \
        sh "$RENDER" 2>/dev/null | sed $'s/\033\[[0-9;]*m//g'
}

POUT="$(plain_render | head -1)"
has   "the REAL model name is rendered on a plain PATH" "$POUT" "qwen-3.6-operator"
hasnt "...and the spoofed model name is not shown instead" "$POUT" "Opus 5"

# Sanity: the helpers really are unreachable via PATH here, so the assertions above pass
# because of absolute-path discovery and not because the environment leaked them in.
if env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin sh -c 'command -v la-session-identity.sh' >/dev/null 2>&1; then
    bad "the test environment genuinely lacks the plugin bin on PATH" "not found" "found on PATH"
else
    ok "the test environment genuinely lacks the plugin bin on PATH"
fi

rm -rf "$PTMP"

rm -rf "$FTMP"

echo
printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
