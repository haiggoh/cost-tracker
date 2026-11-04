"""Tests for the interactive menu (bin/cost_tracker_menu.py) — Task 1: the TTY gate and the
branded header.

The gate is the safety half: bare `cost-tracker` is also what a script, a pipe and a hook run,
so the menu may only open when BOTH streams are a terminal, and COST_TRACKER_NO_MENU is the
explicit opt-out for a terminal that still wants the old plain behaviour. The header is the
branding half: the 💸 mark leads the menu the same way it leads the credit moments
(2026-10-28 branding spec).

Tasks 1-2 (the gate and the loop) take injected streams and an env dict — every case a plain
function call, no subprocess. Task 3 wires the REAL CLI: subprocess and pty cases drive
`bin/cost-tracker` itself under a sandbox env that pins every path the CLI reads into tmp_path.
"""
import importlib.util
import os
import pathlib
import pty
import select
import subprocess
import sys
import time

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


# --------------------------------------------------------------------------- CLI wiring (Task 3)
#
# Tasks 1-2 tested the gate and the loop with injected streams. These drive the REAL
# `bin/cost-tracker` in a subprocess, so the argparse wiring itself is under test: `menu` is
# a subcommand, a bare invocation on a terminal opens the selector, and a bare invocation
# without a terminal is byte-for-byte the report it printed before the menu existed.

# Captured verbatim 2026-11-04 from `bin/cost-tracker` with stdin=DEVNULL under the sandbox env
# below, BEFORE the gate was wired — the exact bytes a pipe got and must keep getting.
BARE_REPORT_SNAPSHOT = (
    "cost-tracker — period: today (UTC 2026-09-03)\n"
    "membership: all sessions with a ledger or history entry on this UTC day\n"
    "\n"
    "session       axis           today $  session lifetime $\n"
    "--------------------------------------------------------\n"
    "(no sessions in this period)\n"
    "--------------------------------------------------------\n"
    "TOTAL cloud                     0.00                   —\n"
    "local saved   local   not measured (savings ledger unavailable)\n"
)


def _sandbox_env(tmp_path, extra=None):
    """The child-process twin of conftest.load_ct: every path the CLI (and the budget-tally /
    connection modules it loads) reads is pointed into tmp_path, so no test touches the real
    ~/.claude state and the output does not depend on this machine's ledger, cap or provider."""
    env = dict(os.environ)
    env.update({
        "COST_TRACKER_LEDGER_DIR": str(tmp_path / "cost-ledger"),
        "COST_TRACKER_HISTORY": str(tmp_path / "history.log"),
        "COST_TRACKER_CONFIG_DIR": str(tmp_path / "config"),
        "COST_TRACKER_PROJECTS_DIR": str(tmp_path / "projects"),
        "COST_TRACKER_REMEMBER_DIRS": str(tmp_path / "remember-none"),
        "COST_TRACKER_SG_LOG": str(tmp_path / "sg-none" / "log.txt"),
        "COST_TRACKER_TODAY": "2026-09-03",
        "COST_TRACKER_SAVINGS_CMD": "/bin/false",
        "COST_TRACKER_NO_SPAWN": "1",
        "ANTHROPIC_BASE_URL": "https://gw.example.test",   # neutral: unregistered, fee 1.0
    })
    for var in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
                "COST_TRACKER_NO_MENU", "COST_TRACKER_CAP_USD", "BUDGET_TALLY_CAP_USD",
                "COST_TRACKER_FEE", "COST_TRACKER_MARKUP", "COST_TRACKER_APPLY_GAP",
                "COST_TRACKER_PHANTOM_BASIS", "COST_TRACKER_ESTIMATES"):
        env.pop(var, None)
    if extra:
        env.update(extra)
    return env


def _bare_argv():
    return [sys.executable, str(ROOT / "bin" / "cost-tracker")]


def _run_cli(tmp_path, *args, extra=None):
    """The CLI in a child with stdin=DEVNULL — the shape a pipe, a script and a hook give it.
    timeout=30 is the hang guard: the regression this gate exists to prevent is a bare piped
    call blocking forever on a prompt that will never be answered."""
    (tmp_path / "cost-ledger").mkdir(parents=True, exist_ok=True)
    return subprocess.run(_bare_argv() + list(args),
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          env=_sandbox_env(tmp_path, extra), timeout=30)


def _under_pty(argv, keystrokes, env, timeout=30):
    """Run the CLI with a real pseudo-terminal on both ends, feed keystrokes, return
    (output, rc). This is the only way to exercise the TTY branch at all (the waypoints
    tests/test_menu.py:_under_pty shape)."""
    parent, child = pty.openpty()
    p = subprocess.Popen(argv, stdin=child, stdout=child, stderr=child, env=env,
                         close_fds=True)
    os.close(child)
    if keystrokes:
        os.write(parent, keystrokes)
    chunks = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        r, _w, _x = select.select([parent], [], [], 0.2)
        if r:
            try:
                data = os.read(parent, 65536)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
        elif p.poll() is not None:
            break
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:          # pragma: no cover - only on a hang regression
        p.kill()
        p.wait()
        os.close(parent)
        raise AssertionError("the menu hung under a pty") from None
    os.close(parent)
    return b"".join(chunks).decode("utf-8", "replace"), p.returncode


def test_bare_piped_invocation_unchanged(tmp_path):
    """THE regression pin: bare `cost-tracker` with stdin=DEVNULL still prints exactly the
    report it printed before the menu existed — rc 0, nothing on stderr, byte-for-byte the
    captured snapshot, no prompt. A pipe, a script and a hook all take this path."""
    r = _run_cli(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stderr == "", r.stderr
    assert r.stdout == BARE_REPORT_SNAPSHOT, r.stdout


def test_bare_tty_invocation_opens_the_menu(tmp_path):
    """End to end on a real pty: bare `cost-tracker` with both streams a terminal opens the
    selector, and q quits with rc 0. The q-quit path must touch NOTHING — no dispatch, no
    write anywhere in the sandbox."""
    out, rc = _under_pty(_bare_argv(), b"q\r", _sandbox_env(tmp_path))
    assert rc == 0, out
    assert "💸 Cost Tracker" in out, out
    assert "cost-tracker>" in out, out
    assert "per-session breakdown" in out, out          # the actions are offered
    assert "$ cost-tracker " not in out, out            # q dispatched nothing
    # Untouched means never created OR left empty — the strongest shape the sandbox can prove
    # from the outside (the menu q-quit path reads no store and creates no directory).
    ledger = tmp_path / "cost-ledger"
    assert not ledger.exists() or not list(ledger.iterdir()), "the menu wrote to the ledger"
    assert not (tmp_path / "config").exists(), "the menu wrote config"
    assert not (tmp_path / "history.log").exists(), "the menu wrote history"


def test_menu_subcommand_opens_the_menu(tmp_path):
    """`cost-tracker menu` opens the selector explicitly — same gate, same header, q quits."""
    out, rc = _under_pty(_bare_argv() + ["menu"], b"q\r", _sandbox_env(tmp_path))
    assert rc == 0, out
    assert "💸 Cost Tracker" in out, out
    assert "cost-tracker>" in out, out


def test_menu_subcommand_without_a_tty_refuses_with_a_reason(tmp_path):
    """The gate is one gate for BOTH routes (the plan's constraint is unqualified): an explicit
    `menu` without a terminal refuses rather than half-opens — the same call waypoints' own
    selector makes."""
    r = _run_cli(tmp_path, "menu")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "interactive terminal" in r.stderr, r.stderr
    assert "💸" not in r.stdout


def test_no_menu_env_opt_out(tmp_path):
    """COST_TRACKER_NO_MENU=1 on an interactive terminal: bare `cost-tracker` behaves exactly as
    it did before the menu — the plain report, no header, no prompt."""
    out, rc = _under_pty(_bare_argv(), b"",
                         _sandbox_env(tmp_path, {"COST_TRACKER_NO_MENU": "1"}))
    assert rc == 0, out
    assert "💸" not in out, out
    assert "cost-tracker>" not in out, out
    assert "cost-tracker — period: today (UTC 2026-09-03)" in out, out


def test_argparse_error_path_unchanged(tmp_path):
    """A bad flag still dies in argparse with exit 2 — the gate sits AFTER parse_args, so an
    argument error never reaches it. The error line is verbatim from the pre-menu capture
    (2026-11-04). The usage line is asserted structurally, twice over: its {choices} tuple
    grows when the CLI grows a subcommand (it did here, with `menu` — pinning it verbatim
    would have made that growth impossible), and argparse WRAPS it to the terminal width, so
    even its line count is not stable across environments."""
    r = _run_cli(tmp_path, "report", "--bogus")
    assert r.returncode == 2, r.stdout + r.stderr
    err_lines = r.stderr.splitlines()
    assert err_lines[0].startswith("usage: cost-tracker"), r.stderr
    assert err_lines[-1] == "cost-tracker: error: unrecognized arguments: --bogus", r.stderr
