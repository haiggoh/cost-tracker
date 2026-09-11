
echo
echo "C. budget-tally.py — the gateway markup shrinks the DENOMINATOR, and only when measured"

# The warning line is the surface that actually misled a live session: "$32.43 of $40 cap"
# reads as 19% of headroom left, while the gateway was already at the wall. C1/C2 are the
# COMPATIBILITY half — with nothing recorded the message must be what it always was, which
# is what keeps ordinary first-party Claude Code unaffected.
HELPER="$(cd "$(dirname "$0")" && pwd)/helpers/tally_line.py"   # $0 is the SOURCING suite

tally_line() {  # $1 = cap.json contents, or "" for a machine that has learned nothing
  cfgdir="$TMP/ct-config-$$-$RANDOM"
  mkdir -p "$cfgdir"
  if [ -n "$1" ]; then printf '%s\n' "$1" > "$cfgdir/cap.json"; fi
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

plain="$(tally_line "")"
contains "no markup: the message is unchanged" "$plain" 'of $40 cap'
contains "no markup: 81% of the raw cap" "$plain" "81% of daily cap"

marked="$(tally_line '{"contract":1,"markup":{"factor":1.2271,"n_days":19}}')"
# BOTH user-facing figures sit on the GATEWAY axis: the cap the user is measured against
# is the gateway's $40 and its refusal message quotes that number, so a deflated
# denominator here read as a second, unexplained cap. The list-price total stays canonical
# in the LEDGER (unchanged on disk — asserted below) and in `cost-tracker report`; it is
# simply not the figure a one-line warning leads with.
contains "markup: the cap shown is the gateway's own \$40" "$marked" 'of $40 cap'
contains "markup: and says the factor was applied" "$marked" 'gateway ×1.23 applied'
contains "markup: the total shown is the BILLED figure, 32.43 x 1.2271" "$marked" '$39.79'
lacks "markup: no deflated second cap is invented" "$marked" 'effective cap'
# The RECORDED value must stay at list price — the multiplier is applied at the surface,
# never baked into the ledger. This is what keeps the recording canonical.
contains "markup: the ledger row on disk is still list price" \
    "$(cat "$TMP/mk-ledger/aaaaaaaa-0000-0000-0000-000000000001")" '32.43'
# The finding, as a number a reader can act on: the same spend goes from a comfortable
# 81% to essentially spent.
contains "markup: the same spend is 99%, not 81%" "$marked" "99% of daily cap"

# A corrupt factor must fall back to identity, never zero or invert the denominator.
broken="$(tally_line '{"contract":1,"markup":{"factor":"nope"}}')"
contains "a corrupt markup falls back to the raw cap" "$broken" 'of $40 cap'
