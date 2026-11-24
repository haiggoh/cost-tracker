# HANDOFF — cost-tracker per-key budgets (free session, 2026-11-24)

## Why this exists
The paid Opus session that designed this ran on a JoyIA key with a $40/day cap and had to keep its
budget. It wrote the spec with the user and handed implementation to you (a free session).

## State
- Spec (READ FULLY FIRST, it is the requirement): `~/.claude/plans/2026-11-24-cost-tracker-per-key-budgets-SPEC.md`
  (latest commit in the plans repo: `561e202`). Sibling spec for context only, NOT your job:
  `~/.claude/plans/2026-11-24-joyia-keyswitch-SPEC.md`.
- Worktree: `~/ClaudeWorkspace/cost-tracker/.wt/per-key-budgets`, branch `feat/per-key-budgets`,
  based on origin/main `112e872` (0.21.5). Nothing implemented yet.
- Installed copy (DO NOT EDIT): `~/.claude/plugins/cache/haiggoh/cost-tracker/0.21.5/`.

## Goal (verbatim from the user, 2026-11-24)
"technically what matters for each app is how far it is from its own limit whether or not another app
that shares the same key contributes to it or has its own key doesn't change that equation from a user
perspective, but of course in the ledger we can't mix up numbers from different keys"
"cost tracker can't assume the same $40 individual budget for everyone from the start, it needs the 422
errors in transcripts as evidence for that ... I'd like to be ready for a potential future key policy
regression if admin ever decides to put all of my keys into the same shared $40 bucket again, cost
tracker would notice that"

## Standing rules
1. Autonomous: decide and record instead of asking; stop only for forks that cannot be weighed.
2. Work ONLY in the worktree above. Never switch the branch of `~/ClaudeWorkspace/cost-tracker`
   (the plugin resolves through it).
3. Commit every milestone, staged BY PATH (never `git add -A`), with the running model's co-author
   trailer (read memory `commit-attribution-by-model.md` before the first commit). Push the branch
   `feat/per-key-budgets` to origin. NEVER merge, tag or release — the user/paid session ships it.
4. TDD every change (red for the right reason, then green); mutation-check each new guard; never weaken
   a test. Full suite in parallel (`pytest -n auto` if available); test py3.14 AND py3.15 where the
   repo's gate does.
5. Review each milestone with an independent dispatch (`~/ClaudeWorkspace/free-agents/bin/free-agent-tool.py`)
   and check every finding against the real code.
6. NEVER print, log, commit or put in a test fixture any real API key value. Key identity = last-4 hint
   only (`sk-...xxxx`). When reading `~/.joyia/joyia.db` select only `substr(key,-4)`. Tests use fake
   keys and a temp HOME / `COST_TRACKER_CONFIG_DIR` — never the real ledger or real config.
7. Never run `remote-session.sh --stop`, never start another remote session, never kill anything on
   ports 4141–4151 or 8000–8010.
8. Record progress below at each milestone, in the same commit (copy this file into the worktree as
   `docs/superpowers/plans/2026-11-24-per-key-budgets-HANDOFF.md` first and keep THAT copy updated).
9. You are the successor; do not hand this work to yet another session.
10. Ledger format changes must stay readable by old readers (append fields; see memory
    `extend-pinned-contracts-with-subclass.md`). Migrate `budget-scopes.json` visibly (no hidden changes).

## Next action
1. Read the spec. 2. Write the TDD plan with superpowers:writing-plans into
`docs/superpowers/plans/2026-11-24-per-key-budgets.md` in the worktree (Phase 0 = read-only survey of:
`bin/cost-ledger-capture.sh`, `bin/cost-tracker` cap learning (regex near line 87) + `render_statusline`
segment (~line 1875), `bin/shared_snapshot.py`, `bin/shared_costs.py`, `bin/cost_connection.py`; and
verify the probe locations the spec lists, printing hints only). Commit it. 3. Implement the plan,
milestone by milestone. 4. Push the branch and append a final "Ready to ship" summary here.

## Progress
- 2026-11-24 (free session, Mistral Large 4 "Le Chonk" 1T-A52B): Phase 0 read-only survey DONE —
  probe locations verified with hints only (statusline child inherits launch env; codex
  config.toml base_url/env_key; Desktop configLibrary inferenceGatewayBaseUrl/ApiKey; joyia.db
  substr(key,-4) + folder links; api.openai.com pairing gotcha confirmed). Airtight TDD plan
  written to docs/superpowers/plans/2026-11-24-per-key-budgets.md (Tasks 1-6). Baseline gate
  green on pristine 112e872 ONLY with the lane env scrubbed (`env -u ANTHROPIC_BASE_URL -u
  ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_API_KEY -u CLAUDE_CODE_USE_BEDROCK -u CLAUDE_CODE_USE_VERTEX
  -u CLAUDE_CODE_USE_FOUNDRY`): pytest 341 passed, all test_*.sh passed, node passed. Without
  the scrub, ambient CLAUDE_CODE_USE_VERTEX=0 (truthy string "0") flips _kind() to cloud-provider
  and reds test_budget_ledger.sh + test_statusline_render.sh — pre-existing test-env
  sensitivity, waypointed, not fixed here. Implementation starts at Task 1 (bin/key_identity.py).
- 2026-11-24 ~23:40 (same session): Task 1 DONE (ea084f6: bin/key_identity.py + 18 tests,
  mutation-checked; independent validator dispatch = nvidia-step5-preview, 4 findings, all
  verified against the code and fixed: first-party-API hosts excluded even if mapped,
  malformed providers.json cannot raise, hint sliced at call site, allowlist-in-host_map
  test added). Task 2 DONE (0e65a3d + 8961a70 portable regen): capture appends trailing
  k=<last4> on gateway renders only (field 6 session / field 7 history), parser peels and
  validates it (bad-key-hint quarantine, legacy lines unchanged, budget-tally prefix read
  pinned green), key_identity.py ships in portable/scripts/. Gate: pytest 368 passed,
  shell suites all green (budget_ledger 105), node green. Mutations verified red: allowlist
  drop, hint-regex drop. Pushed: feat/per-key-budgets @ 8961a70. Next: Task 3 (per-key caps,
  cap --record, pooled-budget flag). User added: final /code-review over the whole branch
  before any ship step (ship itself stays with the paid session per rule 3).
