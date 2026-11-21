#!/usr/bin/env python3
"""Shared-key budget snapshot: ONE cached, versioned accounting generation.

Deliverable A of the shared-key budget plan. The producer (cost-tracker report
--combined / statusline --combined, or the detached --refresh-shared worker)
writes one snapshot per (scope, UTC day); every consumer — the ordinary
statusline, the CLI, warnings and later the panel — reads that SAME generation
instead of recomputing its own numbers.

The snapshot is an ESTIMATE of the shared access's spend plus whatever gateway
evidence exists. It never invents headroom, never fabricates a correction
factor, and marks every unknown as null/unknown rather than zero.

Run `shared_snapshot.py --help`. Read-only subcommand: print a cached snapshot.

Environment (all optional):
  COST_TRACKER_CONFIG_DIR   default ~/.claude/cost-tracker
  COST_TRACKER_TODAY        override "today" as YYYY-MM-DD (tests)

Scope config (budget-scopes.json in the config dir):
  {"scopes": [{"id": "joyia-shared", "label": "JoyIA shared key"}]}

No scope file, or more than one scope with no explicit --scope, means NO
shared scope is selected: consumers keep their unscoped behaviour and never
present a combined amount as verified.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal

SCHEMA = "cost-tracker/shared-snapshot/1"
TTL_S = 900  # a snapshot older than this is labelled stale and refresh is re-armed
SCOPE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
COVERAGE_KEYS = ("complete", "missing_sources", "unpriced_requests",
                 "ambiguous_overlap", "inferred_bindings", "freshness")


def _config_dir():
    override = os.environ.get("COST_TRACKER_CONFIG_DIR")
    if override is not None and not override.strip():
        raise ValueError("COST_TRACKER_CONFIG_DIR must not be empty")
    return os.path.expanduser(override) if override else os.path.expanduser(
        "~/.claude/cost-tracker")


def _validate(scope_id, day_utc):
    if not isinstance(scope_id, str) or not SCOPE_RE.match(scope_id):
        raise ValueError("invalid scope id: " + repr(scope_id))
    if not isinstance(day_utc, str) or not DAY_RE.match(day_utc):
        raise ValueError("invalid UTC day: " + repr(day_utc))


def read_scopes(config_dir=None):
    config_dir = config_dir or _config_dir()
    try:
        with open(os.path.join(config_dir, "budget-scopes.json")) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return []
    scopes = doc.get("scopes") if isinstance(doc, dict) else None
    if not isinstance(scopes, list):
        return []
    out = []
    for s in scopes:
        if (isinstance(s, dict) and isinstance(s.get("id"), str)
                and SCOPE_RE.match(s["id"]) and isinstance(s.get("label"), str)):
            out.append({"id": s["id"], "label": s["label"]})
    return out


def resolve_scope(scope_id=None, config_dir=None):
    """The selected scope, or None when nothing is (or can be) selected.

    An EXPLICIT scope_id that is not configured fails loudly — a silent
    fallthrough would run the wrong budget. Auto-selection only when exactly
    one scope is configured; ambiguity selects nothing.
    """
    scopes = read_scopes(config_dir)
    if scope_id is not None:
        if not SCOPE_RE.match(scope_id):
            raise ValueError("invalid scope id: " + repr(scope_id))
        for s in scopes:
            if s["id"] == scope_id:
                return s
        raise ValueError("unknown scope id: " + scope_id
                         + " (configured: " + ", ".join(s["id"] for s in scopes) + ")")
    if len(scopes) == 1:
        return scopes[0]
    return None


def snapshot_path(scope_id, day_utc, config_dir=None):
    _validate(scope_id, day_utc)
    return os.path.join(config_dir or _config_dir(), "shared-snapshots",
                        scope_id + "-" + day_utc + ".json")


def read_cached_snapshot(scope_id, day_utc, config_dir=None):
    """The cached snapshot, or None (missing OR unreadable — callers must label
    the state, never treat None as a zero). A snapshot for another scope/day is
    not this snapshot."""
    try:
        path = snapshot_path(scope_id, day_utc, config_dir)
        with open(path) as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(snap, dict) or snap.get("schema") != SCHEMA:
        return None
    if snap.get("scope_id") != scope_id or snap.get("day_utc") != day_utc:
        return None
    if not isinstance(snap.get("coverage"), dict) or any(
            k not in snap["coverage"] for k in COVERAGE_KEYS):
        return None
    return snap


def is_stale(snap, now=None):
    try:
        ts = datetime.fromisoformat(str(snap.get("last_refreshed_at")).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return True
    if ts.tzinfo is None:
        return True
    now = now or datetime.now(timezone.utc)
    return (now - ts.astimezone(timezone.utc)).total_seconds() > TTL_S


def write_snapshot(snapshot, config_dir=None):
    """Atomic private write; a failed write raises — the producer is the only
    writer and must know."""
    _validate(snapshot["scope_id"], snapshot["day_utc"])
    path = snapshot_path(snapshot["scope_id"], snapshot["day_utc"], config_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True, mode=0o700)
    os.chmod(os.path.dirname(path), 0o700)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(snapshot, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    return path


def build_snapshot(scope, day_utc, claude_billed, shared_result, cap_usd=None):
    """Assemble the contract snapshot from the producer's own numbers.

    `scope` is a {"id","label"} dict; `shared_result` is shared_costs.combine()'s
    output; `cap_usd` is the CLI's already-resolved cap for the day. Gateway
    observation fields pass through when the shared result carries them; the
    primary amount prefers a genuine gateway observation and labels its basis.
    """
    if not isinstance(scope, dict) or "id" not in scope:
        raise ValueError("invalid scope")
    claude = float(claude_billed or 0.0)
    codex = float((shared_result or {}).get("codex_usd") or 0.0)
    estimated = Decimal(str(claude + codex)).quantize(Decimal("0.000001"))
    confirmed = (shared_result or {}).get("gateway_confirmed_usd")
    observed_at = (shared_result or {}).get("gateway_observed_at")
    source = (shared_result or {}).get("gateway_source")
    diagnostics = list((shared_result or {}).get("diagnostics") or [])
    complete = bool((shared_result or {}).get("complete")) and not diagnostics
    refreshed_at = datetime.now(timezone.utc).isoformat()
    cap = float(cap_usd) if cap_usd is not None else None
    if confirmed is not None:
        primary, basis = float(confirmed), "gateway observation"
        if observed_at:
            basis += " at " + str(observed_at)
        if source:
            basis += " (" + str(source) + ")"
    else:
        primary, basis = float(estimated), "local estimate (coverage complete)" if complete \
            else "local estimate (coverage incomplete)"
    budget_state = "unknown"
    if confirmed is not None and cap is not None and float(confirmed) >= cap:
        budget_state = "cap-reached"
    payload = {
        "schema": SCHEMA,
        "scope_id": scope["id"],
        "scope_label": scope.get("label", scope["id"]),
        "day_utc": day_utc,
        "estimated_usd": format(estimated, "f"),
        "combined_usd": float(estimated),  # legacy numeric field, an estimate
        "by_host": {"claude_code": claude, "codex": codex,
                    "claude_desktop": None, "other": None},
        "unassigned_usd": None,
        "gateway_confirmed_usd": None if confirmed is None else str(confirmed),
        "gateway_observed_at": observed_at,
        "gateway_source": source,
        "reserved_usd": (shared_result or {}).get("reserved_spend_usd"),
        "cap_usd": cap,
        "discrepancy_usd": None,  # needs a MATCHED accounting cutoff; never guessed
        "last_refreshed_at": refreshed_at,
        "pricing_sources": dict((shared_result or {}).get("price_sources") or {}),
        "coverage": {"complete": complete,
                     "missing_sources": [],
                     "unpriced_requests": int((shared_result or {}).get("unpriced_responses") or 0),
                     "ambiguous_overlap": any("overlap" in d for d in diagnostics),
                     "inferred_bindings": False,
                     "freshness": "live-at-write"},
        "diagnostics": diagnostics,
        "primary_amount_usd": primary,
        "primary_basis": basis,
        "budget_state": budget_state,
        "headroom_usd": None,  # unknown scope/sources never produce reassuring headroom
    }
    payload["snapshot_id"] = hashlib.sha256(json.dumps(
        [payload["schema"], payload["scope_id"], payload["day_utc"],
         payload["estimated_usd"], payload["gateway_confirmed_usd"], refreshed_at],
        sort_keys=True).encode()).hexdigest()[:16]
    return payload


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Read one cached shared-key budget snapshot (see module docstring).")
    ap.add_argument("--scope", metavar="ID", default=None,
                    help="shared scope id; default: the single configured scope")
    ap.add_argument("--day", metavar="YYYY-MM-DD", default=None,
                    help="UTC day (default: today)")
    ap.add_argument("--json", action="store_true", help="print the raw snapshot JSON")
    args = ap.parse_args(argv)
    try:
        scope = resolve_scope(args.scope)
    except ValueError as e:
        print("shared_snapshot: " + str(e), file=sys.stderr)
        return 2
    if scope is None:
        print("shared_snapshot: no shared scope configured", file=sys.stderr)
        return 2
    day = args.day or os.environ.get("COST_TRACKER_TODAY") or \
        datetime.now(timezone.utc).date().isoformat()
    snap = read_cached_snapshot(scope["id"], day)
    if snap is None:
        print("shared_snapshot: no cached snapshot for " + scope["id"] + " on " + day,
              file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(snap, indent=2, sort_keys=True))
    else:
        amount = snap.get("primary_amount_usd")
        cap = snap.get("cap_usd")
        line = "scope " + scope["label"] + " (" + scope["id"] + ") " + day + ": "
        line += ("$" + format(amount, ".2f") if amount is not None else "unknown")
        if cap is not None:
            line += "/$" + format(cap, ".0f")
        line += " [" + str(snap.get("primary_basis")) + "]"
        if not snap["coverage"]["complete"]:
            line += " (INCOMPLETE)"
        if is_stale(snap):
            line += " (stale)"
        if snap.get("budget_state") == "cap-reached":
            line += " CAP REACHED"
        print(line)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
