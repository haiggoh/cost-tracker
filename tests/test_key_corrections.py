"""tests/test_key_corrections.py — the key-corrections overlay and `keys --reconstruct` (0.24.0).

Raw history is never edited. `cost-tracker keys --reconstruct` is a dry run by default;
`--write` stores per-day entries in key-corrections.json, which read_history() applies on
top of the raw rows and books as inferred. All paths are scratch.
"""
import json
import os
import pathlib

import pytest

from conftest import load_ct

D = "2026-09-03"
SID = "11111111-1111-1111-1111-111111111111"
KILL = ("API Error: Request rejected (429) · Budget has been exceeded! "
        "Key=Fake-A (sk-...aaaa) Current cost: {cost}, Max budget: {cap}")
HIST = [
    f"{D}T10:00:00Z {SID} {D} 10 0 10 p=1",
    f"{D}T10:30:00Z {SID} {D} 30 0 30 p=1",          # untagged (pre-tag era) $30
    f"{D}T11:10:00Z {SID} {D} 30 0 30 k=bbbb p=2",   # resumed under bbbb (restore)
    f"{D}T11:30:00Z {SID} {D} 35 0 35 k=bbbb p=2",   # +$5 on bbbb
]


def _refusal(ts, cost="40.0", cap="40.0"):
    return json.dumps({"type": "assistant", "timestamp": ts, "isApiErrorMessage": True,
                       "message": {"content": [{"type": "text",
                                                "text": KILL.format(cost=cost, cap=cap)}]}})


@pytest.fixture()
def ct(tmp_path):
    os.environ.pop("COST_TRACKER_KEY_CORRECTIONS", None)
    os.environ["COST_TRACKER_JOYIA_HOME"] = str(tmp_path / "no-joyia")
    mod, _ = load_ct(tmp_path)
    pathlib.Path(mod.HISTORY_PATH).write_text("".join(r + "\n" for r in HIST))
    proj = pathlib.Path(mod.TRANSCRIPTS_DIR) / "-Users-someone"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / f"{SID}.jsonl").write_text(_refusal(f"{D}T11:00:00.000Z") + "\n")
    return mod


def _overlay(ct):
    return pathlib.Path(ct.KEY_CORRECTIONS_PATH)


def test_dry_run_writes_nothing(ct, capsys):
    assert ct.main(["keys", "--reconstruct", "--since", D]) == 0
    out = capsys.readouterr().out
    assert not _overlay(ct).exists()
    assert D in out and "aaaa" in out and "→" in out
    assert "dry run" in out.lower()


def test_write_then_collect_uses_overlay(ct):
    before = ct.collect("today")
    # raw: the resume restores cum 30 exactly, so it continues the SAME stream and the
    # untagged-prefix backfill hands the pre-resume $30 to the resume's key — the
    # 2026-11-26 complaint. The refusal proves those $30 were aaaa's.
    assert before["by_key"] == pytest.approx({"bbbb": 35.0})
    assert before["by_key_inferred"] == pytest.approx({"bbbb": 30.0})
    assert ct.main(["keys", "--reconstruct", "--since", D, "--write"]) == 0
    after = ct.collect("today")
    assert after["by_key"] == pytest.approx({"aaaa": 30.0, "bbbb": 5.0})
    assert after["by_key_inferred"] == pytest.approx({"aaaa": 30.0})
    assert not after["unattributed_usd"]
    assert after["by_key_inferred"].get("bbbb", 0) == 0
    assert after["cloud_usd"] == pytest.approx(before["cloud_usd"])   # attribution only
    doc = json.loads(_overlay(ct).read_text())
    assert doc["contract"] == 1 and D in doc["days"]
    assert {e["basis"] for e in doc["days"][D]["entries"]} >= {"refusal"}


def test_fast_path_applies_the_overlay_too(ct):
    assert ct.main(["keys", "--reconstruct", "--since", D, "--write"]) == 0
    assert ct.collect("today", fast=True)["by_key"] == pytest.approx({"aaaa": 30.0, "bbbb": 5.0})


def test_overlay_absent_is_identity(ct):
    assert not _overlay(ct).exists()
    data = ct.collect("today")
    assert data["by_key"] == pytest.approx({"bbbb": 35.0})
    assert data["by_key_inferred"] == pytest.approx({"bbbb": 30.0})


def test_malformed_overlay_is_ignored_and_reported_by_doctor(ct, capsys):
    _overlay(ct).parent.mkdir(parents=True, exist_ok=True)
    _overlay(ct).write_text("{not json")
    data = ct.collect("today")
    assert data["by_key"] == pytest.approx({"bbbb": 35.0})
    assert ct.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "key-corrections" in out and "malformed" in out


def test_override_env_missing_path_fails_loudly(ct, tmp_path, capsys):
    os.environ["COST_TRACKER_KEY_CORRECTIONS"] = str(tmp_path / "nope" / "kc.json")
    try:
        assert ct.main(["keys"]) == 2
        assert "COST_TRACKER_KEY_CORRECTIONS" in capsys.readouterr().err
    finally:
        os.environ.pop("COST_TRACKER_KEY_CORRECTIONS", None)


def test_rewrite_replaces_only_the_named_days(ct):
    _overlay(ct).parent.mkdir(parents=True, exist_ok=True)
    keep = {"entries": [{"day": "2026-09-01", "sid": "x", "pid": None, "stream": 0,
                         "from_ts": "2026-09-01T10:00:00Z", "to_ts": "2026-09-01T10:00:00Z",
                         "key": "zzzz", "basis": "refusal", "usd": 1.0, "evidence": "e"}],
            "violations": []}
    _overlay(ct).write_text(json.dumps({"contract": 1, "written_at": "x",
                                        "days": {"2026-09-01": keep}}))
    assert ct.main(["keys", "--reconstruct", "--since", D, "--write"]) == 0
    doc = json.loads(_overlay(ct).read_text())
    assert doc["days"]["2026-09-01"] == keep and D in doc["days"]
    backups = list(_overlay(ct).parent.glob("key-corrections.json.bak-*"))
    assert len(backups) == 1
