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


# --------------------------------------------------------------------------- the action loop


class Fake:
    """Stands in for the CLI dispatch; records argvs so a keystroke sequence can be asserted
    as the command it composes."""

    def __init__(self, rc=0):
        self.calls = []
        self.rc = rc

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self.rc


def drive(monkeypatch, keys, echo=True):
    """Run the selector over a scripted keystroke list. An exhausted script raises EOFError,
    which the selector must treat as 'back out' — so a script that forgets to quit still ends
    the test instead of hanging it."""
    seq = list(keys)

    def fake_input(_prompt=""):
        if not seq:
            raise EOFError()
        return seq.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    fake = Fake()
    rc = menu.run(dispatch=fake, echo=echo)
    return fake.calls, rc


def test_actions_are_exactly_the_six_routine_commands():
    """Only the routine read-shaped subcommands. The machine surface (statusline) and the
    pre-0.12/informational ones (markup, prices) are never offered."""
    names = [name for name, _desc, _fn in menu.ACTIONS]
    assert set(names) == {"report", "cap", "connection", "fee", "calibrate", "doctor"}


def test_report_action_composes_report_argv(monkeypatch):
    """The report action prompts for a period (default today) and hands the REAL CLI argv to
    dispatch — the menu composes, it never reimplements."""
    calls, rc = drive(monkeypatch, ["report", "", "q"])
    assert calls == [["report", "today"]]
    assert rc == 0


def test_echo_prints_the_command(monkeypatch, capsys):
    """The echoed line teaches the CLI: what ran is printed as a pasteable command."""
    drive(monkeypatch, ["cap", "q"])
    assert "$ cost-tracker cap" in capsys.readouterr().out


def test_echo_absent_when_disabled(monkeypatch, capsys):
    drive(monkeypatch, ["cap", "q"], echo=False)
    assert "$ cost-tracker" not in capsys.readouterr().out


def test_eof_backs_out_cleanly(monkeypatch):
    """^D means back out — never a partial dispatch, never a crash."""
    calls, rc = drive(monkeypatch, [])
    assert calls == []
    assert rc == 0


def test_eof_mid_action_backs_out_without_dispatch(monkeypatch):
    """^D inside an action's own prompt (report's period) backs out of the ACTION — no argv is
    composed or dispatched. Pins the inner Cancelled handler: a mutation removing it would let
    a half-built action escape the loop while the rest of the suite stays green."""
    calls, rc = drive(monkeypatch, ["report"])
    assert calls == []
    assert rc == 0


def test_unknown_pick_reprompts(monkeypatch, capsys):
    calls, rc = drive(monkeypatch, ["banana", "q"])
    assert calls == []
    assert "no action 'banana'" in capsys.readouterr().out
    assert rc == 0
