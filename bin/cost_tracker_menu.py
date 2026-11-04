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
    if env.get("COST_TRACKER_NO_MENU", "").strip() not in ("", "0", "false", "no"):
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
