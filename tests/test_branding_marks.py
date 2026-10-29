"""tests/test_branding_marks.py — the 💸 identity mark leads the credit moments.

Credit moments per the 2026-10-28 branding spec: the daily-cap WARNING line carries the mark;
the routine (non-warning) tally line and the free-lane line stay unbranded, and the statusline
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


def test_warn_line_leads_with_the_brand_mark(btc):
    line = btc.format_line(36.0, 0.9, 4.0, [], "today across all sessions",
                           0.0, 36.0, 0.0, warn=True)
    assert line.startswith("💸 ⚠️ WARNING — ")
    assert "% of daily cap used" in line


def test_routine_line_stays_unbranded(btc):
    line = btc.format_line(4.0, 0.1, 36.0, [], "today across all sessions",
                           0.0, 4.0, 0.0, warn=True)
    assert line.startswith("budget-tally: ") and "💸" not in line


def test_free_lane_line_stays_unbranded(btc):
    line = btc.format_line(0.0, 0.0, None, [], "test scope", 0.0, 0.0, 0.0, warn=True, free=True)
    assert line.startswith("budget-tally: free lane") and "💸" not in line
