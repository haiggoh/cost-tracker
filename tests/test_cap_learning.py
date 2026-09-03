"""The cap is LEARNED from the gateway's own refusal.

llmgw will not tell us the daily cap (/key/info is 403 for a virtual key scoped to
llm_api_routes) but it states it plainly the moment it refuses:

    API Error: Request rejected (429) · Budget has been exceeded!
    Key=Joyia-Code-M4m (sk-...YxHg) Current cost: 40.11333501999997, Max budget: 40.0

The refusal is persisted into the transcript, so the cap is readable after the fact.
The load-bearing test here is test_prose_quoting_the_message_is_not_a_source: the first
probe of this learner picked the message out of the session that was investigating it,
and would have learned the cap from a sentence rather than from the gateway.
"""
import json
import pathlib

import pytest

from conftest import load_ct

KILL = ("API Error: Request rejected (429) · Budget has been exceeded! "
        "Key=Joyia-Code-M4m (sk-...YxHg) Current cost: {cost}, Max budget: {cap}")
TEAM_KILL = ("API Error: Request rejected (429) · Budget has been exceeded! "
             "Team=9104302b-a3ac-463e-a22b-2152904b5b65 Current cost: 1401.2, "
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


def test_a_real_refusal_teaches_the_cap(tmp_path):
    _transcripts(tmp_path, [_rec(KILL.format(cost="40.11", cap="40.0"),
                                 "2026-09-02T02:17:19.496Z")])
    ct, _ = _load(tmp_path)
    out = ct.learn_cap()
    assert out["found"] is True
    assert out["key"]["cap_usd"] == 40.0
    assert out["key"]["cost_at_kill_usd"] == pytest.approx(40.11)
    assert out["key"]["scope"] == "key"
    assert out["key"]["label"] == "Joyia-Code-M4m"
    assert out["key"]["observed_at"] == "2026-09-02T02:17:19.496Z"


def test_prose_quoting_the_message_is_not_a_source(tmp_path):
    """A conversation ABOUT a budget kill is a poisoned source. The discriminator is
    `isApiErrorMessage`, which Claude Code sets on the turn it writes for a rejected
    request and which no amount of quoting reproduces."""
    _transcripts(tmp_path, [
        _rec("Earlier today I saw: " + KILL.format(cost="99.99", cap="999.0")
             + " — so the cap may have changed.", "2026-09-03T12:16:38.345Z",
             api_error=False),
        _rec("Let me check. The message reads 'Budget has been exceeded! "
             "Key=Joyia-Code-M4m (sk-...YxHg) Current cost: 12.0, Max budget: 12.0'",
             "2026-09-03T12:17:00.000Z", api_error=False),
    ])
    ct, _ = _load(tmp_path)
    out = ct.learn_cap()
    assert out["found"] is False
    assert out["key"] is None
    assert ct.cap_usd() is None          # and nothing bogus reaches the reports


def test_the_newest_refusal_wins(tmp_path):
    _transcripts(tmp_path, [
        _rec(KILL.format(cost="40.5", cap="40.0"), "2026-07-14T14:14:00.000Z"),
        _rec(KILL.format(cost="60.2", cap="60.0"), "2026-09-01T03:00:00.000Z"),
        _rec(KILL.format(cost="40.9", cap="40.0"), "2026-08-20T09:00:00.000Z"),
    ])
    ct, _ = _load(tmp_path)
    out = ct.learn_cap()
    assert out["key"]["cap_usd"] == 60.0            # not the last line, the newest record
    assert out["key"]["observed_at"] == "2026-09-01T03:00:00.000Z"


def test_the_newest_refusal_wins_across_files(tmp_path):
    _transcripts(tmp_path, [_rec(KILL.format(cost="40.5", cap="40.0"),
                                 "2026-07-14T14:14:00.000Z")], name="old.jsonl")
    _transcripts(tmp_path, [_rec(KILL.format(cost="75.1", cap="75.0"),
                                 "2026-09-02T01:00:00.000Z")], name="new.jsonl")
    ct, _ = _load(tmp_path)
    assert ct.learn_cap()["key"]["cap_usd"] == 75.0


def test_the_shared_team_cap_is_never_used_as_a_daily_cap(tmp_path):
    """Same message shape, 35x the number. Using it as a personal daily denominator
    would make every report look like 2% of budget."""
    _transcripts(tmp_path, [_rec(TEAM_KILL, "2026-09-02T02:00:00.000Z")])
    ct, _ = _load(tmp_path)
    out = ct.learn_cap()
    assert out["found"] is False                    # no KEY observation
    assert out["team"]["cap_usd"] == 1400.0         # but recorded, informationally
    # PERSIST it before asserting: cap_usd() resolves through the CONFIG, so a test that
    # never writes one exercises the "no config" path and would pass even if the team cap
    # were being promoted to the daily denominator.
    ct.write_config(out)
    assert ct.cap_usd() is None
    assert ct.collect("today")["cap_usd"] is None
    assert "informational only" in ct.render_cap(out, None)


def test_a_key_refusal_wins_over_a_team_one(tmp_path):
    _transcripts(tmp_path, [
        _rec(TEAM_KILL, "2026-09-03T02:00:00.000Z"),
        _rec(KILL.format(cost="40.1", cap="40.0"), "2026-09-01T02:00:00.000Z"),
    ])
    ct, _ = _load(tmp_path)
    ct.write_config(ct.learn_cap())
    assert ct.cap_usd() == 40.0


def test_an_env_override_beats_a_learned_cap(tmp_path):
    _transcripts(tmp_path, [_rec(KILL.format(cost="40.1", cap="40.0"),
                                 "2026-09-02T02:00:00.000Z")])
    ct, _ = _load(tmp_path, cap=25)
    ct.write_config(ct.learn_cap())
    assert ct.cap_usd() == 25.0
    data = ct.collect("today")
    assert data["cap_source"] == "env override"


def test_the_first_report_learns_once_and_then_stops_scanning(tmp_path):
    """A machine that has never hit the cap must not re-read every transcript on every
    invocation — so the not-found result is written too."""
    _transcripts(tmp_path, [_rec("nothing to see", "2026-09-02T02:00:00.000Z",
                                 api_error=False)])
    ct, ledger = _load(tmp_path)
    (ledger / "ffffffff-0000-0000-0000-000000000001").write_text("2026-09-03 1 0")
    cfg_path = pathlib.Path(ct.CONFIG_PATH)
    assert not cfg_path.exists()
    ct.collect("today")
    assert cfg_path.exists()
    first = json.loads(cfg_path.read_text())
    assert first["found"] is False
    cfg_path.write_text(json.dumps(dict(first, learned_at="SENTINEL")))
    ct.collect("today")
    assert json.loads(cfg_path.read_text())["learned_at"] == "SENTINEL"   # no rescan


def test_the_statusline_path_never_scans_transcripts(tmp_path):
    """--fast runs on every render. It may READ a learned cap but must never go looking
    for one."""
    _transcripts(tmp_path, [_rec(KILL.format(cost="40.1", cap="40.0"),
                                 "2026-09-02T02:00:00.000Z")])
    ct, ledger = _load(tmp_path)
    (ledger / "ffffffff-0000-0000-0000-000000000001").write_text("2026-09-03 7.5 0")
    data = ct.collect("today", fast=True)
    assert not pathlib.Path(ct.CONFIG_PATH).exists()
    assert data["cap_usd"] is None
    assert ct.render_statusline(data) == "today: cloud $7.50"
    # once learned, the fast path DOES use it — it just never learns it itself
    ct.write_config(ct.learn_cap())
    assert ct.render_statusline(ct.collect("today", fast=True)) == "today: cloud $7.50/$40"


def test_set_records_a_cap_by_hand_with_honest_provenance(tmp_path):
    _transcripts(tmp_path, [])
    ct, _ = _load(tmp_path)
    assert ct.main(["cap", "--set", "55"]) == 0
    cfg = json.loads(pathlib.Path(ct.CONFIG_PATH).read_text())
    assert cfg["key"]["cap_usd"] == 55.0
    assert cfg["key"]["source_file"] == "(manual)"
    assert ct.cap_usd() == 55.0


def test_a_measured_spend_above_the_cap_prompts_a_relearn(tmp_path):
    """If we are past the cap and the gateway has NOT refused, the cap we hold is
    probably stale. Say so rather than printing 150% and looking broken."""
    _transcripts(tmp_path, [_rec(KILL.format(cost="40.1", cap="40.0"),
                                 "2026-09-02T02:00:00.000Z")])
    ct, ledger = _load(tmp_path)
    (ledger / "ffffffff-0000-0000-0000-000000000001").write_text("2026-09-03 61 0")
    cfg = ct.learn_cap()
    ct.write_config(cfg)
    out = ct.render_cap(cfg, 40.0, measured_today=61.0)
    assert "EXCEEDS this cap" in out
    assert "--learn" in out


def test_a_malformed_config_is_ignored_not_fatal(tmp_path):
    _transcripts(tmp_path, [])
    ct, _ = _load(tmp_path)
    cfg = pathlib.Path(ct.CONFIG_PATH)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{ not json")
    assert ct.read_config() is None
    assert ct.main(["cap"]) == 0
