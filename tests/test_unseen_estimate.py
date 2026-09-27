"""Spend the ledger cannot see: SDK/headless sessions and the auto-mode classifier.

Measured 2026-09-26: the ledger held $20.36 while the gateway refused at $40.15. The gap was
14 SDK sessions with no statusline (no ledger entry) plus auto-mode classifier side requests,
which appear in neither the transcript nor total_cost_usd. These tests pin the estimator's
discriminants — each one is a way the previous code was wrong.
"""
import json
import os

from conftest import load_ct

DAY = "2026-09-03"


def _asst(mid, ts, model="claude-opus-5-5", tools=(), usage=None, block="text"):
    u = usage or {"input_tokens": 100, "output_tokens": 1000,
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    content = [{"type": "tool_use", "id": f"{mid}-{t}-{i}", "name": t, "input": {}}
               for i, t in enumerate(tools)] or [{"type": block, "text": "x"}]
    return json.dumps({"type": "assistant", "timestamp": ts, "sessionId": "s",
                       "message": {"id": mid, "model": model, "content": content,
                                   "usage": u}})


def _mode(mode):
    return json.dumps({"type": "permission-mode", "permissionMode": mode})


def _write(tmp_path, name, lines, sub=None):
    proj = tmp_path / "projects" / "-Users-someone"
    if sub:
        proj = proj / sub
    proj.mkdir(parents=True, exist_ok=True)
    (proj / name).write_text("\n".join(lines) + "\n")


def _bt(tmp_path, per_call="0.0265"):
    os.environ["COST_TRACKER_CLASSIFIER_USD_PER_CALL"] = per_call
    ct, ledger = load_ct(tmp_path, today=DAY)
    return ct, ct._budget_tally(), ledger


TS = f"{DAY}T10:00:00.000Z"
# 1000 output tokens of Opus 5.5 at $20/MTok + 100 input at $4/MTok
ONE_MSG = 1000 * 20e-6 + 100 * 4e-6


def test_one_response_split_across_records_is_priced_once(tmp_path):
    """Claude Code writes a record per content block, each repeating the full usage."""
    _write(tmp_path, "sdk-1.jsonl", [_asst("msg_bdrk_A", TS, block="thinking"),
                                     _asst("msg_bdrk_A", TS, block="text"),
                                     _asst("msg_bdrk_A", TS, tools=["Bash"])])
    _ct, bt, _ = _bt(tmp_path)
    e = bt.estimate_day(DAY, covered_sids=set())
    assert abs(e["uncovered_usd"] - ONE_MSG) < 1e-9, e


def test_a_covered_session_is_not_reconstructed_on_top_of_its_ledger(tmp_path):
    _write(tmp_path, "sess-covered.jsonl", [_asst("msg_bdrk_B", TS)])
    _ct, bt, _ = _bt(tmp_path)
    assert bt.estimate_day(DAY, covered_sids={"sess-covered"})["uncovered_usd"] == 0.0


def test_a_subagent_belongs_to_its_parent_session(tmp_path):
    """Covered parent -> subagent skipped (already in total_cost_usd); uncovered parent ->
    subagent counted (an SDK run's subagents are real spend nobody else records)."""
    _write(tmp_path, "agent-x.jsonl", [_asst("msg_bdrk_C", TS)], sub="parent-1/subagents")
    _ct, bt, _ = _bt(tmp_path)
    assert bt.estimate_day(DAY, covered_sids={"parent-1"})["uncovered_usd"] == 0.0
    e = bt.estimate_day(DAY, covered_sids=set())
    assert abs(e["uncovered_usd"] - ONE_MSG) < 1e-9
    assert e["uncovered_sessions"] == ["parent-1"]


def test_local_server_ids_are_free_even_under_a_spoofed_cloud_model(tmp_path):
    """Local sessions spoof `claude-opus-5`; only the message id tells the lanes apart."""
    _write(tmp_path, "local.jsonl", [_asst("chatcmpl-19b0", TS, model="claude-opus-5"),
                                     _asst("msg_0c931bc8494c4a45b27346f7", TS,
                                           model="claude-opus-5")])
    _ct, bt, _ = _bt(tmp_path)
    assert bt.estimate_day(DAY, covered_sids=set())["uncovered_usd"] == 0.0


def test_classifier_counts_only_auto_mode_non_read_only_calls(tmp_path):
    _write(tmp_path, "cloud.jsonl", [
        _mode("acceptEdits"),
        _asst("msg_bdrk_1", TS, tools=["Bash"]),            # not auto -> 0
        _mode("auto"),
        _asst("msg_bdrk_2", TS, tools=["Read", "Grep"]),    # read-only -> 0
        _asst("msg_bdrk_3", TS, tools=["Bash", "Edit"]),    # 2 calls
        _asst("msg_bdrk_3", TS, tools=["Bash", "Edit"]),    # same blocks repeated -> 0
        _asst("chatcmpl-9", TS, tools=["Bash"]),            # local lane -> 0
    ])
    _ct, bt, _ = _bt(tmp_path)
    e = bt.estimate_day(DAY, covered_sids={"cloud"})
    assert e["classifier_calls"] == 2, e
    assert abs(e["classifier_usd"] - 2 * 0.0265) < 1e-9


def test_classifier_estimate_can_be_switched_off(tmp_path):
    _write(tmp_path, "cloud.jsonl", [_mode("auto"), _asst("msg_bdrk_4", TS, tools=["Bash"])])
    _ct, bt, _ = _bt(tmp_path, per_call="0")
    assert bt.estimate_day(DAY, covered_sids={"cloud"})["classifier_usd"] == 0.0


def test_estimates_are_separate_keys_and_cloud_usd_keeps_its_meaning(tmp_path):
    _write(tmp_path, "cloud.jsonl", [_mode("auto"), _asst("msg_bdrk_5", TS, tools=["Bash"])])
    _write(tmp_path, "sdk-2.jsonl", [_asst("msg_bdrk_6", TS)])
    ct, _bt_mod, ledger = _bt(tmp_path)
    (ledger / "cloud").write_text(f"{DAY} 2.0 0")
    data = ct.collect("today")
    assert data["cloud_usd"] == 2.0
    assert abs(data["estimated_uncovered_usd"] - ONE_MSG) < 1e-6
    assert abs(data["estimated_classifier_usd"] - 0.0265) < 1e-9
    assert abs(data["billed_usd"] - (2.0 + ONE_MSG + 0.0265)) < 1e-4


def test_the_fast_path_never_scans_and_serves_the_cache(tmp_path):
    """The statusline must not pay the transcript scan; it reads the cache the background
    refresh wrote, and reports `pending` (zero) before one exists."""
    _write(tmp_path, "sdk-3.jsonl", [_asst("msg_bdrk_7", TS)])
    ct, _bt_mod, _ = _bt(tmp_path)
    fast = ct.collect("today", fast=True)
    assert fast["estimate_source"] == "pending" and fast["estimated_uncovered_usd"] == 0.0
    assert ct.main(["statusline", "--refresh-estimate"]) == 0
    fast = ct.collect("today", fast=True)
    assert abs(fast["estimated_uncovered_usd"] - ONE_MSG) < 1e-6


def test_stale_classifier_estimate_is_flagged_when_it_alone_overshoots_the_gateway(tmp_path):
    ct, _bt_mod, _ = _bt(tmp_path)
    def row(day, ledger, cls, gw):
        tot = ledger + cls
        return {"day": day, "verdict": "ok", "ledger_usd": ledger, "uncovered_usd": 0.0,
                "classifier_usd": cls, "gateway_usd": gw, "delta_usd": tot - gw}
    # classifier now free: ledger alone ~= gateway, the estimate is pure overshoot
    stale = [row("d1", 40.0, 8.0, 40.1), row("d2", 39.5, 7.0, 40.0)]
    assert ct.classifier_estimate_check(stale)["stale"] is True
    # one day over is noise, not a signal
    assert ct.classifier_estimate_check(stale[:1])["stale"] is False
    # overshoot caused by the LEDGER (an overcount elsewhere) is not blamed on the classifier
    ledger_over = [row("d1", 50.0, 8.0, 40.0), row("d2", 52.0, 8.0, 40.0)]
    assert ct.classifier_estimate_check(ledger_over)["stale"] is False
    # healthy: estimate closes the gap
    ok = [row("d1", 32.0, 8.0, 40.0), row("d2", 31.0, 9.5, 40.1)]
    assert ct.classifier_estimate_check(ok)["stale"] is False
