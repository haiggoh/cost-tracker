#!/usr/bin/env bash
# Tests for the two 2026-08-29 fixes to the cost-accounting chain:
#
#   A. cost-ledger-capture.sh — the ENDPOINT-CHANGE GUARD. A session that runs LOCAL and is then
#      resumed ON CLOUD must not book its phantom local cumulative as cloud spend. Field 4 carries
#      the suppressed phantom so a local zero is distinguishable from a not-yet-spent zero.
#   B. budget-tally.py — the rate table must be able to price the model this machine ACTUALLY runs.
#      An unpriceable current model made the reconstruction fallback silently return $0.00 for
#      44.3M tokens, so the "never dropped" guarantee was false.
#
# Everything runs against a temp HOME. Nothing here touches the live ledger, and the wrapper is
# invoked exactly the way the statusLine does: JSON on stdin, renderer output on stdout.
#
# Written for bash 3.2 (/bin/bash on macOS) — no associative arrays, no ${var^^}.
set -u

# This machine's locale is comma-decimal (de_DE), which makes awk PRINT "1,07813" and, worse,
# would make it PARSE "40.42" as 40 if any arithmetic were added here. Pin the numeric locale so
# the assertions test the code rather than the environment.
export LC_ALL=C

# This suite loads budget-tally.py via importlib, which otherwise drops a __pycache__ directory
# into ~/.claude/scripts — a stray artifact that was deliberately cleaned out of that dir on
# 2026-08-29. Don't let running the tests litter the thing they test.
export PYTHONDONTWRITEBYTECODE=1

# Resolve the scripts INSIDE this plugin, not the machine's wired copies: a test that
# silently exercises ~/.claude/scripts would pass against code this repo does not ship.
SCRIPTS_DIR="$(cd "$(dirname "$0")/../bin" && pwd)"
WRAPPER="$SCRIPTS_DIR/cost-ledger-capture.sh"
TALLY="$SCRIPTS_DIR/budget-tally.py"
export TALLY

PASS=0
FAIL=0
ok()   { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  FAIL %s\n     expected: %s\n     actual:   %s\n' "$1" "$2" "$3"; }
is()   { [ "$2" = "$3" ] && ok "$1" || bad "$1" "$3" "$2"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# A fake renderer stands in for statusline-render.sh so we can prove the passthrough is verbatim.
# The wrapper resolves it as "$HOME/.claude/scripts/statusline-render.sh".
mkdir -p "$TMP/.claude/scripts" "$TMP/.claude/cost-ledger"
cat > "$TMP/.claude/scripts/statusline-render.sh" <<'EOF'
cat > /dev/null; printf 'RENDERED_OK'
EOF
chmod +x "$TMP/.claude/scripts/statusline-render.sh"

SID="11111111-2222-3333-4444-555555555555"
LEDGER="$TMP/.claude/cost-ledger/$SID"

# Drive one statusLine render. $1 = reported total_cost_usd, $2 = ANTHROPIC_BASE_URL ("" = cloud).
render() {
  printf '{"session_id":"%s","cost":{"total_cost_usd":%s}}' "$SID" "$1" \
    | HOME="$TMP" ANTHROPIC_BASE_URL="${2:-}" sh "$WRAPPER"
}

echo "A. cost-ledger-capture.sh — endpoint-change guard"

# --- A1: a LOCAL render records $0 AND remembers the phantom in field 4.
out="$(render 12.5 http://localhost:8000)"
is "local render passes the renderer output through verbatim" "$out" "RENDERED_OK"
set -- $(cat "$LEDGER"); is "local render books \$0 (field 2)" "$2" "0"
is "local render remembers the phantom (field 4)" "$4" "12.5"

# --- A2: phantom keeps updating while local; still $0 booked.
render 40.42186999999999 http://127.0.0.1:8001 > /dev/null
set -- $(cat "$LEDGER")
is "still \$0 while local" "$2" "0"
is "phantom tracks the latest local cumulative" "$4" "40.42186999999999"

# --- A3: THE REGRESSION. Resumed on cloud: the first cloud render hands over the whole lifetime
# cumulative. Baseline must become the phantom, so today's spend is only the cloud delta.
render 41.5 "" > /dev/null
set -- $(cat "$LEDGER")
is "cloud render records the real cumulative" "$2" "41.5"
is "baseline re-anchored to the phantom" "$3" "40.42186999999999"
delta="$(awk -v c="$2" -v b="$3" 'BEGIN{printf "%.5f", c-b}')"
is "attributable spend is the CLOUD delta only, not the whole local run" "$delta" "1.07813"
is "field 4 dropped once cloud (3-field record)" "$#" "3"

# --- A4: FALSE-POSITIVE CHECK. An ordinary cloud session that renders BEFORE its first billed
# turn also has cum=0 — the naive "0 -> nonzero" rule would zero its first real turn. It must not.
SID2="99999999-8888-7777-6666-555555555555"
L2="$TMP/.claude/cost-ledger/$SID2"
render2() { printf '{"session_id":"%s","cost":{"total_cost_usd":%s}}' "$SID2" "$1" \
  | HOME="$TMP" ANTHROPIC_BASE_URL="${2:-}" sh "$WRAPPER" > /dev/null; }
render2 0 ""      # statusline render at session start, nothing spent yet
render2 0.85 ""   # first billed turn
set -- $(cat "$L2")
is "cloud-zero is NOT treated as a phantom (baseline stays 0)" "$3" "0"
is "first real turn is still counted in full" "$2" "0.85"

# --- A5: the local gate must not fire for a non-local base URL that merely mentions localhost.
SID3="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
printf '{"session_id":"%s","cost":{"total_cost_usd":3.3}}' "$SID3" \
  | HOME="$TMP" ANTHROPIC_BASE_URL="https://llmgw.example.com/localhost" sh "$WRAPPER" > /dev/null
set -- $(cat "$TMP/.claude/cost-ledger/$SID3")
is "remote host containing 'localhost' still books real cost" "$2" "3.3"
is "remote host writes no phantom field" "$#" "3"

# --- A6: history log gets the raw (pre-gate) cost as field 6, so a handoff is legible later.
hist_line="$(grep " $SID " "$TMP/.claude/cost-ledger-history.log" | head -1)"
set -- $hist_line
is "history records the phantom as field 6 while local" "$6" "12.5"
is "history still records the gated cost as field 4" "$4" "0"

# --- A7: fail-safe. The passthrough must survive a ledger it cannot write.
chmod 500 "$TMP/.claude/cost-ledger"
out="$(render 99 "")"
is "statusline still renders when the ledger is unwritable" "$out" "RENDERED_OK"
chmod 700 "$TMP/.claude/cost-ledger"

echo
echo "B. budget-tally.py — the live model must be priceable"

price_of() {  # $1 = model id -> "PRICED" or "UNPRICED"
  BUDGET_TALLY_LEDGER_DIR="$TMP/empty" python3 - "$1" <<'PY'
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("bt", os.environ["TALLY"])
bt = importlib.util.module_from_spec(spec); spec.loader.exec_module(bt)
print("PRICED" if bt.rate_for(sys.argv[1]) else "UNPRICED")
PY
}

is "claude-opus-5 is priceable (the gap that returned \$0 for 44.3M tokens)" "$(price_of claude-opus-5)" "PRICED"
is "bracketed long-context variant normalizes" "$(price_of 'claude-opus-5[1m]')" "PRICED"
is "claude-opus-4-8 still priceable" "$(price_of claude-opus-4-8)" "PRICED"
is "a genuinely unknown model is still reported" "$(price_of claude-nonexistent-9)" "UNPRICED"

# --- B5: THE RECURRENCE GUARD. Every model id actually present in recent transcripts must be
# priceable. This is what fails at the NEXT model rename, instead of silently tallying $0.
unpriced="$(python3 - <<'PY'
import glob, importlib.util, json, os
spec = importlib.util.spec_from_file_location("bt", os.environ["TALLY"])
bt = importlib.util.module_from_spec(spec); spec.loader.exec_module(bt)
seen, bad = set(), set()
paths = sorted(glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")),
               key=os.path.getmtime, reverse=True)[:25]
for p in paths:
    try:
        with open(p, errors="ignore") as f:
            for line in f:
                if '"model"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                m = (d.get("message") or {}).get("model")
                if m and m not in seen:
                    seen.add(m)
                    if not bt.is_non_billable(m) and bt.rate_for(m) is None:
                        bad.add(m)
    except OSError:
        continue
print(",".join(sorted(bad)))
PY
)"
is "no model id in the 25 most recent transcripts is unpriceable" "${unpriced:-none}" "none"

# --- B6: <synthetic> must be silently non-billable, not reported as a pricing gap.
noise="$(python3 - <<'PY'
import importlib.util, os
spec = importlib.util.spec_from_file_location("bt", os.environ["TALLY"])
bt = importlib.util.module_from_spec(spec); spec.loader.exec_module(bt)
_t, _p, unknown = bt._price({"<synthetic>": {"input":0,"output":0,"cache_write_5m":0,
                                             "cache_write_1h":0,"cache_read":0}})
print(",".join(sorted(unknown)) or "none")
PY
)"
is "<synthetic> is not reported as an unpriced model" "$noise" "none"

# --- B7: a LOCAL model served under its on-disk path is non-billable, not a pricing gap.
# Regression for the 2026-09-03 recurrence-guard hit: local inference is free compute, and
# reporting it as "unpriced" is how a genuine gap gets lost in permanent noise. The second
# half of the assertion is the one that matters — a bare cloud slug must STILL be reported.
localnoise="$(python3 - <<'PYSNIP'
import importlib.util, os
spec = importlib.util.spec_from_file_location("bt", os.environ["TALLY"])
bt = importlib.util.module_from_spec(spec); spec.loader.exec_module(bt)
zero = {"input":0,"output":0,"cache_write_5m":0,"cache_write_1h":0,"cache_read":0}
_t, _p, unknown = bt._price({"/Users/me/.models/Ornith-1.5-35B-A3B-MLX-4bit": zero,
                             "claude-imaginary-7": zero})
print(",".join(sorted(unknown)) or "none")
PYSNIP
)"
is "a local path-style model id is not reported as unpriced" "$localnoise" "claude-imaginary-7"

. "$(cd "$(dirname "$0")" && pwd)/helpers/markup_section.inc"

# --- 0.5.1: --help is answered, never silently RUN -----------------------------------
# Before this, --help fell through to the SessionStart path and printed a LIVE spend line,
# so probing the script both triggered the work and produced output that read as help.
TALLY="$(cd "$(dirname "$0")" && pwd)/../bin/budget-tally.py"
H="$(python3 "$TALLY" --help 2>&1 </dev/null)"
case "$H" in
  *"tally today's Claude Code API spend"*) ok "--help explains what the script does" ;;
  *) bad "--help explains what the script does" "a purpose line" "$H" ;;
esac
case "$H" in
  *"--check"*) ok "--help documents the --check mode" ;;
  *) bad "--help documents the --check mode" "--check" "$H" ;;
esac
case "$H" in
  *"COST_TRACKER_CAP_USD"*) ok "--help documents the env vars" ;;
  *) bad "--help documents the env vars" "COST_TRACKER_CAP_USD" "$H" ;;
esac
# The tell that it did not RUN: a real run always prints "today's spend ... $<digit>".
if printf '%s' "$H" | grep -q "today's spend"; then
  bad "--help does not print a live tally" "no live tally" "printed one"
else
  ok "--help does not print a live tally"
fi
python3 "$TALLY" --nope >/dev/null 2>&1
if [ "$?" -eq 2 ]; then ok "an unrecognised flag exits 2"; else bad "an unrecognised flag exits 2" "exit 2" "exit $?"; fi


echo
printf 'passed %s, failed %s\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
