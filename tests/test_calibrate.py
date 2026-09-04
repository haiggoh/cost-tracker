"""Calibration against the gateway's own figure — the only EXTERNAL reference we have.

Every previous version of the daily-total bug agreed with itself. It over-counted (token
reconstruction pricing cache tokens ~4x high), then under-counted ($31.76 shown while the
gateway refused at $40.07), and plugin-ifying the reporting layer changed neither: the
chain was internally consistent throughout. So internal consistency proves nothing here,
and the deliverable is a harness that compares our computed daily total against a figure
we did not compute.

A refusal states `Current cost` — the cumulative FOR THE KEY in the current budget window,
which is exactly cost-tracker's `today` axis. Like-for-like, and free: it is already in the
transcripts, 119 of them across 37 UTC days on this machine.

The load-bearing test is test_a_post_midnight_repeat_belongs_to_the_previous_day. The
gateway's counter resets at 00:00 UTC but propagation lags ~5-10 minutes, so a refusal just
after midnight restates YESTERDAY's cumulative. Attributing that to the new day invents a
~$40 ground truth for a day that has barely spent anything — i.e. it would manufacture a
phantom undercount on every such day, which is the exact failure this harness exists to
detect. Measured instances: 2026-07-31T00:05 and 2026-08-21T00:00 both repeat the prior
day's figure to the cent.
"""
import json
import subprocess
import sys

import pytest

from conftest import load_ct

KILL = ("API Error: Request rejected (429) · Budget has been exceeded! "
        "Key=Joyia-Code-M4m (sk-...YxHg) Current cost: {cost}, Max budget: 40.0")
TEAM_KILL = ("API Error: Request rejected (429) · Budget has been exceeded! "
             "Team=9104302b-a3ac-463e-a22b-2152904b5b65 Current cost: {cost}, "
             "Max budget: 1400.0")


def _rec(text, ts, api_error=True):
    d = {"type": "assistant", "timestamp": ts,
         "message": {"model": "claude-opus-5", "content": [{"type": "text", "text": text}]}}
    if api_error:
        d.update({"isApiErrorMessage": True, "apiErrorIsTransient": False,
                  "error": "rate_limit"})
    return json.dumps(d)


def _transcripts(tmp_path, records, name="session-a.jsonl"):
    proj = tmp_path / "projects" / "-Users-someone"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / name).write_text("\n".join(records) + "\n")
    return proj


def _load(tmp_path, **kw):
    import os
    os.environ["COST_TRACKER_PROJECTS_DIR"] = str(tmp_path / "projects")
    os.environ["COST_TRACKER_CONFIG_DIR"] = str(tmp_path / "config")
    return load_ct(tmp_path, **kw)


def _history(tmp_path, rows):
    """rows: (ts, sid, day, cum, baseline) — the real history-log shape."""
    (tmp_path / "history.log").write_text(
        "".join(" ".join(str(c) for c in r) + "\n" for r in rows))


def _day(data, day):
    for row in data["days"]:
        if row["day"] == day:
            return row
    raise AssertionError(f"{day} absent from {[r['day'] for r in data['days']]}")


# --- the measurement itself ---------------------------------------------------

def test_a_total_matching_the_gateway_reports_no_undercount(tmp_path):
    _transcripts(tmp_path, [_rec(KILL.format(cost="40.02"), "2026-09-02T02:17:19.496Z")])
    _history(tmp_path, [("2026-09-02T02:00:00Z", "sess-a", "2026-09-02", 40.02, 0.0)])
    ct, _ = _load(tmp_path, today="2026-09-03")
    data = ct.calibrate()
    row = _day(data, "2026-09-02")
    assert row["gateway_usd"] == pytest.approx(40.02)
    assert row["ours_usd"] == pytest.approx(40.02)
    assert row["delta_usd"] == pytest.approx(0.0)
    assert row["verdict"] == "ok"
    assert data["undercount_days"] == 0


def test_a_total_below_the_gateway_is_reported_as_an_undercount_with_its_size(tmp_path):
    """The live defect: statusline said $31.76 while the gateway refused at $40.07."""
    _transcripts(tmp_path, [_rec(KILL.format(cost="40.07488347500004"),
                                 "2026-09-03T20:55:31.772Z")])
    _history(tmp_path, [("2026-09-03T20:00:00Z", "sess-a", "2026-09-03", 31.76, 0.0)])
    ct, _ = _load(tmp_path, today="2026-09-03")
    data = ct.calibrate()
    row = _day(data, "2026-09-03")
    assert row["verdict"] == "undercount"
    assert row["delta_usd"] == pytest.approx(-8.315, abs=0.001)
    assert data["undercount_days"] == 1
    assert data["worst_day"] == "2026-09-03"


def test_spending_more_than_the_gateway_stated_is_not_an_undercount(tmp_path):
    """The refusal is a lower bound: it names the cumulative at that instant, and the
    day's real total can exceed it (overshoot to $43.17 is on record). Only the
    ours-BELOW-gateway direction is a defect."""
    _transcripts(tmp_path, [_rec(KILL.format(cost="40.00"), "2026-08-05T10:04:16.042Z")])
    _history(tmp_path, [("2026-08-05T23:00:00Z", "sess-a", "2026-08-05", 43.17, 0.0)])
    ct, _ = _load(tmp_path, today="2026-08-06")
    data = ct.calibrate()
    assert _day(data, "2026-08-05")["verdict"] == "ok"
    assert data["undercount_days"] == 0


def test_repeated_refusals_in_one_day_are_one_calibration_point(tmp_path):
    """Once blocked, every further request re-reports the same cached cumulative — 14 of
    them on 2026-08-24. Summing those would claim a $560 day."""
    _transcripts(tmp_path, [
        _rec(KILL.format(cost="40.02049"), "2026-08-24T02:15:32.914Z"),
        _rec(KILL.format(cost="40.02049"), "2026-08-24T08:23:20.575Z"),
        _rec(KILL.format(cost="40.02049"), "2026-08-24T23:59:29.575Z"),
    ])
    _history(tmp_path, [("2026-08-24T23:00:00Z", "sess-a", "2026-08-24", 40.03, 0.0)])
    ct, _ = _load(tmp_path, today="2026-08-25")
    row = _day(ct.calibrate(), "2026-08-24")
    assert row["gateway_usd"] == pytest.approx(40.02049)
    assert row["n_refusals"] == 3
    assert row["verdict"] == "ok"


def test_the_highest_refusal_of_the_day_is_the_ground_truth(tmp_path):
    """Spend only climbs within a budget window, so the last/highest observation is the
    tightest lower bound. Taking the first would understate the reference and hide a
    real undercount."""
    _transcripts(tmp_path, [
        _rec(KILL.format(cost="40.01"), "2026-08-22T10:06:31.077Z"),
        _rec(KILL.format(cost="42.19537"), "2026-08-22T23:51:48.755Z"),
    ])
    _history(tmp_path, [("2026-08-22T23:00:00Z", "sess-a", "2026-08-22", 40.50, 0.0)])
    ct, _ = _load(tmp_path, today="2026-08-23")
    row = _day(ct.calibrate(), "2026-08-22")
    assert row["gateway_usd"] == pytest.approx(42.19537)
    assert row["verdict"] == "undercount"


# --- what must NOT become a calibration point ---------------------------------

def test_a_post_midnight_repeat_belongs_to_the_previous_day(tmp_path):
    """00:00 UTC resets the window but propagation lags minutes, so this refusal quotes
    yesterday. Counting it as today's ground truth invents a $40 reference for a day
    with $0.30 of real spend — a phantom undercount, on every such day."""
    _transcripts(tmp_path, [
        _rec(KILL.format(cost="40.51571"), "2026-07-30T08:18:51.146Z"),
        _rec(KILL.format(cost="40.51571"), "2026-07-31T00:05:08.305Z"),
    ])
    _history(tmp_path, [
        ("2026-07-30T08:00:00Z", "sess-a", "2026-07-30", 40.52, 0.0),
        ("2026-07-31T00:30:00Z", "sess-b", "2026-07-31", 0.30, 0.0),
    ])
    ct, _ = _load(tmp_path, today="2026-08-01")
    data = ct.calibrate()
    assert _day(data, "2026-07-30")["verdict"] == "ok"
    with pytest.raises(AssertionError):
        _day(data, "2026-07-31")          # no ground truth of its own
    assert data["undercount_days"] == 0
    assert _day(data, "2026-07-30")["stale_repeats"] == 1


def test_a_post_midnight_refusal_with_a_fresh_figure_is_kept(tmp_path):
    """The stale rule must key on the VALUE repeating, not merely on the clock: a genuine
    same-night kill after the reset states a different cumulative and is real data."""
    _transcripts(tmp_path, [
        _rec(KILL.format(cost="40.05725"), "2026-08-20T23:50:23.995Z"),
        _rec(KILL.format(cost="40.00983"), "2026-08-21T00:06:48.040Z"),
    ])
    _history(tmp_path, [
        ("2026-08-20T23:00:00Z", "sess-a", "2026-08-20", 40.06, 0.0),
        ("2026-08-21T01:00:00Z", "sess-b", "2026-08-21", 40.01, 0.0),
    ])
    ct, _ = _load(tmp_path, today="2026-08-22")
    data = ct.calibrate()
    assert _day(data, "2026-08-21")["gateway_usd"] == pytest.approx(40.00983)
    assert _day(data, "2026-08-21")["verdict"] == "ok"


def test_the_team_cap_is_never_ground_truth_for_a_personal_day(tmp_path):
    """Same message shape, 35x the number. Using it would call every day a $1400
    undercount."""
    _transcripts(tmp_path, [_rec(TEAM_KILL.format(cost="1401.2"),
                                 "2026-09-02T02:17:19.496Z")])
    _history(tmp_path, [("2026-09-02T02:00:00Z", "sess-a", "2026-09-02", 12.0, 0.0)])
    ct, _ = _load(tmp_path, today="2026-09-03")
    data = ct.calibrate()
    assert data["days"] == []
    assert data["undercount_days"] == 0


def test_prose_quoting_a_refusal_is_not_a_calibration_point(tmp_path):
    """The same poisoned-source trap the cap learner already guards: a session
    INVESTIGATING a kill quotes the message verbatim. Only `isApiErrorMessage` marks the
    turn the harness wrote for a genuinely rejected request."""
    _transcripts(tmp_path, [
        _rec("Earlier I saw " + KILL.format(cost="40.07") + " which is why I am checking.",
             "2026-09-03T12:16:38.345Z", api_error=False),
    ])
    _history(tmp_path, [("2026-09-03T12:00:00Z", "sess-a", "2026-09-03", 5.00, 0.0)])
    ct, _ = _load(tmp_path, today="2026-09-03")
    assert ct.calibrate()["days"] == []


def test_a_day_with_no_refusal_gets_no_verdict(tmp_path):
    """No external reference means no opinion. A day under the cap is silent, not 'ok' —
    claiming otherwise would dress up the absence of evidence as a pass."""
    _transcripts(tmp_path, [_rec(KILL.format(cost="40.02"), "2026-09-02T02:17:19.496Z")])
    _history(tmp_path, [
        ("2026-09-02T02:00:00Z", "sess-a", "2026-09-02", 40.02, 0.0),
        ("2026-09-01T02:00:00Z", "sess-b", "2026-09-01", 11.50, 0.0),
    ])
    ct, _ = _load(tmp_path, today="2026-09-03")
    days = [r["day"] for r in ct.calibrate()["days"]]
    assert days == ["2026-09-02"]


# --- our side of the comparison ----------------------------------------------

def test_our_total_for_a_past_day_sums_every_session_on_that_day(tmp_path):
    """The comparison is only like-for-like if our figure is the whole day across
    sessions — the `today` axis — computed for a day that is not TODAY."""
    _transcripts(tmp_path, [_rec(KILL.format(cost="40.05"), "2026-08-12T10:06:20.707Z")])
    _history(tmp_path, [
        ("2026-08-12T09:00:00Z", "sess-a", "2026-08-12", 18.00, 0.0),
        ("2026-08-12T10:00:00Z", "sess-b", "2026-08-12", 22.05, 0.0),
        ("2026-08-13T10:00:00Z", "sess-c", "2026-08-13", 99.00, 0.0),   # another day
    ])
    ct, _ = _load(tmp_path, today="2026-09-03")
    row = _day(ct.calibrate(), "2026-08-12")
    assert row["ours_usd"] == pytest.approx(40.05)
    assert row["sessions_counted"] == 2


def test_a_multi_day_session_contributes_only_that_days_delta(tmp_path):
    """D15's rule, restated as calibration: a session's lifetime cumulative is not the
    day's spend. Charging the lifetime here would fake agreement with a $40 gateway
    figure while the daily attribution stayed broken."""
    _transcripts(tmp_path, [_rec(KILL.format(cost="40.00"), "2026-08-19T07:28:23.806Z")])
    _history(tmp_path, [
        ("2026-08-18T20:00:00Z", "sess-a", "2026-08-18", 30.00, 0.0),
        ("2026-08-19T07:00:00Z", "sess-a", "2026-08-19", 70.00, 30.00),
    ])
    ct, _ = _load(tmp_path, today="2026-09-03")
    row = _day(ct.calibrate(), "2026-08-19")
    assert row["ours_usd"] == pytest.approx(40.00)       # not 70.00
    assert row["verdict"] == "ok"


def test_a_local_session_never_counts_toward_a_cloud_day(tmp_path):
    """Local inference is not gateway spend, so it must not paper over a cloud
    undercount."""
    _transcripts(tmp_path, [_rec(KILL.format(cost="40.00"), "2026-09-02T02:17:19.496Z")])
    _history(tmp_path, [
        ("2026-09-02T01:00:00Z", "sess-a", "2026-09-02", 31.00, 0.0),
        ("2026-09-02T02:00:00Z", "sess-b", "2026-09-02", 0.0, 0.0, 9.00),   # local
    ])
    ct, _ = _load(tmp_path, today="2026-09-03")
    row = _day(ct.calibrate(), "2026-09-02")
    assert row["ours_usd"] == pytest.approx(31.00)
    assert row["verdict"] == "undercount"


# --- the ratio, because a kill day's true total is very nearly the cap ----------
# Once the gateway refuses, the key is blocked for the REST of the budget window, and the
# window is the UTC day. So on any day with a refusal the gateway's stated cumulative is
# not just a floor — it is approximately the day's FINAL total, and our shortfall is the
# whole error rather than a bound on it. That is what makes the ratio worth reporting: a
# ledger that reads $32 on a day that provably reached $40 is wrong by a fifth, and the
# ratio holding steady across days with very different session mixes is the evidence that
# the missing component is systematic rather than a few absent sessions.

def test_each_day_reports_our_share_of_the_gateway_figure(tmp_path):
    _transcripts(tmp_path, [_rec(KILL.format(cost="40.00"), "2026-09-02T02:17:19.496Z")])
    _history(tmp_path, [("2026-09-02T02:00:00Z", "sess-a", "2026-09-02", 32.00, 0.0)])
    ct, _ = _load(tmp_path, today="2026-09-03")
    row = _day(ct.calibrate(), "2026-09-02")
    assert row["ours_pct_of_gateway"] == pytest.approx(80.0, abs=0.1)


def test_the_summary_reports_the_ratio_across_calibrated_days(tmp_path):
    """A per-day delta invites 'a session was missed'. The same ratio on every day is a
    different claim, and it is the one the data supports."""
    _transcripts(tmp_path, [
        _rec(KILL.format(cost="40.00"), "2026-09-01T02:00:00.000Z"),
        _rec(KILL.format(cost="40.00"), "2026-09-02T02:00:00.000Z"),
    ])
    _history(tmp_path, [
        ("2026-09-01T01:00:00Z", "sess-a", "2026-09-01", 32.00, 0.0),
        ("2026-09-02T01:00:00Z", "sess-b", "2026-09-02", 30.00, 0.0),
    ])
    ct, _ = _load(tmp_path, today="2026-09-03")
    data = ct.calibrate()
    assert data["median_pct_of_gateway"] == pytest.approx(77.5, abs=0.1)
    assert "77.5%" in ct.render_calibrate(data)


def test_a_no_data_day_is_left_out_of_the_ratio(tmp_path):
    """Including a day with no basis would drag the ratio toward 0% and make a systematic
    20% shortfall look like a catastrophic one."""
    _transcripts(tmp_path, [
        _rec(KILL.format(cost="40.00"), "2026-07-27T02:00:00.000Z"),
        _rec(KILL.format(cost="40.00"), "2026-09-02T02:00:00.000Z"),
    ])
    _history(tmp_path, [("2026-09-02T01:00:00Z", "sess-b", "2026-09-02", 32.00, 0.0)])
    ct, _ = _load(tmp_path, today="2026-09-03")
    assert ct.calibrate()["median_pct_of_gateway"] == pytest.approx(80.0, abs=0.1)


# --- days we have no basis for at all ------------------------------------------
# Measured on the real machine: the history log begins 2026-08-18, but refusals go back to
# 2026-07-26. Those earlier days therefore report ours=$0.00 against a $40 gateway figure
# and would be printed as 11 maximal UNDERCOUNTs — inflating the headline from $290 to
# $701 and pointing the investigation at an attribution bug that cannot be there, because
# there is nothing to attribute. Absence of the source is not evidence of a defect.

def test_a_day_before_the_ledger_existed_is_no_data_not_an_undercount(tmp_path):
    _transcripts(tmp_path, [
        _rec(KILL.format(cost="41.72"), "2026-07-27T07:56:39.157Z"),
        _rec(KILL.format(cost="40.02"), "2026-09-02T02:17:19.496Z"),
    ])
    _history(tmp_path, [("2026-09-02T02:00:00Z", "sess-a", "2026-09-02", 40.02, 0.0)])
    ct, _ = _load(tmp_path, today="2026-09-03")
    data = ct.calibrate()
    assert _day(data, "2026-07-27")["verdict"] == "no-data"
    assert data["no_data_days"] == 1
    assert data["undercount_days"] == 0
    assert data["unaccounted_usd"] == pytest.approx(0.0)   # never guessed at


def test_a_stale_zero_cost_record_does_not_make_a_day_calibrated(tmp_path):
    """Pre-coverage ledger FILES survive on disk as 2-field `<day> 0` records, so the day
    looks like it has sessions while carrying no spend. Counting those as a real $0 total
    is how absence of data disguises itself as a measurement."""
    ledger = tmp_path / "cost-ledger"
    ledger.mkdir(parents=True, exist_ok=True)
    (ledger / "05d580fd").write_text("2026-07-27 0\n")
    _transcripts(tmp_path, [_rec(KILL.format(cost="41.72"), "2026-07-27T07:56:39.157Z")])
    _history(tmp_path, [("2026-09-02T02:00:00Z", "sess-a", "2026-09-02", 40.02, 0.0)])
    ct, _ = _load(tmp_path, today="2026-09-03")
    row = _day(ct.calibrate(), "2026-07-27")
    assert row["verdict"] == "no-data"


def test_a_day_inside_coverage_with_no_records_is_a_real_undercount(tmp_path):
    """The rule must key on the ledger's COVERAGE WINDOW, not on 'we found nothing'.
    Inside coverage, a day whose sessions never wrote a record is exactly the structural
    blindness worth reporting — the leading suspect for the live gap."""
    _transcripts(tmp_path, [
        _rec(KILL.format(cost="40.02"), "2026-09-01T02:17:19.496Z"),
        _rec(KILL.format(cost="40.02"), "2026-09-03T02:17:19.496Z"),
    ])
    _history(tmp_path, [
        ("2026-08-31T02:00:00Z", "sess-a", "2026-08-31", 30.00, 0.0),
        ("2026-09-03T02:00:00Z", "sess-c", "2026-09-03", 40.02, 0.0),
    ])
    ct, _ = _load(tmp_path, today="2026-09-03")
    data = ct.calibrate()
    assert _day(data, "2026-09-01")["verdict"] == "undercount"   # inside coverage
    assert data["no_data_days"] == 0


def test_no_history_log_at_all_calibrates_nothing(tmp_path):
    """A machine with no history log has no basis for ANY day. Honest INCONCLUSIVE."""
    _transcripts(tmp_path, [_rec(KILL.format(cost="40.02"), "2026-09-02T02:17:19.496Z")])
    ct, _ = _load(tmp_path, today="2026-09-03")
    data = ct.calibrate()
    assert _day(data, "2026-09-02")["verdict"] == "no-data"
    assert data["undercount_days"] == 0


# --- the CLI ------------------------------------------------------------------

def _run(tmp_path, *args):
    import os
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    env.update({
        "COST_TRACKER_PROJECTS_DIR": str(tmp_path / "projects"),
        "COST_TRACKER_CONFIG_DIR": str(tmp_path / "config"),
        "COST_TRACKER_LEDGER_DIR": str(tmp_path / "cost-ledger"),
        "COST_TRACKER_HISTORY": str(tmp_path / "history.log"),
        "COST_TRACKER_SAVINGS_CMD": "/bin/false",
        "COST_TRACKER_TODAY": "2026-09-03",
    })
    env.pop("COST_TRACKER_CAP_USD", None)
    env.pop("BUDGET_TALLY_CAP_USD", None)
    (tmp_path / "cost-ledger").mkdir(parents=True, exist_ok=True)
    return subprocess.run([sys.executable, str(root / "bin" / "cost-tracker")] + list(args),
                          capture_output=True, text=True, env=env)


def test_cli_exits_nonzero_when_an_undercount_is_measured(tmp_path):
    """So this can gate a release instead of being a report nobody reads."""
    _transcripts(tmp_path, [_rec(KILL.format(cost="40.07"), "2026-09-03T20:55:31.772Z")])
    _history(tmp_path, [("2026-09-03T20:00:00Z", "sess-a", "2026-09-03", 31.76, 0.0)])
    r = _run(tmp_path, "calibrate")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "undercount" in r.stdout.lower()
    assert "8.31" in r.stdout


def test_cli_exits_zero_when_every_day_agrees(tmp_path):
    _transcripts(tmp_path, [_rec(KILL.format(cost="40.02"), "2026-09-02T02:17:19.496Z")])
    _history(tmp_path, [("2026-09-02T02:00:00Z", "sess-a", "2026-09-02", 40.02, 0.0)])
    r = _run(tmp_path, "calibrate")
    assert r.returncode == 0, r.stdout + r.stderr


def test_cli_json_is_machine_readable(tmp_path):
    _transcripts(tmp_path, [_rec(KILL.format(cost="40.07"), "2026-09-03T20:55:31.772Z")])
    _history(tmp_path, [("2026-09-03T20:00:00Z", "sess-a", "2026-09-03", 31.76, 0.0)])
    r = _run(tmp_path, "calibrate", "--json")
    data = json.loads(r.stdout)
    assert data["contract"] == 1
    assert data["days"][0]["day"] == "2026-09-03"
    assert data["days"][0]["verdict"] == "undercount"


def test_cli_says_so_when_there_is_nothing_to_calibrate_against(tmp_path):
    """A machine that has never hit the cap has no reference. That is an honest
    INCONCLUSIVE, not a pass — and it must not exit 1 either."""
    _transcripts(tmp_path, [_rec("all good", "2026-09-02T02:17:19.496Z", api_error=False)])
    _history(tmp_path, [("2026-09-02T02:00:00Z", "sess-a", "2026-09-02", 12.0, 0.0)])
    r = _run(tmp_path, "calibrate")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no refusal" in r.stdout.lower()
