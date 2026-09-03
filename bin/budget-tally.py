#!/usr/bin/env python3
"""budget-tally.py — SessionStart + Stop hook.

Purpose: tally today's actual Claude Code API spend against the llmgw
$40/day cap and print a summary the user sees as a system message.

Two invocations, two jobs:
  (no args)  — SessionStart: print today's tally-so-far, over every session
               that has a ledger entry today. For a session starting fresh,
               that is only the earlier sessions (its own transcript doesn't
               exist yet). But do NOT state it as "prior sessions": a session
               that has been running since an earlier day already has a ledger
               entry which the statusline wrapper keeps updating, so it is
               included. Assuming otherwise under-counted a real long-running
               session by ~$14 (2026-08-10). The label describes membership;
               it never infers it from session age.
  --check    — Stop (fires after every assistant turn): recompute today's
               tally, this time including the current session's turns so
               far. Stays silent unless the warning threshold is newly
               crossed today, so it doesn't spam a line after every turn.

The running session's own transcript is located deterministically via
CLAUDE_CODE_SESSION_ID (see current_session_path()) — the same mechanism
~/.claude/scripts/compact_session.py uses to find "the current session":
the env var holds the session's UUID, which is also its transcript's
filename under the cwd-slug project dir. Its usage is tallied both as part
of the aggregate "files modified today" scan (which independently catches
it by mtime) and in isolation, so the reported line can show the current
session's own contribution as a real number, not just a scope label.
Fixed 2026-07-16 — previously there was no way to isolate "this session"'s
spend at all. The separate overshoot bug (tally reading HIGHER than real spend,
because token reconstruction over-priced cache tokens ~4x) is addressed
2026-07-21 by reading the authoritative cost-ledger instead of reconstructing —
see the "Spend source" note below and memory: cost-ledger-capture. Overshoot
can still occur only for the fallback path (sessions with no ledger entry).

Trigger: SessionStart + Stop — deliberately NOT a cron job. The task ("how
much have I spent today") is only relevant while a session is actually
running; a cron would fire uselessly into the void on days with no session.
See memory: llm-gateway-budget-limit, and the measure-twice skill's
survey-then-match-trigger rule.

Spend source (as of 2026-07-21): PRIMARY is the authoritative per-session
cost-ledger at ~/.claude/cost-ledger/ — Claude Code's own .cost.total_cost_usd,
captured by the statusLine wrapper cost-ledger-capture.sh (one-way: the
statusline's proven cost info -> ledger -> here; budget-tally only READS it,
it does NOT touch the statusline). This replaces the old token-reconstruction
for every session the ledger covers, fixing the ~4x over-count (cache-token
rates were too high). FALLBACK for sessions with no ledger entry (ran before
the wrapper existed, or before their first statusLine render): reconstruct cost
from raw token usage in the transcript (*.jsonl under ~/.claude/projects/**)
using the same math Claude Code's built-in tracker uses (published Anthropic
per-token rates + time-boxed intro discounts). See the pricing table below and
memory: cost-ledger-capture, budget-tally-sessionstart-hook.

Warning threshold: when today's tally reaches BUDGET_TALLY_WARN_PCT (default
0.75) of the cap, the printed line is prefixed with a ⚠️ WARNING tag instead
of the plain summary. On the `--check` (Stop) path, once that warning has
fired for today it's suppressed for the rest of the day via a dated stamp
file (BUDGET_TALLY_STAMP) — otherwise every subsequent turn would repeat it.

Test overrides (all optional):
  BUDGET_TALLY_PROJECTS_DIR   root to scan for *.jsonl (default ~/.claude/projects)
  BUDGET_TALLY_CAP_USD        the daily cap to report against (default 40)
  BUDGET_TALLY_WARN_PCT       warning threshold as a fraction (default 0.75)
  BUDGET_TALLY_STAMP          path to the once-per-day warn stamp (default ~/.claude/.budget-tally-warned)
  BUDGET_TALLY_TODAY          override "today" as YYYY-MM-DD (for testing)
"""
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

PROJECTS_DIR = os.environ.get(
    "BUDGET_TALLY_PROJECTS_DIR", os.path.expanduser("~/.claude/projects")
)
CAP_USD = float(os.environ.get("BUDGET_TALLY_CAP_USD", "40"))
WARN_PCT = float(os.environ.get("BUDGET_TALLY_WARN_PCT", "0.75"))
STAMP_PATH = os.environ.get("BUDGET_TALLY_STAMP", os.path.expanduser("~/.claude/.budget-tally-warned"))
# Authoritative per-session cost ledger written by the statusLine wrapper
# ~/.claude/scripts/cost-ledger-capture.sh (see memory: cost-ledger-capture).
LEDGER_DIR = os.environ.get("BUDGET_TALLY_LEDGER_DIR", os.path.expanduser("~/.claude/cost-ledger"))
# "Today" is UTC, matching the gateway's cap-reset clock (not local time) — record
# timestamps in transcripts are UTC (`...Z`), so this keeps the tally window aligned
# with what's actually being priced instead of drifting by the local UTC offset.
TODAY = os.environ.get("BUDGET_TALLY_TODAY") or datetime.now(timezone.utc).date().isoformat()
YESTERDAY = (date.fromisoformat(TODAY) - timedelta(days=1)).isoformat()

# Rate = $ per token (not per-MTok) to keep the multiply-by-tokens math simple.
# intro_until: if set and TODAY <= that date, use intro_input/intro_output.
PRICING = {
    "claude-sonnet-5": {
        "input": 3.00e-6, "output": 15.00e-6,
        "intro_input": 2.00e-6, "intro_output": 10.00e-6, "intro_until": "2026-08-31",
        "cache_write_5m_mult": 1.25, "cache_write_1h_mult": 2.0, "cache_read_mult": 0.1,
    },
    "claude-sonnet-4-6": {
        "input": 3.00e-6, "output": 15.00e-6,
        "cache_write_5m_mult": 1.25, "cache_write_1h_mult": 2.0, "cache_read_mult": 0.1,
    },
    "claude-opus-5": {
        "input": 5.00e-6, "output": 25.00e-6,
        "cache_write_5m_mult": 1.25, "cache_write_1h_mult": 2.0, "cache_read_mult": 0.1,
    },
    "claude-opus-4-8": {
        "input": 5.00e-6, "output": 25.00e-6,
        "cache_write_5m_mult": 1.25, "cache_write_1h_mult": 2.0, "cache_read_mult": 0.1,
    },
    "claude-opus-4-7": {
        "input": 5.00e-6, "output": 25.00e-6,
        "cache_write_5m_mult": 1.25, "cache_write_1h_mult": 2.0, "cache_read_mult": 0.1,
    },
    "claude-opus-4-6": {
        "input": 5.00e-6, "output": 25.00e-6,
        "cache_write_5m_mult": 1.25, "cache_write_1h_mult": 2.0, "cache_read_mult": 0.1,
    },
    "claude-haiku-4-5": {
        "input": 1.00e-6, "output": 5.00e-6,
        "cache_write_5m_mult": 1.25, "cache_write_1h_mult": 2.0, "cache_read_mult": 0.1,
    },
    "claude-fable-5": {
        "input": 10.00e-6, "output": 50.00e-6,
        "cache_write_5m_mult": 1.25, "cache_write_1h_mult": 2.0, "cache_read_mult": 0.1,
    },
}


# Pseudo-models that appear in the "model" field of a transcript record but are NOT billable
# and have no rates by design. They must not be reported as "unpriced model(s) excluded" —
# that note is meant to flag a real PRICING gap, and burying it among permanent non-entries is
# how a genuine gap goes unnoticed. `<synthetic>` is Claude Code's marker for records it
# generates itself (123 of them on this machine, all with zero token usage).
NON_BILLABLE_MODELS = {"<synthetic>"}


def is_non_billable(model):
    """True for a model id that has no cloud price BY DESIGN, so it must never be
    reported as a pricing gap.

    Two kinds:
      * `<synthetic>` — Claude Code's marker for records it generates itself.
      * a LOCAL model served under its on-disk path, e.g.
        `/Users/me/.models/Ornith-1.5-35B-A3B-MLX-4bit`. Local inference is free
        compute; pricing it is meaningless, and flagging it as unpriced buries a real
        gap in permanent noise. A `/` is the discriminator and a safe one: every
        cloud model id is a bare slug (`claude-opus-5`, optionally with a bracketed
        context variant) and never contains a path separator.

    Found by the recurrence guard on 2026-09-03, which is exactly its job: a local
    session's model id had started appearing in transcripts and was being reported as
    an unpriced cloud model."""
    if not model:
        return False
    if model in NON_BILLABLE_MODELS:
        return True
    return "/" in model or model.startswith("~")


def _canonical_model(model):
    """Strip a trailing bracketed variant suffix, e.g. 'claude-opus-5[1m]' -> 'claude-opus-5'.

    Claude Code identifies a long-context variant that way (this machine's own model reports as
    `claude-opus-5[1m]`), and such an id is a DIFFERENT dict key — so an unstripped suffix
    silently prices the session at $0 while looking like an unknown model. Rates are per-token
    and identical across the context variants, so the base id is the correct lookup."""
    if not model:
        return model
    base, sep, _ = model.partition("[")
    return base if sep else model


def rate_for(model):
    p = PRICING.get(_canonical_model(model))
    if p is None:
        return None
    intro_until = p.get("intro_until")
    if intro_until and TODAY <= intro_until:
        in_rate = p.get("intro_input", p["input"])
        out_rate = p.get("intro_output", p["output"])
    else:
        in_rate, out_rate = p["input"], p["output"]
    return {
        "input": in_rate,
        "output": out_rate,
        "cache_write_5m": in_rate * p["cache_write_5m_mult"],
        "cache_write_1h": in_rate * p["cache_write_1h_mult"],
        "cache_read": in_rate * p["cache_read_mult"],
    }


def _session_id_of(path):
    base = os.path.basename(path)
    return base[:-6] if base.endswith(".jsonl") else base


def read_ledger_today():
    """Read the authoritative per-session cost ledger written by the statusLine
    wrapper ~/.claude/scripts/cost-ledger-capture.sh (see memory: cost-ledger-capture).

    Each file is named by session UUID and holds '<utc_date> <cum_cost_usd> <baseline>'
    (3-field, current) or the legacy '<utc_date> <cum_cost_usd>' (2-field). cum_cost_usd
    is the session's latest cumulative .cost.total_cost_usd — Claude Code's own
    ground-truth number (same as the built-in cost tracker / joyia statusline). baseline
    is the cumulative CARRIED INTO <utc_date> (the cumulative at the last render on the
    prior UTC day). A session's spend attributable to <utc_date> is therefore
    (cum - baseline). budget-tally only READS this; capture is done entirely by the
    statusLine wrapper (one-way: statusline info -> ledger -> here). Returns
    {session_id: today_spend} for entries dated TODAY.

    Authoritative — replaces token reconstruction for any session it covers, because
    reconstruction over-counts (cache-token rates ~4x too high). Sessions with no ledger
    entry (ran before the wrapper existed, or before their first statusLine render) fall
    back to reconstruction so they're never dropped.

    Multi-day sessions (FIXED 2026-07-29): the 3-field baseline is what makes a session
    spanning several UTC days count only THAT day's delta instead of dumping its whole
    lifetime cumulative onto the day it last rendered. This bug is what once showed a
    4-day session's $75 lifetime cost as "today" (189% of the $40 cap). Legacy 2-field
    entries have no baseline -> baseline 0 -> today_spend = full cumulative (the old
    behaviour); such an entry self-heals the first time the capture wrapper re-renders it
    across a UTC midnight. An already-EXITED legacy multi-day entry can only be corrected
    by hand (we did this once for session 1b225c8c)."""
    out = {}
    try:
        entries = os.listdir(LEDGER_DIR)
    except OSError:
        return out
    for name in entries:
        if name.endswith(".tmp"):
            continue
        path = os.path.join(LEDGER_DIR, name)
        try:
            if not os.path.isfile(path):
                continue
            with open(path, "r", errors="ignore") as f:
                parts = f.read().split()
        except OSError:
            continue
        # Accept 2-field (legacy) or 3-field (current, with baseline).
        if len(parts) < 2 or parts[0] != TODAY:
            continue
        try:
            cum = float(parts[1])
            baseline = float(parts[2]) if len(parts) >= 3 else 0.0
        except ValueError:
            continue
        # today's attributable spend = cumulative minus what was carried into today.
        out[name] = max(0.0, cum - baseline)
    return out


def current_session_path():
    """Deterministically locate the running session's own transcript file,
    the same way ~/.claude/scripts/compact_session.py's is_likely_current_session()
    does: CLAUDE_CODE_SESSION_ID (or legacy CLAUDE_SESSION_ID) is the session's
    UUID, and its transcript is named exactly that under the cwd-slug project
    dir. Returns None if the env var isn't set or the file doesn't exist yet
    (e.g. at SessionStart, before the current session has written anything)."""
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID")
    if not session_id:
        return None
    cwd_slug = os.getcwd().replace("/", "-")
    candidate = os.path.join(PROJECTS_DIR, cwd_slug, f"{session_id}.jsonl")
    return candidate if os.path.isfile(candidate) else None


def files_modified_today():
    """Pre-filter by mtime (UTC date) to bound how many files tally() has to open.
    Widened to include yesterday's mtime too — a file can still be mid-write (mtime
    lagging) or hold a mix of yesterday's and today's records across a midnight-crossing
    session; tally() re-filters by each record's own timestamp, so this is just an
    inclusive candidate set, not the actual cutoff."""
    out = []
    for path in glob.glob(os.path.join(PROJECTS_DIR, "**", "*.jsonl"), recursive=True):
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).date().isoformat()
        except OSError:
            continue
        if mtime in (TODAY, YESTERDAY):
            out.append(path)
    # Belt-and-suspenders: the running session's own transcript should already
    # be caught by the mtime scan above (it's actively being written to), but
    # explicitly folding it in via CLAUDE_CODE_SESSION_ID removes any reliance
    # on mtime timing/clock-skew for the one file we can name with certainty.
    cur = current_session_path()
    if cur and cur not in out:
        out.append(cur)
    return out


def _record_is_today(d):
    ts = d.get("timestamp")
    if not ts:
        return False
    try:
        # Transcript timestamps are ISO-8601 UTC with a "Z" suffix.
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).date().isoformat() == TODAY
    except ValueError:
        return False


def tally(paths):
    usage = defaultdict(lambda: defaultdict(int))
    unknown_models = set()
    for path in set(paths):
        try:
            f = open(path, "r", errors="ignore")
        except OSError:
            continue
        with f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("type") != "assistant":
                    continue
                if not _record_is_today(d):
                    continue
                msg = d.get("message") or {}
                model = msg.get("model")
                u = msg.get("usage") or {}
                if not model or not u:
                    continue
                cc = u.get("cache_creation") or {}
                usage[model]["input"] += u.get("input_tokens", 0) or 0
                usage[model]["output"] += u.get("output_tokens", 0) or 0
                usage[model]["cache_write_5m"] += cc.get("ephemeral_5m_input_tokens", 0) or 0
                usage[model]["cache_write_1h"] += cc.get("ephemeral_1h_input_tokens", 0) or 0
                usage[model]["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
                if (_canonical_model(model) not in PRICING
                        and not is_non_billable(model)):
                    unknown_models.add(model)
    return usage, unknown_models


def _price(usage):
    total = 0.0
    priced_any = False
    unknown_models = set()
    for model, u in usage.items():
        rates = rate_for(model)
        if rates is None:
            if not is_non_billable(model):
                unknown_models.add(model)
            continue
        priced_any = True
        total += u["input"] * rates["input"]
        total += u["output"] * rates["output"]
        total += u["cache_write_5m"] * rates["cache_write_5m"]
        total += u["cache_write_1h"] * rates["cache_write_1h"]
        total += u["cache_read"] * rates["cache_read"]
    return total, priced_any, unknown_models


def compute():
    """Returns (total, pct, remaining, unknown_models, priced_any,
    current_session_total, ledger_total, recon_total).

    Spend is the authoritative cost-ledger sum (per-session .cost.total_cost_usd
    captured by the statusLine wrapper) PLUS token reconstruction for only those
    sessions the ledger doesn't cover — so we never double-count and never drop a
    session. ledger_total / recon_total are split out for the reported wording.

    current_session_total isolates the running session's own contribution to
    today's total (prefers its authoritative ledger value; falls back to
    reconstruction; 0.0 if it has no usage yet, e.g. at SessionStart before it's
    written anything)."""
    ledger = read_ledger_today()
    ledger_total = sum(ledger.values())

    paths = files_modified_today()
    # Reconstruct spend ONLY for sessions the ledger doesn't already cover
    # authoritatively — avoids double-counting a session both ways.
    uncovered = [p for p in paths if _session_id_of(p) not in ledger]
    usage, unknown_models = tally(uncovered)
    recon_total, recon_priced, unpriced = _price(usage)
    unknown_models |= unpriced

    total = ledger_total + recon_total
    priced_any = recon_priced or bool(ledger)
    pct = total / CAP_USD if CAP_USD else 0.0
    remaining = CAP_USD - total

    current_session_total = 0.0
    cur = current_session_path()
    if cur:
        sid = _session_id_of(cur)
        if sid in ledger:
            current_session_total = ledger[sid]
        else:
            cur_usage, _ = tally([cur])
            current_session_total, _, _ = _price(cur_usage)

    return total, pct, remaining, unknown_models, priced_any, current_session_total, ledger_total, recon_total


# Warning bands (fractions of the cap). Warn once per band per day, so a missed/invisible lower
# band still lets higher bands alert. WARN_PCT is the lowest band. (Replaced the old once-per-day
# stamp, which silenced the whole day after a single — sometimes invisible — fire.)
BANDS = sorted({WARN_PCT, 0.90, 1.00})


def bands_fired_today():
    """Set of band-floats already warned for TODAY, per the dated stamp `YYYY-MM-DD:0.75,0.9`.
    Any missing / stale-date / old-bare-date / corrupt stamp → empty set. Safe default: at worst we
    re-warn once; we never wrongly SUPPRESS a warning. partition() tolerates stray colons; the
    (OSError, ValueError) guard tolerates a non-float payload (validator-flagged edge case)."""
    try:
        with open(STAMP_PATH) as f:
            content = f.read().strip()
        date_part, sep, bands_part = content.partition(":")
        if date_part != TODAY or not sep or not bands_part:
            return set()  # stale date, or the legacy bare-date format → nothing fired today
        return {float(s) for s in bands_part.split(",") if s.strip()}
    except (OSError, ValueError):
        return set()


def record_bands_today(bands_iterable):
    try:
        with open(STAMP_PATH, "w") as f:
            f.write(f"{TODAY}:" + ",".join(str(b) for b in sorted(bands_iterable)))
    except OSError:
        pass  # non-fatal — worst case a band warning repeats once more


def format_line(total, pct, remaining, unknown_models, session_scope,
                current_session_total, ledger_total, recon_total):
    note = ""
    if unknown_models:
        note = f" (unpriced model(s) excluded: {', '.join(sorted(unknown_models))})"
    if current_session_total > 0:
        session_scope = f"{session_scope}, this session ≈ ${current_session_total:.2f}"
    # Report the basis honestly: authoritative ledger vs token reconstruction
    # (reconstruction over-counts, so flag when any is mixed in).
    if ledger_total > 0 and recon_total > 0:
        basis = f"${ledger_total:.2f} authoritative + ${recon_total:.2f} reconstructed"
    elif ledger_total > 0:
        basis = "authoritative statusline cost-ledger"
    else:
        basis = "reconstructed from token usage"
    prefix = f"⚠️ WARNING — {pct * 100:.0f}% of daily cap used: " if pct >= WARN_PCT else "budget-tally: "
    return (
        f"{prefix}today's spend ({session_scope}) ≈ ${total:.2f} of ${CAP_USD:.0f} "
        f"cap (~${remaining:.2f} left, {basis}){note}"
    )


def main_session_start():
    total, pct, remaining, unknown_models, priced_any, current_session_total, ledger_total, recon_total = compute()
    if not priced_any:
        return  # nothing billable found for today yet — stay silent
    # NOT "prior sessions": the label must describe what the number aggregates, not assume the
    # current session is absent. A session that has been running since an earlier day already has a
    # ledger entry the statusline wrapper keeps updating, so it IS in this total. Labelling it
    # "prior sessions" under-counted by ~$14 in a real long-running session (2026-08-10).
    print(format_line(total, pct, remaining, unknown_models, "all sessions with an entry today",
                      current_session_total, ledger_total, recon_total))


def main_check():
    """Stop hook (fires after every assistant turn): recompute today's tally including the current
    session so far, and warn when a NEW threshold band is crossed today. Delivery: a Claude Code Stop
    hook ignores plain stdout, but surfaces a user-visible notice for `{"systemMessage": "..."}` — so
    emit that JSON, not a bare print (the old bare print fired invisibly). Per-band dedupe means a
    missed 75% still alerts at 90%/100%."""
    total, pct, remaining, unknown_models, priced_any, current_session_total, ledger_total, recon_total = compute()
    if not priced_any:
        return
    reached = [b for b in BANDS if pct >= b]
    if not reached:
        return
    already = bands_fired_today()
    if all(b in already for b in reached):
        return  # already warned for the highest band reached today
    line = format_line(total, pct, remaining, unknown_models, "all sessions",
                       current_session_total, ledger_total, recon_total)
    print(json.dumps({"systemMessage": line}))
    record_bands_today(reached)


if __name__ == "__main__":
    try:
        if "--check" in sys.argv[1:]:
            main_check()
        else:
            main_session_start()
    except Exception as e:  # never break a hook over a tallying bug
        print(f"budget-tally: skipped ({e})", file=sys.stderr)
