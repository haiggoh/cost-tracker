
echo
echo "C. budget-tally.py — the gateway markup shrinks the DENOMINATOR, and only when measured"

# The warning line is the surface that actually misled a live session: "$32.43 of $40 cap"
# reads as 19% of headroom left, while the gateway was already at the wall. C1/C2 are the
# COMPATIBILITY half — with nothing recorded the message must be what it always was, which
# is what keeps ordinary first-party Claude Code unaffected.
HELPER="$(cd "$(dirname "$0")" && pwd)/helpers/tally_line.py"   # $0 is the SOURCING suite

tally_line() {  # $1 = cap.json contents ("" = nothing learned), $2 = ANTHROPIC_BASE_URL
  cfgdir="$TMP/ct-config-$$-$RANDOM"
  mkdir -p "$cfgdir"
  if [ -n "$1" ]; then printf '%s\n' "$1" > "$cfgdir/cap.json"; fi
  # every -u BEFORE the first VAR=: env stops parsing options at an assignment
  env -u ANTHROPIC_AUTH_TOKEN -u COST_TRACKER_MARKUP -u COST_TRACKER_FEE \
  -u COST_TRACKER_CAP_USD -u BUDGET_TALLY_CAP_USD \
  ANTHROPIC_API_KEY="${TALLY_API_KEY-}" \
  ANTHROPIC_BASE_URL="${2-https://gw.example.test}" \
  COST_TRACKER_CONFIG_DIR="$cfgdir" \
  BUDGET_TALLY_LEDGER_DIR="$TMP/mk-ledger" \
  BUDGET_TALLY_TODAY="2026-09-10" \
  BUDGET_TALLY_PROJECTS_DIR="$TMP/empty-projects" \
  python3 "$HELPER"
}

contains() {  # $1 = label, $2 = haystack, $3 = needle
  case "$2" in
    *"$3"*) is "$1" "ok" "ok" ;;
    *)      is "$1" "$2" "...$3..." ;;
  esac
}

lacks() {  # $1 = label, $2 = haystack, $3 = needle that must be ABSENT
  case "$2" in
    *"$3"*) is "$1" "$2" "NOT to contain '$3'" ;;
    *)      is "$1" "ok" "ok" ;;
  esac
}

mkdir -p "$TMP/mk-ledger" "$TMP/empty-projects"
echo "2026-09-10 32.43 0" > "$TMP/mk-ledger/aaaaaaaa-0000-0000-0000-000000000001"

# 0.12.0: the daily cap is a GATEWAY's, learned from its refusal. Every pre-0.12 assertion below
# now runs on an (unregistered) gateway endpoint WITH a learned $40 — the shape it was written for.
CAP='"key":{"cap_usd":40.0,"scope":"key"}'

plain="$(tally_line "{\"contract\":1,$CAP}")"
contains "no fee: the message is unchanged" "$plain" 'of $40 cap'
contains "no fee: 81% of the raw cap" "$plain" "81% of daily cap"

# A HAND-SET fee (the legacy hand-set markup migrates to it) moves both figures to the gateway axis.
marked="$(tally_line "{\"contract\":1,$CAP,\"markup\":{\"factor\":1.2271,\"n_days\":0,\"basis\":\"set by hand\"}}")"
contains "fee: the cap shown is the gateway's own \$40" "$marked" 'of $40 cap'
contains "fee: and names the factor as a step" "$marked" 'fee 1.23 (set by hand (migrated from markup)) = $39.79'
contains "fee: the total shown is the BILLED figure, 32.43 x 1.2271" "$marked" '$39.79'
lacks "fee: no deflated second cap is invented" "$marked" 'effective cap'
contains "fee: the ledger row on disk is still list price" \
    "$(cat "$TMP/mk-ledger/aaaaaaaa-0000-0000-0000-000000000001")" '32.43'
contains "fee: the same spend is 99%, not 81%" "$marked" "99% of daily cap"

broken="$(tally_line "{\"contract\":1,$CAP,\"markup\":{\"factor\":\"nope\"}}")"
contains "a corrupt factor falls back to the raw cap" "$broken" 'of $40 cap'

# A LEARNED markup is an observation: it migrates to the GAP, which the warning projects and
# names separately, while the fee and the billed figure stay at list price.
gap="$(tally_line "{\"contract\":1,$CAP,\"markup\":{\"factor\":1.1,\"n_days\":7,\"cv\":0.02,\"basis\":\"measured\"}}")"
# 0.13.0: the HEADLINE is the projection (what the gateway will quote); the breakdown follows.
contains "gap: the headline is the projection, 32.43 x 1.1" "$gap" '≈ $35.67 of $40 cap'
contains "gap: the breakdown starts from the calculated list figure" "$gap" 'calculated $32.43 at list price'
contains "gap: the projection is named as an unknown" "$gap" 'measured gap 1.100 (cause unknown, calibrated on 7 refusal day(s)) = $35.67'
lacks "gap: no fee step when the fee is 1.0" "$gap" ' fee '
contains "gap: the WARNING fires on the projection (32.43 x 1.1 = 89%)" "$gap" '89% of daily cap'
both="$(tally_line "{\"contract\":1,$CAP,\"fee\":{\"factor\":1.05},\"gap\":{\"factor\":1.1,\"n_days\":7}}")"
contains "fee+gap: headline = 32.43 x 1.05 x 1.1" "$both" '≈ $37.46 of $40 cap'
contains "fee+gap: the fee step shows the billed figure" "$both" 'fee 1.05 (set by hand) = $34.05'
nogap="$(COST_TRACKER_APPLY_GAP=0 tally_line "{\"contract\":1,$CAP,\"gap\":{\"factor\":1.1,\"n_days\":7}}")"
lacks "gap: COST_TRACKER_APPLY_GAP=0 removes the projection" "$nogap" 'measured gap'

# THE DISCRIMINANT for strangers: a direct Anthropic connection never sees a $40 it does not
# have — neither the old hard default nor this machine's learned gateway cap.
direct="$(tally_line "{\"contract\":1,$CAP}" "")"
lacks "direct Anthropic: no \$40 cap is invented" "$direct" '$40'
lacks "direct Anthropic: no daily-cap percentage" "$direct" 'of daily cap'
fresh="$(tally_line "" "")"
lacks "a fresh machine with no config shows no \$40 either" "$fresh" '$40'
key="$(TALLY_API_KEY=sk-test-not-real tally_line "{\"contract\":1,$CAP,\"fee\":{\"factor\":1.05}}" "")"
contains "an API-key session says its cap is MONTHLY" "$key" 'MONTHLY tier cap'
lacks "…and a gateway fee on this machine never reaches it" "$key" 'fee ×'
