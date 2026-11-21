// node --test tests/cost-panel-view.test.ts — pure view-model tests over fixture states.
/* eslint-disable ts-patterns/no-magic-numbers, ts-patterns/require-regex-example --
   test fixtures: the amounts ARE the fixture; assertion regexes need no prose examples */
import { test } from "node:test";
import assert from "node:assert/strict";
import { panelRows, type Snapshot, type SnapshotReply } from "../hooks/cost-panel-view.ts";

const base: Snapshot = {
  schema: "cost-tracker/shared-snapshot/1",
  snapshot_id: "abc123",
  scope_id: "joyia-shared",
  scope_label: "JoyIA shared key",
  day_utc: "2026-11-21",
  estimated_usd: "31.370000",
  gateway_confirmed_usd: null,
  cap_usd: 40,
  primary_amount_usd: 31.37,
  primary_basis: "local estimate (coverage complete)",
  budget_state: "unknown",
  headroom_usd: null,
  last_refreshed_at: "2026-11-21T10:00:00+00:00",
  coverage: { complete: true, missing_sources: [], unpriced_requests: 0,
              ambiguous_overlap: false, inferred_bindings: false, freshness: "live-at-write" },
  by_host: { claude_code: 30.9, codex: 0.47, claude_desktop: null, other: null },
  diagnostics: [],
};

const texts = (r: SnapshotReply) => panelRows(r).map(row => row.text).join("\n");

test("complete snapshot: amount, cap, hosts, unknown desktop and headroom", () => {
  const t = texts({ snapshot: base });
  assert.match(t, /\$31\.37 \/ \$40\.00 cap/);
  assert.match(t, /JoyIA shared key — 2026-11-21 \(UTC\)/);
  assert.match(t, /claude_code: \$30\.90/);
  assert.match(t, /claude_desktop: unknown/);
  assert.match(t, /headroom unknown/);
  // snapshot generation identity is shown (the same id the CLI/statusline print)
  assert.match(t, /snapshot abc123 · refreshed 2026-11-21T10:00:00\+00:00/);
  assert.doesNotMatch(t, /INCOMPLETE|stale|CAP REACHED/);
});

test("incomplete coverage is flagged, not hidden", () => {
  const s: Snapshot = { ...base,
    coverage: { ...base.coverage, complete: false },
    diagnostics: ["1 legacy usage record(s) suppressed as ambiguous native/legacy overlap"] };
  const t = texts({ snapshot: s });
  assert.match(t, /INCOMPLETE/);
  assert.match(t, /ambiguous native\/legacy overlap/);
});

test("stale flag is visible", () => {
  assert.match(texts({ snapshot: base, stale: true }), /stale — refresh pending/);
});

test("cap-reached wins and headroom stays unknown", () => {
  const s: Snapshot = { ...base, budget_state: "cap-reached",
    gateway_confirmed_usd: "40.03006327",
    primary_basis: "gateway observation at 2026-11-21T09:00:00Z (native refusal)" };
  const t = texts({ snapshot: s });
  assert.match(t, /CAP REACHED/);
  assert.match(t, /gateway-confirmed: 40\.03006327/);
  assert.match(t, /headroom unknown/);
});

test("missing snapshot renders the error, never a $0.00", () => {
  const t = texts({ snapshot: null, error: "no cached snapshot yet; refresh first" });
  assert.match(t, /no cached snapshot yet/);
  assert.doesNotMatch(t, /\$0\.00/);
});
