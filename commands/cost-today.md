---
description: Show today's Claude Code spend per session, on labelled axes, with the quarantine count
---

Run `cost-tracker report --today` and show the user the table verbatim.

Then, in at most three sentences:

1. State the **today** figure with its membership (the table's `membership:` line
   says it — do not paraphrase it into "prior sessions" or "other sessions", which
   is the mislabel this tool exists to prevent).
2. If a cap is set, give the percentage and the headroom. If no cap is set, say the
   cap is unset rather than assuming $40.
3. If any records are quarantined, say how many sessions are affected and offer
   `cost-tracker doctor` — do not describe the total as complete without mentioning
   them.

Never add a dollar figure of your own that isn't in the table. If the user asks about
a longer window, use `--week`, `--month`, or `--since YYYY-MM-DD`; those read the
history log, since the per-session ledger file only holds the latest render.
