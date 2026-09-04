# cost-tracker roadmap

## Current released version

`0.4.0`

If this disagrees with `.claude-plugin/plugin.json`, treat everything below as
suspect — the manifest is authoritative. `tests/test_version_consistency.sh`
asserts the two agree, along with the top numbered CHANGELOG heading.

## Shipped

- **Calibration against the gateway's own figure (`calibrate`, 0.4.0).** Our daily total
  vs the cumulative the gateway stated when it refused, per UTC day, with the sign and the
  ratio. Exits 1 on a measured undercount. Three verdicts: `ok`, `undercount`, and
  `no-data` for days that have a reference but no basis on our side.
- The daily cap, learned from the gateway's own refusal message (`cap --learn`).
- Reset-anchored attribution for resumed sessions (see 0.2.0).
- Three labelled axes (session / today / local) and the per-session table.
- Period reporting from the grouped history log.
- Quarantine with grouped diagnostics.
- The absorbed capture wrapper, renderer, and budget-tally hook.

## Open

- **THE DAILY TOTAL UNDERCOUNTS BY ~20%, AND IT IS MEASURED.** `calibrate` reports 16 of
  17 calibrated days short by $6.84-$9.16 — a median **80.8%** of what the gateway
  charged. Because a refusal blocks the key for the rest of the window, the gateway's
  figure is very nearly the day's final total, so on every one of those days the ledger
  should have read close to the $40 cap and instead read ~$32. The steadiness of the ratio
  across very different session mixes is the evidence that this is a systematic missing
  component rather than absent sessions.
  - **ELIMINATED as the main cause: sessions that never render a statusline.** They exist
    and were the leading suspicion, but they are 1-3 billable turns each, worth 1-3% of a
    day — real, and ~10x too small. (Token reconstruction cannot arbitrate the absolute
    figure: it prices cache tokens ~4x high and returns $77-$242 for these days.)
  - **Remaining candidates**, in order: per-iteration accounting (sub-agent / sidechain
    turns and server-side compaction iterations that may never reach
    `.cost.total_cost_usd`); and gateway-side metering of traffic no session attributes to
    itself. The next decisive step needs a per-request view the key currently cannot read.
  - **Do NOT close this by scaling or clamping.** Every previous version of this bug was
    self-consistent; a fudge factor restores that comfort and destroys the only external
    check we have.
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
