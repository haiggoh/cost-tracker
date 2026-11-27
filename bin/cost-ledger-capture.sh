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
# LOCAL records grow two more fields (0.14.0): "<date> <cum> <base> <phantom> <phantom_base>"
# — field 4 is the suppressed phantom cumulative (see below), field 5 the phantom CARRIED
# INTO <utc_date>, mirroring field 3. A multi-day free session's phantom attributable to
# today is (field 4 - field 5), so it no longer re-donates its earlier days to "today
# saved" (measured 2026-10-13: c441f503 carried ~$73.77 of pre-midnight phantom inside a
# "~$427 saved today" statusline). Legacy 4-field lines have no field 5 and self-heal at
# the next UTC midnight, like the 2-field legacy rule above.
#
# A gateway render appends one more token, `k=<last4>` (field 6 on the session line,
# field 7 on the history row): the last-4 hint of the key that paid at this render,
# probed read-only from this process's env via bin/key_identity.py next to this script.
# Old readers ignore trailing tokens (budget-tally reads fields 1-3; cost-tracker's
# parser peels a trailing k= token), so the format stays append-only. Local renders
# never carry it — free compute is no key's gateway spend.
#
# The HISTORY row alone may also carry `p=<pid>` (field 8, 0.23.0): the owning
# `claude` process, found by walking up to 4 parent hops from this wrapper. It lets
# read_history() attribute counter streams exactly; legacy rows without it keep the
# inference rules. The session (ledger) line is NOT tagged.
#
# FAIL-SAFE: every capture step is guarded; the passthrough ALWAYS runs, so a
# bug here can never blank the statusline.

# COST_TRACKER_PS is a TEST-ONLY override for the ps binary used by the pid-tag
# walk below. A set-but-not-executable override is a hard error, never a silent
# fallthrough to the real ps (which would let a test pass against the machine's
# real process table instead of the stub).
if [ -n "${COST_TRACKER_PS:-}" ] && [ ! -x "$COST_TRACKER_PS" ]; then
  printf 'cost-ledger-capture.sh: COST_TRACKER_PS is set but not executable: %s\n' "$COST_TRACKER_PS" >&2
  exit 2
fi

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
  # The UTC day, retried once: an empty $(date) (see NOW_TS below) would otherwise write a
  # dateless ledger record. Still empty -> skip this render's ledger write entirely; the
  # statusline still renders, and the next render records the cumulative.
  TODAY_UTC=$(date -u +%F 2>/dev/null)
  [ -n "$TODAY_UTC" ] || TODAY_UTC=$(date -u +%F 2>/dev/null)
  if [ -n "$SID" ] && [ -n "$COST" ] && [ "$COST" != "null" ] && [ -n "$TODAY_UTC" ]; then
    LEDGER="$HOME/.claude/cost-ledger"
    mkdir -p "$LEDGER" 2>/dev/null || true
    DEST="$LEDGER/$SID"
    # Establish today's BASELINE = the cumulative cost carried into today for this
    # session. Read the prior record (own file only -> no cross-session race):
    #   - no file / brand-new session      -> baseline 0 (session starts today)
    #   - prior record dated today          -> keep the baseline already set today
    #   - prior record dated an earlier day -> baseline = that day's ending cumulative
    # today's attributable spend is then (cum - baseline); see header.
    BASE=0
    PRIOR_PHANTOM=""
    PRIOR_CUM=0
    # PHANTOM BASELINE (0.14.0), field 5 — the phantom carried INTO today, mirroring
    # BASE: no file -> 0; prior record dated today -> keep the baseline established
    # today (a legacy 4-field line has none: 0 — old behaviour, self-heals at the next
    # midnight); prior record dated an earlier day -> that day's ENDING anchor (field 4).
    PHBASE=0
    if [ -f "$DEST" ]; then
      # split "<date> <cum> [<baseline>] [<phantom> [<phantom_baseline>]]" into $1..$5
      # shellcheck disable=SC2046
      set -- $(cat "$DEST" 2>/dev/null)
      PRIOR_PHANTOM="${4:-}"     # non-empty only if the PREVIOUS render was local (field 4)
      PRIOR_CUM="${2:-0}"
      if [ "$1" = "$TODAY_UTC" ]; then
        BASE="${3:-0}"           # same UTC day: keep established baseline (legacy 2-field -> 0)
        PHBASE="${5:-0}"         # same UTC day: keep established phantom baseline (legacy -> 0)
      else
        BASE="${2:-0}"           # day rolled over mid-session: prior cumulative carries in
        PHBASE="${4:-0}"         # ...and the prior ENDING phantom anchor carries in
      fi
    fi
    # TRANSITION LOG (0.14.0). Lane changes RE-ANCHOR state silently (the freeze below,
    # the endpoint-change guard, the zero-carry); a wrong re-anchor is exactly the kind
    # of anomaly the history log exists for but cannot show. One line per LANE CHANGE —
    # never per render: steady local or steady cloud renders log nothing. Best-effort,
    # and deliberately OUTSIDE "$LEDGER" like the history log below (readers scan that
    # directory as sessions; see the note there).
    TRANS_LOG="$HOME/.claude/cost-ledger-transitions.log"
    # ONE timestamp per render, retried once (0.21.5). `$(date ...)` intermittently came back
    # EMPTY (58 history rows 2026-11-19..22, across 15 sessions, no torn writes): the row then
    # starts with a blank field, every column shifts left, and readers quarantine it. An empty
    # stamp now SKIPS the log lines instead — history rows are cumulative snapshots, so the
    # next render supersedes a skipped one; a malformed row is worse than a missing one.
    NOW_TS=$(date -u +%FT%TZ 2>/dev/null)
    [ -n "$NOW_TS" ] || NOW_TS=$(date -u +%FT%TZ 2>/dev/null)
    log_transition() {
      [ -n "$NOW_TS" ] || return 0
      printf '%s %s %s\n' "$NOW_TS" "$SID" "$1" >> "$TRANS_LOG" 2>/dev/null || true
    }
    # CLOUD -> LOCAL (added 0.10.1). A local render must not ERASE the cloud cumulative the
    # session already reached: it FREEZES it. Before this, the gate wrote 0 and the record's
    # last row — which is what every reader uses — reported a session that had spent real gateway
    # money as $0.00. Measured 2026-10-04 on 9bc4ee06: $4.64 on Opus 5.5, then a switch to the
    # local lane, then a day total missing exactly that $4.64. A brand-new local session has no
    # prior record, so PRIOR_CUM is 0 and the old "<date> 0 <baseline> <phantom>" shape is kept.
    if [ "$IS_LOCAL" = "1" ]; then
      COST="$PRIOR_CUM"
      # cloud -> local transition: log it — only the FIRST local render after a cloud
      # one; while local, the prior record itself carries field 4.
      if [ -f "$DEST" ] && [ -z "$PRIOR_PHANTOM" ]; then
        log_transition "cloud->local frozen_cum=$PRIOR_CUM anchor=$RAW_COST"
      fi
    fi
    # MIDNIGHT-CROSSING LIMITATION (known, unfixed). When a session starts before 00:00 UTC
    # and its FIRST render lands after it, there is no prior entry to carry a baseline in, so
    # the whole incoming cumulative is attributed to the new day: an OVERCOUNT for that session
    # on that day. Deriving the true pre-midnight split would require re-pricing the transcript
    # per model, which this hot path (one run per statusline render) cannot afford. The bias is
    # deliberately toward overcounting rather than undercounting, so the cap is never
    # under-reported. Tracked as an open defect; do not "fix" it with a transcript scan here.
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
      OLD_BASE="$BASE"
      # LC_ALL=C because this machine's locale is comma-decimal: awk would otherwise parse
      # "40.42186999999999" as 40, truncating at the '.'. Harmless for a >0 test, but the next
      # person to do arithmetic here would get a silently wrong baseline.
      #
      # The new baseline is phantom - frozen_cloud + old_baseline, not the bare phantom: the
      # phantom is the LIFETIME cumulative and so includes any cloud stretch BEFORE the local
      # one, which is real spend already booked today and must survive the handoff
      # (cloud -> local -> cloud). With no earlier cloud stretch this reduces to the phantom.
      # Clamped at 0: a resume can restart the counter below the frozen figure, and a negative
      # baseline would be quarantined by every reader; the clamp undercounts only that case.
      #
      # RESTORED-CLOUD RESUME (0.21.5, measured 2026-11-23 on ec04ea46). The formula above assumes
      # the phantom INCLUDES the frozen cloud stretch. When the phantom is SMALLER than the frozen
      # cloud cumulative it cannot — the local counter restarted at 0 — and if the resumed cloud
      # counter is back at (or past) the frozen figure, Claude Code restored the cloud lifetime:
      # the phantom is not in it at all, so the baseline already in force (same-day, or the
      # rolled-over prior cumulative) is correct and must be kept. The formula here produced
      # 1.21 - 33.43 + 33.43 = 1.21 and booked the whole pre-switch $33 to the new day.
      if LC_ALL=C awk -v p="$PRIOR_PHANTOM" -v f="$PRIOR_CUM" -v c="$COST" \
           'BEGIN{ exit !(p+0 > 0 && p+0 < f+0 && c+0 >= f+0) }' 2>/dev/null; then
        log_transition "local->cloud-restored prior_phantom=$PRIOR_PHANTOM prior_cum=$PRIOR_CUM kept_base=$BASE"
      elif LC_ALL=C awk -v p="$PRIOR_PHANTOM" -v c="$COST" \
           'BEGIN{ exit !(p+0 > 0 && c+0 > 0) }' 2>/dev/null; then
        if LC_ALL=C awk -v f="$PRIOR_CUM" 'BEGIN{ exit !(f+0 > 0) }' 2>/dev/null; then
          BASE=$(LC_ALL=C awk -v p="$PRIOR_PHANTOM" -v f="$PRIOR_CUM" -v b="$BASE" \
            'BEGIN{ n = p - f + b; if (n < 0) n = 0; printf "%.15g\n", n }' 2>/dev/null) \
            || BASE="$PRIOR_PHANTOM"
        else
          BASE="$PRIOR_PHANTOM"  # no earlier cloud stretch: the phantom verbatim, unrounded
        fi
        log_transition "local->cloud prior_phantom=$PRIOR_PHANTOM prior_cum=$PRIOR_CUM old_base=$OLD_BASE new_base=$BASE"
      fi
    fi
    # KEY HINT (per-key budgets). When this render's endpoint is a KNOWN gateway and a
    # token env is present, the paying key's last-4 hint is appended as a trailing
    # `k=<hint>` token. The allowlist (provider registry + host_map + price cache) is
    # evaluated inside bin/key_identity.py, so a key paired with e.g. api.openai.com is
    # never tagged. Best-effort: any failure (no python3, probe error, odd token) leaves
    # the line in its legacy shape; local renders never carry a hint.
    KEY_HINT=""
    HINT_TOK=""
    if [ "$IS_LOCAL" = "0" ] && command -v python3 >/dev/null 2>&1; then
      KISELF="$0"; HOPS=0
      while [ -L "$KISELF" ] && [ "$HOPS" -lt 10 ]; do
        LINK="$(readlink "$KISELF")"
        case "$LINK" in /*) KISELF="$LINK" ;; *) KISELF="$(dirname "$KISELF")/$LINK" ;; esac
        HOPS=$(( HOPS + 1 ))
      done
      # --register also records the key's login route (gateway host) in keys.json, so a
      # key without a refusal of its own inherits a cap only from a same-route sibling.
      KEY_HINT="$(python3 "$(dirname "$KISELF")/key_identity.py" --probe-claude-cli --register 2>/dev/null | awk '{print $2}')"
      # exactly 4 non-whitespace chars (anything hint_of can produce), nothing else
      case "$KEY_HINT" in
        ""|*[[:space:]]*) HINT_TOK="" ;;
        *) if [ "${#KEY_HINT}" -eq 4 ]; then HINT_TOK=" k=$KEY_HINT"; else HINT_TOK=""; fi ;;
      esac
    fi
    # PID TAG (0.23.0): a trailing `p=<pid>` token on the HISTORY row only, naming the
    # owning `claude` process so read_history() can attribute counter streams exactly.
    # Walk from this wrapper's parent up at most 4 hops (the statusline chain is
    # sh -> claude, so the common case matches on the first hop). Best-effort: no
    # claude found, or a non-numeric pid, -> no token. The ledger line is never tagged.
    PID_TOK=""
    _ocp_pid="${PPID:-}"
    case "$_ocp_pid" in ''|*[!0-9]*) _ocp_pid=$(LC_ALL=C "${COST_TRACKER_PS:-ps}" -o ppid= -p $$ 2>/dev/null | tr -d ' ') ;; esac
    _ocp_hops=0
    while [ "$_ocp_hops" -lt 4 ]; do
      case "$_ocp_pid" in ''|*[!0-9]*) break ;; esac
      _ocp_line=$(LC_ALL=C "${COST_TRACKER_PS:-ps}" -o ppid=,comm= -p "$_ocp_pid" 2>/dev/null)
      _ocp_ppid=${_ocp_line%% *}
      _ocp_comm=${_ocp_line#* }
      if [ "${_ocp_comm##*/}" = "claude" ]; then
        PID_TOK=" p=$_ocp_pid"
        break
      fi
      _ocp_pid="$_ocp_ppid"
      _ocp_hops=$((_ocp_hops+1))
    done
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
    [ -n "$NOW_TS" ] && [ -n "$TODAY_UTC" ] && \
      printf '%s %s %s %s %s %s%s%s\n' "$NOW_TS" "$SID" "$TODAY_UTC" "$COST" "$BASE" "$RAW_COST" "$HINT_TOK" "$PID_TOK" \
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
    # next render can tell a local zero from a not-yet-spent zero (see the guard above) —
    # and a FIFTH (0.14.0): the phantom carried into today, so the day's phantom is the
    # field-4 delta over it (see the header). budget-tally.py reads fields 1-3 and ignores
    # extras, so this stays backward compatible.
    #
    # FREE -> CLOUD with a ZERO in between (0.11.2). `claude --resume` on the gateway can render
    # BEFORE the resumed lifetime cumulative is restored, reporting 0. The guard above does not
    # fire on a zero (correctly), but writing a 3-field record here would DROP the phantom, so the
    # next render — carrying the whole free lifetime — would have nothing to re-anchor on and
    # would book it all as gateway spend. A cloud zero after a local stretch therefore CARRIES the
    # phantom forward (and the frozen cloud cumulative) until real cloud cost arrives.
    CARRY=""
    if [ "$IS_LOCAL" = "0" ] && [ -n "$PRIOR_PHANTOM" ] && \
       LC_ALL=C awk -v c="$COST" 'BEGIN{ exit !(c+0 == 0) }' 2>/dev/null; then
      CARRY="$PRIOR_PHANTOM"
      COST="$PRIOR_CUM"
      log_transition "local->cloud-zero carried_phantom=$PRIOR_PHANTOM frozen_cum=$PRIOR_CUM"
    fi
    if [ "$IS_LOCAL" = "1" ] || [ -n "$CARRY" ]; then
      printf '%s %s %s %s %s%s\n' "$TODAY_UTC" "$COST" "$BASE" "${CARRY:-$RAW_COST}" "$PHBASE" "$HINT_TOK" > "$DEST.tmp" 2>/dev/null \
        && mv "$DEST.tmp" "$DEST" 2>/dev/null || true
    else
      printf '%s %s %s%s\n' "$TODAY_UTC" "$COST" "$BASE" "$HINT_TOK" > "$DEST.tmp" 2>/dev/null \
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
# (0.7.8) The SIBLING renderer is preferred: settings.json now calls this file in the
# plugin checkout directly, so the renderer next to it is the one that matches it.
# $0 is resolved through symlinks first, so the old ~/.claude/scripts entry point
# lands in the same place. No readlink -f: not portable to every /bin/sh.
SELF="$0"; HOPS=0
while [ -L "$SELF" ] && [ "$HOPS" -lt 10 ]; do
  LINK="$(readlink "$SELF")"
  case "$LINK" in /*) SELF="$LINK" ;; *) SELF="$(dirname "$SELF")/$LINK" ;; esac
  HOPS=$((HOPS + 1))
done
RENDERER="$(dirname "$SELF")/statusline-render.sh"
[ -f "$RENDERER" ] || RENDERER="$HOME/.claude/scripts/statusline-render.sh"
[ -f "$RENDERER" ] || RENDERER="$HOME/.claude/joyia-statusline.sh"
printf '%s' "$INPUT" | sh "$RENDERER"
