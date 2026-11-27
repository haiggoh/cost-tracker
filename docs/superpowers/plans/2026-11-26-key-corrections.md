# Per-key attribution: fast-path fix, dated pooled check, and a key-corrections overlay — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (native: the executor does every task, then one final whole-branch review). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-key "today" figures are correct after a key switch/resume, the statusline never drops the real $40 per-key cap because of a stale refusal, and 2026-11-24..2026-11-26 are re-attributed in hindsight from the gateway's own refusals.

**Architecture:** Three defects fixed in `bin/cost-tracker` (fast path collapses a session's day onto its last key; pooled-budget check compares refusals of any date; contract-1 `cap.json` never upgraded). A new pure module `bin/key_reconstruct.py` turns history stream deltas + gateway refusals into a reviewable `~/.claude/cost-tracker/key-corrections.json` overlay (written only on `--write`), which `read_history()` applies on top of the raw rows and books as `≈ inferred`. The raw history log is never edited.

**Tech Stack:** Python 3 stdlib (py3.14 AND 3.15), pytest + xdist, existing `tests/conftest.py::load_ct`.

**Spec:** this conversation's decisions (user, 2026-11-26): scope = code fixes + reconstruction; storage = correction overlay file; untagged days = full inference. Evidence gathered in-session: `/tmp/ctfix/{streams,refusals,perday,postkill}.py` (scratch, not shipped).

## Global Constraints

- Never edit `~/.claude/cost-ledger-history.log` or the per-session ledger files; corrections live only in `key-corrections.json`.
- Every reconstructed dollar is labelled inferred (`by_key_inferred`, `≈` in renders) — never shown as observed.
- Refusals count only with `isApiErrorMessage is True` (existing `find_refusals`) — prose about a kill is never evidence.
- Key identity is the last-4 hint only; no full key value in any new file, log line or output.
- **No cap value is ever hardcoded (user, 2026-11-26).** A key's cap comes ONLY from its own newest refusal (`Max budget`). A key without a refusal inherits, marked `≈`, the cap of the newest refusal among keys on the **same login route** (route = the gateway host its probe paired it with); a key on a different route, or with no known route, inherits nothing and shows no denominator. Every threshold in the reconstruction is a FRACTION of that key's cap, never dollars. The user's $40 appears only in test fixtures as an example value, alongside a non-40 fixture.
- The public repo never names the real gateway host: tests use `gw-a.example` / `gw-b.example`; the host lives only in the local registry.
- `--write` is opt-in; the default of the new subcommand is a dry-run table that writes nothing. Unknown flags exit non-zero (argparse).
- Version bump 0.23.1 → 0.24.0 on every version surface (`.claude-plugin/plugin.json`, CHANGELOG, `PORTABLE-BUILD.json` via `scripts/build-portable.py`); fetch + `git ls-remote --tags` before picking the tag.
- Work only in `.wt/key-corrections` (branch `fix/key-corrections`); never switch the canonical checkout's branch. A concurrent branch `codex-compat/shared-accounting` also edits cost-tracker — rebase/merge check before ship, port by function body if it moved code.

## Attribution rules (the spec for Task 3, user's words made exact)

Per UTC day D, with `CAP[K]` = the `Max budget` quoted by K's own refusal (route-inherited `≈` cap only for validation, never for closing — only a refusal closes a key):

1. **Kill closes a key.** The first refusal on D naming key K with `cost_at_kill >= CAP[K]` sets `closed_at[K]`. After it, K may accrue at most `OVERSHOOT_FRAC × CAP[K]` more on D (`OVERSHOOT_FRAC = 0.05`; measured 2026-11-25: 40.00→41.35 = 3.4%, 40.00→40.72 = 1.8%). No reset before 00:00 UTC.
2. **Premature kill.** A refusal with `(1 - PREMATURE_FRAC) × CAP[K] <= cost < CAP[K]` (`PREMATURE_FRAC = 0.10`, i.e. ≥ 90% of cap — the user's "~$36 and higher" on a $40 key) does NOT close K; later K spend on D is allowed but K's day total must stay `<= (1 + OVERSHOOT_FRAC) × CAP[K]`.
3. **Refusal anchors a window.** A refusal at T in session S naming K attributes S's stream deltas in `(prev_boundary, T]` to K, where prev_boundary = the previous refusal in S naming a different key, else the start of the stream that contains T.
4. **Observed tags stand unless contradicted.** A `k=K` delta is kept unless it lands after `closed_at[K] + overshoot budget used up` — then it is a contradiction and goes to rule 5.
5. **Concurrency inference.** An unresolved or contradicted delta at time t in session S gets key J if exactly one key J is (a) not closed at t and (b) billing another session's stream within ±15 min of t (by observed tag or rule 3). Zero or several candidates → stays unattributed.
6. **Validation, never clamping.** After allocation, any key whose day total exceeds `(1 + OVERSHOOT_FRAC) × CAP[K]` (own or route-inherited cap; no cap → no check) is reported as a violation line; numbers are not scaled to fit.

Basis strings (stored per entry): `"refusal"`, `"tag"`, `"tag-after-kill->concurrent"`, `"concurrent"`.

## Review Focus

1. A refusal for key K from an EARLIER day while K spends today → must not flag a shared budget (today's 3quw: refused 2026-11-25 at 40.01, spends $24 today).
2. A resumed session whose new process restores the old cumulative (restore-to-point) → no phantom delta booked to the new key.
3. `key-corrections.json` absent, empty, or malformed → reader behaves exactly as today (malformed = loud line in `doctor`, entries ignored, never a crash on the statusline path).
4. Two keys both alive and both concurrent at t → stays unattributed, not first-wins.
5. A contract-1 `cap.json` (no `keys`) → per-key caps still resolve after the upgrade; no learned denominator lost.
6. A brand-new key on a SECOND gateway route while route A keys have refusals → no inherited cap, no denominator (not route A's cap).

---

### Task 1: Fast path keeps the per-key split

**Files:**
- Modify: `bin/cost-tracker` — `_merged_records()` (~line 1161)
- Test: `tests/test_per_key_render.py`

**Interfaces:**
- Produces: `_merged_records(fast=True)` returns records whose `key_split` / `key_split_inferred` equal the `fast=False` ones for today.

- [ ] **Step 1: Write the failing test** `test_fast_path_splits_resumed_session_across_keys`: history rows `s1 p=1 k=aaaa` cum 0→10, then `s1 p=2 k=bbbb` cum 10→13 (restored counter); ledger file for s1 holds the last row (`k=bbbb`, cum 13). Assert `collect("today", fast=True)["by_key"] == {"aaaa": 10.0, "bbbb": 3.0}` and equals `collect("today")["by_key"]`.
- [ ] **Step 2: Run** `pytest tests/test_per_key_render.py -k fast_path -v` → FAIL (`{"bbbb": 13.0}`).
- [ ] **Step 3: Implement** — in the fast branch call `read_history()` too (measured 2026-11-26: 20,357 rows / 1.9 MB; full parse is sub-second CPU). Remove the "history not read at all" comment and replace it with the reason it is read now.
- [ ] **Step 4: Run** the test → PASS; then `pytest tests/test_counter_streams.py tests/test_per_key_render.py -q` → all pass.
- [ ] **Step 5: Commit** `fix(keys): fast path reads history so a resumed session is split across its keys`.

### Task 2: Pooled-budget check is day-scoped; contract-1 cap.json upgrades itself

**Files:**
- Modify: `bin/cost-tracker` — `detect_shared_budget()` (~284), its caller in `collect()` (~1578), `learned_cap()`
- Test: `tests/test_cap_learning.py`

**Interfaces:**
- Produces: `detect_shared_budget(observations, per_key_day_usd, markup=1.0, day=None)` — when `day` is given, observations whose `observed_at[:10] != day` are skipped. `collect()` passes `day=TODAY` for single-day periods.

- [ ] **Step 1: Failing tests** — `test_old_refusal_never_flags_shared_budget` (obs observed 2026-09-02 cost 40.02, own spend 5.67 on D → `suspected is False`); `test_same_day_inconsistent_refusal_still_flags` (same obs dated D → `True`); `test_contract1_cap_json_relearns_per_key` (cap.json with only `key`, transcripts holding a refusal per key → after `learned_cap(auto_learn=True)` the config has `keys` for both hints).
- [ ] **Step 2: Run** `pytest tests/test_cap_learning.py -v` → the three new tests FAIL.
- [ ] **Step 3: Implement** the `day` filter; in `learned_cap`, treat a stored config with `contract < 2` as stale and re-learn when `auto_learn` is set.
- [ ] **Step 4: Run** `pytest tests/test_cap_learning.py tests/test_cap_record.py tests/test_per_key_render.py -q` → PASS.
- [ ] **Step 5: Commit** `fix(keys): pooled-budget check compares same-day refusals only; upgrade contract-1 cap.json`.

### Task 2b: Caps are inherited per login route, never across routes

**Files:**
- Modify: `bin/cost-ledger-capture.sh` (~line 221: keep `<host>` from `--probe-claude-cli`, not only `$2`), `bin/key_identity.py` (`register(data, hint, app, now=None, host=None)` stores `routes: [host, ...]` per hint), `bin/cost-tracker` — `caps_for_hints()` and its caller in `collect()`
- Test: `tests/test_key_identity.py`, `tests/test_per_key_render.py`, `tests/test_ledger_key_hint.py`

**Interfaces:**
- Produces: `caps_for_hints(cfg=None, hints_with_spend=None, routes=None)` where `routes: dict[hint, set[host]]` from the registry. Own refusal → `{"cap_usd", "approx": False}`; else newest refusal among hints sharing a route → `{"cap_usd", "approx": True, "route": host}`; else no entry. History rows are NOT changed (no new field); the route travels via the registry, which the capture already updates each render.

- [ ] **Step 1: Failing tests** — `test_new_key_inherits_cap_from_same_route_only` (aaaa on gw-a refused at Max budget 25.0; bbbb on gw-a no refusal; cccc on gw-b no refusal → bbbb `≈25`, cccc absent); `test_own_refusal_beats_route_sibling` (bbbb refused at 60.0 → 60, not approx); `test_unknown_route_inherits_nothing`; `test_registry_records_route_from_probe` (capture with a stubbed probe printing `gw-a.example abcd` → registry `routes == ["gw-a.example"]`); `test_no_literal_cap_in_bin` (grep `bin/` for `40.0`/`\$40` outside comments → none).
- [ ] **Step 2: Run** those tests → FAIL.
- [ ] **Step 3: Implement.** Registry migration: hints without `routes` keep inheriting nothing until their next render records a route (fail-closed: no guessed denominator).
- [ ] **Step 4: Run** `pytest tests/test_key_identity.py tests/test_per_key_render.py tests/test_ledger_key_hint.py -q` and `bash tests/test_statusline_render.sh` → PASS.
- [ ] **Step 5: Commit** `fix(keys): inherit a cap only from a key on the same login route`.

### Task 3: `key_reconstruct.py` — the allocation engine (pure)

**Files:**
- Create: `bin/key_reconstruct.py` (with `--help`; no I/O beyond what `main()` is given)
- Create: `tests/test_key_reconstruct.py`

**Interfaces:**
- Consumes: `Delta(ts: str, sid: str, pid: str|None, stream: int, usd: float, hint: str)` and `Refusal(ts: str, sid: str, hint: str, cost: float, cap: float)`.
- Produces: `reconstruct(day: str, deltas: list[Delta], refusals: list[Refusal]) -> Result` with `Result.entries: list[dict]` (keys: `day, sid, pid, from_ts, to_ts, key, basis, usd, evidence`), `Result.by_key: dict[str,float]`, `Result.unattributed: float`, `Result.violations: list[str]`. Constants `OVERSHOOT_FRAC = 0.05`, `PREMATURE_FRAC = 0.10`, `CONCURRENCY_WINDOW_S = 900`. `reconstruct(..., caps: dict[hint, float])` takes the per-key caps from Task 2b's `caps_for_hints` (no cap → rules 1/2/6 skip that key). Every test runs twice, parametrised over cap 40.0 and cap 25.0, so no dollar threshold can hide in the engine.

- [ ] **Step 1: Failing tests**, one per rule:
  - `test_refusal_anchors_untagged_window` — untagged s1 deltas 10:00–11:00 $30, refusal s1 11:00 K=aaaa 40.0 → entry basis `refusal`, key aaaa, usd 30.
  - `test_kill_closes_key_and_post_kill_tag_goes_concurrent` — aaaa killed 12:00; s2 `k=aaaa` delta $5 at 13:00; s3 `k=bbbb` alive 12:55–13:10 → s2's $5 → bbbb, basis `tag-after-kill->concurrent`.
  - `test_overshoot_within_budget_is_kept` — `k=aaaa` delta of 3% of cap after the kill → stays aaaa, basis `tag`.
  - `test_premature_kill_does_not_close` — refusal at 94% of cap then 2.5% of cap `k=aaaa` → stays aaaa; no violation.
  - `test_two_live_candidates_stay_unattributed` — Review Focus 4.
  - `test_over_cap_is_a_violation_not_a_clamp` — tags summing to $50 on aaaa with no kill → `by_key["aaaa"] == 50` and one violation line.
- [ ] **Step 2: Run** `pytest tests/test_key_reconstruct.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement** in rule order 1→6 from "Attribution rules"; sort deltas and refusals by ts; merge consecutive deltas with the same (sid, pid, stream, key, basis) into one entry.
- [ ] **Step 4: Run** → PASS; `bin/key_reconstruct.py --help` prints usage, exits 0; `bin/key_reconstruct.py --bogus` exits 2.
- [ ] **Step 5: Commit** `feat(keys): key_reconstruct allocation engine for hindsight per-key attribution`.

### Task 4: Overlay file + reader + `cost-tracker keys --reconstruct`

**Files:**
- Modify: `bin/cost-tracker` — `read_history()` (delta booking, ~1000), `keys` subparser (~2970) and dispatch (~3202), `doctor`
- Modify: `README.md` (Environment + `keys` section), `skills/cost-tracker/SKILL.md` (one line under "Reading it honestly": reconstructed = inferred, `keys --reconstruct` dry-run first)
- Test: `tests/test_key_corrections.py`

**Interfaces:**
- Consumes: Task 3 `reconstruct()`.
- Produces: file `$COST_TRACKER_CONFIG_DIR/key-corrections.json` = `{"contract": 1, "written_at": iso, "days": {D: {"entries": [...], "violations": [...]}}}`; env override `COST_TRACKER_KEY_CORRECTIONS` (set-but-missing path → loud error). CLI `cost-tracker keys --reconstruct --since YYYY-MM-DD [--until YYYY-MM-DD] [--write] [--json]`.

- [ ] **Step 1: Failing tests** — `test_dry_run_writes_nothing` (no file after run, table printed with per-day per-key before→after); `test_write_then_collect_uses_overlay` (after `--write`, `collect` for D books entry usd under the entry key AND in `by_key_inferred`); `test_overlay_absent_is_identity`; `test_malformed_overlay_is_ignored_and_reported_by_doctor` (Review Focus 3); `test_override_env_missing_path_fails_loudly`; `test_rewrite_replaces_only_the_named_days` (re-running for 11-26 keeps 11-24's entries).
- [ ] **Step 2: Run** `pytest tests/test_key_corrections.py -v` → FAIL.
- [ ] **Step 3: Implement** — in `read_history` the delta's hint is replaced when a correction entry for (sid, day) covers the row's ts (and pid when the entry names one); the delta is added to `inferred_acc` too. Build `Delta`s from the same loop (expose a `history_deltas(days)` helper so the reconstruct command and the reader parse the log once, through one code path). `--write` takes a timestamped backup of an existing overlay first.
- [ ] **Step 4: Run** the file, then the full gate: `pytest -q tests -n 8 -p no:cacheprovider` and every `tests/test_*.sh` → all green, on python3.14 and python3.15.
- [ ] **Step 5: Commit** `feat(keys): key-corrections overlay and keys --reconstruct (dry-run by default)`.

### Task 5: Reconstruct 2026-11-24..2026-11-26 on the real machine (receipts)

- [ ] **Step 1:** `cost-tracker cap --learn` from the worktree's bin → `cap.json` now contract 2 with `keys` for YxHg, 3quw, HzPw, 6fWQ, k4vA, each with its OWN quoted Max budget (receipt: `cost-tracker cap`). The registry's `routes` fill in on the next render of each key; for the five keys above, all seen only on the joyia route, seed `routes` once from the capture probe of this session (same host) — a key whose route cannot be shown stays route-less.
- [ ] **Step 2:** `bin/cost-tracker keys --reconstruct --since 2026-11-24` (dry-run). **Known-bad discriminant:** 11-25 currently shows YxHg $67.18 against its own gateway kill at $41.35 and HzPw $0 despite a kill at 07:00 — the dry-run must show YxHg ≤ 1.05 × its learned cap and HzPw > $0, else STOP and debug, do not write.
- [ ] **Step 3:** Show the user the before→after table and violations; write only on their OK (`--write`).
- [ ] **Step 4:** Receipts: `cost-tracker keys`, `cost-tracker statusline` → per-key `/$40` denominators present, no "shared budget suspected"; quote output.

### Task 6: Ship 0.24.0

- [ ] Bump every version surface, CHANGELOG entry (Fixed: fast path, dated pooled check, cap.json upgrade; Added: key-corrections overlay), `scripts/build-portable.py`.
- [ ] Check `codex-compat/shared-accounting` overlap: `git merge-tree --write-tree origin/main fix/key-corrections` clean, else stop and report.
- [ ] Ship via run-to-completion `rtc-ship.sh .wt/key-corrections 0.24.0 "<title>"` (gate, merge, tag, release, refresh); verify `git ls-remote --tags origin v0.24.0` and the installed plugin version; dogfood: `cost-tracker keys` through the installed copy.

**Offload note (Credit Efficient Mode):** Tasks 1–4 are design-coupled edits in one 3.4k-line file and the reconstruction needs live-machine refusals — `cloud:spec-coupled`. One `local:` step: after Task 5 Step 2, dispatch the dry-run table + the refusal list to a free reviewer as an independent cross-check of rule application.
