"""0.23.0: budget-tally's read_ledger_today() reads the sibling cost-tracker CLI's
merged history+ledger records when it can load it, so a counter reset recovered from
the history log is reported in full instead of as a floor. Without the sibling it
falls back to the ledger-file read and the warning labels the figure a ledger-file
floor. The sibling is found by walking __file__ THROUGH symlinks (the wired
~/.claude/scripts symlink must still find bin/cost-tracker)."""
import importlib.machinery
import importlib.util
import os
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
D = "2026-09-03"


def _load_bt(path, ledger_dir, monkeypatch):
    monkeypatch.setenv("BUDGET_TALLY_LEDGER_DIR", str(ledger_dir))
    monkeypatch.setenv("BUDGET_TALLY_TODAY", D)
    monkeypatch.setenv("BUDGET_TALLY_PROJECTS_DIR", str(pathlib.Path(ledger_dir).parent / "none"))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    loader = importlib.machinery.SourceFileLoader("bt_streams_under_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = mod
    loader.exec_module(mod)
    return mod


def _fixture(tmp_path):
    """E6 shape: 32.25 pre-reset (k=aaaa), then a fresh counter at 9.48 (k=bbbb, p=2);
    the ledger file holds only the post-reset row."""
    ledger = tmp_path / "cost-ledger"
    ledger.mkdir()
    # the sibling cost-tracker derives the history path from the ledger dir
    hist = tmp_path / "cost-ledger-history.log"
    sid = "s1"
    hist.write_text(
        f"2026-09-03T10:00:00Z {sid} {D} 32.25 0 32.25 k=aaaa\n"
        f"2026-09-03T12:49:58Z {sid} {D} 9.48 0 9.48 k=bbbb p=2\n")
    (ledger / sid).write_text(f"{D} 9.48 0 k=bbbb\n")
    return ledger, hist, sid


def test_read_ledger_today_recovers_reset_from_history(tmp_path, monkeypatch):
    ledger, hist, sid = _fixture(tmp_path)
    bt = _load_bt(ROOT / "bin" / "budget-tally.py", ledger, monkeypatch)
    out = bt.read_ledger_today()
    assert out[sid] == pytest.approx(32.25 + 9.48)
    assert bt._LEDGER_TODAY_SOURCE == "history+ledger"
    assert bt._ledger_source_note() == ""


def test_read_ledger_today_falls_back_without_sibling(tmp_path, monkeypatch):
    ledger, hist, sid = _fixture(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shutil.copy(ROOT / "bin" / "budget-tally.py", bindir / "budget-tally.py")
    bt = _load_bt(bindir / "budget-tally.py", ledger, monkeypatch)
    out = bt.read_ledger_today()
    assert out[sid] == pytest.approx(9.48)          # ledger-file floor: post-reset only
    assert bt._LEDGER_TODAY_SOURCE == "ledger-file"
    assert bt._ledger_source_note() == " (ledger-file floor)"


def test_sibling_resolution_walks_through_symlink(tmp_path, monkeypatch):
    """The wired entry point is a symlink (~/.claude/scripts/budget-tally.py -> the
    plugin bin/): the sibling lookup must realpath __file__, or bin/cost-tracker
    would silently vanish and every figure would fall back to the floor."""
    ledger, hist, sid = _fixture(tmp_path)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    os.symlink(ROOT / "bin" / "budget-tally.py", scripts / "budget-tally.py")
    bt = _load_bt(scripts / "budget-tally.py", ledger, monkeypatch)
    out = bt.read_ledger_today()
    assert out[sid] == pytest.approx(32.25 + 9.48)
    assert bt._LEDGER_TODAY_SOURCE == "history+ledger"


def test_history_readers_accept_the_pid_token(tmp_path, monkeypatch):
    """Receipt 0's inventory found two more positional history readers in
    budget-tally.py: _ledger_first_cum (fields 1 and 3) and local_lane_sessions
    (fields 1,2,3,5 with a len<6 guard). Both index fixed columns and ignore
    trailing tokens, so an 8-field k=+p= row must parse exactly like a 6-field one."""
    ledger = tmp_path / "cost-ledger"
    ledger.mkdir()
    hist = tmp_path / "cost-ledger-history.log"
    hist.write_text(
        f"2026-09-03T10:00:00Z s1 {D} 3.0 0 3.0\n"
        f"2026-09-03T11:00:00Z s1 {D} 0 0 9.0 k=aaaa p=1\n")
    bt = _load_bt(ROOT / "bin" / "budget-tally.py", ledger, monkeypatch)
    assert bt._ledger_first_cum("s1") == 3.0          # field 4 of the FIRST row, unshifted
    assert "s1" in bt.local_lane_sessions(D)          # 8-field local row still detected
