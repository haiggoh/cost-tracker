# Changelog

All notable changes to cost-tracker are documented here.

## [0.5.1] — 2026-09-11

### Changed
- **Every user-facing figure now sits on the GATEWAY axis, matching the cap the gateway's own
  refusal message quotes.** Previously the surfaces kept the numerator at list price and deflated
  the denominator instead (`today: cloud $30.12/$32 eff (×1.24 gw)`). That was arithmetically
  identical, but the user reads `$40` in the refusal and `$32` here and concludes the tool is
  wrong. The status-line segment now reads `today: cloud $37.35/$40 gw`, and the `budget-tally`
  warning reads `$37.35 of $40 cap (gateway ×1.24 applied)`. The percentage and the headroom are
  unchanged — the ratio is the same whichever axis both sides are expressed in.
- The **recording is untouched and stays canonical**: ledger rows remain list price, the markup is
  stored separately in config and applied at the surface, never baked into what is written. This is
  what keeps `TOTAL cloud` the figure Claude Code itself asserts. `cost-tracker report` now leads
  with `billed (gw)` and the gateway cap, keeps `TOTAL cloud` visible and labelled as list price,
  and prints `eff. cap` as that same cap expressed back in list-price dollars.
- **The statusline is three lines instead of two.** The budget segment has its own line: together
  with the model, effort, context and session cost it reached 108 columns and wrapped, which costs
  more vertical space than a deliberate line break and wraps at an arbitrary point. Measured after:
  59 / 38 / 44 columns. It is not folded into the dir/branch line, which is variable-length per
  project and would reintroduce the overflow.
- The model's context-window suffix is abbreviated for width: `Opus 5 (1M context)` renders as
  `Opus 5 (1M)`. Nothing else on the line is measured in M, so the word was pure padding. A
  display name without the suffix passes through untouched.

### Added
- The skill now carries an unmistakable, general instruction never to quote a raw spend number
  from a ledger file, a transcript's `total_cost_usd`, or a `cloud_usd` JSON field — those are
  list price and therefore SMALLER than what the user is charged, so repeating one under-reports.
  Written for a reader with no other context: it names the commands to run instead, gives a
  want/run/do-NOT table, and says why the trap is invisible (both numbers look equally plausible
  and nothing is malformed).

### Notes
- 8 new assertions across the shell suites (40 statusline, 32 budget-ledger) plus the updated
  markup expectations; 99 pytest assertions unchanged and passing. A test asserting the ledger row
  on disk is still list price was added deliberately, so the canonical-recording guarantee is
  covered by a test rather than only by a comment.

## [0.5.0] — 2026-09-10

### Added — the gateway markup: why a reselling gateway makes an accurate total misleading

`calibrate` had been reporting a steady ~20% undercount for weeks, and the assumption was a
bug in our own arithmetic. It is not. Measured, then closed:

- Claude Code's `total_cost_usd` is **correct** — reconstructing a session from its own usage
  records reproduces it (one session at 0.9996, median 1.0207 across 14 single-day sessions).
  We read that figure and never recompute it, so nothing on our side was losing spend.
- The entire gap is **gateway-side**: this gateway bills a median **×1.23** of Anthropic list
  price, steady over 19 calibrated days (×1.205–×1.289, CV 0.053). A multiplicative model fit
  ~4× tighter than an additive one (CV 0.018 vs 0.075), ruling out a fixed per-day fee — the
  flat-looking ~$8 shortfall was the cap truncating every day near $40.
- Ruled out: day-boundary misattribution, missing sessions, a long-context premium (1M
  context carries none), tokenizer inflation, and a second consumer of the key.

The consequence is operational, not cosmetic: with a $40 cap the refusal lands at about **$32**
of list-price spend, so "$32.43 of $40" advertised 19% of headroom that did not exist.

So the markup is a **second labelled axis, never a correction**. `cloud_usd` keeps its exact
prior meaning; what moves is the **denominator** — the cap re-expressed in units we can
measure (`effective_cap_usd`), plus additive `markup`, `billed_usd` and `billed_pct_of_cap`
keys. `budget-tally`'s warning percentage now uses the effective cap, which is the surface
that actually misled a live session.

- `cost-tracker calibrate --learn-markup` records the factor so it never has to be re-derived.
  It **refuses** unless the evidence supports one rate: ≥5 calibrated days and per-day ratios
  with CV ≤ 0.25. Learning nothing is a result, not a failure.
- `cost-tracker markup [--set F] [--clear] [--json]` shows the factor and its provenance — and
  when there is none, explains why that is the correct state and carries the measured finding
  so the next reader inherits it instead of re-investigating.

**Normal Claude Code is unaffected, structurally rather than by a flag.** The default is
exactly `1.0`, and a markup can only be learned from a gateway refusal message, which a
first-party account never emits. All 93 pre-existing tests pass untouched, and the statusline
and table strings are asserted byte-identical at the default.

### Fixed — test isolation, one axis further

`tests/conftest.py` now also clears `COST_TRACKER_MARKUP`; an inherited value would have
rescaled every denominator in the suite. Same class of leak as the learned cap in 0.4.1.

## [0.4.1] — 2026-09-10

### Fixed — test isolation: the learned cap leaked in from the real machine

`tests/conftest.py` redirected the ledger, history, today and savings paths at a
throwaway dir but not `COST_TRACKER_CONFIG_DIR`, so every test read the REAL
`~/.claude/cost-tracker/cap.json`. `test_statusline_labels_its_axis_and_respects_an_unset_cap`
asserts that no denominator is invented when there is no cap — and inherited this
machine's learned $40, failing on a pristine checkout (`today: cloud $12.40/$40` vs the
expected `today: cloud $12.40`) while passing anywhere the cap had never been learned.
Clearing the two cap OVERRIDE env vars was not sufficient, because the cap also resolves
from the config file. `COST_TRACKER_PROJECTS_DIR` is now redirected for the same reason,
so no test can scan the real transcripts.

### Added — the midnight-crossing day/lifetime distinction, locked in as behaviour

A waypoint reported a day-boundary bug: `report` printed `$0.1164` for 2026-09-08 while
the session's ledger record held `$20.596`, and the conclusion recorded was that a
session spanning midnight UTC "has its spend attributed to the wrong day, so BOTH days
are wrong". Investigated against the real ledger and history: **that is not what
happens.** The `$0.1164` is the spend accrued after the baseline carried into day N+1,
the remaining `$20.48` is day N's and is counted there, and the two figures answer
different questions on separately labelled axes. A conservation audit over all 238
ledger sessions found **no unexplained cases** — the apparent $501 of "lost" spend
decomposes into 14 sessions predating the history log (added 2026-08-18, so their
earlier days were never recorded), one correct local→cloud phantom baseline, and one
session dated the cutover day itself.

Two regression tests now pin the correct behaviour rather than a fix for a non-bug:

- a small day figure beside a large lifetime is correct, asserted via **conservation**
  (the per-day deltas must sum to the lifetime) — the assertion that would actually fail
  for a lossy implementation, where checking the day figure alone would not;
- the transient mid-crossing baseline **self-heals** at the next render, which is why the
  reported symptom could not be reproduced afterwards.

Mutation-tested 3/3: returning the lifetime instead of the delta, clamping a small delta
to zero, and dropping the non-today day from the period filter are each caught.

## [0.4.0] — 2026-09-04

### Added — calibration against the gateway's own figure

The daily total has now been wrong in **both** directions: it over-counted (token
reconstruction priced cache tokens ~4x high), then under-counted ($31.76 displayed while
the gateway was already refusing at $40.07), and moving it into a plugin fixed neither.
Every one of those versions agreed with itself. Internal consistency is therefore not
evidence, and the only way to know is to compare against a number we did not compute.

A budget refusal states one: `Current cost` is the cumulative **for the key** in the
current budget window, which is exactly the `today` axis — like-for-like. It is already
in the transcripts, and there were 119 of them across 37 UTC days on the machine this
was built on.

- `cost-tracker calibrate` — one row per UTC day that has a refusal to check against:
  our total, the gateway's, the delta with its sign, and a verdict. `--json` for machines,
  `--tolerance` and `--since-days` to bound it. **Exits 1 on a measured undercount**, so
  it can gate a release rather than being a report nobody reads.
- `find_refusals()` — all refusals, not just the newest. `learn_cap()` now sits on top of
  it, so the cap learner and the calibrator cannot disagree about what counts as a
  refusal — including the poisoned-source filter (`isApiErrorMessage`), which keeps a
  session *discussing* a kill from being read as one.
- `history_coverage()` and a third verdict, **`no-data`**. Refusals reach back to
  2026-07-26; the history log starts 2026-08-18. Those 19 earlier days have a reference
  and no basis on our side, and printing them as maximal undercounts inflated the
  headline from $290 to **$701** while pointing at an attribution bug that cannot exist
  where there is nothing to attribute. Absence of a source is not evidence of a defect.
  Keyed on the log's coverage window, not on "we found no records" — *inside* the window
  an empty day is the real structural blindness and is still reported.
- `collect(days=[...])` and `daily_totals()` are built from the same `_merged_records()`
  assembly the reports use. A harness that computed the total a second way would only
  prove two implementations agree.
- **`ours_pct_of_gateway` per day and a median across calibrated days.** A refusal blocks
  the key for the rest of the budget window, and the window is the UTC day — so the stated
  cumulative is approximately the day's FINAL total, not merely a floor. The shortfall is
  the whole error, and the ratio is the number that distinguishes a systematic missing
  component from a few absent sessions. Median on this machine: **80.8%**. A per-day
  dollar delta invites "a session was missed"; the same ratio on sixteen days with very
  different session mixes is a different claim, and it is the one the data supports.
  Median rather than mean, and no-data days excluded — including them would drag it toward
  0% and make a one-fifth shortfall look catastrophic.

### Measured — the undercount is real, and it is not missing sessions

**16 of 17 calibrated days undercount, by $6.84–$9.16 — a near-constant 17–23% of each
day** (ours/gateway 0.775–0.829), independent of session count. The leading suspicion had
been sessions that never render a statusline and so write no ledger record. Those exist
and are now measured: 1–3 billable turns each, worth 1–3% of the day. **Real, but ~10x
too small to explain the gap.** The remaining candidates are per-iteration accounting
(sub-agent and server-side compaction turns that may never reach `.cost.total_cost_usd`)
and gateway-side metering of traffic no session attributes to itself.

Deliberately NOT closed by scaling or clamping to match: every previous version of this
bug was self-consistent, and a fudge factor would restore that comfort while destroying
the only external check.

### Two traps this had to handle to avoid inventing findings
- **Repeats.** Once blocked, every further request re-reports the same cached cumulative
  — 14 of them on 2026-08-24. They are one measurement, not fourteen.
- **The post-midnight repeat.** The window resets at 00:00 UTC but propagation lags
  minutes, so a refusal just after midnight restates *yesterday's* cumulative. Counted as
  the new day it invents a ~$40 reference for a day with almost no spend — a phantom
  undercount on every such day. Confirmed on 2026-07-31T00:05 and 2026-08-21T00:00. The
  rule keys on the value repeating **and** the clock, because a genuine post-reset kill
  the same night states a different figure and is real data (2026-08-21T00:06 does).

### Tests
21 new pytest cases (161 total, all green), and the calibration subsystem is
mutation-tested **10/10**: summing refusals instead of taking the day's max, min for max,
dropping either half of the stale-repeat rule, dropping the `Key=`-scope filter (the
`Team=` cap is 1400 — 35x), treating an overshoot as an undercount, ignoring the coverage
window, charging a session's lifetime to one day, counting local spend as cloud, and
exiting 0 on a measured undercount.

## [0.3.0] — 2026-09-03

### Added — the cap is learned from the gateway's own refusal
The daily cap could not be *asked* for: llmgw's `/key/info` returns 403 for a virtual
key scoped to `llm_api_routes`. But the gateway states it plainly every time it refuses,
and that turn is persisted in the transcript:

```
API Error: Request rejected (429) · Budget has been exceeded!
Key=Joyia-Code-M4m (sk-...YxHg) Current cost: 40.11333501999997, Max budget: 40.0
```

- `cost-tracker cap` — the resolved cap plus the refusal it was read from: the value,
  the cost at which the gateway refused, the scope, when it was observed, and which
  transcript it came from. A cap with no provenance is a number someone has to
  re-derive later.
- `cost-tracker cap --learn` — re-read it now. **This is what "the daily budget cap
  changed" should run**; the newest refusal wins. `--set USD` records one by hand with
  honest provenance.
- Learned once, lazily, on the first report — and the *not found* result is cached too,
  so a machine that has never hit the cap does not re-read every transcript on every
  invocation. The statusline path (`--fast`) may READ a learned cap but never goes
  looking for one. Cached at `~/.claude/cost-tracker/cap.json`, outside the plugin, so
  updates never touch it.
- Resolution order: `COST_TRACKER_CAP_USD` / `BUDGET_TALLY_CAP_USD` → learned cap →
  none. An explicit override always wins; with nothing at all, spend still prints
  without a denominator rather than an invented one.
- Only the per-**key** scope is usable. The same message shape carries the shared
  **team** cap (historically 1300 → 1400); using that as a personal daily denominator
  would divide by a number 35x too large, so scope is recorded and filtered on, never
  assumed. A team observation is kept and shown as informational only.
- If measured spend passes the cap with no refusal, `cap` says the cap is probably
  stale and points at `--learn` instead of printing 150% and looking broken.

### Fixed before it shipped
- **Prose that quotes the refusal is not a source.** The first probe of this learner
  read the message out of the very session that was investigating it — a sentence, not
  a gateway response — and would have learned a cap from it. The learner now requires
  `isApiErrorMessage`, the marker Claude Code sets on a rejected turn, which no amount
  of quoting reproduces. Any conversation *about* a budget kill is a poisoned source.

### Fixed
- `tests/test_statusline_render.sh` was not sandboxing the config or transcripts dir, so
  the "no cap means no denominator" assertion started reading this machine's real
  learned cap and failed. Every store the renderer can reach is now redirected into the
  temp dir — a missing one fails OPEN onto live state, which is the worse failure.

### Added
- `tests/test_cap_learning.py` — 12 tests: real refusal vs quoted prose, newest wins
  within and across files, team cap never promoted, env override precedence, learn-once
  caching, the fast path never scanning, `--set` provenance, a stale-cap warning, and a
  malformed config being ignored rather than fatal. Mutation-tested 6/6.

## [0.2.0] — 2026-09-03

### Changed — a resumed session's spend is recovered instead of dropped
The `baseline-exceeds-cumulative` quarantine introduced in 0.1.0 was diagnosing the
wrong thing. 131 records across two sessions were being reported as structurally
invalid; investigating them from the history log showed the actual mechanism, and it
is a real accounting bug rather than corruption:

**Claude Code restarts `total_cost_usd` at 0 when a session is RESUMED.** The ledger's
baseline still holds the cumulative carried into the day, so the incoming cumulative
can be *below* it. Subtracting gives a negative delta, and `max(0, …)` — what
`budget-tally.py` does — reports the day as free. Measured: session e4d10d09 reached
**$16.07** on 2026-08-26 against a stale baseline of $17.35 and was reported as
**$0.00**. Session 960b07ca on 2026-08-24 reset at 00:33, climbed back past its stale
baseline, and lost exactly that $3.61.

- Such a day is now **anchored at the reset**: the day's spend is the cumulative
  itself. The figure is marked `*` in the table and described as a **floor**, because
  spend earlier that same day, before the reset, is not in the ledger at all.
- A reset that later climbs back above the stale baseline is invisible in the final
  record, so it is detected from the **row sequence** in the history log — and the live
  ledger record no longer erases a flag history established.
- A local render legitimately reports 0 on every row and is explicitly not read as a
  cumulative going down; nor is a session that switches cloud → local mid-day.
- `<date> 0 <baseline>` with no field 4 is now reported as axis **`zero`** rather than
  asserted to be `local`. Before 2026-08-29 those two are genuinely
  indistinguishable — a local render and a resumed counter that has not billed yet
  look identical — and both are $0, so the honest label is the ambiguous one.
- `doctor` gained a COUNTER RESETS section naming each affected session-day, and an
  AMBIGUOUS ZEROS section. There are now **no quarantined records** on the author's
  machine; quarantine is back to meaning "structurally invalid" only.

### Added
- 7 tests for the above and 34 fixtures (was 33), including the two real records from
  the affected sessions. 55 pytest tests total, mutation-tested 6/6 on the new logic:
  clamping a reset to zero, not detecting it, removing the sequence detector, letting
  the ledger erase the flag, asserting an ambiguous zero is local, and reading a local
  render as a reset are all caught.

## [0.1.5] — 2026-09-03

### Fixed
- **The `today:` segment never appeared in the WIRED setup**, which is the only setup
  that matters. The renderer found its sibling CLI with `dirname "$0"`, but once wired
  it is reached as `~/.claude/scripts/statusline-render.sh` — a symlink — so that
  resolved to a directory with no `cost-tracker` in it and the segment silently
  vanished. `$0` is now walked through symlinks (bounded at 10 hops, no `readlink -f`,
  which is not portable to every `/bin/sh`).
- Every renderer assertion in 0.1.4 ran the script directly in the repo, where the
  sibling happens to be present, so none of them could see it — it was caught by an
  end-to-end run against the live chain. There is now a test that invokes the renderer
  THROUGH a symlink.

## [0.1.4] — 2026-09-03

### Added
- **The `today:` segment now actually renders in the status line.** Until now the CLI
  could produce the frozen segment but nothing displayed it, so the status line still
  showed only a session-lifetime figure. `statusline-render.sh` appends it when a
  sibling `bin/cost-tracker` is present; opt out with `COST_TRACKER_STATUSLINE=0`.
- **The lifetime figure is labelled `session $3.14` only when the today segment is
  present.** Two dollar figures on one line must each name their axis or this
  reproduces the very mislabel the plugin exists to prevent; alone, the label would be
  noise, so it is not added.
- `cost-tracker statusline --fast` — skips the history log (the ledger dir alone can
  answer "today") and reads savings straight from the derived rollup instead of
  spawning the savings ledger. 51ms rather than 192ms; a full render goes from ~49ms
  to ~116ms. Staleness is checked, not assumed: if the savings log has grown past the
  event count the rollup was built from, the answer is *unmeasured* rather than a stale
  number presented as current.
- 8 more renderer tests for the segment, and the pre-existing renderer assertions now
  pin `COST_TRACKER_STATUSLINE=0` — with it on, "a missing cost is not rendered as
  $0.00" would fail against a correct renderer on any day with $0.00 of spend.

## [0.1.3] — 2026-09-03

### Fixed
- **Restored `docs/ROADMAP.md`, which 0.1.2 shipped empty.** The bump script read and
  wrote the file in one expression — `open(p,"w").write(open(p).read())` — and `"w"`
  truncates before the read runs, so the content was gone before it was ever read.
  `tests/test_version_consistency.sh` caught it immediately, which is the argument
  for that test: the damage was to a file nothing else reads at runtime, so nothing
  else would have noticed.

## [0.1.2] — 2026-09-03

### Added
- `tests/test_statusline_render.sh` — 24 tests covering the renderer contract this
  plugin inherited, including the one the contract explicitly asked to keep a
  regression for: the field-shift bug. Fields are extracted in a single jq pass and
  read with one `read`; when the delimiter was a TAB, an absent `.effort.level`
  silently pushed the DIRECTORY into the effort slot, because tab is an IFS
  whitespace character and `read` collapses runs of it. `\037` (ASCII US) is not
  whitespace, so empty fields survive. Reverting the delimiter to a tab fails four
  of these assertions.
- Also asserted: absent fields are dropped rather than printed as `null` or `$0.00`,
  variable-length names stay off line 1 so a long project name cannot wrap it, and
  the renderer exits 0 on every malformed payload — a renderer bug must never blank
  the status line.

## [0.1.1] — 2026-09-03

### Fixed
- **`install/wire-statusline.sh` no longer leaves a fictional session behind.** Its
  verification render goes through the real capture path, so it wrote a ledger entry
  and a history row under a synthetic id — which then appeared as a `wire-check`
  session in every future report. Both are now removed.
- **The history cleanup was gated on `grep -v`'s exit code**, which is 1 when it
  prints nothing — exactly the fresh-install case where the sentinel is the only row
  in a brand-new history file. A machine with thousands of existing rows would never
  have exposed it.

### Added
- `tests/test_wire_statusline.sh` — 20 tests against a temp HOME covering dry-run
  inertness, symlinking, backup mode preservation (600 stays 600), sentinel cleanup,
  idempotence, and rollback driven by an actually-broken chain rather than an
  assumed one. Both fixes above were found by writing it.

## [0.1.0] — 2026-09-03

First release. Absorbs the hand-maintained cost-accounting chain that had been
living in `~/.claude/scripts/` into a versioned plugin, and adds the reporting
layer it never had.

### Added
- `bin/cost-tracker` — the reporting CLI.
  - `report [--today|--week|--month|--since YYYY-MM-DD] [--json] [--full-ids]`:
    a per-session breakdown table with a **Period $** and a **session lifetime $**
    column. The table is the audit of the label — a reader can add the column up
    themselves instead of trusting a prose scope.
  - `statusline`: the one-line segment, `today: cloud $30.12/$40 · local saved $4.80`.
  - `doctor`: quarantined records, grouped by (reason, session), plus resolved config.
- Period reporting over the append-only history log, grouped by
  `(session_id, utc_date)` with the last row per group winning. Weekly and monthly
  totals were previously impossible: the per-session ledger file is overwritten on
  every render.
- Quarantine with visible diagnostics for structurally invalid records
  (`too-few-fields`, `too-many-fields`, `bad-date`, `non-numeric`, `negative-cost`,
  `baseline-exceeds-cumulative`, `empty`). A quarantined record counts as $0, is
  never treated as a session, and is never silently dropped.
- A 33-case fixture matrix covering every record shape the capture wrapper has
  written — 2-field legacy, 3-field with baseline, 4-field with a local phantom,
  history rows, and the malformed shapes. 48 pytest tests; the suite is
  mutation-tested (7/7 planted defects caught).
- `install/wire-statusline.sh` — dry-run by default; symlinks this machine's
  `~/.claude/scripts/` copies at the plugin, with a timestamped backup and an
  automatic rollback if the chain stops rendering.

### Fixed
- **`baseline-exceeds-cumulative` no longer misfires on local records.** A local
  render zeroes the cost field while the baseline keeps the cumulative carried
  into the day, so `<date> 0 <baseline>` is the local signature — and the only
  shape a pre-2026-08-29 local record has, since field 4 did not exist yet. An
  earlier version of the parser quarantined 139 real records on the author's
  machine and reported working capture as broken.
- **`budget-tally.py`: a local model served under its on-disk path is now
  classified non-billable.** It was being reported as an unpriced *cloud* model,
  which is how a genuine pricing gap gets lost in permanent noise. Found by the
  suite's own recurrence guard, which is exactly its job.

### Carried over unchanged
- `bin/cost-ledger-capture.sh` (endpoint-change guard, 4-field ledger record) and
  `bin/statusline-render.sh` (single jq pass, `\037`-delimited, always exits 0)
  are the live scripts verbatim. The ledger format is unchanged, so nothing that
  reads it needs updating.
