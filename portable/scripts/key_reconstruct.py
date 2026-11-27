#!/usr/bin/env python3
"""Hindsight per-key attribution (cost-tracker 0.24.0).

Turns one UTC day's history stream deltas plus the gateway's own budget refusals into
per-key attribution. A refusal is ground truth: "session S was billing key K at T, and K
stood at $C of its Max budget". Rules (user, 2026-11-26), applied in order:

1. A refusal quoting cost >= its Max budget CLOSES that key for the rest of the UTC day;
   after it the key may accrue at most OVERSHOOT_FRAC x cap more (in-flight turns).
2. A refusal below the cap (a premature kill near the cap) does not close the key.
3. A refusal anchors the session's stream window that led up to it to the named key.
4. An observed k= tag stands unless it bills a closed key beyond the overshoot budget.
5. An unresolved or contradicted delta takes key J only if exactly ONE open key J is
   billing another session within CONCURRENCY_WINDOW_S; otherwise it stays unattributed.
6. A key whose day total exceeds (1 + OVERSHOOT_FRAC) x cap is a VIOLATION line — the
   figures are never scaled or clamped to fit.

No cap value is built in: every threshold is a fraction of the cap the key's own refusal
quoted (or, for the violation check only, the caller-supplied route-inherited cap).

CLI: reads {"day", "deltas": [...], "refusals": [...], "caps": {...}} as JSON on stdin and
prints the result as JSON. `cost-tracker keys --reconstruct` is the normal entry point.
"""
import argparse
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime

OVERSHOOT_FRAC = 0.05
PREMATURE_FRAC = 0.10        # informational: a kill in [(1-x) cap, cap) is "premature"
CONCURRENCY_WINDOW_S = 900


@dataclass(frozen=True)
class Delta:
    ts: str
    sid: str
    pid: object
    stream: int
    usd: float
    hint: str = ""


@dataclass(frozen=True)
class Refusal:
    ts: str
    sid: str
    hint: str
    cost: float
    cap: float


@dataclass
class Result:
    entries: list = field(default_factory=list)
    by_key: dict = field(default_factory=dict)
    unattributed: float = 0.0
    violations: list = field(default_factory=list)


def _dt(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _sid_of(d):
    return (d.sid, d.pid, d.stream)


def reconstruct(day, deltas, refusals, caps=None):
    """Attribute `day`'s deltas to keys. `caps` maps hint -> cap for keys without a
    refusal that day (route-inherited, used only for the violation check)."""
    caps = dict(caps or {})
    ds = sorted((d for d in deltas if d.ts[:10] == day and d.usd > 0), key=lambda d: d.ts)
    rs = sorted((r for r in refusals if r.ts[:10] == day), key=lambda r: r.ts)
    own_cap = {}
    closed = {}
    for r in rs:
        if r.cap:
            own_cap[r.hint] = r.cap
            if r.hint not in closed and r.cost >= r.cap:
                closed[r.hint] = r.ts                       # rule 1 (rule 2: below cap)

    def cap_of(k):
        return own_cap.get(k) or caps.get(k)

    # rules 3 + 4: refusal anchor, else the observed tag
    assign: list = [None] * len(ds)    # per delta: (key, basis, evidence) or None
    for i, d in enumerate(ds):
        nxt = next((r for r in rs if r.sid == d.sid and r.ts >= d.ts), None)
        if nxt is not None:
            before = [x for x in ds if x.sid == d.sid and x.ts <= nxt.ts]
            if before and _sid_of(max(before, key=lambda x: x.ts)) == _sid_of(d):
                assign[i] = (nxt.hint, "refusal",
                             f"refusal {nxt.ts} cost {nxt.cost:.2f} of {nxt.cap:g}")
                continue
        if d.hint:
            assign[i] = (d.hint, "tag", "observed k= tag")

    # rule 1 enforcement, in time order: a closed key only absorbs its overshoot budget
    used = defaultdict(float)
    pending = []
    for i, d in enumerate(ds):
        a = assign[i]
        if a is None:
            pending.append((i, "concurrent"))
            continue
        k = a[0]
        if k in closed and d.ts > closed[k]:
            budget = OVERSHOOT_FRAC * (cap_of(k) or 0.0)
            if used[k] + d.usd <= budget + 1e-9:
                used[k] += d.usd
            else:
                assign[i] = None
                pending.append((i, a[1] + "-after-kill->concurrent"))

    # rule 5: exactly one open key billing another session nearby
    resolved = [(ds[i], a[0]) for i, a in enumerate(assign) if a is not None]
    for i, basis in pending:
        d = ds[i]
        t = _dt(d.ts)
        cands = {}
        for x, k in resolved:
            if x.sid == d.sid or (k in closed and closed[k] < d.ts):
                continue
            if abs((_dt(x.ts) - t).total_seconds()) <= CONCURRENCY_WINDOW_S:
                cands.setdefault(k, x.sid)
        if len(cands) == 1:
            k, other = next(iter(cands.items()))
            assign[i] = (k, basis, f"concurrent with session {other[:8]}")

    res = Result()
    last = {}
    by_key = defaultdict(float)
    for i, d in enumerate(ds):
        a = assign[i]
        if a is None:
            res.unattributed += d.usd
            continue
        k, basis, ev = a
        by_key[k] += d.usd
        prev = last.get(_sid_of(d))
        if prev is not None and prev["key"] == k and prev["basis"] == basis:
            prev["to_ts"] = d.ts
            prev["usd"] += d.usd
            continue
        e = {"day": day, "sid": d.sid, "pid": d.pid, "stream": d.stream, "from_ts": d.ts,
             "to_ts": d.ts, "key": k, "basis": basis, "usd": d.usd, "evidence": ev}
        res.entries.append(e)
        last[_sid_of(d)] = e
    res.by_key = dict(by_key)
    for k, v in sorted(res.by_key.items()):
        c = cap_of(k)
        if c and v > (1 + OVERSHOOT_FRAC) * c + 1e-9:
            res.violations.append(
                f"{day} key {k}: ${v:.2f} exceeds {1 + OVERSHOOT_FRAC:g} x cap ${c:g}")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="key_reconstruct",
        description="Hindsight per-key attribution: reads {day, deltas, refusals, caps} "
                    "JSON on stdin, prints the attribution as JSON. No environment "
                    "variables. Normal entry point: `cost-tracker keys --reconstruct`.")
    ap.parse_args(argv)
    doc = json.load(sys.stdin)
    res = reconstruct(doc["day"], [Delta(**d) for d in doc.get("deltas") or []],
                      [Refusal(**r) for r in doc.get("refusals") or []], doc.get("caps"))
    print(json.dumps(asdict(res), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
