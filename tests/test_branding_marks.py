"""tests/test_branding_marks.py — the 💸 identity mark leads the credit moments.

Credit moments per the 2026-10-28 branding spec, amended 2026-10-30: the daily-cap WARNING
line keeps its plain ⚠️ form — at the moment the budget actually runs out the whimsical 💸
would read as mocking the user (tone amendment), so the warn line is pinned 💸-free; the
routine (non-warning) tally line and the free-lane line stay unbranded, and the statusline
is out of scope entirely (user decision 2026-10-28: never brand the statusline).
All scratch env — no real ledger, cap or clock.
"""
import importlib.util
import os

import pytest

TALLY = os.path.join(os.path.dirname(__file__), "..", "bin", "budget-tally.py")


@pytest.fixture()
def btc(tmp_path, monkeypatch):
    ledger = tmp_path / "cost-ledger"
    recon = tmp_path / "cost-ledger-reconstructed"
    projects = tmp_path / "projects"
    for d in (ledger, recon, projects):
        d.mkdir()
    monkeypatch.setenv("BUDGET_TALLY_LEDGER_DIR", str(ledger))
    monkeypatch.setenv("COST_LEDGER_RECON_DIR", str(recon))
    monkeypatch.setenv("COST_TRACKER_PROJECTS_DIR", str(projects))
    monkeypatch.setenv("BUDGET_TALLY_TODAY", "2026-10-25")   # no clock in this test at all
    monkeypatch.setenv("COST_TRACKER_CAP_USD", "40")        # the cap branch, so the warn prefix renders
    s = importlib.util.spec_from_file_location("bt_branding", TALLY)
    mod = importlib.util.module_from_spec(s)
    s.loader.exec_module(mod)
    return mod


def test_warn_line_keeps_the_sober_warning(btc):
    line = btc.format_line(36.0, 0.9, 4.0, [], "today across all sessions",
                           0.0, 36.0, 0.0, warn=True)
    # Exact prefix through the computed figure — a rewording of the header fails this.
    # Tone amendment 2026-10-30: no 💸 at the running-out moment — the plain ⚠️ is the
    # whole message; the mark belongs to cost-tracker's other credit moments.
    assert line.startswith("⚠️ WARNING — 90% of daily cap used: ")
    assert "% of daily cap used" in line
    assert "💸" not in line


def test_routine_line_stays_unbranded(btc):
    line = btc.format_line(4.0, 0.1, 36.0, [], "today across all sessions",
                           0.0, 4.0, 0.0, warn=True)
    # Exact stable prefix (the dollar figure depends on the shipped fee, so it is not pinned).
    assert line.startswith("budget-tally: today's spend (today across all sessions) ≈ $")
    assert "💸" not in line


def test_statusline_wrapper_carries_no_mark():
    """User decision 2026-10-28: the identity mark never appears in the statusline.

    A source-level pin, so the constraint fails loudly if anyone ever prefixes the
    wrapper's output rather than relying on a reviewer remembering the decision.
    """
    wrapper = os.path.join(os.path.dirname(__file__), "..", "bin", "statusline-render.sh")
    assert os.path.isfile(wrapper), "statusline wrapper missing — the pin must not pass vacuously"
    text = open(wrapper, encoding="utf-8", errors="replace").read()
    assert "💸" not in text


def test_free_lane_line_stays_unbranded(btc):
    line = btc.format_line(0.0, 0.0, None, [], "test scope", 0.0, 0.0, 0.0, warn=True, free=True)
    assert line.startswith("budget-tally: free lane") and "💸" not in line
