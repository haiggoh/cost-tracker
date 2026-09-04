---
name: cost-tracker
description: "Use when reporting, reconciling, or reasoning about Claude Code API spend — today's cost, a session's cost, whether a budget cap is close, what local offload saved, or why two spend figures disagree. Also use before writing any dollar figure about token spend into a message, a note, or a status line, and when the cost ledger looks wrong (a total that exceeds the cap, a session counted twice, a figure that dropped). ALSO use whenever the daily budget cap has changed, was raised or lowered, or the user says the cap is wrong / out of date — that is a `cost-tracker cap --learn`, which re-reads the cap from the gateway's own refusal message. Do NOT use for non-spend statusline work or for gateway auth problems."
---

# cost-tracker

## The one rule

**Every dollar figure names its own membership.** An unlabelled figure is not a
minor style problem; it is the failure mode this tool was built after.

On 2026-08-10 a spend line labelled "prior sessions" silently included the running
session and under-counted **$14 of $18.16**. The label had *inferred* membership
from session age — a long-running session already has a ledger entry, so it was in
the total all along. The fix is not a better guess; it is never guessing:

| Axis | Means | Never |
|---|---|---|
| `session` | one session's LIFETIME cost, possibly across several UTC days | called "today" |
| `today` | gateway spend across ALL sessions with an entry on one UTC day | assumed to exclude the current session |
| `local` | localhost inference — free compute, reported as savings | added to cloud spend |

Two dollar figures on one line with only one label is the same bug wearing a hat.
The status line already shows a session-lifetime `$cost`; the segment this plugin
adds says `today: cloud …` out loud for exactly that reason.

## Commands

```
cost-tracker report                      # today, per-session table
cost-tracker report --week   --json      # 7 days; --month, --since YYYY-MM-DD
cost-tracker statusline                  # today: cloud $30.12/$40 · local saved $4.80
cost-tracker doctor                      # quarantined records + resolved config
cost-tracker calibrate                   # our daily totals vs the gateway's own figure
```

The table **is** the audit of the label — a reader can total the period column
themselves. Quote it rather than retyping numbers out of it.

## Reading it honestly

- **A quarantined record is not a missing one.** It counts as $0, is excluded from
  every figure, and is listed by `doctor` with a reason. Never describe a total as
  complete while records are quarantined; say how many sessions are affected.
- **The cap is LEARNED, not remembered.** llmgw's `/key/info` is 403 for a scoped
  virtual key, so the cap cannot be asked for — but the gateway states it whenever it
  refuses: `Budget has been exceeded! Key=… Current cost: 40.11, Max budget: 40.0`.
  That turn is persisted, so `cost-tracker cap --learn` reads the cap out of the newest
  refusal and stores it with its provenance. **When the user says the cap changed, run
  `--learn`** — the newest refusal wins. An explicit `COST_TRACKER_CAP_USD` still beats
  it, and with neither, spend prints no denominator. Never substitute a remembered
  number for a read one; `cost-tracker cap` shows exactly which refusal a cap came from.
- **Prose that quotes the refusal is not evidence.** The learner keys on
  `isApiErrorMessage`, the marker Claude Code sets on a rejected turn. A conversation
  *about* a budget kill — including this one — must never teach the cap. That mistake
  was made on the first probe of the feature.
- **`local saved: not measured` is not `$0.00`.** Zero saved claims local work
  happened and was worth nothing; not-measured says nothing priced it. Reporting an
  absent savings ledger as zero understates savings forever while looking right.
- **A period longer than today comes from the history log**, grouped by
  `(session_id, utc_date)` with the last row winning. Rows are cumulative
  snapshots: summing them multiplies a session by how many times it rendered.
- **Local sessions show $0 cloud by design.** A local render zeroes the cost field
  while the baseline keeps the cumulative carried in. With field 4 present that is
  unambiguously local; a 3-field `<date> 0 <baseline>` (before 2026-08-29) could
  equally be a resumed counter that has not billed yet, so it reports as `zero`
  rather than being asserted local. Either way it is $0.
- **A figure marked `*` is a FLOOR, not an estimate.** The session's cost counter
  RESET mid-period — Claude Code restarts `total_cost_usd` at 0 when a session is
  resumed — so the baseline carried into the day describes a counter that no longer
  exists. The day is anchored at the reset, which means spend earlier that same day,
  before the reset, is not in the ledger at all. Say "at least $X" for those.

## ⚠️ The daily total is KNOWN to undercount by ~20%

Do not present `today` as exact. `cost-tracker calibrate` measures it against the gateway's
own stated cumulative and finds 16 of 17 calibrated days short by $6.84-$9.16 — a median
**80.8%** of what was actually charged. Because a refusal blocks the key for the rest of
the window, on every day the cap was hit the ledger should have read close to $40 and read
~$32 instead.

**What that means when you quote the figure:** treat `today` as a FLOOR, and say so when
the number is being used to decide whether there is headroom — the error runs in the
direction that walks a session into a hard stop while the display still promises room.
Sessions that never render a statusline were the leading suspicion and are now measured
out: 1-3 turns each, 1-3% of a day, ~10x too small. Never close the gap by scaling or
clamping; every self-consistent version of this bug was wrong.

## When a figure looks wrong

1. `cost-tracker calibrate` — is this day's total externally wrong, and by how much?
2. `cost-tracker doctor` — quarantine first, grouped by session.
3. `cost-tracker report --json` — the exact per-session split.
4. `~/.claude/cost-ledger-history.log` — one row per statusline render, so an
   anomaly stays diagnosable after the per-session file has been overwritten.

Do not "fix" a figure by clamping it. A cumulative BELOW its carried-in baseline is
a resumed counter, and clamping the negative delta to 0 is what reported $16.07 of
real spend as $0.00 on 2026-08-26 — a plausible-looking figure hiding a whole
session. Anchor at the reset and label the result a floor.

## Wiring on a machine

A session has one status line, so this plugin never claims it. It ships the wrapper
pattern; `install/wire-statusline.sh` (dry-run by default) points a machine's
`~/.claude/scripts/` copies at the plugin, backs up what it replaces, and rolls
back if the chain stops rendering.
