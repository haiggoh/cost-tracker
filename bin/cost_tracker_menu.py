#!/usr/bin/env python3
"""Interactive menu for the cost-tracker CLI — the selector for the subcommands nobody
remembers under pressure.

THE CENTRAL DESIGN RULE (inherited from the waypoints selector this module is modelled on):
the menu never reimplements a command. Every action composes an argv and hands it to the
ordinary CLI dispatch, so the guards, the ledger writes and the confirmations behave exactly
as if the command had been typed. The menu can therefore SHOW the argv before running it —
echoing the command teaches the real CLI, so a reader graduates off the menu instead of
depending on it.

Pure stdlib, no curses, no third-party TUI: the tool has to work when nothing else does.
"""

import os
import sys


def available(stream_in=None, stream_out=None, env=None):
    """Whether the menu may run at all.

    Gated on BOTH streams being a terminal, because a bare `cost-tracker` is also what a
    script, a pipe (`cost-tracker | head`) and a hook run — and any of those blocking forever
    on a prompt for input that will never arrive is a far worse regression than the missing
    convenience. The plain subcommands stay the non-interactive answer, unchanged.

    COST_TRACKER_NO_MENU is the explicit opt-out for an interactive terminal that still wants
    the old plain behaviour.
    """
    env = os.environ if env is None else env
    stream_in = sys.stdin if stream_in is None else stream_in
    stream_out = sys.stdout if stream_out is None else stream_out
    if env.get("COST_TRACKER_NO_MENU", "").strip().lower() not in ("", "0", "false", "no"):
        return False
    try:
        return bool(stream_in.isatty() and stream_out.isatty())
    except (AttributeError, ValueError):
        return False


def print_header():
    """The branded block that opens the menu — the 💸 mark leads it the same way it leads the
    credit moments (2026-10-28 branding spec). Pinned verbatim by tests/test_cost_tracker_menu.py;
    a rewording fails that test."""
    print()
    print("💸 Cost Tracker")
    print("   Today's spend, the cap, and how this session connects — one place for the money questions")
    print("=" * 72)


class Cancelled(Exception):
    """Raised by any prompt when the user backs out (^D/^C). Unwinds to the menu loop, never
    to a half-built argv — an action is either fully specified or not attempted."""


def _in(prompt):
    """One line of input. EOF (^D) and interrupt (^C) both mean 'back out of this action',
    never 'crash' and never 'proceed with a default' — a mis-typed ^D must not touch anything."""
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise Cancelled() from None


# --------------------------------------------------------------------------- actions


def _a_report():
    period = _in("  period [today]: ")
    return ["report", period or "today"]


def _a_cap():
    return ["cap"]


def _a_connection():
    return ["connection"]


def _a_fee():
    return ["fee"]


def _a_calibrate():
    return ["calibrate"]


def _a_doctor():
    return ["doctor"]


# The six routine subcommands only: the machine surface (statusline) and the pre-0.12 /
# informational ones (markup, prices) are deliberately not offered.
ACTIONS = [
    ("report", "per-session breakdown for a period", _a_report),
    ("cap", "the daily cap and where it came from", _a_cap),
    ("connection", "how this session connects: limit, fee, gap", _a_connection),
    ("fee", "the gateway FEE (published, deterministic) and its source", _a_fee),
    ("calibrate", "our daily totals vs the gateway's own figure", _a_calibrate),
    ("doctor", "quarantined records and resolved config", _a_doctor),
]


def _print_actions(rows):
    print()
    for n, (name, desc, _fn) in enumerate(rows, 1):
        print(f"   {n:>2}) {name:<11} {desc}")
    print()


def _quote(a):
    """Shell-quote for the ECHO only. The argv handed to dispatch is untouched, so this can
    never affect what runs — it exists so the printed line is one a reader can paste."""
    if a and all(ch.isalnum() or ch in "-_./:=@" for ch in a):
        return a
    return "'" + a.replace("'", "'\\''") + "'"


def run(dispatch, echo=True):
    """The selector loop (modelled on the waypoints selector).

    Stays in the loop after each command: the reason to open this at all is a money question
    or two — a cap, then a report — and one action per invocation would make the common case
    the slow one. Every action composes an argv and hands it to the ordinary dispatch; the
    echo exists so the reader learns the real CLI.
    """
    rows = list(ACTIONS)
    by_name = {name: fn for name, _d, fn in rows}
    print_header()
    _print_actions(rows)

    while True:
        try:
            pick = _in("cost-tracker> ")
        except Cancelled:
            return 0
        if not pick:
            _print_actions(rows)
            continue
        if pick.lower() in ("q", "quit", "exit"):
            return 0
        if pick.lower() in ("?", "h", "help", "m", "menu"):
            _print_actions(rows)
            continue

        fn = None
        if pick.isdigit() and 1 <= int(pick) <= len(rows):
            fn = rows[int(pick) - 1][2]
        elif pick in by_name:
            fn = by_name[pick]
        if fn is None:
            print(f"  (no action '{pick}' — press return for the menu, q to quit)")
            continue

        try:
            argv = fn()
        except Cancelled:
            print("  (cancelled — nothing was changed)")
            continue

        if echo:
            print(f"\n  $ cost-tracker {' '.join(_quote(a) for a in argv)}\n")
        try:
            rc_val = dispatch(argv)
        except SystemExit as e:
            # The CLI exits rather than returns on a rejected argument; that must end the
            # ACTION, not the session.
            if e.code:
                print(f"  (command exited {e.code} — nothing further in this action ran)")
        else:
            # A subcommand that signals failure by RETURNING non-zero must not read as
            # menu-success either.
            if rc_val:
                print(f"  (command exited {rc_val} — nothing further in this action ran)")
        print("  — press return for the menu, or type the next action.")
