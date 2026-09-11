---
name: cost-tracker
description: "Use when reporting, reconciling, or reasoning about Claude Code API spend — today's cost, a session's cost, whether a budget cap is close, what local offload saved, or why two spend figures disagree. Also use before writing any dollar figure about token spend into a message, a note, or a status line, and when the cost ledger looks wrong (a total that exceeds the cap, a session counted twice, a figure that dropped). ALSO use whenever the daily budget cap has changed, was raised or lowered, or the user says the cap is wrong / out of date — that is a `cost-tracker cap --learn`, which re-reads the cap from the gateway's own refusal message. ALSO use when spend looks systematically LOW against a budget, when a gateway or LiteLLM account bills more than the reported figure, or the user asks why the cap is hit earlier than the total suggests — that is `cost-tracker markup`. Do NOT use for non-spend statusline work or for gateway auth problems."
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

## NEVER quote a raw spend number from the ledger or a JSON field

**Read this even if you read nothing else on this page.**

The numbers stored on disk are **list price** — what the tokens would cost with no
gateway in front of them. This gateway bills **more** than list price. So a stored
number is **smaller than what the user is actually charged**, and repeating it
under-reports their spend.

**So: do not read spend out of `~/.claude/cost-ledger/*`, out of a transcript's
`total_cost_usd`, or out of a `cloud_usd` JSON field, and put it in front of the
user. Run the command instead:**

```bash
cost-tracker statusline    # one line, correct
cost-tracker report        # the full table, correct
cost-tracker today         # today's spend, correct
```

Every one of these applies the markup for you. **Whatever they print is the number
to say. Nothing else is.**

Why this warning exists: the raw figure and the real figure look equally plausible —
neither is malformed, and there is no error to notice. Reading the file "just to
check" is exactly how a wrong number reaches the user with total confidence behind
it. There is no case where hand-reading the ledger is the right move; the commands
are not a slower path to the same answer, they are the only path to the right one.

| If you want | Run this | Do NOT |
|---|---|---|
| today's spend | `cost-tracker statusline` | `cat` a ledger file and add it up |
| a session's cost | `cost-tracker report` | read `total_cost_usd` from the transcript |
| is the cap close? | `cost-tracker statusline` | compare a raw total to `$40` yourself |

### Which axis a figure is on

The recording stays at list price on purpose — it is the canonical measurement and
the only figure Claude Code itself asserts. The markup is stored **separately** and
applied on top, at every surface the user sees. Both figures in a user-facing pair
are therefore on the **gateway axis**, matching the cap the gateway's own refusal
message quotes:

| Surface | Shows | Axis |
|---|---|---|
| status line segment | `today: cloud $37.35/$40 gw` | gateway — both figures |
| `budget-tally` warning | `$37.35 of $40 cap (gateway ×1.24 applied)` | gateway — both figures |
| `cost-tracker report` | `TOTAL cloud` **and** `billed (gw)`, each labelled | both, named |
| the ledger on disk | `2026-09-11 30.12 0` | list price — never shown raw |

An earlier version deflated the **denominator** instead (`$30.12/$32 eff`). It was
arithmetically identical, but the user reads `$40` in the refusal message and `$32`
here, and concludes the tool is wrong. Same ratio, worse answer.

## Commands

```
cost-tracker report                      # today, per-session table
cost-tracker report --week   --json      # 7 days; --month, --since YYYY-MM-DD
cost-tracker statusline                  # today: cloud $30.12/$40 · local saved $4.80
cost-tracker doctor                      # quarantined records + resolved config
cost-tracker calibrate                   # our daily totals vs the gateway's own figure
cost-tracker markup                      # gateway markup + provenance (1.0 on a normal account)
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

## ⚠️ On a reselling gateway, an ACCURATE total still under-predicts the cap

The long-standing "~20% undercount" is **resolved, and it was not our arithmetic.** Claude
Code's `total_cost_usd` is correct (a session reconstructed from its own usage records
reproduces it), and cost-tracker reads that figure rather than recomputing it. The gap was
**entirely gateway-side**: this gateway bills a median **×1.23** of Anthropic list price,
steady over 19 calibrated days (CV 0.053).

**What that means when you quote the figure.** Our number is what the tokens cost, and it
is right. It is *not* what the cap counts. With a $40 cap the refusal lands near **$32** of
list-price spend, so `today` is a **FLOOR against the cap** — say so whenever the number is
being used to decide whether there is headroom, because the error runs in the direction that
walks a session into a hard stop while the display still promises room. "$32.43 of $40" is
the shape of that trap.

- Run `cost-tracker markup` to see whether a factor is recorded on this machine. If one is,
  `statusline` and `budget-tally` already report against the **effective** cap and you can
  quote the percentage as-is.
- If `calibrate` shows a steady ratio and no markup is recorded yet, run
  `cost-tracker calibrate --learn-markup`. It refuses unless ≥5 calibrated days agree
  closely enough to be one rate — a refusal is a result, not a failure.
- **On a normal Anthropic account none of this applies**, the factor is `1.0`, and no markup
  can ever be learned (it takes a gateway refusal message, which never occurs). Do not go
  hunting for a missing 20% there.
- **Still never close a gap by scaling or clamping OUR figure.** The markup is a separate,
  provenance-carrying axis that moves the denominator; that is the opposite of a fudge
  factor, and the external check stays intact. Ruled out, so don't re-investigate:
  day-boundary misattribution, missing sessions, a long-context premium (there is none),
  tokenizer inflation, a second consumer of the key.

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
