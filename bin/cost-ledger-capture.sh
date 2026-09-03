#!/usr/bin/env sh
# Cost-ledger capture wrapper for the Claude Code statusLine.
#
# WHY: budget-tally.py reconstructing spend by re-pricing transcript tokens
# over-counts (cache-token rates ~4x too high). The AUTHORITATIVE number is
# .cost.total_cost_usd, which Claude Code delivers to the statusLine on stdin
# but never persists. This wrapper persists it, then hands stdin to the real
# joyia statusline UNCHANGED so the display is identical.
#
# Ledger: ~/.claude/cost-ledger/<session_id>  ->  "<utc_date> <cum_cost_usd> <baseline>"
# where cum_cost_usd is the session's latest cumulative .cost.total_cost_usd and
# baseline is the cumulative value CARRIED INTO <utc_date> (i.e. the cumulative at
# the last render on the prior UTC day). A session's spend attributable to <utc_date>
# is therefore (cum - baseline). budget-tally sums (cum - baseline) across sessions
# whose date == today (UTC). This 3-field format fixes the multi-day-session bug:
# a session spanning several UTC days no longer dumps its whole lifetime cost onto
# whichever day it last rendered — each UTC day's baseline resets to the cumulative
# carried in, so only that day's delta counts. Legacy 2-field entries ("<date> <cum>")
# are still read (baseline defaults to 0) and self-heal the first time the day rolls
# over under this code.
#
# FAIL-SAFE: every capture step is guarded; the passthrough ALWAYS runs, so a
# bug here can never blank the statusline.

INPUT="$(cat)"

# --- capture (best-effort; never fails the passthrough) ---
if command -v jq >/dev/null 2>&1; then
  SID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || true)
  COST=$(printf '%s' "$INPUT" | jq -r '.cost.total_cost_usd // empty' 2>/dev/null || true)
  # Local Plan-B sessions run on FREE local compute, but Claude Code still computes
  # total_cost_usd from token usage (phantom, endpoint-agnostic). Never book that against
  # the paid gateway budget — record $0 (user requirement: local session == $0).
  #
  # GATE ON THE ACTUAL ENDPOINT, not CLAUDE_IS_LOCAL. That flag LEAKS: a gateway `claude`
  # launched from a shell that earlier ran launch-claude-agent.sh inherits CLAUDE_IS_LOCAL=true,
  # which under the old flag-only gate booked REAL gateway spend as $0 — silently undercounting
  # the $40 cap (confirmed 2026-07-24: session 48cc2c86 on llmgw recorded $0). The launcher points
  # ANTHROPIC_BASE_URL at http://localhost:<port>, so the endpoint is the ground truth; if it's
  # unset we fail SAFE (record real cost, never wrongly zero).
  #
  # RAW_COST keeps what Claude Code actually REPORTED, before the gate zeroes it. While the
  # session is local that number is phantom (free compute, priced at Opus rates) — but it has
  # to be REMEMBERED, because a local session can later be resumed ON CLOUD. At that first
  # cloud render Claude Code hands us the whole LIFETIME cumulative, and with no phantom to
  # subtract, the entire local run lands on the handoff day as cloud spend. Measured
  # 2026-08-29 on session 289b103c: $55.93 reported = 140% of the $40 cap, all of it phantom.
  # The phantom is carried in FIELD 4 and consumed by the endpoint-change guard below.
  RAW_COST="$COST"
  IS_LOCAL=0
  case "$ANTHROPIC_BASE_URL" in
    http://localhost*|http://127.0.0.1*|https://localhost*|https://127.0.0.1*)
      COST="0"; IS_LOCAL=1 ;;
  esac
  if [ -n "$SID" ] && [ -n "$COST" ] && [ "$COST" != "null" ]; then
    LEDGER="$HOME/.claude/cost-ledger"
    mkdir -p "$LEDGER" 2>/dev/null || true
    DEST="$LEDGER/$SID"
    TODAY_UTC=$(date -u +%F)
    # Establish today's BASELINE = the cumulative cost carried into today for this
    # session. Read the prior record (own file only -> no cross-session race):
    #   - no file / brand-new session      -> baseline 0 (session starts today)
    #   - prior record dated today          -> keep the baseline already set today
    #   - prior record dated an earlier day -> baseline = that day's ending cumulative
    # today's attributable spend is then (cum - baseline); see header.
    BASE=0
    PRIOR_PHANTOM=""
    if [ -f "$DEST" ]; then
      # split "<date> <cum> [<baseline>] [<local_phantom_cum>]" (whitespace) into $1..$4
      # shellcheck disable=SC2046
      set -- $(cat "$DEST" 2>/dev/null)
      PRIOR_PHANTOM="${4:-}"     # non-empty only if the PREVIOUS render was local (field 4)
      if [ "$1" = "$TODAY_UTC" ]; then
        BASE="${3:-0}"           # same UTC day: keep established baseline (legacy 2-field -> 0)
      else
        BASE="${2:-0}"           # day rolled over mid-session: prior cumulative carries in
      fi
    fi
    # ENDPOINT-CHANGE GUARD (added 2026-08-29). A session that ran LOCAL and is then resumed
    # ON CLOUD is the one case where the incoming cumulative is not this day's spend: it is
    # the phantom local total, which the gate above has been zeroing render after render. So
    # when the PREVIOUS render was local (field 4 present) and this one is NOT (COST > 0),
    # the phantom becomes the baseline — today's attributable spend is what accrues AFTER the
    # handoff, which is exactly the cloud portion.
    #
    # Field 4 is what makes this exact, and it is why the guard is not written as the tempting
    # "prior cum == 0 and now > 0". That naive form cannot tell a LOCAL zero from an ordinary
    # cloud session that simply rendered its statusline BEFORE its first billed turn — which is
    # the common case, and would silently zero that session's first turn of REAL spend.
    if [ -n "$PRIOR_PHANTOM" ] && [ "$IS_LOCAL" = "0" ]; then
      # LC_ALL=C because this machine's locale is comma-decimal: awk would otherwise parse
      # "40.42186999999999" as 40, truncating at the '.'. Harmless for a >0 test, but the next
      # person to do arithmetic here would get a silently wrong baseline.
      if LC_ALL=C awk -v p="$PRIOR_PHANTOM" -v c="$COST" \
           'BEGIN{ exit !(p+0 > 0 && c+0 > 0) }' 2>/dev/null; then
        BASE="$PRIOR_PHANTOM"
      fi
    fi
    # HISTORY (added 2026-08-18): the per-session file is OVERWRITTEN every render, so when a
    # figure looks wrong the state that produced it is already gone. That is exactly why the
    # 2026-08-08 over-report ($33.41 reported vs $8.37 authoritative) could not be root-caused:
    # by the time it was noticed, the record had self-healed at the next render 5 minutes later.
    # Append-only history makes the next anomaly diagnosable after the fact instead of requiring
    # someone to catch it live at a UTC midnight. One line per render, same fields plus a UTC
    # timestamp. Best-effort and guarded like everything else here.
    # DELIBERATELY OUTSIDE "$LEDGER": budget-tally.py's read_ledger_today() iterates every file in
    # the ledger dir and parses it as "<date> <cum> <baseline>", so a history file living there
    # would be parsed as if it were a session. It happens to be skipped today only because field 1
    # is an ISO timestamp that never equals a bare date -- an accident, not a guarantee. Keeping it
    # out of the scanned directory makes that structural instead of lucky.
    HIST="$HOME/.claude/cost-ledger-history.log"
    # Field 6 (RAW_COST, added 2026-08-29) is what Claude Code reported before the local gate —
    # so the history shows the phantom accruing while local instead of a flat run of zeros, and
    # a handoff is legible after the fact rather than looking like a sudden spike.
    printf '%s %s %s %s %s %s\n' "$(date -u +%FT%TZ)" "$SID" "$TODAY_UTC" "$COST" "$BASE" "$RAW_COST" \
      >> "$HIST" 2>/dev/null || true
    # Keep it bounded: trim to the most recent 20000 lines (~weeks of renders) when it grows past
    # 25000, so an append-only file cannot grow without limit.
    if [ -f "$HIST" ]; then
      HL=$(wc -l < "$HIST" 2>/dev/null | tr -d ' ')
      case "$HL" in
        ''|*[!0-9]*) : ;;
        *) if [ "$HL" -gt 25000 ]; then
             tail -n 20000 "$HIST" > "$HIST.tmp" 2>/dev/null \
               && mv "$HIST.tmp" "$HIST" 2>/dev/null || true
           fi ;;
      esac
    fi

    # session_id is a UUID -> safe filename. Atomic-ish overwrite.
    # A LOCAL render writes a FOURTH field: the phantom cumulative it just suppressed, so the
    # next render can tell a local zero from a not-yet-spent zero (see the guard above).
    # budget-tally.py reads fields 1-3 and ignores extras, so this stays backward compatible.
    if [ "$IS_LOCAL" = "1" ]; then
      printf '%s %s %s %s\n' "$TODAY_UTC" "$COST" "$BASE" "$RAW_COST" > "$DEST.tmp" 2>/dev/null \
        && mv "$DEST.tmp" "$DEST" 2>/dev/null || true
    else
      printf '%s %s %s\n' "$TODAY_UTC" "$COST" "$BASE" > "$DEST.tmp" 2>/dev/null \
        && mv "$DEST.tmp" "$DEST" 2>/dev/null || true
    fi
  fi
fi

# --- passthrough to the renderer (verbatim stdout) ---
# RENDERER (changed 2026-08-26): scripts/statusline-render.sh, a SUPERSET of the
# vendored joyia-statusline.sh -- same fields plus effort, dir basename, absolute
# context tokens / window size, per-request fresh+cache-read+cache-write tokens,
# and 5h/7d rate-limit percentages. The vendored script is NOT edited because
# `joyia agent --setup` regenerates it; it stays on disk as the fallback below.
RENDERER="$HOME/.claude/scripts/statusline-render.sh"
[ -f "$RENDERER" ] || RENDERER="$HOME/.claude/joyia-statusline.sh"
printf '%s' "$INPUT" | sh "$RENDERER"
