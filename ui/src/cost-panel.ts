// cost-panel — MCP Apps UI for the shared budget snapshot. ARCHIVED abandoned
// variant (superseded by hooks/cost-panel-register.tsx pane mod; see git history).
/* eslint-disable ts-patterns/require-regex-example, ts-patterns/no-magic-numbers, ts-patterns/no-nested-ternary, ts-patterns/no-param-reassign, ts-patterns/prefer-await-to-then --
   archived verbatim as written; not maintained, do not revive without cleanup */
// No framework, no inference: the host bridge (window.openai) supplies the data
// via tools/call; this file only renders the server-provided snapshot and calls
// back for refresh while mounted. All numbers come from the server as strings;
// arithmetic here is display-only formatting.

export interface Snapshot {
  schema: string;
  snapshot_id: string;
  scope_id: string;
  scope_label: string;
  day_utc: string;
  estimated_usd: string | null;
  gateway_confirmed_usd: string | null;
  cap_usd: number | null;
  primary_amount_usd: number | null;
  primary_basis: string;
  budget_state: string;
  headroom_usd: number | null;
  last_refreshed_at: string;
  coverage: {
    complete: boolean;
    missing_sources: string[];
    unpriced_requests: number;
    ambiguous_overlap: boolean;
    inferred_bindings: boolean;
    freshness: string;
  };
  by_host: Record<string, number | null>;
  diagnostics: string[];
}

export interface SnapshotReply {
  scope?: { id: string; label: string };
  snapshot: Snapshot | null;
  stale?: boolean;
  error?: string;
}

const esc = (s: string): string =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

const fmt = (v: number | null | undefined): string =>
  v === null || v === undefined ? "unknown" : `$${v.toFixed(2)}`;

/** Pure renderer: snapshot -> HTML string. Exported for tests (no DOM needed). */
export function renderSnapshot(reply: SnapshotReply): string {
  const s = reply.snapshot;
  if (!s) {
    return `<p class="state error">${esc(reply.error ?? "no snapshot available")}</p>`;
  }
  const cap = s.cap_usd !== null ? ` / ${fmt(s.cap_usd)} cap` : "";
  const amount = fmt(s.primary_amount_usd);
  const state =
    s.budget_state === "cap-reached" ? '<span class="state cap">CAP REACHED</span>'
    : !s.coverage.complete ? '<span class="state incomplete">INCOMPLETE</span>'
    : reply.stale ? '<span class="state stale">stale</span>' : "";
  const hosts = Object.entries(s.by_host)
    .map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v === null ? "unknown" : fmt(v)}</td></tr>`)
    .join("");
  const diags = s.diagnostics.length
    ? `<ul class="diags">${s.diagnostics.map(d => `<li>${esc(d)}</li>`).join("")}</ul>` : "";
  const missing = s.coverage.missing_sources.length
    ? `<p class="missing">missing sources: ${esc(s.coverage.missing_sources.join(", "))}</p>` : "";
  return `
<header>
  <h1>${esc(s.scope_label)} — ${esc(s.day_utc)} (UTC)</h1>
  <p class="amount">${amount}${cap} ${state}</p>
  <p class="basis">basis: ${esc(s.primary_basis)}${s.gateway_confirmed_usd !== null ? ` · gateway-confirmed ${esc(s.gateway_confirmed_usd)}` : ""}</p>
  <p class="meta">snapshot ${esc(s.snapshot_id)} · refreshed ${esc(s.last_refreshed_at)} · headroom ${s.headroom_usd === null ? "unknown" : fmt(s.headroom_usd)}</p>
</header>
<table class="hosts"><thead><tr><th>host</th><th>today</th></tr></thead><tbody>${hosts}</tbody></table>
${missing}${diags}
<footer>
  <button id="refresh" type="button">refresh (cached snapshot; bounded, no inference)</button>
  <span id="refresh-note"></span>
</footer>`;
}

/** Host bridge wiring: runs only inside an MCP Apps host. */
export function mount(root: { innerHTML: string }, bridge: unknown): void {
  const render = (reply: SnapshotReply) => {
    root.innerHTML = renderSnapshot(reply);
    const btn = root.querySelector?.("#refresh") as HTMLButtonElement | null;
    const note = root.querySelector?.("#refresh-note") as HTMLElement | null;
    if (btn && note) {
      btn.onclick = async () => {
        note.textContent = "refreshing…";
        const out = await (bridge as { callTool: (n: string, a: object) => Promise<unknown> })
          .callTool("refresh_cost_snapshot", {});
        root.innerHTML = renderSnapshot(JSON.parse(out as string) as SnapshotReply);
      };
    }
  };
  const b = bridge as {
    callTool: (n: string, a: object) => Promise<unknown>;
    toolOutput?: unknown;
  };
  const initial = b.toolOutput !== undefined ? JSON.parse(b.toolOutput as string) : null;
  if (initial) {
    render(initial as SnapshotReply);
  } else {
    b.callTool("get_cost_snapshot", {}).then(out =>
      render(JSON.parse(out as string) as SnapshotReply));
  }
}

declare global {
  interface Window {
    openai?: unknown;
  }
}

if (typeof window !== "undefined" && window.openai) {
  mount(window.document.getElementById("root") ?? window.document.body, window.openai);
}
