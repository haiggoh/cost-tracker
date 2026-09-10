# cost-tracker roadmap

## Current released version

`0.5.0`

If this disagrees with `.claude-plugin/plugin.json`, treat everything below as
suspect — the manifest is authoritative. `tests/test_version_consistency.sh`
asserts the two agree, along with the top numbered CHANGELOG heading.

## Shipped

- **Calibration against the gateway's own figure (`calibrate`, 0.4.0).** Our daily total
  vs the cumulative the gateway stated when it refused, per UTC day, with the sign and the
  ratio. Exits 1 on a measured undercount. Three verdicts: `ok`, `undercount`, and
  `no-data` for days that have a reference but no basis on our side.
- **The gateway markup (`markup`, `calibrate --learn-markup`, 0.5.0).** What a reselling
  gateway bills per dollar of list price, measured locally from refusal evidence and gated on
  the per-day ratios actually being one rate. Defaults to identity, so first-party Claude Code
  is untouched; carries the measured finding in its own empty state so the next account does
  not repeat the investigation.
- The daily cap, learned from the gateway's own refusal message (`cap --learn`).
- Reset-anchored attribution for resumed sessions (see 0.2.0).
- Three labelled axes (session / today / local) and the per-session table.
- Period reporting from the grouped history log.
- Quarantine with grouped diagnostics.
- The absorbed capture wrapper, renderer, and budget-tally hook.

## Open

- ~~**THE DAILY TOTAL UNDERCOUNTS BY ~20%.**~~ **RESOLVED in 0.5.0 — it was never our
  arithmetic.** Both remaining candidates named here (per-iteration accounting, and gateway
  metering of unattributed traffic) were WRONG. What the evidence actually showed:
  - Claude Code's `total_cost_usd` is correct — a session reconstructed from its own usage
    records reproduces it (0.9996 on one session; median 1.0207 across 14). We read that
    number and never recompute it, so no spend was being lost on our side.
  - The gap is **entirely gateway-side**: a median **×1.23** of Anthropic list price, steady
    over 19 days (×1.205–×1.289, CV 0.053). The multiplicative model fit ~4× tighter than an
    additive one, ruling out a fixed fee; the flat-looking ~$8 was the cap truncating days
    near $40.
  - Rates for the record (Opus 5): $5/MTok in, $25/MTok out, cache write $6.25, cache read
    $0.50. **No long-context premium.** Also ruled out: day-boundary misattribution, missing
    sessions, tokenizer inflation, a second consumer of the key.
  - **The "do NOT close this by scaling" warning was right, and is preserved.** Our figure is
    still never rewritten. 0.5.0 adds the markup as a separate labelled axis that moves the
    DENOMINATOR only, defaults to identity, and refuses to learn a factor the evidence does
    not support. That is the opposite of a fudge factor: it is a measured, provenance-carrying
    second quantity, and the external check it was protecting remains intact.

- **Coverage before 2026-08-18.** The history log starts there; refusals reach back to
  2026-07-26. Those 19 days are reported `no-data` rather than as maximal undercounts,
  which is honest but also unrecoverable — the records were never written.
- **Auto-discovered cap — RESOLVED in 0.3.0**, and the note that the cap comes from
  `COST_TRACKER_CAP_USD` is obsolete: it is now LEARNED from the refusal message and
  cached with provenance, so the denominator appears with no env var. What remains open is
  only the *direct* read: `/key/info` is still 403 for a key scoped to `llm_api_routes`,
  and a per-request spend view (which the undercount investigation now wants) needs that
  same scope widened.
- ~~**Historical repair.**~~ RESOLVED in 0.2.0, and the diagnosis in earlier versions
  was wrong. The two sessions (2026-08-24, 2026-08-26) were not local→cloud handoffs:
  their cost counter RESET on resume, so the cumulative fell below the baseline carried
  into the day. They are now attributed at the reset rather than quarantined, recovering
  $16.07 on 2026-08-26 (reported as $0.00 by budget-tally) and $3.61 on 2026-08-24.
  Remaining limitation, stated rather than fixed: spend earlier in the same day, before
  the reset, is not in the ledger, so those figures are floors.
