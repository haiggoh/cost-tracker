# cost-tracker — history and incidents (moved out of SKILL.md for the 1500-word budget)

The SKILL.md keeps the imperative rules; this file keeps the stories behind them.

## The membership-label incident (2026-08-10)

A spend line labelled "prior sessions" silently included the running session and
under-counted **$14 of $18.16**. The label had *inferred* membership from session age — a
long-running session already has a ledger entry, so it was in the total all along. The fix
was never guessing: every figure names its own membership (see the axis table in SKILL.md).

## The reselling-gateway "~20% undercount" (resolved)

The long-standing "~20% undercount" was **not our arithmetic.** Claude Code's
`total_cost_usd` is correct (a session reconstructed from its own usage records reproduces
it), and cost-tracker reads that figure rather than recomputing it. The gap was **entirely
gateway-side**: the gateway bills a median **×1.23** of Anthropic list price, steady over
19 calibrated days (CV 0.053).

**What that means when quoting a figure:** our number is what the tokens cost, and it is
right. It is *not* what the cap counts. With a $40 cap the refusal lands near **$32** of
list-price spend, so `today` is a **FLOOR against the cap** — say so whenever the number is
used to decide headroom; the error runs toward walking a session into a hard stop while the
display still promises room. "$32.43 of $40" is the shape of that trap.

A past trap worth remembering: assuming "list price + a reseller's published fee" once hid a
flat ×1.10 regional-endpoint price behind a ×1.05 fee and a ×1.047 "unknown gap" — hence
`cost-tracker prices` first (0.15.0+).

---

## From SKILL.md (moved during the budget trim)

On 2026-08-10 a spend line labelled "prior sessions" silently included the running
session and under-counted **$14 of $18.16**. The label had *inferred* membership
from session age — a long-running session already has a ledger entry, so it was in
the total all along. The fix is not a better guess; it is never guessing:



The long-standing "~20% undercount" is **resolved, and it was not our arithmetic.** Claude
Code's `total_cost_usd` is correct (a session reconstructed from its own usage records
reproduces it), and cost-tracker reads that figure rather than recomputing it. The gap was
**entirely gateway-side**: this gateway bills a median **×1.23** of Anthropic list price,
steady over 19 calibrated days (CV 0.053).

**What that means when you quote the figure.** Our number is what the tokens cost, and it
is right. It is *not* what the cap counts. With a $40 cap the refusal lands near **$32** of
list-price spend, so `today` is a **FLOOR against the cap** — say so whenever the number is
being used to decide whether there is headroom, because the error runs in the direction that
walks a session into a hard stop while the display still promises room. "$32.43 of $40" is
the shape of that trap.

