"""Spend made by processes OUTSIDE the interactive session, attributed to its source.

Measured 2026-10-07: the ledger held $34.63, the gateway refused at $40.02, and the OTel sink
matched the ledger to the cent — so the gap was other processes on the same key. Two plugins:
security-guidance (Agent-SDK commit/push reviews with a transcript, plus Stop-hook reviews by
direct HTTP with none) and remember (`claude -p` summaries that price themselves in their own
log). Each must show up under its own label, so a reader can tell which is which.
"""
import json
import os
import time

import pytest

from conftest import load_ct

DAY = "2026-09-03"
TS = f"{DAY}T10:00:00.000Z"
REVIEW_PROMPT = ("Review this change for security vulnerabilities.\n\n"
                 "Changed files (you may Read these and any other file in the repo):\n  - a.sh\n")
# 1000 output tokens of Opus 4.7 at $25/MTok + 100 input at $5/MTok
ONE_REVIEW_MSG = 1000 * 25e-6 + 100 * 5e-6


def _user(text, entrypoint="sdk-py"):
    return json.dumps({"type": "user", "timestamp": TS, "entrypoint": entrypoint,
                       "message": {"role": "user", "content": text}})


def _asst(mid, model="claude-opus-4-7"):
    return json.dumps({"type": "assistant", "timestamp": TS,
                       "message": {"id": mid, "model": model,
                                   "content": [{"type": "text", "text": "x"}],
                                   "usage": {"input_tokens": 100, "output_tokens": 1000,
                                             "cache_read_input_tokens": 0,
                                             "cache_creation_input_tokens": 0}}})


def _session(tmp_path, name, lines):
    proj = tmp_path / "projects" / "-Users-someone-repo"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / name).write_text("\n".join(lines) + "\n")


@pytest.fixture(autouse=True)
def _berlin_tz():
    """Log stamps are local time; pin the zone so the UTC split is tested, not this machine's tz.
    2026-09 is CEST (UTC+2). tzset() is what makes the env change reach datetime."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Berlin"
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


def _bt(tmp_path, remember_dirs=None, sg_log=None):
    os.environ["COST_TRACKER_CLASSIFIER_USD_PER_CALL"] = "0"
    ct, ledger = load_ct(tmp_path, today=DAY)   # pins both plugin-log paths to tmp first
    if remember_dirs:
        os.environ["COST_TRACKER_REMEMBER_DIRS"] = ":".join(remember_dirs)
    if sg_log:
        os.environ["COST_TRACKER_SG_LOG"] = sg_log
    return ct, ct._budget_tally(), ledger


def test_security_review_sdk_session_is_attributed(tmp_path):
    _session(tmp_path, "rev.jsonl", [_user(REVIEW_PROMPT), _asst("msg_bdrk_R1")])
    _ct, bt, _ = _bt(tmp_path)
    src = bt.estimate_day(DAY, covered_sids=set())["by_source"]
    sg = src["security-guidance-sdk"]
    assert sg["label"] == "security-guidance (sdk reviews)"
    assert abs(sg["usd"] - ONE_REVIEW_MSG) < 1e-9, sg
    assert sg["sessions"] == ["rev"]


def test_other_sdk_session_stays_anonymous(tmp_path):
    """Discriminant: the entrypoint alone must not label every SDK run as a security review."""
    _session(tmp_path, "other.jsonl", [_user("Summarise this file."), _asst("msg_bdrk_O1")])
    _ct, bt, _ = _bt(tmp_path)
    e = bt.estimate_day(DAY, covered_sids=set())
    assert "security-guidance-sdk" not in e["by_source"], e["by_source"]
    assert abs(e["uncovered_usd"] - ONE_REVIEW_MSG) < 1e-9   # still counted, just unlabelled


def test_uncovered_usd_keeps_its_meaning(tmp_path):
    """The attributed review is a SUBSET of uncovered_usd, not added on top of it."""
    _session(tmp_path, "rev.jsonl", [_user(REVIEW_PROMPT), _asst("msg_bdrk_R2")])
    _ct, bt, _ = _bt(tmp_path)
    e = bt.estimate_day(DAY, covered_sids=set())
    assert abs(e["uncovered_usd"] - ONE_REVIEW_MSG) < 1e-9


def test_remember_log_is_summed_per_utc_day(tmp_path):
    """Its stamps are LOCAL time under the file's date. 01:30 CEST on the 4th is 23:30 UTC on the 3rd."""
    logs = tmp_path / "rem" / "logs"
    logs.mkdir(parents=True)
    (logs / "memory-2026-09-03.log").write_text(
        "01:30:00 [tokens] tokens: 10+0cache->5out ($0.0100)\n"      # 09-02 23:30 UTC -> NOT today
        "12:00:00 [tokens] tokens: 10+0cache->5out ($0.0200)\n"      # today
        "12:00:01 [ndc] tokens: 3018+0cache->664out ($0.0300)\n"     # today, other component
        "12:00:02 [haiku] call exited 1 after spending tokens: 1+0cache->1out ($0.0400)\n"  # failed call still billed
        "12:00:03 [hook] save-session: nothing priced here\n")
    (logs / "memory-2026-09-04.log").write_text(
        "01:30:00 [tokens] tokens: 10+0cache->5out ($0.5000)\n")     # 09-03 23:30 UTC -> today
    _ct, bt, _ = _bt(tmp_path, remember_dirs=[str(tmp_path / "rem")])
    r = bt.estimate_day(DAY, covered_sids=set())["by_source"]["remember"]
    assert r["label"] == "remember (logged)"
    assert abs(r["usd"] - (0.02 + 0.03 + 0.04 + 0.5)) < 1e-9, r
    assert r["calls"] == 4


def test_security_stop_reviews_are_counted_and_estimated(tmp_path):
    log = tmp_path / "sg-log.txt"
    log.write_text(
        "[2026-09-03 12:00:00.000] Stop hook: reviewing 6 changed file(s)\n"
        "[2026-09-03 12:01:00.000] Stop hook: empty review set\n"
        "[2026-09-03 12:02:00.000] Stop hook: reviewing 1 changed file(s)\n"
        "[2026-09-03 01:00:00.000] Stop hook: reviewing 1 changed file(s)\n")   # 09-02 UTC
    _ct, bt, _ = _bt(tmp_path, sg_log=str(log))
    src = bt.estimate_day(DAY, covered_sids=set())["by_source"]
    stop = src["security-guidance-stop"]
    assert stop["label"] == "security-guidance (stop-hook reviews, est.)"
    assert stop["calls"] == 2
    assert abs(stop["usd"] - 2 * 0.31) < 1e-9, stop        # calibrated default per call
    assert "COST_TRACKER_SG_STOP_USD" in stop["basis"]      # the estimate says how to tune it


def test_stop_review_rate_is_tunable(tmp_path):
    log = tmp_path / "sg-log.txt"
    log.write_text("[2026-09-03 12:00:00.000] Stop hook: reviewing 6 changed file(s)\n")
    os.environ["COST_TRACKER_SG_STOP_USD"] = "0.3"
    try:
        _ct, bt, _ = _bt(tmp_path, sg_log=str(log))
        stop = bt.estimate_day(DAY, covered_sids=set())["by_source"]["security-guidance-stop"]
    finally:
        os.environ.pop("COST_TRACKER_SG_STOP_USD", None)
    assert abs(stop["usd"] - 0.3) < 1e-9


def test_review_session_with_no_spend_today_is_not_listed(tmp_path):
    """A transcript touched today whose records are all from yesterday is not today's review."""
    old = json.loads(_asst("msg_bdrk_Y1"))
    old["timestamp"] = "2026-09-02T10:00:00.000Z"
    _session(tmp_path, "yday.jsonl", [_user(REVIEW_PROMPT), json.dumps(old)])
    _ct, bt, _ = _bt(tmp_path)
    assert "security-guidance-sdk" not in bt.estimate_day(DAY, covered_sids=set())["by_source"]


def test_outside_process_spend_reaches_billed_usd(tmp_path):
    logs = tmp_path / "rem" / "logs"
    logs.mkdir(parents=True)
    (logs / "memory-2026-09-03.log").write_text("12:00:00 [tokens] x ($0.2500)\n")
    ct, _bt_mod, ledger = _bt(tmp_path, remember_dirs=[str(tmp_path / "rem")])
    (ledger / "cloud").write_text(f"{DAY} 2.0 0")
    data = ct.collect("today")
    assert abs(data["estimated_outside_usd"] - 0.25) < 1e-9, data
    assert data["estimated_by_source"]["remember"]["usd"] == 0.25
    assert abs(data["billed_usd"] - 2.25) < 1e-4


def test_tally_line_names_each_source(tmp_path):
    logs = tmp_path / "rem" / "logs"
    logs.mkdir(parents=True)
    (logs / "memory-2026-09-03.log").write_text("12:00:00 [tokens] x ($0.2500)\n")
    _session(tmp_path, "rev.jsonl", [_user(REVIEW_PROMPT), _asst("msg_bdrk_R4")])
    _ct, bt, _ = _bt(tmp_path, remember_dirs=[str(tmp_path / "rem")])
    line = bt.side_spend_note(bt.estimate_day(DAY, covered_sids=set())["by_source"])
    assert "security-guidance (sdk reviews) $0.03" in line, line
    assert "remember (logged) $0.25" in line, line
    assert bt.side_spend_note({}) == ""


def test_estimate_loads_without_load_module(tmp_path, monkeypatch):
    """Python 3.15 removed SourceFileLoader.load_module(). 0.11.0 still called it, the estimate's
    catch-all turned the AttributeError into source='unavailable', and every side-spend figure
    read $0 on the live CLI while this suite (run on 3.14, where it only warns) stayed green.
    Remove the method so the test fails on any interpreter, then assert the estimate RAN."""
    from importlib.machinery import SourceFileLoader

    def _removed(self, *a, **k):
        raise AttributeError("'SourceFileLoader' object has no attribute 'load_module'")
    monkeypatch.setattr(SourceFileLoader, "load_module", _removed)   # inherited, so delattr fails
    logs = tmp_path / "rem" / "logs"
    logs.mkdir(parents=True)
    (logs / "memory-2026-09-03.log").write_text("12:00:00 [tokens] x ($0.2500)\n")
    os.environ["COST_TRACKER_CLASSIFIER_USD_PER_CALL"] = "0"
    ct, ledger = load_ct(tmp_path, today=DAY)
    os.environ["COST_TRACKER_REMEMBER_DIRS"] = str(tmp_path / "rem")
    (ledger / "cloud").write_text(f"{DAY} 2.0 0")
    data = ct.collect("today")
    assert data["estimate_source"].startswith("estimated"), data["estimate_source"]
    assert abs(data["estimated_outside_usd"] - 0.25) < 1e-9, data
