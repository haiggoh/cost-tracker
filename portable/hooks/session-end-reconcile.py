#!/usr/bin/env python3
"""hooks/session-end-reconcile.py — give a session that never rendered a PERMANENT cost record.

WHY. The cost ledger is written by the statusLine render, so a headless session (claude -p, the
SDK, a background task, an agent run) records NOTHING: no spend line, and no phantom-savings line
either — the phantom axis reads the ledger, so a free headless session saves $0 on paper no matter
what it actually avoided. The user's requirement (2026-10-24): "these recordings should not depend
on a statusline render; the statusline and permanent record should both be informed by the same
source."

WHAT IT WRITES, AND WHERE. For a session with no ledger line, reconstruct its cost from the
transcript the SessionEnd payload carries and write it to its OWN directory:

    ~/.claude/cost-ledger-reconstructed/<session_id>   ->  "<utc-date> <reconstructed-cost>"

The separate directory is the load-bearing decision. Every reader of ~/.claude/cost-ledger treats
what it finds there as the authoritative .cost.total_cost_usd, in a 3/5-field format whose first
numeric field is a CUMULATIVE. A reconstructed figure must never be able to sit in that directory
and be misread as one, so it does not: it goes somewhere only this hook and an explicitly labelled
reader look, in a deliberately different shape (two fields, a point-in-time figure, not a
cumulative) that a format-expecting parser cannot silently reinterpret.

Reconstruction AGREES closely — measured 2026-10-25 across eight days, 0.99–1.00x against the
ledger, worst −1.4% — but agreeing is not being identical, and the label is what keeps a reader
from treating it as authoritative.

FAILS OPEN, always. This hook must never be the reason a session cannot exit: a missing or
unreadable transcript, malformed stdin, an absent session id, an unwritable directory — each
exits 0 having written nothing. A hook that blocks exit converts a bookkeeping nicety into a
broken session.

Env: BUDGET_TALLY_LEDGER_DIR     authoritative ledger (default ~/.claude/cost-ledger)
     COST_LEDGER_RECON_DIR       where reconstructed lines go (default ~/.claude/cost-ledger-reconstructed)
     COST_TRACKER_PROJECTS_DIR   transcript root (default ~/.claude/projects)
Usage: hooks/session-end-reconcile.py        (SessionEnd payload on stdin; declared in hooks.json)
"""
import importlib.util
import json
import os
import sys

HOME = os.path.expanduser("~")


def _plugin_root():
    """The directory holding bin/ and hooks/. CLAUDE_PLUGIN_ROOT is how the harness points at the
    INSTALLED copy; the fallback is this file's own location, so running it from a checkout works."""
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root and os.path.isdir(os.path.join(root, "bin")):
        return root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_tally():
    """budget-tally's own pricing and transcript math. Re-deriving it here would guarantee drift."""
    path = os.path.join(_plugin_root(), "bin", "budget-tally.py")
    spec = importlib.util.spec_from_file_location("_bt_for_reconcile", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dirs():
    ledger = os.environ.get("BUDGET_TALLY_LEDGER_DIR") or os.path.join(HOME, ".claude/cost-ledger")
    recon = os.environ.get("COST_LEDGER_RECON_DIR") or os.path.join(HOME, ".claude/cost-ledger-reconstructed")
    return ledger, recon


def _transcript_from_payload(payload, projects):
    """The transcript path, taken from the payload and TRUSTED ONLY AFTER it is checked.

    The payload's transcript_path is authoritative when present, but a hook that path-walks on
    attacker-controlled input is a hazard; and a payload that names nothing is common enough (a
    subagent, a killed session) that the fallback matters.
    """
    p = payload.get("transcript_path")
    if isinstance(p, str) and p and os.path.isfile(p):
        return p
    sid = payload.get("session_id")
    if not sid:
        return None
    # Deterministic, same mechanism budget-tally uses: <projects>/<cwd-slug>/<sid>.jsonl
    for slug in sorted(os.listdir(projects)):
        cand = os.path.join(projects, slug, sid + ".jsonl")
        if os.path.isfile(cand):
            return cand
    return None


def reconcile(payload, out=None):
    """Write a reconstructed line for `payload` if — and only if — the session never rendered.

    Returns a short status string for the log. Never raises: every failure is a reason to do
    nothing, because doing nothing is always safe and raising is not.
    """
    out = out if out is not None else sys.stderr
    ledger, recon = _dirs()
    sid = payload.get("session_id")
    if not isinstance(sid, str) or not sid:
        return "no session id — nothing to reconcile"

    # A rendered session is authoritative. Touching it would be the one truly harmful thing this
    # hook could do, so this check comes before anything else.
    if os.path.exists(os.path.join(ledger, sid)):
        return "%s rendered — authoritative line kept" % sid[:8]

    projects = os.environ.get("COST_TRACKER_PROJECTS_DIR") or os.path.join(HOME, ".claude/projects")
    path = _transcript_from_payload(payload, projects)
    if not path:
        return "no transcript for %s — nothing reconstructed" % sid[:8]

    try:
        bt = _load_tally()
        usage, _unknown = bt.tally([path])
        cost, _priced, _unk = bt._price(usage)
    except Exception as exc:  # noqa: BLE001 — a hook must not take the session down with it
        return "reconstruction failed for %s (%s)" % (sid[:8], exc.__class__.__name__)

    if not cost or cost <= 0:
        # A zero-cost session (free lane, or genuinely nothing spent) has no figure to record, and
        # inventing one would corrupt the very axis this exists to fix.
        return "%s reconstructed to $0 — no line written" % sid[:8]

    dest = os.path.join(recon, sid)
    if os.path.exists(dest):
        return "%s already has a reconstructed line — kept" % sid[:8]

    try:
        os.makedirs(recon, exist_ok=True)
        tmp = dest + ".tmp"
        with open(tmp, "w") as fh:
            # budget-tally's own UTC day (it honours BUDGET_TALLY_TODAY, so a test can pin the date)
            fh.write("%s %.8f\n" % (bt.TODAY, cost))
        os.replace(tmp, dest)  # atomic: a reader never sees a half-written line
    except OSError as exc:
        return "could not write %s (%s)" % (sid[:8], exc.__class__.__name__)

    print("💸 cost-tracker: %s never rendered — reconstructed $%.4f to %s"
          % (sid[:8], cost, dest), file=out)
    return "reconstructed $%.4f for %s" % (cost, sid[:8])


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-h", "--help"):
        sys.stdout.write(__doc__)
        return 0
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
    except Exception as exc:  # noqa: BLE001
        print("session-end-reconcile: malformed stdin (%s) — doing nothing" % exc.__class__.__name__,
              file=sys.stderr)
        return 0
    try:
        reconcile(payload)
    except Exception as exc:  # noqa: BLE001 — the last line of defence: never fail the session
        print("session-end-reconcile: %s — doing nothing" % exc.__class__.__name__, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
