---
description: Open/close the dedicated cost panel pane — the shared budget snapshot, same generation as the statusline
---

Run the plugin's `cost-panel` command handler (the pane mod): it opens the **Cost** pane
wherever the surface seats it (beside the transcript in fullscreen, above the prompt
otherwise, a widget in the desktop app).

Lead your reply with `💸 cost-panel:` — the mark says which plugin produced the pane.

The pane shows the cached shared-scope snapshot for today (UTC): primary amount and cap,
cap-reached/INCOMPLETE/stale states, per-host subtotals, missing sources and diagnostics,
snapshot id and refresh time. It reads the SAME cached snapshot
(`cost-tracker/shared-snapshot/1`) as the statusline, `statusline --combined --fast` and
the budget-tally warnings — no MCP server, no second ledger, no inference. The pane's
refresh button and its mounted-only 30 s cadence call the bounded single-flight producer
(`statusline --refresh-shared`); a closed pane makes no refresh calls.

If no shared scope is configured (`~/.claude/cost-tracker/budget-scopes.json`), say so
plainly and point at `cost-tracker report --combined` — never invent a combined figure.
