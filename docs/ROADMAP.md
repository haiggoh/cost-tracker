# cost-tracker roadmap

## Current released version

`0.2.0`

If this disagrees with `.claude-plugin/plugin.json`, treat everything below as
suspect — the manifest is authoritative. `tests/test_version_consistency.sh`
asserts the two agree, along with the top numbered CHANGELOG heading.

## Shipped

- Three labelled axes (session / today / local) and the per-session table.
- Period reporting from the grouped history log.
- Quarantine with grouped diagnostics.
- The absorbed capture wrapper, renderer, and budget-tally hook.

## Open

- **Auto-discovered cap.** The authoritative daily cap lives on the gateway, but
  llmgw's `/key/info` returns 403 for a virtual key scoped to `llm_api_routes`,
  so it is unreadable from here. Until that scope is widened, the cap comes from
  `COST_TRACKER_CAP_USD` and an unset cap prints spend with no denominator rather
  than inventing one. A probe path becomes real the day the scope changes.
- **Per-iteration accounting.** When server-side compaction or a sub-agent runs,
  top-level usage can omit differently priced iterations. The ledger is
  authoritative today because it reads Claude Code's own `total_cost_usd`, so this
  matters only for the reconstruction fallback in `budget-tally.py`.
- ~~**Historical repair.**~~ RESOLVED in 0.2.0, and the diagnosis in earlier versions
  was wrong. The two sessions (2026-08-24, 2026-08-26) were not local→cloud handoffs:
  their cost counter RESET on resume, so the cumulative fell below the baseline carried
  into the day. They are now attributed at the reset rather than quarantined, recovering
  $16.07 on 2026-08-26 (reported as $0.00 by budget-tally) and $3.61 on 2026-08-24.
  Remaining limitation, stated rather than fixed: spend earlier in the same day, before
  the reset, is not in the ledger, so those figures are floors.
