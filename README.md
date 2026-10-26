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
| `cost-tracker report --breakdown` | the same period as labelled percentage bars: spend by category, and savings on the free lanes (one row per free-lane session) |
| `cost-tracker report --days N [--breakdown]` | the last N UTC days ending today, with a per-day trend (bars scaled to the cap, not the busiest day) |

## Writing a release entry

`bin/changelog-prepend.py` adds a release section to `CHANGELOG.md`. It finds the current top
heading **itself**, so the previous version never has to be named by hand:

    python3 bin/changelog-prepend.py 0.19.0 "A release" --body notes.md

It refuses a duplicate version, keeps an `## [Unreleased]` block above the new dated release,
normalises a `v`-prefixed tag, preserves the file's mode, and `--check` gates a release.
`--help` writes nothing.
| `cost-tracker report --phantom-basis opus\|sonnet` | which price the savings axis is quoted at (default `opus`, as recorded). Also `COST_TRACKER_PHANTOM_BASIS` |
| `cost-tracker statusline` | `today: cloud $30.12/$40 · local saved $4.80` |
| `cost-tracker doctor` | quarantined records grouped by (reason, session) + resolved config |
| `cost-tracker connection [--map-host HOST PROVIDER] [--json]` | how this session connects, and which limit, fee and gap apply |
| `cost-tracker cap [--learn] [--set USD]` | a gateway's daily cap and the refusal it was read from |
| `cost-tracker fee [--set F] [--clear] [--json]` | the gateway's published fee, its source, and why a direct account has none |
| `cost-tracker calibrate [--json] [--tolerance USD] [--since-days N] [--learn-gap]` | our daily total (list × fee) vs the gateway's own figure; **exits 1 on a measured undercount** |
| `cost-tracker markup [--set F] [--clear] [--json]` | pre-0.12 name: fee and measured gap together |
| `cost-tracker prices [--refresh] [--json]` | which price table applies (official Anthropic, or the gateway's own), the derived markup, custom rows, and every model's billed rate |

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
~/.claude/cost-ledger/<session_id>    "<utc_date> <cum> [<baseline>] [<local_phantom> [<phantom_baseline>]]"
~/.claude/cost-ledger-history.log     "<iso_ts> <sid> <utc_date> <cum> <baseline> [<raw>]"
~/.claude/cost-ledger-transitions.log "<iso_ts> <sid> <lane-change> <k=v ...>"   (0.14.0)
```

On local records the phantom attributable to the day is `local_phantom − phantom_baseline`
(the anchor minus what it carried into the day), so a multi-day free session no longer
re-donates its earlier days to "today saved". The transitions log appends one line per
lane change — cloud→local, local→cloud, local→cloud-zero — never per render.

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

`get-haiggoh apply` (0.8.0 or later) also puts `cost-tracker` on your own shell `PATH`, through the
`shortcuts` file at this plugin's root.

`install.sh` is the alternative: it links `~/.local/bin/cost-tracker` to a checkout, and treats a
get-haiggoh shim at that path as a foreign file (it backs it up, then replaces it). Pick one
mechanism per machine.

```sh
/plugin install cost-tracker            # from the marketplace
bash install.sh --dry-run   # show what is wrong and what would change
bash install.sh             # wire the statusLine + put cost-tracker on PATH (backups first)
bash install.sh --help
```

`install.sh` is also the **repair** command: after `joyia agent --setup` or anything else
rewrites your status line, re-run it. See [docs/RECOVERY.md](docs/RECOVERY.md).
The statusLine step on its own is `install/wire-statusline.sh` (same flags).

A session may have only one status line, so this plugin never claims it: it ships
the wrapper pattern and the machine wires it in. Wiring points `settings.json`
`statusLine.command` at `~/.claude/scripts/cost-ledger-capture.sh` — a **versionless
path** — and turns the `~/.claude/scripts` helpers into **shims**: small generated
files that resolve which plugin copy to run on every invocation. So a plugin update
takes effect with no re-wiring, and a branch switch in a workspace checkout changes
nothing live. Re-run it whenever another tool rewrites the status line: it detects a
bypassed or foreign target.

**Developer mode** is the override, and it is visible and reversible: set
`COST_TRACKER_PLUGIN` to a checkout and every shim (and the statusline) runs that
copy instead. It must contain `.claude-plugin/plugin.json`; a typo fails loudly
rather than silently resolving somewhere else. `CLAUDE_PLUGINS_DIR` moves the whole
lookup tree (registry, cache) and exists mainly for tests.

Ask any shim where it resolved: `~/.claude/scripts/budget-tally.py --which`.

Set `COST_TRACKER_CAP_USD=40` (or `BUDGET_TALLY_CAP_USD`) for a cap.

## Tests

```sh
pytest tests/                            # 91 tests, incl. a 33-case fixture matrix
bash tests/test_budget_ledger.sh         # 23 tests for the capture chain
bash tests/test_statusline_render.sh     # 34 tests for the renderer contract
bash tests/test_wire_statusline.sh       # 44 tests for wiring, backup, rollback
bash tests/test_install.sh               # 21 tests incl. the joyia-overwrite replay
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

## How it decides your limits (0.12.0)

The limit that applies depends on how the paid session reaches Claude, so cost-tracker works
that out first (`cost-tracker connection`). It follows Claude Code's documented credential
order ([authentication](https://code.claude.com/docs/en/authentication)), reads only the
environment and the statusline payload, and never starts a process or touches the network.

| Connection | Detected by | Limit | Fee |
|---|---|---|---|
| Claude subscription (Pro / Max / Team / Enterprise) | `rate_limits.five_hour` / `seven_day` in the statusline payload | rolling 5-hour + weekly windows, in **percent**; the dollar figure is an API-equivalent estimate that "isn't relevant for billing" ([costs](https://code.claude.com/docs/en/costs)) | none |
| Claude Console API key | `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`, no custom endpoint | a **monthly** tier spend cap (Start $500, Build $1,000, Scale $200,000), no daily one ([rate limits](https://platform.claude.com/docs/en/api/rate-limits)) | none |
| Bedrock / Agent Platform / Foundry | `CLAUDE_CODE_USE_BEDROCK` / `_VERTEX` / `_FOUNDRY` | in your cloud console; none knowable here | none |
| Claude apps gateway | `rate_limits.spend_limit` in the payload | its `limit_usd` per `period` | none |
| Reselling LLM gateway | `ANTHROPIC_BASE_URL` on a non-Anthropic host | its own cap, learned from its refusal message (daily per key for llmgateway.io) | its **own price table** when it publishes one (LiteLLM `/model/info`), else its published fee, if the provider is known |
| Local / free lane | a localhost `ANTHROPIC_BASE_URL` | none (free) | none |

Before 0.12.0 every user was measured against a hard-coded `$40/day`. That is one gateway
key's cap, and nobody else has it.

## Prices: official by default, the gateway's own table when it has one

- **Official prices** ship in [`config/anthropic-prices.json`](config/anthropic-prices.json),
  copied from Anthropic's [pricing page](https://platform.claude.com/docs/en/about-claude/pricing)
  with the date they were verified. Direct Anthropic users (subscription, Console key) are
  priced from it as-is. When Anthropic changes a price or ships a model, this one file changes.
- **A gateway's own table.** On a non-Anthropic, non-local `ANTHROPIC_BASE_URL`, cost-tracker
  asks the endpoint once a day, in the background, for `GET <base>/model/info` (LiteLLM's
  price route, sent with the session's own credential via stdin, never on a command line). If
  it answers with a price table, that table is the bill:
  1. a model the table lists is priced **exactly as listed**;
  2. a model it does not list (say, a release the gateway has not added yet) is priced at
     official × the table's **derived markup**: the median gateway/official ratio across
     every model both list;
  3. a gateway that cannot be reached keeps its last table.

  Rows more than 2% off that median are reported as **custom** (`cost-tracker prices`), so an
  exception is visible, not averaged away. Detection is by behaviour, not by hostname, so no
  private hostname is ever shipped. `COST_TRACKER_PROXY_PRICES_URL` points it at another
  route, or `off` stops the probe.

  Why: one LiteLLM gateway billed exactly official × 1.10 on every token type. That is
  Anthropic's documented 10% premium for regional cloud endpoints. Until 0.15.0 that factor
  was read as a reseller's 5% fee times an unexplained ×1.047 "gap".

## Fee vs gap: two numbers, kept apart

With a gateway price table, the fee is the table's markup and the gap is **superseded**: it
is shown, not applied. Without one, the rules below still hold.

A reselling gateway can bill more than list price for the same tokens. cost-tracker keeps
the two reasons for that apart:

- **Fee**: the gateway's published, deterministic charge, cited in
  [`config/providers.json`](config/providers.json) (llmgateway.io: list rates + 5%, from
  its [pricing page](https://llmgateway.io/pricing)). It applies **only** to sessions routed
  through that provider. Billed figures are `list × fee`. A private gateway hostname is
  mapped to a provider **locally**, so it is never shipped:
  `cost-tracker connection --map-host llmgw.example.corp llmgateway.io`. `fee --set` records
  one by hand.
- **Gap**: whatever the gateway charges beyond `list × fee`. `calibrate` measures it from the
  gateway's own `Current cost` at refusals, and `--learn-gap` records it once at least 5
  days are on record with per-day ratios steady enough (CV ≤ 0.25). Its cause is unknown by
  definition, so it is **never folded into the fee**. Warnings *project* with it so they fire
  before the refusal does, always labelled `incl. measured gap ×1.047 (cause unknown)`.
  Set `COST_TRACKER_APPLY_GAP=0` to switch the projection off.

Why keep them apart: a single fitted "markup" of ×1.23 once looked like a steady gateway rate
for weeks. It was really unseen spend from other processes on the same key. A fitted constant
agrees with itself; only a separate, labelled unknown keeps the question open.

Claude Code's own `total_cost_usd` is **correct** at list price. Reconstructing a session
from its usage records reproduces it (median 1.0207 across 14 sessions). cost-tracker reads
that figure and never recomputes or rewrites it.

## Environment

| Variable | Default |
|---|---|
| `COST_TRACKER_LEDGER_DIR` | `~/.claude/cost-ledger` |
| `COST_TRACKER_HISTORY` | `~/.claude/cost-ledger-history.log` |
| `COST_TRACKER_CAP_USD` | unset (falls back to `BUDGET_TALLY_CAP_USD`, then a gateway's learned cap) |
| `COST_TRACKER_CONFIG_DIR` | `~/.claude/cost-tracker` — learned cap, fee, gap and host map |
| `COST_TRACKER_FEE` | unset — a gateway fee override (`COST_TRACKER_MARKUP` is the pre-0.12 alias); `1` disables it |
| `COST_TRACKER_APPLY_GAP` | `1` — set `0` so warnings stop projecting the measured gap |
| `COST_TRACKER_PROVIDERS` | the shipped `config/providers.json` — provider fee registry |
| `COST_TRACKER_OFFICIAL_PRICES` | the shipped `config/anthropic-prices.json` — official Anthropic prices |
| `COST_TRACKER_PROXY_PRICES_URL` | `<ANTHROPIC_BASE_URL>/model/info` — a gateway's price table; `off` disables the probe |
| `COST_TRACKER_PROJECTS_DIR` | `~/.claude/projects` — transcripts the learner reads |
| `COST_TRACKER_TODAY` | today, UTC — override for tests |
| `COST_TRACKER_SAVINGS_CMD` | auto-discovered `local-agents` savings ledger |
| `COST_TRACKER_STATUSLINE` | `1` — set `0` to keep the `today:` segment out of the status line |

MIT.
