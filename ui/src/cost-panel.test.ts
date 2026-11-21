// ARCHIVED abandoned variant (superseded by the pane mod; see git history).
// node --test src/cost-panel.test.ts — pure-renderer tests over fixture states.
/* eslint-disable ts-patterns/no-magic-numbers, ts-patterns/require-regex-example --
   test fixtures: the amounts ARE the fixture; assertion regexes need no prose examples */
import { test } from "node:test";
import assert from "node:assert/strict";
import { renderSnapshot, type Snapshot } from "./cost-panel.ts";

const base: Snapshot = {
  schema: "cost-tracker/shared-snapshot/1",
  snapshot_id: "abc123",
  scope_id: "joyia-shared",
  scope_label: "JoyIA shared key",
  day_utc: "2026-11-21",
  estimated_usd: "31.370000",
  gateway_confirmed_usd: null,
  cap_usd: 40.0,
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

test("complete snapshot shows amount, cap, per-host rows, and unknown desktop", () => {
  const html = renderSnapshot({ snapshot: base });
  assert.match(html, /\$31\.37 \/ \$40\.00 cap/);
  assert.match(html, /JoyIA shared key — 2026-11-21/);
  assert.match(html, /claude_code<\/td><td>\$30\.90/);
  assert.match(html, /codex<\/td><td>\$0\.47/);
  assert.match(html, /claude_desktop<\/td><td>unknown/);
  assert.match(html, /headroom unknown/);
  assert.doesNotMatch(html, /INCOMPLETE|stale|CAP REACHED/);
});

test("incomplete coverage is shown before any reassuring figure", () => {
  const s = { ...base, coverage: { ...base.coverage, complete: false },
              diagnostics: ["1 legacy usage record(s) suppressed as ambiguous native/legacy overlap"] };
  const html = renderSnapshot({ snapshot: s });
  assert.match(html, /INCOMPLETE/);
  assert.match(html, /ambiguous native\/legacy overlap/);
});

test("stale flag is visible", () => {
  assert.match(renderSnapshot({ snapshot: base, stale: true }), /stale/);
});

test("cap-reached wins over everything and headroom stays unknown", () => {
  const s = { ...base, budget_state: "cap-reached", gateway_confirmed_usd: "40.03006327",
              primary_basis: "gateway observation at 2026-11-21T09:00:00Z (native refusal)" };
  const html = renderSnapshot({ snapshot: s });
  assert.match(html, /CAP REACHED/);
  assert.match(html, /gateway-confirmed 40\.03006327/);
  assert.match(html, /headroom unknown/);
});

test("missing snapshot renders the error, never a $0.00", () => {
  const html = renderSnapshot({ snapshot: null, error: "no cached snapshot yet; call refresh_cost_snapshot" });
  assert.match(html, /no cached snapshot yet/);
  assert.doesNotMatch(html, /\$0\.00/);
});

test("html is escaped against snapshot-controlled strings", () => {
  const s = { ...base, scope_label: "<script>alert(1)</script>" };
  const html = renderSnapshot({ snapshot: s });
  assert.doesNotMatch(html, /<script>alert/);
  assert.match(html, /&lt;script&gt;/);
});
