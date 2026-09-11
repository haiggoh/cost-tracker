"""cost-tracker tests.

The fixture matrix in fixtures/ledger_records.json is the parser's ground truth:
every real record shape the capture wrapper has ever written (2-field legacy,
3-field with baseline, 4-field with a local phantom, history rows) plus the
malformed shapes that must be quarantined rather than counted.
"""
import json
import pathlib

import pytest

from conftest import load_ct

FIXTURES = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "ledger_records.json").read_text()
)


@pytest.mark.parametrize("fx", FIXTURES, ids=[f["name"] for f in FIXTURES])
def test_fixture_matrix(tmp_path, fx):
    ct, ledger = load_ct(tmp_path)
    sid = "11111111-1111-1111-1111-111111111111"
    (ledger / sid).write_text(fx["content"])
    recs = ct.read_ledger()
    assert len(recs) == 1
    rec = recs[0]
    exp = fx["expect"]
    assert rec.valid is exp["valid"], f"{fx['name']}: {rec.reason}"
    assert rec.reason == exp["reason"]
    assert rec.axis == exp["axis"]
    assert rec.reset_anchored is exp.get("reset_anchored", False)
    # day_usd is the spend attributable to the record's OWN day; the fixture's
    # today_usd is what a "today" report may count, which is 0 for a record dated
    # any other day. Assert both, so neither the record maths nor the period
    # filtering can regress unnoticed.
    if rec.valid and rec.day == "2026-09-03":
        assert rec.day_usd == pytest.approx(exp["today_usd"])
    else:
        assert exp["today_usd"] == 0.0
    counted = ct.collect("today")["cloud_usd"]
    # abs tolerance: collect() rounds published figures to 4dp on purpose, so a
    # fixture carrying full float precision must not be compared exactly.
    assert counted == pytest.approx(exp["today_usd"], abs=1e-4)


def test_quarantined_records_are_excluded_from_the_total(tmp_path):
    ct, ledger = load_ct(tmp_path)
    (ledger / "aaaaaaaa-0000-0000-0000-000000000001").write_text("2026-09-03 10 4")
    (ledger / "aaaaaaaa-0000-0000-0000-000000000002").write_text("2026-09-03 -5")
    data = ct.collect("today")
    assert data["cloud_usd"] == pytest.approx(6.0)
    assert len(data["quarantined"]) == 1
    assert data["quarantined"][0]["reason"] == "negative-cost"
    # a quarantined record is not a session
    assert "aaaaaaaa-0000-0000-0000-000000000002" not in data["sessions"]


def test_a_resumed_counter_is_anchored_at_the_reset_not_clamped_to_zero(tmp_path):
    """The real 2026-08-26 case. Claude Code's total_cost_usd restarts at 0 on resume,
    so the cumulative falls below the baseline carried into the day. Subtracting the
    stale baseline is negative and clamping it to 0 reported $16.07 of real spend as
    $0.00 — which is what budget-tally did, and why this needed fixing rather than
    quarantining."""
    ct, ledger = load_ct(tmp_path)
    sid = "e4d10d09-35c1-46b7-97de-56157c965fcc"
    (ledger / sid).write_text("2026-09-03 16.06745625 17.350854999999992")
    data = ct.collect("today")
    assert data["cloud_usd"] == pytest.approx(16.06745625, abs=1e-4)
    assert data["reset_anchored_sessions"] == [sid]
    assert data["sessions"][sid]["reset_anchored"] is True
    assert data["quarantined"] == []
    table = ct.render_table(data)
    assert "16.07*" in table                      # the figure is marked
    assert "FLOORS" in table                      # and named as a floor
    assert "RESET" in ct.render_doctor(data)


def test_a_reset_that_climbs_back_above_the_stale_baseline_is_still_detected(tmp_path):
    """Session 960b07ca, 2026-08-24: 0 -> 1.11 -> 3.68 -> 15.998 against a baseline of
    3.614. The FINAL record looks perfectly ordinary, so anchoring on it alone
    under-counted the day by exactly that stale $3.61. The reset is only visible in the
    row sequence, and the live ledger record must not erase what history established."""
    ct, ledger = load_ct(tmp_path)
    sid = "960b07ca-a2e8-4bb5-978c-c2b4a9f47bd7"
    pathlib.Path(ct.HISTORY_PATH).write_text(
        f"2026-09-03T00:33:07Z {sid} 2026-09-03 0 3.6144925 0\n"
        f"2026-09-03T00:34:21Z {sid} 2026-09-03 1.11415125 3.6144925 1.11415125\n"
        f"2026-09-03T00:43:16Z {sid} 2026-09-03 3.680644 3.6144925 3.680644\n"
        f"2026-09-03T09:00:00Z {sid} 2026-09-03 15.99793825 3.6144925 15.99793825\n"
    )
    (ledger / sid).write_text("2026-09-03 15.99793825 3.6144925")
    data = ct.collect("today")
    assert data["sessions"][sid]["reset_anchored"] is True
    assert data["cloud_usd"] == pytest.approx(15.99793825, abs=1e-4)  # not 15.998 - 3.614


def test_a_mid_day_reset_with_no_baseline_is_detected_from_the_sequence(tmp_path):
    """A reset needs no baseline to be visible: a cumulative that goes DOWN is one."""
    ct, ledger = load_ct(tmp_path)
    sid = "bbbbbbbb-0000-0000-0000-000000000009"
    pathlib.Path(ct.HISTORY_PATH).write_text(
        f"2026-09-03T01:00:00Z {sid} 2026-09-03 9.0 0 9.0\n"
        f"2026-09-03T02:00:00Z {sid} 2026-09-03 2.0 0 2.0\n"
    )
    data = ct.collect("today")
    assert data["sessions"][sid]["reset_anchored"] is True
    assert data["cloud_usd"] == pytest.approx(2.0)


def test_a_local_render_is_not_mistaken_for_a_reset(tmp_path):
    """A local render legitimately reports 0 on every row. Treating each as a
    cumulative going DOWN would flag every local session as a reset."""
    ct, ledger = load_ct(tmp_path)
    sid = "cccccccc-0000-0000-0000-000000000009"
    pathlib.Path(ct.HISTORY_PATH).write_text(
        f"2026-09-03T01:00:00Z {sid} 2026-09-03 0 0 5.0\n"
        f"2026-09-03T02:00:00Z {sid} 2026-09-03 0 0 9.0\n"
    )
    (ledger / sid).write_text("2026-09-03 0 0 9.0")
    data = ct.collect("today")
    assert data["sessions"][sid]["reset_anchored"] is False
    assert data["sessions"][sid]["axis"] == "local"
    assert data["cloud_usd"] == 0.0


def test_a_pre_field4_zero_is_reported_as_ambiguous_not_asserted_local(tmp_path):
    ct, ledger = load_ct(tmp_path)
    sid = "dddddddd-0000-0000-0000-000000000009"
    (ledger / sid).write_text("2026-09-03 0 114.25")
    data = ct.collect("today")
    assert data["sessions"][sid]["axis"] == "zero"
    assert data["zero_sessions"] == [sid]
    assert data["local_sessions"] == []
    assert data["cloud_usd"] == 0.0
    assert "cannot tell those apart" in ct.render_table(data)


def test_local_traffic_never_enters_cloud_spend(tmp_path):
    ct, ledger = load_ct(tmp_path)
    (ledger / "cloudses0-0000-0000-0000-000000000001").write_text("2026-09-03 8 3")
    (ledger / "localses0-0000-0000-0000-000000000002").write_text("2026-09-03 0 0 55.93")
    (ledger / "localses0-0000-0000-0000-000000000003").write_text("2026-09-03 0 114.25")
    data = ct.collect("today")
    assert data["cloud_usd"] == pytest.approx(5.0)
    # only the field-4 record is asserted local; the pre-field-4 zero is `zero`
    assert len(data["local_sessions"]) == 1
    assert len(data["zero_sessions"]) == 1


def test_multi_day_session_counted_once_per_day_not_lifetime(tmp_path):
    """The regression the waypoint names: a session with an entry on the current
    UTC day that has also run on earlier days must be counted EXACTLY once, at its
    daily delta — never at its lifetime cumulative."""
    ct, ledger = load_ct(tmp_path)
    sid = "bbbbbbbb-0000-0000-0000-000000000001"
    hist = pathlib.Path(ct.HISTORY_PATH)
    hist.write_text(
        "2026-09-01T10:00:00Z %s 2026-09-01 10 0 10\n"
        "2026-09-01T11:00:00Z %s 2026-09-01 25 0 25\n"
        "2026-09-02T10:00:00Z %s 2026-09-02 40 25 40\n"
        "2026-09-03T10:00:00Z %s 2026-09-03 55 40 55\n"
        "2026-09-03T11:00:00Z %s 2026-09-03 60 40 60\n" % ((sid,) * 5)
    )
    (ledger / sid).write_text("2026-09-03 60 40")
    today = ct.collect("today")
    assert today["cloud_usd"] == pytest.approx(20.0)          # 60 - 40, not 60
    assert today["sessions"][sid]["lifetime_usd"] == pytest.approx(60.0)
    week = ct.collect("week")
    # 25 + 15 + 20 = 60 across three days: the lifetime, reached by summing daily
    # deltas, and each render counted once despite five history rows.
    assert week["cloud_usd"] == pytest.approx(60.0)
    assert len(week["sessions"]) == 1


def test_history_rows_are_grouped_not_summed(tmp_path):
    ct, ledger = load_ct(tmp_path)
    sid = "cccccccc-0000-0000-0000-000000000001"
    pathlib.Path(ct.HISTORY_PATH).write_text(
        "".join(f"2026-09-03T1{i}:00:00Z {sid} 2026-09-03 {i + 1} 0 {i + 1}\n"
                for i in range(6))
    )
    data = ct.collect("today")
    assert data["cloud_usd"] == pytest.approx(6.0)   # last row wins, not 1+2+3+4+5+6


def test_live_ledger_wins_over_history_for_the_same_session_day(tmp_path):
    ct, ledger = load_ct(tmp_path)
    sid = "dddddddd-0000-0000-0000-000000000001"
    pathlib.Path(ct.HISTORY_PATH).write_text(f"2026-09-03T10:00:00Z {sid} 2026-09-03 5 0 5\n")
    (ledger / sid).write_text("2026-09-03 9 0")
    assert ct.collect("today")["cloud_usd"] == pytest.approx(9.0)


def test_period_window_excludes_days_outside_it(tmp_path):
    ct, ledger = load_ct(tmp_path)
    sid = "eeeeeeee-0000-0000-0000-000000000001"
    pathlib.Path(ct.HISTORY_PATH).write_text(
        f"2026-07-01T10:00:00Z {sid} 2026-07-01 100 0 100\n"
        f"2026-09-03T10:00:00Z {sid} 2026-09-03 130 100 130\n"
    )
    assert ct.collect("today")["cloud_usd"] == pytest.approx(30.0)
    assert ct.collect("month")["cloud_usd"] == pytest.approx(30.0)   # July is outside 30 days
    assert ct.collect("today", since="2026-06-01")["cloud_usd"] == pytest.approx(130.0)


def test_statusline_labels_its_axis_and_respects_an_unset_cap(tmp_path):
    ct, ledger = load_ct(tmp_path)
    (ledger / "ffffffff-0000-0000-0000-000000000001").write_text("2026-09-03 12.40 0")
    line = ct.render_statusline(ct.collect("today"))
    assert line == "today: $12.40"          # no denominator invented
    ct, ledger = load_ct(tmp_path, cap=40)
    (ledger / "ffffffff-0000-0000-0000-000000000001").write_text("2026-09-03 30.12 0")
    assert ct.render_statusline(ct.collect("today")) == "today: $30.12/$40"


def test_savings_absent_is_reported_as_unmeasured_not_zero(tmp_path):
    ct, ledger = load_ct(tmp_path, savings_cmd="/bin/false")
    data = ct.collect("today")
    assert data["local_saved_usd"] is None
    assert "not measured" in ct.render_table(data)
    assert "local saved" not in ct.render_statusline(data)


def test_savings_present_is_appended_to_the_statusline(tmp_path):
    fake = tmp_path / "fake-savings"
    fake.write_text('#!/bin/sh\necho \'{"saved_usd": 4.8, "events": 3}\'\n')
    fake.chmod(0o755)
    ct, ledger = load_ct(tmp_path, cap=40, savings_cmd=str(fake))
    (ledger / "ffffffff-0000-0000-0000-000000000001").write_text("2026-09-03 30.12 0")
    line = ct.render_statusline(ct.collect("today"))
    # "cloud" is back HERE and only here: with a savings figure beside it the word is
    # load-bearing again — it names which of the two dollar figures is the cloud one.
    assert line == "today: cloud $30.12/$40 · local saved $4.80"


def test_every_figure_in_the_table_names_its_axis(tmp_path):
    """The D15 guard as a test: no dollar column may appear without a label saying
    what it aggregates."""
    ct, ledger = load_ct(tmp_path, cap=40)
    (ledger / "ffffffff-0000-0000-0000-000000000001").write_text("2026-09-03 30.12 10")
    table = ct.render_table(ct.collect("today"))
    assert "membership: all sessions with a ledger or history entry on this UTC day" in table
    assert "today $" in table and "session lifetime $" in table
    assert "TOTAL cloud" in table
    # the total must NOT fill the lifetime column: summing overlapping cumulatives
    # across sessions is a meaningless number.
    total_row = [l for l in table.splitlines() if l.startswith("TOTAL cloud")][0]
    assert total_row.rstrip().endswith("—")


def test_missing_stores_are_not_an_error(tmp_path):
    ct, _ = load_ct(tmp_path / "nonexistent", make_dirs=False)
    data = ct.collect("today")
    assert data["cloud_usd"] == 0.0
    assert data["sessions"] == {}


def test_cli_always_exits_zero_even_on_a_broken_store(tmp_path, capsys):
    ct, ledger = load_ct(tmp_path)
    (ledger / "garbage").write_text("not a record at all")
    assert ct.main(["report"]) == 0
    assert ct.main(["statusline"]) == 0
    assert ct.main(["doctor"]) == 0
    assert ct.main(["report", "--json"]) == 0
    out = capsys.readouterr().out
    assert "Traceback" not in out


def test_json_output_is_contract_stamped(tmp_path):
    ct, ledger = load_ct(tmp_path)
    (ledger / "ffffffff-0000-0000-0000-000000000001").write_text("2026-09-03 1 0")
    data = ct.collect("today")
    assert data["contract"] == 1
    json.dumps(data)          # must be serialisable, including a None cap


def test_incompatible_savings_json_is_unmeasured_not_zero(tmp_path):
    """A savings tool that RUNS but returns a shape we don't understand (a future
    contract, a renamed field) must read as unmeasured. Falling back to 0.00 there
    is the same lie as an absent ledger, and harder to spot because the command
    succeeded."""
    fake = tmp_path / "wrong-shape"
    fake.write_text('#!/bin/sh\necho \'{"contract": 99, "total_saved": 4.8}\'\n')
    fake.chmod(0o755)
    ct, ledger = load_ct(tmp_path, cap=40, savings_cmd=str(fake))
    (ledger / "ffffffff-0000-0000-0000-000000000001").write_text("2026-09-03 5 0")
    data = ct.collect("today")
    assert data["local_saved_usd"] is None
    assert "not measured" in ct.render_table(data)
    assert ct.render_statusline(data) == "today: $5.00/$40"


def test_non_json_savings_output_is_unmeasured(tmp_path):
    fake = tmp_path / "chatty"
    fake.write_text('#!/bin/sh\necho "savings: about five dollars"\n')
    fake.chmod(0o755)
    ct, _ = load_ct(tmp_path, savings_cmd=str(fake))
    assert ct.collect("today")["local_saved_usd"] is None


def test_a_cloud_session_that_switches_to_local_mid_day_is_not_a_reset(tmp_path):
    """The case that actually exposes the guard: a session bills on cloud, then the
    endpoint switches to localhost and every later render reports 0. Reading that drop
    as a cumulative going DOWN would flag a normal endpoint switch as a counter reset
    and re-anchor the day on the local zero."""
    ct, ledger = load_ct(tmp_path)
    sid = "eeeeeeee-0000-0000-0000-000000000009"
    pathlib.Path(ct.HISTORY_PATH).write_text(
        f"2026-09-03T01:00:00Z {sid} 2026-09-03 5.0 0 5.0\n"
        f"2026-09-03T02:00:00Z {sid} 2026-09-03 0 0 7.5\n"
        f"2026-09-03T03:00:00Z {sid} 2026-09-03 0 0 9.0\n"
    )
    data = ct.collect("today")
    assert data["sessions"][sid]["reset_anchored"] is False
    assert data["sessions"][sid]["axis"] == "local"


def test_a_small_day_figure_beside_a_large_lifetime_is_correct_not_a_lost_day(tmp_path):
    """The 2026-09-08 false alarm, locked in as behaviour rather than fixed as a bug.

    A session that began 17:46Z on day N and crossed midnight reported $0.1164 for
    day N+1 while its lifetime cumulative stood at $20.596. That was read as the
    day-boundary logic losing $20.48, and a waypoint was filed against it. It is not
    a defect: $0.1164 is the spend that accrued AFTER the baseline carried into day
    N+1, and the remaining $20.48 belongs to day N, where it is counted. The two
    figures answer different questions and the report prints both on labelled axes.

    Conservation is the assertion that matters: the per-day deltas must sum to the
    lifetime. A test that only checked the day figure would pass for a genuinely
    lossy implementation too."""
    ct, ledger = load_ct(tmp_path, today="2026-09-08")
    sid = "3065c2cf-0000-0000-0000-000000000001"
    pathlib.Path(ct.HISTORY_PATH).write_text(
        f"2026-09-07T17:46:00Z {sid} 2026-09-07 0 0 0\n"
        f"2026-09-07T23:59:00Z {sid} 2026-09-07 20.4796692 0 20.4796692\n"
        f"2026-09-08T04:00:00Z {sid} 2026-09-08 20.596069250000003 20.4796692 20.596069250000003\n"
    )
    (ledger / sid).write_text("2026-09-08 20.596069250000003 20.4796692")

    today = ct.collect("today")
    assert today["cloud_usd"] == pytest.approx(0.1164, abs=1e-4)
    assert today["sessions"][sid]["lifetime_usd"] == pytest.approx(20.5961, abs=1e-4)

    # the $20.48 is not lost — it is day N's, and summing the two days recovers the
    # lifetime exactly. This is the assertion that would fail if a day were dropped.
    prior = ct.collect("today", since=None, days=["2026-09-07"])
    assert prior["cloud_usd"] == pytest.approx(20.4797, abs=1e-4)
    assert prior["cloud_usd"] + today["cloud_usd"] == pytest.approx(20.5961, abs=1e-4)


def test_a_transient_mid_crossing_baseline_self_heals_at_the_next_render(tmp_path):
    """Why the false alarm was transient and could not be reproduced afterwards.

    The capture wrapper sets day N+1's baseline from the prior record's cumulative.
    A render that lands mid-crossing can therefore write a baseline that momentarily
    over-states what carried in, making the day figure too SMALL. The next render
    re-reads its own record, takes the same-day branch and keeps the established
    baseline — so the figure corrects itself and the anomalous state is gone before
    anyone can inspect it. Asserted here so the self-heal is a guarantee rather than
    an accident, since it is the reason the reported symptom vanished."""
    ct, ledger = load_ct(tmp_path, today="2026-09-08")
    sid = "3065c2cf-0000-0000-0000-000000000002"

    # the anomalous render: baseline over-states the carry-in, day looks near-free
    (ledger / sid).write_text("2026-09-08 20.60 20.48")
    assert ct.collect("today")["cloud_usd"] == pytest.approx(0.12, abs=1e-2)

    # the healed render: same day, true carry-in restored, full day visible again
    (ledger / sid).write_text("2026-09-08 33.007983750000015 0.679908")
    assert ct.collect("today")["cloud_usd"] == pytest.approx(32.328, abs=1e-3)


# --- the gateway markup -------------------------------------------------------
# The compatibility requirement is the load-bearing one: a normal Claude Code user has no
# gateway, no refusals, and must see EXACTLY what they saw before. So these tests assert
# byte-identical output at the default as carefully as they assert the adjustment works.

def test_no_markup_is_the_default_and_output_is_unchanged(tmp_path):
    """The first-party case. No config, no env, no evidence — identity."""
    ct, ledger = load_ct(tmp_path, cap=40)
    (ledger / "ffffffff-0000-0000-0000-000000000001").write_text("2026-09-03 30.12 0")
    assert ct.markup_factor() == 1.0
    data = ct.collect("today")
    assert data["markup"] == 1.0
    # The pre-markup strings, character for character.
    assert ct.render_statusline(data) == "today: $30.12/$40"
    table = ct.render_table(data)
    assert "billed" not in table and "eff. cap" not in table and "×" not in table
    # The additive keys exist but say nothing new, so a consumer reading either is right.
    assert data["billed_usd"] == pytest.approx(data["cloud_usd"])
    assert data["effective_cap_usd"] == pytest.approx(40.0)


def test_a_markup_moves_the_denominator_and_never_our_measured_total(tmp_path):
    """The whole design in one assertion: our figure is untouched, the cap shrinks."""
    ct, ledger = load_ct(tmp_path, cap=40)
    (ledger / "ffffffff-0000-0000-0000-000000000001").write_text("2026-09-03 30.12 0")
    import os
    os.environ["COST_TRACKER_MARKUP"] = "1.25"
    ct, ledger = load_ct(tmp_path, cap=40)
    os.environ["COST_TRACKER_MARKUP"] = "1.25"
    (ledger / "ffffffff-0000-0000-0000-000000000001").write_text("2026-09-03 30.12 0")
    data = ct.collect("today")
    assert data["cloud_usd"] == pytest.approx(30.12)      # UNCHANGED — the point
    assert data["markup"] == pytest.approx(1.25)
    assert data["billed_usd"] == pytest.approx(37.65)
    assert data["effective_cap_usd"] == pytest.approx(32.0)
    # The SEGMENT shows one axis, the GATEWAY's, because that is the cap the user is
    # measured against and the figure its refusal message quotes. Showing $30.12/$32
    # was arithmetically equivalent but read as an unexplained second cap: the refusal
    # says $40, so anything else on the denominator confuses. The list-price figure and
    # the effective cap remain in the DATA (asserted above) and in `report`.
    line = ct.render_statusline(data)
    assert line == "today: $37.65/$40 gw"
    assert "$32" not in line and "eff" not in line
    # 94% of the effective cap, not the reassuring 75% of the raw one.
    assert data["billed_pct_of_cap"] == pytest.approx(37.65 / 40, abs=1e-4)
    os.environ.pop("COST_TRACKER_MARKUP", None)


def test_a_markup_is_refused_when_the_per_day_ratios_are_not_a_single_rate(tmp_path):
    """A scattered ratio is not a rate. Learning nothing is the correct outcome."""
    ct, _ = load_ct(tmp_path)
    steady = {"days": [{"verdict": "ok", "ours_usd": 10.0, "gateway_usd": 12.3}
                       for _ in range(8)]}
    m = ct.measure_markup(steady)
    assert m["reason"] is None and m["factor"] == pytest.approx(1.23)
    # Same mean, wild spread.
    scattered = {"days": [{"verdict": "ok", "ours_usd": 10.0, "gateway_usd": g}
                          for g in (5.0, 25.0, 6.0, 22.0, 8.0, 19.0, 30.0, 4.0)]}
    s = ct.measure_markup(scattered)
    assert s["reason"] and "scatter" in s["reason"]
    # Too few days, however steady.
    few = {"days": [{"verdict": "ok", "ours_usd": 10.0, "gateway_usd": 12.3}
                    for _ in range(2)]}
    assert "calibrated day" in ct.measure_markup(few)["reason"]
    # Ours already at or above the gateway is not a discount to apply.
    over = {"days": [{"verdict": "ok", "ours_usd": 12.0, "gateway_usd": 10.0}
                     for _ in range(8)]}
    assert "nothing to mark up" in ct.measure_markup(over)["reason"]
    # A no-data day contributes no ratio.
    assert ct.measure_markup({"days": [{"verdict": "no-data", "ours_usd": 0,
                                        "gateway_usd": 40.0}]})["n_days"] == 0


def test_the_markup_command_explains_the_absence_on_a_first_party_account(tmp_path):
    """The empty state must carry the finding, or the next user re-derives it."""
    ct, _ = load_ct(tmp_path)
    out = ct.render_markup(None, 1.0)
    assert "NONE (×1.0)" in out
    assert "list price" in out
    assert "correct and expected state" in out
    # The measured evidence travels with the tool.
    assert "1.23" in out and "$32" in out
    # And it says what was ruled out, so the dead ends aren't re-explored.
    assert "day-boundary" in out and "tokenizer" in out


def test_learn_markup_persists_the_factor_and_survives_a_reload(tmp_path):
    ct, _ = load_ct(tmp_path)
    cfg = {"contract": 1, "markup": {"factor": 1.2271, "n_days": 19, "cv": 0.0525,
                                     "basis": "median per-day ratio"}}
    ct.write_config(cfg)
    ct2, _ = load_ct(tmp_path)
    assert ct2.markup_factor() == pytest.approx(1.2271)
    # `markup --clear` goes back to list price only.
    assert ct2.main(["markup", "--clear"]) == 0
    ct3, _ = load_ct(tmp_path)
    assert ct3.markup_factor() == 1.0


def test_a_corrupt_or_absurd_markup_falls_back_to_identity_never_to_zero(tmp_path):
    """A bad factor must not silently zero or explode every reported figure."""
    ct, _ = load_ct(tmp_path)
    for bad in ({"factor": "abc"}, {"factor": 0}, {"factor": -2}, {"factor": None}, "nope"):
        ct.write_config({"contract": 1, "markup": bad})
        ct2, _ = load_ct(tmp_path)
        assert ct2.markup_factor() == 1.0
