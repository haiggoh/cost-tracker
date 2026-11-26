#!/usr/bin/env bash
# Tests for the 0.23.0 pid tag: history rows gain a trailing `p=<pid>` token naming
# the owning `claude` process, so read_history() can attribute counter streams
# exactly instead of inferring them. Legacy untagged rows keep the inference rules.
#
# The parent-chain walk is driven through COST_TRACKER_PS, a stub that answers
# `ps -o ppid=,comm= -p <pid>` from a canned chain file, one line per call.
# Everything runs against a temp HOME; nothing touches the live ledger/history.
# Written for bash 3.2 — no associative arrays, no ${var^^}.
set -u
export LC_ALL=C
export PYTHONDONTWRITEBYTECODE=1

for a in "$@"; do
  case "$a" in
    --help|-h)
      echo "Usage: test_ledger_pid_tag.sh [--help]"
      echo "Runs the pid-tag (p=) capture tests against a temp HOME; no other arguments."
      exit 0 ;;
    *)
      echo "unknown flag: $a" >&2
      echo "Usage: test_ledger_pid_tag.sh [--help]" >&2
      exit 2 ;;
  esac
done

SCRIPTS_DIR="$(cd "$(dirname "$0")/../bin" && pwd)"
PASS=0
FAIL=0
ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n     expected: %s\n     actual:   %s\n' "$1" "$2" "$3"; }
is()  { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "$3" "$2"; fi; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/.claude/cost-ledger" "$TMP/.claude/cost-tracker" "$TMP/bin"
cp "$SCRIPTS_DIR/cost-ledger-capture.sh" "$SCRIPTS_DIR/key_identity.py" "$SCRIPTS_DIR/cost_connection.py" "$TMP/bin/"
WRAPPER="$TMP/bin/cost-ledger-capture.sh"
cat > "$TMP/bin/statusline-render.sh" <<'EOF'
cat > /dev/null; printf 'RENDERED_OK'
EOF
chmod +x "$TMP/bin/statusline-render.sh"

# gateway allowlist for the key-hint probe (same throwaway-config pattern as
# test_budget_ledger.sh section D)
cat > "$TMP/.claude/cost-tracker/cap.json" <<'JSON'
{"contract": 2, "connection": {"host_map": {"gw-key.example.test": "fake-gw"}}}
JSON
KEYCFG="$TMP/.claude/cost-tracker"

HISTF="$TMP/.claude/cost-ledger-history.log"
SID="21212121-2121-2121-2121-212121212121"

# stub ps: call N answers with line N of $STUB_CHAIN ("<ppid> <comm>"), then "0 none"
cat > "$TMP/ps-stub" <<'EOF'
#!/bin/sh
n=$(cat "${STUB_STATE:?}" 2>/dev/null || echo 0)
n=$((n+1)); printf '%s\n' "$n" > "${STUB_STATE:?}"
line=$(sed -n "${n}p" "${STUB_CHAIN:?}" 2>/dev/null)
[ -n "$line" ] || line="0 none"
printf '%s\n' "$line"
EOF
chmod +x "$TMP/ps-stub"
export STUB_CHAIN="$TMP/chain"
export STUB_STATE="$TMP/state"
setchain() { printf '%s\n' "$@" > "$STUB_CHAIN"; }
resetstub() { : > "$STUB_STATE"; }

# Drive one render with the stub ps. $1 = cost, $2 = base url ("" = cloud), $3 = token.
# Ambient token vars are scrubbed so the hint probe is hermetic (the D4 lesson:
# an explicitly empty token must not fall through to a real ambient key).
render() {
  printf '{"session_id":"%s","cost":{"total_cost_usd":%s}}' "$SID" "$1" \
    | env -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_API_KEY \
      HOME="$TMP" COST_TRACKER_CONFIG_DIR="$KEYCFG" ANTHROPIC_BASE_URL="$2" \
      ANTHROPIC_AUTH_TOKEN="$3" COST_TRACKER_PS="$TMP/ps-stub" sh "$WRAPPER"
}
# Same render against the REAL ps (P6 smoke: row must be well-formed either way).
render_real() {
  printf '{"session_id":"%s","cost":{"total_cost_usd":%s}}' "$SID" "$1" \
    | env -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_API_KEY -u COST_TRACKER_PS \
      HOME="$TMP" COST_TRACKER_CONFIG_DIR="$KEYCFG" ANTHROPIC_BASE_URL="$2" \
      ANTHROPIC_AUTH_TOKEN="$3" sh "$WRAPPER"
}

echo "P. pid tag (p=) on history rows"

# --- P1: chain sh -> claude(4242): history row ends ` p=4242`; ledger file unchanged
setchain "4242 sh" "1 /usr/local/bin/claude" "0 none"
resetstub
out="$(render 6.25 "" "")"
is "P1: passthrough stays verbatim" "$out" "RENDERED_OK"
# shellcheck disable=SC2046  # word splitting IS the record parser (as in test_budget_ledger.sh)
# shellcheck disable=SC2046
set -- $(tail -1 "$HISTF")
is "P1: history row has 7 fields" "$#" "7"
is "P1: field 7 is p=4242" "${7:-MISSING}" "p=4242"
# shellcheck disable=SC2046
set -- $(cat "$TMP/.claude/cost-ledger/$SID")
is "P1: ledger file unchanged (3 fields, no p=)" "$#" "3"

# --- P2: with a key hint, tokens after field 6: k= then p=
setchain "4242 sh" "1 /usr/local/bin/claude" "0 none"
resetstub
render 2.0 "https://gw-key.example.test/v1" "sk-fake-00000000wxyz" > /dev/null
# shellcheck disable=SC2046
set -- $(tail -1 "$HISTF")
is "P2: history row has 8 fields" "$#" "8"
is "P2: field 7 is k=wxyz" "${7:-MISSING}" "k=wxyz"
is "P2: field 8 is p=4242" "${8:-MISSING}" "p=4242"

# --- P3: no claude within 4 hops -> no p= token, row otherwise unchanged
setchain "100 sh" "101 sh" "102 sh" "103 sh" "0 none"
resetstub
render 3.5 "" "" > /dev/null
hist_line="$(tail -1 "$HISTF")"
# shellcheck disable=SC2086
set -- $hist_line
is "P3: history row keeps 6 fields" "$#" "6"
case " $hist_line " in
  *" p="*) bad "P3: no p= token when no claude in chain" "no p=" "present" ;;
  *) ok "P3: no p= token when no claude in chain" ;;
esac

# --- P4: a non-numeric pid from the stub yields no token
setchain "abc sh" "1 sh" "0 none"
resetstub
render 4.5 "" "" > /dev/null
# shellcheck disable=SC2046
set -- $(tail -1 "$HISTF")
is "P4: 6 fields after non-numeric stub pid" "$#" "6"

# --- P5: COST_TRACKER_PS set but not executable -> loud non-zero exit naming the var
echo x > "$TMP/notexec"    # mode 644: present but not executable
printf '{"session_id":"%s","cost":{"total_cost_usd":1}}' "$SID" \
  | env HOME="$TMP" COST_TRACKER_PS="$TMP/notexec" sh "$WRAPPER" > "$TMP/p5.out" 2> "$TMP/p5.err"
rc=$?
if [ "$rc" -ne 0 ]; then ok "P5: non-executable COST_TRACKER_PS exits non-zero (rc=$rc)"; else bad "P5: non-executable COST_TRACKER_PS exits non-zero" "non-zero" "rc=0"; fi
case "$(cat "$TMP/p5.err")" in
  *COST_TRACKER_PS*) ok "P5: stderr names COST_TRACKER_PS" ;;
  *) bad "P5: stderr names COST_TRACKER_PS" "mentions the var" "$(cat "$TMP/p5.err")" ;;
esac

# --- P6: real ps smoke — without the stub the row is well-formed either way
render_real 5.5 "" "" > /dev/null
# shellcheck disable=SC2046
set -- $(tail -1 "$HISTF")
if [ "$#" -eq 6 ]; then
  ok "P6: no claude within 4 hops of the test runner -> 6 fields"
elif [ "$#" -eq 7 ] && printf '%s' "${7:-}" | grep -Eq '^p=[0-9]+$'; then
  ok "P6: when a claude ancestor exists, field 7 is p=<numeric pid> (${7:-})"
else
  bad "P6: row well-formed (6 fields, or 7th = p=<digits>)" "6 fields or p=[0-9]+" "$# fields: $hist_line"
fi

# --- P7: a claude ancestor whose comm is NOT exactly "claude" yields no p= token
# (renamed binary / wrapper -> silent inference fallback, row still well-formed)
setchain "100 claude-wrapper" "1 sh" "0 none"
resetstub
render 6.5 "" "" > /dev/null
# shellcheck disable=SC2046
set -- $(tail -1 "$HISTF")
is "P7: non-claude comm yields 6 fields, no p= token" "$#" "6"

echo
printf 'passed %s, failed %s\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
