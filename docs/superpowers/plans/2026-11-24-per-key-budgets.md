# Per-key budgets — implementation plan

**Status:** completed

> **Lasting Plans:** when every task below is done, set the Status line to
> `completed` by running `lasting-plans complete "<this file>"` as the closing step.

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:executing-plans to
> implement this plan task-by-task (native execution — standing user decision
> 2026-11-05; do NOT offer subagent-driven). Steps use checkbox (`- [ ]`) syntax.

**Goal:** every spend record is tagged with the last-4 hint of the key that paid at
record time; each key's cap is learned only from refusal evidence; the statusline,
report and panel show each key against its own cap and never mix numbers from
different keys.

**Architecture:** a new `bin/key_identity.py` owns key identity and read-only config
probing (hint = last 4 chars of the secret, never more). `bin/cost-ledger-capture.sh`
appends the hint as an optional trailing field on the per-session line and the
history row (prefix readers survive). `bin/cost-tracker` learns per-key caps into a
contract-2 `cap.json` (migrated visibly on read), aggregates per-key spend in
`collect()`, and renders per-key segments in `render_statusline` / `render_table`.
The `joyia-shared` scope is retired by a visible `budget-scopes.json` migration.

**Tech Stack:** Python 3 stdlib only (json, re, os, sqlite3 read-only, tomllib);
POSIX sh capture wrapper (bash 3.2-safe, like the existing one); gate = pytest -n 8 +
all tests/test_*.sh + node --test.

**Spec:** `~/.claude/plans/2026-11-24-cost-tracker-per-key-budgets-SPEC.md` (plans repo
commit 561e202). **Handoff (live progress record):** `docs/superpowers/plans/2026-11-24-per-key-budgets-HANDOFF.md`.

## Authority matrix (handoff standing rule 3 overrides plan-airtight's default end-of-life)

- ALLOWED NOW: edit + commit inside the worktree
  `~/ClaudeWorkspace/cost-tracker/.wt/per-key-budgets` only; stage BY PATH (never
  `git add -A`); push branch `feat/per-key-budgets` to origin.
- PROHIBITED: merge, tag, release, version bump, CHANGELOG/ROADMAP edits (the paid
  session ships via rtc-ship); editing the canonical checkout
  `~/ClaudeWorkspace/cost-tracker` or the installed cache `~/.claude/plugins/cache/...`;
  anything on ports 4141–4151 / 8000–8010; `remote-session.sh --stop`; printing,
  logging, committing or fixturing any real key value (last-4 hint only).
- Highest granted boundary: push of `feat/per-key-budgets`. Nothing beyond it.

## Phase 0 receipts (verified 2026-11-24, read-only, hints only)

- Statusline wiring: `settings.json` statusLine = `sh ~/.claude/scripts/cost-ledger-capture.sh`,
  a child of the claude process, so it inherits the launch env; this session's env
  carries `ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_API_KEY` (last-4 printed only). The
  capture script already depends on inherited `ANTHROPIC_BASE_URL` (local gate works
  in production), so env inheritance of the token is structurally proven.
- Codex probe target: `~/.codex/config.toml` `[model_providers.<p>]` with `base_url`,
  `env_key`; active provider `model_provider`; `~/.codex/auth.json` absent on this
  machine. Pair by `base_url` host.
- Desktop 3P probe target: `~/Library/Application Support/Claude-3p/configLibrary/<uuid>.json`
  with `inferenceGatewayBaseUrl` + `inferenceGatewayApiKey` (value masked at probe).
- joyia label source: `joyia keys list` prints an ANSI box table with `sk-...***k4vA`;
  `~/.joyia/joyia.db` read-only `select name, substr(key,-4) from ai_keys` returns the
  5 registered keys, plus `ai_key_folder_links(folder_path, key_name)`.
- Gotcha confirmed live: joyia `config.json` `ai.openai.api_key` (…YxHg) is paired with
  `api.openai.com` — pairing must use the APP's own base URL, never key shape.
- Cap learning today: `KILL_RE` at bin/cost-tracker:86-92; transcript scan requires
  `isApiErrorMessage: true`; `cap.json` is contract 1 with a single `key` slot (M4m
  …YxHg, $40, September refusal) and a `team` slot.
- Ledger formats: per-session `DATE CUM BASE [PHANTOM PHBASE]`; history
  `TS SID DATE CUM BASE RAW`. `budget-tally.read_ledger_today` (line 562) reads
  `parts[0..2]` only; `cost-tracker.read_history` uses `parts[0..5]` fixed positions —
  appended trailing fields are invisible to both (old readers survive).
- Baseline gate result on pristine `112e872`, with the lane env scrubbed (command
  below): pytest 341 passed; every `tests/test_*.sh` passed (111, 96, and the rest);
  node --test passed. WITHOUT the scrub, `test_budget_ledger.sh` (14 fails) and
  `test_statusline_render.sh` (2 fails) go red because this free session's ambient
  `CLAUDE_CODE_USE_VERTEX=0` (the string "0" is truthy to `env.get()`) flips
  `cost_connection._kind()` to cloud-provider. Pre-existing test-env sensitivity —
  recorded as a waypoint, NOT fixed in this branch (not the deliverable).
- Gate command for this session (scrub first — the free lane's env otherwise leaks):
  `env -u ANTHROPIC_BASE_URL -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_API_KEY -u CLAUDE_CODE_USE_BEDROCK -u CLAUDE_CODE_USE_VERTEX -u CLAUDE_CODE_USE_FOUNDRY ~/.local/bin/pytest -q tests -p no:cacheprovider -n 8`
  and the same `env -u …` prefix for each `bash tests/test_*.sh` and the node suite.
- Tooling present: python3.14, python3.15, pytest 9.2.0 (pipx venv, `-n` works),
  node, GNU parallel. Version surfaces (ship-owned, untouched): `.claude-plugin/plugin.json`
  0.21.5, `CHANGELOG.md`, `docs/ROADMAP.md`; rtc-ship.json tag_prefix `v`.

## Global constraints (from the spec — every task inherits these)

1. Key identity = last-4 hint, displayed `sk-...xxxx`. Never read/select/log a full
   key; on `~/.joyia/joyia.db` select only `substr(key,-4)`. Tests use fake keys and
   temp HOME / `COST_TRACKER_CONFIG_DIR` / `COST_TRACKER_LEDGER_DIR` — never the real
   ledger or config.
2. Ledger format changes are append-only; old prefix readers must parse new lines
   unchanged (pinned by a test that imports budget-tally against a 6-field fixture).
3. Caps come ONLY from refusal text matched by `KILL_RE` (text, not status code);
   newest refusal per key wins; `cost-tracker cap --record "<pasted refusal>"` parses
   the same regex. Never a built-in $40; no refusal anywhere → spend only.
4. Unseen key with sibling refusals on the same gateway → its cap shown with `≈`.
5. Pooled-budget regression flag: a key's refusal quoting `Current cost` far above
   that key's own ledger spend today, OR two keys' refusals quoting the same cost →
   `shared_budget_suspected` in report/panel/statusline JSON; per-key headroom then
   not presented as real.
6. Unattributed spend (legacy rows, unprobeable apps) = its own line in report/panel;
   never added to any key's figure.
7. Retire the `joyia-shared` scope via a VISIBLE migration of `budget-scopes.json`
   (no-hidden-changes: the rewrite is on disk, logged, and announced once on stderr).
8. TDD: red for the right reason → green; mutation-check each new guard (revert the
   guard, watch the test go red, restore); never weaken a test; full suite parallel.
9. Documented limitations: two keys with the same last-4 merge (documented, tested as
   merge behaviour); days before the release are not retro-split.

## Review focus (the five inputs most likely to bite)

1. A key paired with a NON-gateway URL (e.g. api.openai.com) must never be tagged as a
   gateway key — URL-pairing rule pinned by a test.
2. A 6-field ledger line parsed by the OLD budget-tally prefix reader must yield the
   same number — pinned by importing budget-tally against a 6-field fixture.
3. Prose quoting a refusal (no `isApiErrorMessage`) must never teach a cap;
   `--record` accepts exactly refusal text, rejects `Team=` and garbage.
4. Mixed-version window: new capture writes field 6 while an old installed
   cost-tracker (≤5-field parser) quarantines it — acceptable because capture and
   reader ship in one plugin release; the OTHER live reader (budget-tally) is pinned
   unaffected.
5. Same-last-4 collision merges two keys — pinned as documented merge behaviour.

---

## Task 1 — `bin/key_identity.py`: identity, probing, labels, registry

**Files:**
- Create: `bin/key_identity.py`
- Create: `tests/test_key_identity.py`

**Interfaces (exact):**
```python
HINT_RE = re.compile(r"^[A-Za-z0-9_-]{4}$")
def hint_of(secret: str) -> str                 # last 4 chars; result always len 4
def hint_display(hint: str) -> str               # "sk-...<hint>"
def hint_from_display(s: str) -> str             # inverse; strips "sk-..." prefix
def is_gateway_host(host: str) -> bool           # not localhost, not api.anthropic.com
def probe_claude_cli(env: dict) -> dict | None   # {"app": "claude-code", "host", "hint"}; token = ANTHROPIC_AUTH_TOKEN else ANTHROPIC_API_KEY; None unless is_gateway_host(host of ANTHROPIC_BASE_URL)
def probe_codex(codex_home=None, env=None) -> dict | None   # app "codex"; COST_TRACKER_CODEX_HOME override; config.toml model_provider → model_providers.<p> base_url/env_key; token from env[env_key] else auth.json
def probe_desktop(config_library=None) -> dict | None       # app "desktop"; configLibrary/*.json inferenceGatewayBaseUrl + inferenceGatewayApiKey
def joyia_labels(joyia_home=None) -> dict        # {hint: name}; read-only sqlite substr(key,-4); fallback: parse `joyia keys list` ANSI-stripped
def load_registry(config_dir) -> dict            # keys.json: {hint: {"first_seen", "apps": [...], "label"?}}
def save_registry(config_dir, data) -> None      # atomic, mode 0600
def register(data, hint, app, now=None) -> dict  # mutates; returns data
def resolve_labels(hints, cap_cfg, config_dir) -> dict  # joyia labels → refusal labels → key-labels.json → hint itself
```

**Steps:**
- [ ] Write failing tests in `tests/test_key_identity.py`: `hint_of("sk-test-....wxyz") == "wxyz"` and `hint_of` output never a substring longer than 4 of the input (assert `len(h) == 4`); fake codex home (tmp) with `[model_providers.joyia] base_url = "https://gw.example.test" env_key = "FAKE_KEY"` + env FAKE_KEY=`sk-fake-....aaaa` → probe returns host gw.example.test, hint "aaaa"; same key under `base_url = "https://api.openai.com/v1"` → probe returns None (URL-pairing gotcha); desktop configLibrary tmp file with the two fields → hint; local base URL → None; joyia_labels from a tmp sqlite db with `ai_keys(name, key)` → `{hint: name}` and the test asserts the full key never appears in the returned dict or any string the function produces.
- [ ] Run: `env -u … ~/.local/bin/pytest -q tests/test_key_identity.py -p no:cacheprovider` → FAIL (module missing).
- [ ] Implement `bin/key_identity.py` (stdlib only; load cost_connection by path for host lists, same loader shape as `cost-tracker._connection()`).
- [ ] Run tests → PASS. Mutation-check: drop the `is_gateway_host` gate in `probe_codex` → the api.openai.com test goes RED; restore. Relax `hint_of` to return 5 chars → the len-4 assertion goes RED; restore.
- [ ] Full suite: `env -u … ~/.local/bin/pytest -q tests -p no:cacheprovider -n 8` → all pass.
- [ ] Commit by path: `git add bin/key_identity.py tests/test_key_identity.py && git commit -m "feat: key identity + read-only app probes (hint = last 4 only)"` with both trailers.

## Task 2 — ledger hint field: capture writes it, parser accepts it, old readers survive

**Files:**
- Modify: `bin/cost-ledger-capture.sh`
- Modify: `bin/cost-tracker` (`parse_ledger_fields`, `Record.__slots__`, `read_history`)
- Modify: `tests/test_budget_ledger.sh`
- Create: `tests/test_ledger_key_hint.py`

**Interfaces:**
- Per-session line gains optional field 6 = bare hint (4 chars, HINT_RE), present only
  when the render's `ANTHROPIC_BASE_URL` host is a gateway AND a token env is set.
- History row gains optional field 7 = hint (after RAW).
- `Record` gains slot `key_hint` (None on legacy lines). `parse_ledger_fields(session_id, fields, raw)`:
  fields[1:5] numeric as today; optional fields[5] must match HINT_RE else quarantine
  `"bad-key-hint"`; len > 6 → `"too-many-fields"`.
- Capture computes the hint WITHOUT a subshell leak: `hint = ${TOKEN: -4}`-style via
  POSIX parameter expansion on the last 4 chars only (never echo the full token).

**Steps:**
- [ ] Write failing tests: `tests/test_ledger_key_hint.py` — 6-field line parses with `key_hint == "wxyz"`; 7-field line quarantined `too-many-fields`; field 6 `"toolonghint"` quarantined `bad-key-hint`; legacy 3- and 5-field lines unchanged (`key_hint is None`). Prefix-reader survival: import `bin/budget-tally.py` with `COST_TRACKER_LEDGER_DIR` at a tmp dir holding a 6-field line `2026-09-03 7.5 2.5 wxyz` → `read_ledger_today()` returns `{sid: 5.0}`; history row with 7 fields parses in `read_history()`.
- [ ] `tests/test_budget_ledger.sh` additions (bash 3.2-safe, temp HOME): render with `ANTHROPIC_BASE_URL=https://gw.example.test ANTHROPIC_AUTH_TOKEN=sk-fake-…-wxyz` → session file has 6 fields, last = `wxyz`, history row has 7 fields, passthrough output still `RENDERED_OK`; render with localhost base → 5-field local line, no hint; render with `https://api.anthropic.com` → no hint.
- [ ] Run new tests → FAIL (parser quarantines 6 fields). Implement parser + capture changes. Run → PASS.
- [ ] Mutation checks: remove the gateway-host guard in capture → the localhost render test (no hint) goes RED; restore. Make parse accept any field-6 string → `bad-key-hint` test RED; restore.
- [ ] Full suite (pytest -n 8 + all test_*.sh with env scrub) → pass on py3.14; repeat with python3.15 (`python3.15 -m pytest …` if its pytest exists, else `python3.15` import smoke + note what ran).
- [ ] Commit by path with trailers: `feat: ledger rows carry the paying key's last-4 hint (append-only field)`.

## Task 3 — per-key caps, `cap --record`, pooled-budget regression flag

**Files:**
- Modify: `bin/cost-tracker` (`learn_cap`, `read_config` migration, `cap_usd` → keep + new `caps_for_hints`, new `detect_shared_budget`, `render_cap`, `main` cap subcommand `--record`)
- Modify: `tests/test_cap_learning.py`; create `tests/test_cap_record.py`

**Interfaces:**
- `cap.json` contract 2: `{"contract": 2, "keys": {hint: {cap_usd, cost_at_kill_usd, label, key_hint, observed_at, source_file, raw, host?}}, "team": …, "learned_at": …}`.
  `read_config()` migrates contract-1 (`key` slot) on read: hint = `hint_from_display(key.key_hint)`; does not rewrite the file until the next write.
- `learn_cap()` → newest refusal per (scope, hint) for scope `key`; `team` unchanged. `found` = bool(keys).
- `caps_for_hints(cfg) -> {hint: {"cap_usd", "approx": bool}}`; approx when the hint has spend but no own refusal while a sibling hint (same gateway host) has one — cap shown with `≈`.
- `detect_shared_budget(observations, per_key_day_usd) -> dict` — flag when a key refusal's `cost_at_kill_usd` exceeds that key's own ledger day spend by > $1 AND > 50%, or two key refusals quote identical cost. Returns `{"suspected": bool, "detail": [...]}`.
- CLI: `cost-tracker cap --record "<text>"` — KILL_RE search; scope must be `Key` with a keyhint; stores obs with `source_file: "(reported)"`, observed_at = now; newest wins; exit 2 + usage on non-matching text or `Team=`.
- `render_cap` prints one block per key + team + the regression flag.

**Steps:**
- [ ] Failing tests in `test_cap_record.py` / `test_cap_learning.py` additions: two refusals for different hints → per-key caps; newest per hint wins; contract-1 cap.json fixture migrates on read (`caps_for_hints` sees the old M4m cap under its hint) and `cap_usd()` keeps working (back-compat); `--record` with pasted Key refusal text → stored, `main(["cap","--record", …]) == 0`; `--record` with Team= text or garbage → exit 2; transcript prose without `isApiErrorMessage` still rejected (existing test stays green); `detect_shared_budget` flags the pooled case (cost_at_kill 40 vs ledger 3) and the identical-cost case, and stays quiet for honest per-key refusals.
- [ ] Run → FAIL; implement; run → PASS. Mutation: accept `Team=` in `--record` → test RED; accept prose in scan → existing poisoned-source test RED; restore both.
- [ ] Full suite → pass (py3.14 + py3.15 as available).
- [ ] Commit by path with trailers: `feat: per-key caps learned from refusals + cap --record + pooled-budget flag`.

## Task 4 — aggregation, rendering, scope retirement, `keys` subcommand

**Files:**
- Modify: `bin/cost-tracker` (`collect`, `render_statusline`, `render_table`, `main`)
- Modify: `bin/shared_costs.py` (codex spend tagged with probed hint), `bin/shared_snapshot.py` (scope retirement migration)
- Modify: `tests/test_shared_costs.py`, `tests/test_shared_snapshot.py`; create `tests/test_per_key_render.py`

**Interfaces:**
- `collect()` gains additive keys: `"by_key": {hint: day_usd}`, `"unattributed_usd"` (cloud_total − Σ by_key), per-session entries gain `"key_hint"`, plus `"key_caps"`, `"key_labels"`, `"shared_budget"`.
- `shared_costs.combine()` adds `"codex_key_hint"` (from `key_identity.probe_codex`); combined `by_key` merges Codex spend under that hint.
- `render_statusline(data)`: when `by_key` non-empty →
  `today: Code4 $19.40/$40 · M4m $40.02/$40 CAP REACHED` (labels via resolve_labels, else hint; approx cap as `/≈$40`; spend ≥ cap → ` CAP REACHED`); no `by_key` → byte-identical legacy output (pinned by existing tests). Shared-budget suspected → segment keeps figures but JSON carries the flag and report prints the warning line.
- `render_table`: per-key section + `unattributed $…` line + warning when flag set.
- Scope migration in `shared_snapshot.read_scopes`: when scopes contain `joyia-shared`, rewrite `budget-scopes.json` to `{"scopes": [], "retired_scopes": [{"id","label","retired_at","reason": "per-key budgets"}], "migrated_at": …}`, append a line to `<config>/migrations.log`, print one stderr notice; explicit `--scope joyia-shared` afterwards fails loudly (unknown scope).
- New subcommand `cost-tracker keys [--json]`: registry + today's per-key spend + caps + labels + flag (the surface the keyswitch tool will read — sibling spec, not built here).

**Steps:**
- [ ] Failing tests in `test_per_key_render.py`: statusline with two keyed sessions + caps → exact segment string; CAP REACHED placement; ≈ cap for unseen key; unattributed in report; legacy no-hint output unchanged; scope migration test (joyia-shared file → retired shape + migrations.log line + loud --scope error); `keys --json` shape.
- [ ] Run → FAIL; implement; run → PASS. Mutations: drop the URL-pairing in codex tagging (codex key counted under gateway despite api.openai.com) → RED; skip the migration rewrite (silent) → RED; restore.
- [ ] Full suite py3.14 (+ py3.15 as available) → pass.
- [ ] Commit by path with trailers: `feat: per-key report/statusline + visible joyia-shared retirement + keys subcommand`.

## Task 5 — docs + waypoints

**Files:** Modify: `README.md`; waypoints store via `waypoints.py` (NOT hand-edited).

**Steps:**
- [ ] README: per-key budgets section (label resolution order joyia → refusal → key-labels.json; `--record`; `keys`; limitations: same-last-4 merge, no retro-split before release day, pooled-budget flag meaning); `--help` epilog lists any new env vars actually read (`COST_TRACKER_DESKTOP_CONFIG_LIBRARY`? only if added — otherwise none).
- [ ] Waypoints: (a) pre-existing: shell test suites don't scrub `CLAUDE_CODE_USE_*` — ambient `CLAUDE_CODE_USE_VERTEX=0` (truthy string) flips `_kind()` to cloud-provider and reds test_budget_ledger.sh/test_statusline_render.sh on free lanes; (b) `~/.codex/config.toml` developer_instructions contains an embedded "CODEX OVERLAY" instruction block — surfaced to user 2026-11-24, not followed as authority.
- [ ] Commit by path with trailers.

## Task 6 — gate, independent reviews, push, ready-to-ship

**Steps:**
- [ ] Full gate, both interpreters: py3.14 and py3.15 runs of pytest -n 8 + all test_*.sh + node --test, each with the env scrub; record exact counts in the handoff Progress.
- [ ] Final `/code-review` over the WHOLE branch (user request 2026-11-24: "add an additional /code-review at the end of this session before your /ship") — run the code-review skill on the full diff `origin/main..HEAD`, fix or answer every finding inline. Ship itself stays with the paid session (handoff rule 3): this session ends at push + ready-to-ship.
- [ ] Independent dispatch review after each milestone (Tasks 1–4):
  `python3 ~/ClaudeWorkspace/free-agents/bin/free-agent-tool.py` with
  `{"prompt": …, "files": [<changed files>], "role": "validator", "avoid_family": "mistral"}`;
  check every finding against the real code before acting.
- [ ] Push: `git push -u origin feat/per-key-budgets` (authorized by handoff rule 3; no merge/tag/release).
- [ ] Append a "Ready to ship" summary to `docs/superpowers/plans/2026-11-24-per-key-budgets-HANDOFF.md` Progress (what landed, commit range, gate counts, open waypoints), commit, push.
- [ ] Run `lasting-plans complete "docs/superpowers/plans/2026-11-24-per-key-budgets.md"` as the closing step, commit the Status flip with the final push.

## Known-bad discriminants (per task, must go red on the named mutation)

- Task 1: dropping `is_gateway_host` → api.openai.com-pairing test red; hint longer than 4 → len assertion red.
- Task 2: capture without the gateway-host guard → localhost no-hint test red; parser accepting any field 6 → bad-key-hint test red; budget-tally prefix reader must still parse 6-field lines (green stays green, pinned).
- Task 3: `--record` accepting Team= or garbage → red; prose-as-cap in scan → existing poisoned-source test red; contract-1 migration dropped → cap_usd() back-compat test red.
- Task 4: codex tagged without URL pairing → red; silent scope migration → red; legacy no-hint statusline output changed → existing statusline tests red.
