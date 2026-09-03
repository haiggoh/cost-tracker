# Changelog

All notable changes to cost-tracker are documented here.

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
