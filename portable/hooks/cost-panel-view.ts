// Pure view model for the cost panel pane — no engine imports, so node --test
// can exercise every data state directly. The register.tsx mod wraps this with
// ink components; both desktops (Claude Code tab, ChatGPT Codex mode where mods
// are supported) render the SAME snapshot the statusline/CLI consume.

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

export interface PanelRow {
  text: string;
  dim?: boolean;
  bold?: boolean;
}

const CENTS = 2;

const fmt = (v: number | null | undefined): string =>
  v === null || v === undefined ? "unknown" : `$${v.toFixed(CENTS)}`;

/** snapshot -> display rows. Pure; exported for tests. */
export function panelRows(reply: SnapshotReply): PanelRow[] {
  const s = reply.snapshot;
  if (!s) {
    return [{ text: reply.error ?? "no snapshot available", bold: true }];
  }
  const rows: PanelRow[] = [
    { text: `${s.scope_label} — ${s.day_utc} (UTC)`, bold: true },
    { text: `${fmt(s.primary_amount_usd)}${s.cap_usd !== null ? ` / ${fmt(s.cap_usd)} cap` : ""}`, bold: true },
  ];
  if (s.budget_state === "cap-reached") rows.push({ text: "CAP REACHED", bold: true });
  else if (!s.coverage.complete) rows.push({ text: "INCOMPLETE", bold: true });
  if (reply.stale) rows.push({ text: "stale — refresh pending", dim: true });
  rows.push({ text: `basis: ${s.primary_basis}`, dim: true });
  if (s.gateway_confirmed_usd !== null) {
    rows.push({ text: `gateway-confirmed: ${s.gateway_confirmed_usd}`, dim: true });
  }
  rows.push({ text: `snapshot ${s.snapshot_id} · refreshed ${s.last_refreshed_at} · headroom ${s.headroom_usd === null ? "unknown" : fmt(s.headroom_usd)}`, dim: true });
  rows.push({ text: "hosts today:", bold: true });
  rows.push(...Object.entries(s.by_host).map(([k, v]) => ({
    text: `  ${k}: ${v === null ? "unknown" : fmt(v)}`,
  })));
  if (s.coverage.missing_sources.length) {
    rows.push({ text: `missing sources: ${s.coverage.missing_sources.join(", ")}`, dim: true });
  }
  rows.push(...s.diagnostics.map(d => ({ text: `⚠ ${d}`, dim: true })));
  return rows;
}
