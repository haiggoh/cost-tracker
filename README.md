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

Periods longer than today read the append-only history log, grouped by
`(session_id, utc_date)` with the last row per group winning — the per-session
ledger file is overwritten on every render, so it can only ever answer "today".

## What it reads (and never writes)

```
~/.claude/cost-ledger/<session_id>    "<utc_date> <cum> [<baseline>] [<local_phantom>]"
~/.claude/cost-ledger-history.log     "<iso_ts> <sid> <utc_date> <cum> <baseline> [<raw>]"
```

Capture belongs to `bin/cost-ledger-capture.sh`, the statusline wrapper. The flow is
one-way: status line → ledger → reports.

## Honesty rules it enforces

- **Quarantine, don't clamp.** A structurally invalid record counts as $0, is never
  treated as a session, and is listed with a reason. Clamping a bad baseline to zero
  would hide a capture bug behind a plausible figure.
- **An unset cap prints no denominator.** llmgw's `/key/info` returns 403 for a
  virtual key scoped to `llm_api_routes`, so the authoritative cap is unreadable
  from here. Set `COST_TRACKER_CAP_USD` to get a percentage; nothing is invented.
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
pytest tests/                            # 48 tests, incl. a 33-case fixture matrix
bash tests/test_budget_ledger.sh         # 23 tests for the capture chain
bash tests/test_statusline_render.sh     # 24 tests for the renderer contract
bash tests/test_wire_statusline.sh       # 20 tests for wiring, backup, rollback
bash tests/test_version_consistency.sh   # manifest / changelog / roadmap agree
```

The pytest suite is mutation-tested: seven planted defects (lifetime-as-daily,
clamped baseline, local counted as cloud, summed history rows, invented cap,
zeroed savings, ignored period window) are all caught.

## Environment

| Variable | Default |
|---|---|
| `COST_TRACKER_LEDGER_DIR` | `~/.claude/cost-ledger` |
| `COST_TRACKER_HISTORY` | `~/.claude/cost-ledger-history.log` |
| `COST_TRACKER_CAP_USD` | unset (falls back to `BUDGET_TALLY_CAP_USD`) |
| `COST_TRACKER_TODAY` | today, UTC — override for tests |
| `COST_TRACKER_SAVINGS_CMD` | auto-discovered `local-agents` savings ledger |

MIT.
