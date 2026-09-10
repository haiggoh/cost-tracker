# cost-tracker

Reports Claude Code spend on **three explicitly labelled axes** — session lifetime,
today across all sessions, and free local inference — from the authoritative
statusline cost ledger.

```
$ cost-tracker report
cost-tracker — period: today (UTC 2026-09-03)
membership: all sessions with a ledger or history entry on this UTC day

session       axis           today $  session lifetime $
--------------------------------------------------------
4fd535b8-89d  cloud            12.98               17.79
9ab6ba79-5ea  local             0.00                0.00
af719d53-2fe  cloud             1.85                1.85
--------------------------------------------------------
TOTAL cloud                    14.83                   —
cap                            40.00          (37% used)
local saved   local             0.00      (0 dispatches)
```

## Why the labels are the feature

A spend line once read "prior sessions ≈ $4.16" while the real figure was $18.16.
Nothing was mis-added: the label had inferred its own membership from session age,
and a session running since the previous day already had a ledger entry. So every
figure here names what it sums, the total fills only the period column (summing
overlapping lifetime cumulatives across sessions is a meaningless number), and the
per-session table is printed so a reader can audit the label instead of trusting it.

## Commands

| Command | Does |
|---|---|
| `cost-tracker report [--today\|--week\|--month\|--since D] [--json] [--full-ids]` | per-session breakdown for a period |
| `cost-tracker statusline` | `today: cloud $30.12/$40 · local saved $4.80` |
| `cost-tracker doctor` | quarantined records grouped by (reason, session) + resolved config |
| `cost-tracker cap [--learn] [--set USD]` | the daily cap and the refusal it was read from |
| `cost-tracker calibrate [--json] [--tolerance USD] [--since-days N] [--learn-markup]` | our daily total vs the gateway's own figure; **exits 1 on a measured undercount** |
| `cost-tracker markup [--set F] [--clear] [--json]` | the gateway markup, its provenance, and why a normal account has none |

Periods longer than today read the append-only history log, grouped by
`(session_id, utc_date)` with the last row per group winning — the per-session
ledger file is overwritten on every render, so it can only ever answer "today".

## Calibration — the only external check

This number has been wrong in both directions, and every wrong version agreed with
itself. So `calibrate` compares it against a figure this tool did not compute: when the
gateway refuses a request it states `Current cost`, the cumulative **for the key** in the
current budget window — which is exactly the `today` axis. Those turns are already in the
transcripts.

Because a refusal blocks the key for the rest of the window, that figure is very nearly
the day's *final* total: on a day the cap was hit, the ledger should read close to the cap.
The gap is therefore the error itself, not a bound on it.

Three verdicts, and the third one matters:

- `ok` — our total is at or above the gateway's.
- `undercount` — our total is below it, i.e. the display promised headroom into a hard stop.
- `no-data` — the day has a reference but no basis on our side (it predates the history
  log). Reported as its own verdict rather than as a maximal undercount, which would have
  inflated the headline from $290 to $701 and aimed the investigation at an attribution
  bug that cannot exist where there is nothing to attribute.

Two traps it has to handle to avoid inventing findings: repeated refusals within a day are
one measurement, not fourteen; and a refusal just after 00:00 UTC restates *yesterday's*
cumulative, because the window resets before the reset propagates.

## What it reads (and never writes)

```
~/.claude/cost-ledger/<session_id>    "<utc_date> <cum> [<baseline>] [<local_phantom>]"
~/.claude/cost-ledger-history.log     "<iso_ts> <sid> <utc_date> <cum> <baseline> [<raw>]"
```

Capture belongs to `bin/cost-ledger-capture.sh`, the statusline wrapper. The flow is
one-way: status line → ledger → reports.

## Honesty rules it enforces

- **Quarantine, don't clamp.** A structurally invalid record counts as $0, is never
  treated as a session, and is listed with a reason. Clamping a bad number to zero
  hides a capture bug behind a plausible figure — measurably: a cumulative below its
  carried-in baseline is a RESUMED session whose counter restarted at 0, and clamping
  that negative delta reported $16.07 of real spend as $0.00. Such a day is anchored
  at the reset instead and its figure is marked `*` and named a floor, because spend
  earlier the same day is not in the ledger.
- **The cap is learned from the gateway's own refusal.** `/key/info` returns 403 for a
  virtual key scoped to `llm_api_routes`, so the cap cannot be asked for — but it is
  stated outright whenever the gateway refuses (`… Current cost: 40.11, Max budget:
  40.0`), and that turn is persisted in the transcript. cost-tracker reads the newest
  refusal once, caches it in `~/.claude/cost-tracker/cap.json` with its provenance, and
  re-reads it on `cap --learn`. Only the per-**key** scope is used: the same message
  shape also carries the shared **team** cap, 35x larger. Prose quoting the message is
  rejected — the learner keys on `isApiErrorMessage`, not on the text. With no learned
  and no configured cap, spend prints without a denominator; nothing is invented.
- **Not measured ≠ zero.** An absent or unrecognised savings ledger reports as
  unmeasured, never as `$0.00`.
- **Local traffic never enters cloud spend.** The gate is the endpoint, not an env
  flag — that flag leaks into a later cloud session and once booked real gateway
  spend as $0.

## Install

```sh
/plugin install cost-tracker            # from the marketplace
bash install/wire-statusline.sh         # DRY RUN: shows what it would change
bash install/wire-statusline.sh --apply # back up, symlink, verify, roll back on failure
```

A session may have only one status line, so this plugin never claims it: it ships
the wrapper pattern and the machine wires it in. `settings.json` is not touched —
its existing `statusLine` path resolves into the plugin after wiring.

Set `COST_TRACKER_CAP_USD=40` (or `BUDGET_TALLY_CAP_USD`) for a cap.

## Tests

```sh
pytest tests/                            # 91 tests, incl. a 33-case fixture matrix
bash tests/test_budget_ledger.sh         # 23 tests for the capture chain
bash tests/test_statusline_render.sh     # 34 tests for the renderer contract
bash tests/test_wire_statusline.sh       # 20 tests for wiring, backup, rollback
bash tests/test_version_consistency.sh   # manifest / changelog / roadmap agree
# pytest covers three suites: 55 reporting + 12 cap-learning + 24 calibration
```

The pytest suite is mutation-tested. Reporting: seven planted defects (lifetime-as-daily,
clamped baseline, local counted as cloud, summed history rows, invented cap, zeroed
savings, ignored period window). Calibration: ten more (summing refusals instead of taking
the day's max, min for max, dropping either half of the post-midnight staleness rule,
dropping the `Key=`-scope filter so the 1400 team cap leaks in, treating an overshoot as an
undercount, ignoring the coverage window, charging a session's lifetime to one day,
counting local spend as cloud, and exiting 0 on a measured undercount). Markup: seven more
(a non-identity default, dropping the CV scatter gate, dropping the minimum-days gate,
scaling our total instead of the denominator, a corrupt factor falling back to something
other than identity, and — in `budget-tally` — reading the raw cap instead of the effective
one or ignoring a recorded markup). All caught.

## If your spend reads low: the gateway markup

**On a normal Anthropic account this section does not apply, and nothing here is active.**
The markup defaults to exactly `1.0`, no output changes, and it cannot be acquired by
accident — the only evidence it can be learned from is a gateway refusal message, which a
first-party account never produces.

If you go through a **reselling gateway** (LiteLLM and similar), read on, because this cost
one long investigation already and the point of writing it down is that you don't repeat it.

The symptom: `calibrate` reports a steady ~20% undercount that never resolves. The
temptation is to hunt for lost spend in the ledger. **It isn't there.** What we measured:

- Claude Code's own `total_cost_usd` is **correct**. Reconstructing a session from its own
  usage records reproduces it — one session at 0.9996 of the reported figure, median 1.0207
  across 14 single-day sessions, several exactly 1.0000. cost-tracker reads that number and
  does not recompute it, so it inherits that accuracy.
- Rates for the record (Opus 5): **$5/MTok input, $25/MTok output**, cache write 1.25× input
  = $6.25, cache read 0.10× = $0.50. There is **no long-context premium** — 1M context is
  priced the same.
- The gap was **entirely gateway-side**: that gateway billed a median **×1.23** of list
  price, steady across 19 calibrated days (×1.205–×1.289, CV 0.053). A multiplicative model
  fit roughly **4× tighter** than an additive one (CV 0.018 vs 0.075), which is what rules
  out a fixed per-day fee — the flat-looking ~$8 shortfall was an artifact of the cap
  truncating every day near $40.
- Ruled out along the way, so you needn't: day-boundary misattribution, missing sessions, a
  long-context premium, tokenizer inflation, and a second consumer of the same key.

The practical consequence, and the reason this is worth a feature rather than a footnote:
with a $40 cap, the refusal arrives at about **$32** of list-price spend. A tally showing
"$32.43 of $40" looks like 19% of headroom left when there is none. That is not a rounding
concern; it is the difference between planning the next hour of work and having it stop.

So: our figure is **never rewritten**. It is what the tokens cost, and it's right. What the
markup changes is the **denominator** — the cap re-expressed in the units you can actually
measure:

```
$ cost-tracker calibrate --learn-markup
cost-tracker: recorded a gateway markup of ×1.2271 from 19 day(s).

$ cost-tracker statusline
today: cloud $32.43/$33 eff (×1.23 gw)
```

Your gateway's factor is its own, so it is measured locally rather than shipped as a
constant. `--learn-markup` **refuses** to record one the evidence doesn't support: it needs
at least 5 calibrated days and per-day ratios tight enough (CV ≤ 0.25) to be a single rate.
Learning nothing is a result, not a failure — identity stays in force. `cost-tracker markup`
prints the factor and its provenance; `--clear` reverts to list price only.

## Environment

| Variable | Default |
|---|---|
| `COST_TRACKER_LEDGER_DIR` | `~/.claude/cost-ledger` |
| `COST_TRACKER_HISTORY` | `~/.claude/cost-ledger-history.log` |
| `COST_TRACKER_CAP_USD` | unset (falls back to `BUDGET_TALLY_CAP_USD`, then the learned cap) |
| `COST_TRACKER_CONFIG_DIR` | `~/.claude/cost-tracker` — where the learned cap and markup are cached |
| `COST_TRACKER_MARKUP` | unset — a gateway markup override; `1` disables the adjustment entirely |
| `COST_TRACKER_PROJECTS_DIR` | `~/.claude/projects` — transcripts the learner reads |
| `COST_TRACKER_TODAY` | today, UTC — override for tests |
| `COST_TRACKER_SAVINGS_CMD` | auto-discovered `local-agents` savings ledger |
| `COST_TRACKER_STATUSLINE` | `1` — set `0` to keep the `today:` segment out of the status line |

MIT.
