"""Tests for the interactive menu (bin/cost_tracker_menu.py) — Task 1: the TTY gate and the
branded header.

The gate is the safety half: bare `cost-tracker` is also what a script, a pipe and a hook run,
so the menu may only open when BOTH streams are a terminal, and COST_TRACKER_NO_MENU is the
explicit opt-out for a terminal that still wants the old plain behaviour. The header is the
branding half: the 💸 mark leads the menu the same way it leads the credit moments
(2026-10-28 branding spec).

No ledger, no clock, no subprocess here — the gate takes injected streams and an env dict, so
every case is a plain function call.
"""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "cost_tracker_menu", ROOT / "bin" / "cost_tracker_menu.py")
menu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(menu)


# --------------------------------------------------------------------------- helpers


class Tty:
    def isatty(self):
        return True


class NotATty:
    def isatty(self):
        return False


# --------------------------------------------------------------------------- the TTY gate


def test_available_false_without_a_tty():
    """A pipe, a script and a hook all reach the same code path as a bare `cost-tracker`. If the
    gate ever opens for them, they block forever on input that will never arrive."""
    assert menu.available(NotATty(), NotATty(), env={}) is False


def test_available_true_when_both_streams_are_a_tty():
    assert menu.available(Tty(), Tty(), env={}) is True


def test_available_false_when_only_one_stream_is_a_tty():
    """`cost-tracker | head` on a terminal has a tty stdin and a piped stdout. Prompting there
    would print into the pipe and read from the keyboard, which is worse than either alone."""
    assert menu.available(Tty(), NotATty(), env={}) is False
    assert menu.available(NotATty(), Tty(), env={}) is False


@pytest.mark.parametrize("val,expected", [
    ("1", False), ("true", False), ("yes", False),
    ("TRUE", False), (" YES ", False),
    ("", True), ("0", True), ("false", True), ("no", True),
    ("FALSE", True), ("No", True),
])
def test_available_false_when_env_opt_out(val, expected):
    """COST_TRACKER_NO_MENU is the explicit opt-out for an interactive terminal that still wants
    the old plain behaviour. Matched after strip+lower: a user who set FALSE or No asked FOR the
    menu, and mixed case must not silently opt them out (review fix round 1)."""
    assert menu.available(Tty(), Tty(), env={"COST_TRACKER_NO_MENU": val}) is expected


# --------------------------------------------------------------------------- the branded header


def test_header_is_the_branded_block(capsys):
    """The exact block, pinned verbatim: the 💸 mark, the one-line charter, the 72-column rule."""
    menu.print_header()
    out = capsys.readouterr().out
    assert out == (
        "\n💸 Cost Tracker\n"
        "   Today's spend, the cap, and how this session connects — one place for the money questions\n"
        + "=" * 72 + "\n"
    )
