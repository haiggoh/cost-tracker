"""The rolling window (`report --days N`) and its per-day trend.

The property that matters is not the arithmetic — it is the SCALE. A rolling window answers "how
close did each day run to the ceiling", so the bars are scaled to the CAP, not to the busiest
day: scaled to the max, a uniformly bad month looks comfortable, which is the exact wrong lesson
to draw from the view. That is pinned below by a case where the two differ.
"""
import os
from datetime import date, timedelta

from conftest import load_ct

import pytest


def test_a_window_ends_today_and_holds_exactly_n_days(tmp_path):
    ct, _ledger = load_ct(tmp_path)
    today = os.environ["COST_TRACKER_TODAY"]
    for n in (1, 3, 30):
        days = ct.period_days("today", None, n)
        assert len(days) == n, f"--days {n} must yield {n} days, got {len(days)}"
        assert days[-1] == today, "a rolling window ends today, not at the newest data"
        assert days == sorted(days), "oldest first, so the trend reads chronologically"
        assert days[0] == (date.fromisoformat(today) - timedelta(days=n - 1)).isoformat()


def test_a_window_is_the_calendar_window_and_is_capped(tmp_path):
    """A rolling window is bounded by the CALENDAR, not by the data: period_days has no idea what
    days hold records, so `--days 4000` really would enumerate 4000 dates back to 2015. The CLI
    therefore caps the window (MAX_WINDOW_DAYS) rather than pretending the range function clamps —
    days beyond the record simply render as zero rows, which is the honest answer."""
    ct, _ledger = load_ct(tmp_path)
    assert len(ct.period_days("today", None, 4000)) == 4000
    assert len(ct.period_days("today", None, ct.MAX_WINDOW_DAYS)) == ct.MAX_WINDOW_DAYS
    over = ct.MAX_WINDOW_DAYS + 1
    assert len(ct.period_days("today", None, over)) == over, "the RANGE is not clamped — the CLI is"


def test_the_window_label_states_its_membership(tmp_path):
    """The repo's one rule: every figure names what it sums. "last 30 day(s)" says the window is
    bounded by days, not by data."""
    ct, _ledger = load_ct(tmp_path)
    data = ct.collect(ndays=7)
    assert "7 day" in data["period"], data["period"]
    assert len(data["period_days_utc"]) == 7


def test_the_trend_renders_one_row_per_day(tmp_path):
    ct, _ledger = load_ct(tmp_path)
    series = {f"2026-10-{d:02d}": {"ours_usd": 10.0, "estimated_usd": 0.0,
                                   "phantom_usd": 0.0, "sessions_counted": 1,
                                   "reset_anchored_sessions": 0, "classifier_usd": 0.0,
                                   "uncovered_usd": 0.0}
              for d in range(1, 6)}
    out = ct.render_trend({"period": "last 5 day(s)", "cap_usd": 40.0, "markup": 1.0,
                           "daily_series": series})
    for day in series:
        assert day in out, f"{day} must have its own row"
    assert "scaled to $40.00" in out, "the scale must be stated, not assumed"


def test_the_bars_scale_to_the_cap_not_the_busiest_day(tmp_path):
    """THE property, with a fixture where the two scalings DIFFER.

    Days at $10 and $15 against a $40 cap: cap-scaling draws 25% and 37.5% of the track, while
    max-day scaling would draw 67% and 100%. An earlier version of this test used $20/$40 against
    a $40 cap, where the cap and the max day COINCIDE — so both scalings rendered identically and
    a mutation flipping the scale survived it. A planted positive has to be able to fail.
    """
    ct, _ledger = load_ct(tmp_path)
    series = {"2026-10-01": {"ours_usd": 10.0, "estimated_usd": 0.0, "phantom_usd": 0.0,
                             "sessions_counted": 1, "reset_anchored_sessions": 0,
                             "classifier_usd": 0.0, "uncovered_usd": 0.0},
              "2026-10-02": {"ours_usd": 15.0, "estimated_usd": 0.0, "phantom_usd": 0.0,
                             "sessions_counted": 1, "reset_anchored_sessions": 0,
                             "classifier_usd": 0.0, "uncovered_usd": 0.0}}
    out = ct.render_trend({"period": "last 2 day(s)", "cap_usd": 40.0, "markup": 1.0,
                           "daily_series": series})
    lines = [ln for ln in out.splitlines() if "2026-10-0" in ln]
    first = lines[0].count("█") / ct.BAR_WIDTH
    second = lines[1].count("█") / ct.BAR_WIDTH
    assert abs(first - 0.25) < 0.05, \
        f"$10 of a $40 cap must draw a quarter bar, drew {first:.2f}"
    assert abs(second - 0.375) < 0.05, \
        f"$15 of a $40 cap must draw 37.5%, drew {second:.2f}"
    assert "25.0% of cap" in lines[0] and "37.5% of cap" in lines[1]


def test_no_cap_means_no_dangling_of_cap_qualifier(tmp_path):
    """With no cap known the row must not read `40.23 — of cap`: the phrase would look like a
    missing figure rather than an absent denominator (found by running it, not by reading it)."""
    ct, _ledger = load_ct(tmp_path)
    series = {"2026-10-01": {"ours_usd": 41.55, "estimated_usd": 0.0, "phantom_usd": 0.0,
                             "sessions_counted": 1, "reset_anchored_sessions": 0,
                             "classifier_usd": 0.0, "uncovered_usd": 0.0}}
    out = ct.render_trend({"period": "last 1 day(s)", "cap_usd": None, "markup": 1.0,
                           "daily_series": series})
    assert "of cap" not in out
    assert "the highest day — no cap is known" in out, "the fallback scale must be named"


def test_a_days_window_shows_no_trend(tmp_path):
    """A single day's breakdown must not append a trend that restates its own total."""
    ct, _ledger = load_ct(tmp_path)
    data = {"period": "today", "cloud_usd": 1.0, "local_phantom_usd": 0.0,
            "local_saved_usd": 0.0, "local_phantom_basis_usd": 0.0,
            "local_saved_basis_usd": 0.0, "savings_by_session": {},
            "phantom_basis": "Opus 5.5 (as recorded)", "daily_series": None}
    out = ct.render_breakdown(data)
    assert "trend —" not in out


def test_a_multi_day_breakdown_appends_the_trend(tmp_path):
    ct, _ledger = load_ct(tmp_path)
    series = {"2026-10-%02d" % d: {"ours_usd": 5.0, "estimated_usd": 0.0, "phantom_usd": 1.0,
                                   "sessions_counted": 1, "reset_anchored_sessions": 0,
                                   "classifier_usd": 0.0, "uncovered_usd": 0.0}
              for d in range(1, 4)}
    data = {"period": "last 3 day(s)", "cloud_usd": 15.0, "local_phantom_usd": 3.0,
            "local_saved_usd": 0.0, "local_phantom_basis_usd": 3.0,
            "local_saved_basis_usd": 0.0, "savings_by_session": {},
            "phantom_basis": "Opus 5.5 (as recorded)", "daily_series": series,
            "cap_usd": 40.0, "markup": 1.0}
    out = ct.render_breakdown(data)
    assert "trend —" in out
    for day in series:
        assert day in out


def test_days_and_a_period_flag_conflict(tmp_path):
    """The help says --days is mutually exclusive with the period flags, so the code must enforce
    it — a documented constraint nobody checks is the "help that lies" failure."""
    pytest.importorskip("subprocess")
    import subprocess
    # Exercise the CLI directly; the validation lives in main(), so a unit call cannot see it.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for extra in (["--days", "30", "--week"], ["--days", "30", "--since", "2026-10-01"],
                  ["--days", "0"], ["--days", "99999"]):
        p = subprocess.run(["python3", os.path.join(root, "bin", "cost-tracker"), "report"] + extra,
                           capture_output=True, text=True)
        assert p.returncode == 2, f"{extra} must be refused, got {p.returncode}"
        assert ("mutually exclusive" in p.stderr or "positive" in p.stderr
                or "capped at" in p.stderr), p.stderr
    # And the valid combination must still work
    p = subprocess.run(["python3", os.path.join(root, "bin", "cost-tracker"), "report",
                        "--days", "30", "--json"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
