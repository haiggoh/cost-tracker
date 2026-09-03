# Changelog

All notable changes to cost-tracker are documented here.

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
