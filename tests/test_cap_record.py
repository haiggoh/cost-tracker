"""Per-KEY caps (contract 2 cap.json): one cap per last-4 hint, learned only from
refusal text, recordable by hand via `cap --record "<pasted refusal>"`, plus the
pooled-budget regression flag (spec decisions 5, 5b).

The legacy single `key` slot is kept as "newest key refusal overall" so every
pre-existing cap test and every old reader keeps working unchanged.
"""
import json
import pathlib

from conftest import load_ct

KILL_A = ("API Error: Request rejected (422) · Budget has been exceeded! "
          "Key=Fake-A (sk-...aaaa) Current cost: {cost}, Max budget: {cap}")
KILL_B = ("API Error: Request rejected (422) · Budget has been exceeded! "
          "Key=Fake-B (sk-...bbbb) Current cost: {cost}, Max budget: {cap}")
TEAM_KILL = ("API Error: Request rejected (422) · Budget has been exceeded! "
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


def _load(tmp_path):
    import os
    os.environ["COST_TRACKER_PROJECTS_DIR"] = str(tmp_path / "projects")
    os.environ["COST_TRACKER_CONFIG_DIR"] = str(tmp_path / "config")
    return load_ct(tmp_path)


def test_two_keys_learn_two_caps_and_legacy_key_slot_keeps_newest(tmp_path):
    _transcripts(tmp_path, [
        _rec(KILL_A.format(cost="39.9", cap="40.0"), "2026-09-01T02:00:00.000Z"),
        _rec(KILL_B.format(cost="12.0", cap="25.0"), "2026-09-02T03:00:00.000Z"),
    ])
    ct, _ = _load(tmp_path)
    out = ct.learn_cap()
    assert out["contract"] == 2
    assert set(out["keys"]) == {"aaaa", "bbbb"}
    assert out["keys"]["aaaa"]["cap_usd"] == 40.0
    assert out["keys"]["bbbb"]["cap_usd"] == 25.0
    assert out["keys"]["aaaa"]["label"] == "Fake-A"
    # legacy slot = newest overall; cap_usd() back-compat is pinned by the old suite
    assert out["key"]["cap_usd"] == 25.0
    ct.write_config(out)
    assert ct.cap_usd() == 25.0
    caps = ct.caps_for_hints()
    assert caps["aaaa"]["cap_usd"] == 40.0 and caps["aaaa"]["approx"] is False
    assert caps["bbbb"]["cap_usd"] == 25.0


def test_newest_refusal_per_key_wins(tmp_path):
    _transcripts(tmp_path, [
        _rec(KILL_A.format(cost="40.1", cap="40.0"), "2026-09-01T02:00:00.000Z"),
        _rec(KILL_A.format(cost="60.2", cap="60.0"), "2026-09-03T02:00:00.000Z"),
        _rec(KILL_B.format(cost="20.0", cap="20.0"), "2026-09-02T02:00:00.000Z"),
    ])
    ct, _ = _load(tmp_path)
    out = ct.learn_cap()
    assert out["keys"]["aaaa"]["cap_usd"] == 60.0   # newest for THIS key
    assert out["keys"]["bbbb"]["cap_usd"] == 20.0


def test_contract1_cap_json_migrates_on_read(tmp_path):
    """The real 0.21.5 cap.json has a single contract-1 `key` slot (M4m ...YxHg).
    read_config must lift it into `keys` without rewriting the file yet."""
    ct, _ = _load(tmp_path)
    cfg_path = pathlib.Path(ct.CONFIG_PATH)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({
        "contract": 1, "found": True, "learned_at": "2026-09-03T12:25:59Z",
        "key": {"cap_usd": 40.0, "cost_at_kill_usd": 40.018, "scope": "key",
                "label": "Joyia-Code-M4m", "key_hint": "sk-...YxHg",
                "observed_at": "2026-09-02T02:17:19.496Z", "source_file": "x.jsonl"},
    }))
    cfg = ct.read_config()
    assert cfg["keys"]["YxHg"]["cap_usd"] == 40.0
    assert cfg["key"]["cap_usd"] == 40.0            # legacy slot preserved
    assert ct.cap_usd() == 40.0                      # old readers unaffected
    assert json.loads(cfg_path.read_text())["contract"] == 1   # file untouched


def test_cap_record_accepts_a_pasted_key_refusal(tmp_path):
    """A refusal the user REPORTS later (window closed while transient) is evidence
    too — parsed by the same regex, no isApiErrorMessage needed (it is not a
    transcript line)."""
    _transcripts(tmp_path, [])
    ct, _ = _load(tmp_path)
    rc = ct.main(["cap", "--record", KILL_A.format(cost="40.11", cap="40.0")])
    assert rc == 0
    cfg = json.loads(pathlib.Path(ct.CONFIG_PATH).read_text())
    assert cfg["keys"]["aaaa"]["cap_usd"] == 40.0
    assert cfg["keys"]["aaaa"]["source_file"] == "(reported)"
    assert cfg["keys"]["aaaa"]["label"] == "Fake-A"
    assert "sk-...aaaa" in cfg["keys"]["aaaa"]["key_hint"]
    # and a newer recorded refusal wins
    rc = ct.main(["cap", "--record", KILL_A.format(cost="55.0", cap="55.0")])
    assert rc == 0
    cfg = json.loads(pathlib.Path(ct.CONFIG_PATH).read_text())
    assert cfg["keys"]["aaaa"]["cap_usd"] == 55.0


def test_cap_record_rejects_team_and_garbage(tmp_path):
    _transcripts(tmp_path, [])
    ct, _ = _load(tmp_path)
    assert ct.main(["cap", "--record", TEAM_KILL]) == 2
    assert ct.main(["cap", "--record", "the budget was exceeded once"]) == 2
    assert not pathlib.Path(ct.CONFIG_PATH).exists()   # nothing written on reject


def test_prose_in_transcripts_still_never_teaches_a_cap(tmp_path):
    _transcripts(tmp_path, [
        _rec("I saw: " + KILL_A.format(cost="99.9", cap="999.0"), "2026-09-03T12:00:00.000Z",
             api_error=False),
    ])
    ct, _ = _load(tmp_path)
    out = ct.learn_cap()
    assert out["found"] is False
    assert out["keys"] == {}


def test_detect_shared_budget_flags_pooled_and_identical_cost(tmp_path):
    ct, _ = _load(tmp_path)
    obs_a = {"key_hint": "sk-...aaaa", "cost_at_kill_usd": 40.11, "cap_usd": 40.0,
             "observed_at": "2026-11-24T10:00:00Z"}
    obs_b = {"key_hint": "sk-...bbbb", "cost_at_kill_usd": 40.11, "cap_usd": 40.0,
             "observed_at": "2026-11-24T10:05:00Z"}
    # pooled: key a's refusal quotes a cost far above its own ledger spend (list)
    r = ct.detect_shared_budget([obs_a], {"aaaa": 3.10})
    assert r["suspected"] is True
    # pooled: two keys quoting the SAME cost far above BOTH own spends
    r = ct.detect_shared_budget([obs_a, obs_b], {"aaaa": 3.0, "bbbb": 2.0})
    assert r["suspected"] is True
    # HONEST boundary case: both keys hit the same $40 cap, own spends near it
    # (refusal cost = own list x markup) -> must NOT flag
    r = ct.detect_shared_budget([obs_a, obs_b], {"aaaa": 39.0, "bbbb": 38.0}, markup=1.1)
    assert r["suspected"] is False
    # honest per-key refusals: each cost near its own spend -> quiet
    r = ct.detect_shared_budget([obs_a, obs_b], {"aaaa": 40.11, "bbbb": 40.11})
    assert r["suspected"] is False
    r = ct.detect_shared_budget([], {})
    assert r["suspected"] is False


def test_full_unmasked_key_in_refusal_collapses_to_hint_and_is_redacted(tmp_path):
    """Dispatch finding (critical): a gateway may print the FULL key in the parens.
    The learner must collapse it to the last-4 hint and redact `raw` — the full
    secret must appear nowhere in the learned config."""
    full = "API Error: Request rejected (422) · Budget has been exceeded! " \
           "Key=Fake-C (sk-ant-api03-SECRETVALUE9999) Current cost: 40.1, Max budget: 40.0"
    _transcripts(tmp_path, [_rec(full, "2026-09-03T02:00:00.000Z")])
    ct, _ = _load(tmp_path)
    out = ct.learn_cap()
    assert set(out["keys"]) == {"9999"}
    assert out["keys"]["9999"]["key_hint"] == "sk-...9999"
    blob = json.dumps(out)
    assert "SECRETVALUE" not in blob and "sk-ant-api03" not in blob
    # and via --record
    rc = ct.main(["cap", "--record", full])
    assert rc == 0
    cfg_blob = pathlib.Path(ct.CONFIG_PATH).read_text()
    assert "SECRETVALUE" not in cfg_blob
    cfg = json.loads(cfg_blob)
    assert cfg["keys"]["9999"]["cap_usd"] == 40.0


def test_migration_ignores_null_key_and_empty_hint(tmp_path):
    ct, _ = _load(tmp_path)
    cfg_path = pathlib.Path(ct.CONFIG_PATH)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"contract": 1, "key": None}))
    cfg = ct.read_config()
    assert cfg["keys"] == {} and cfg["contract"] == 2
    cfg_path.write_text(json.dumps({"contract": 1, "key": {
        "cap_usd": 40.0, "key_hint": "", "label": "x", "scope": "key"}}))
    cfg = ct.read_config()
    assert cfg["keys"] == {}          # empty hint never inserted


def test_caps_for_hints_ignores_none_hints(tmp_path):
    _transcripts(tmp_path, [
        _rec(KILL_A.format(cost="40.1", cap="40.0"), "2026-09-01T02:00:00.000Z")])
    ct, _ = _load(tmp_path)
    ct.write_config(ct.learn_cap())
    caps = ct.caps_for_hints(hints_with_spend={None, "", "aaaa"})
    assert None not in caps and "" not in caps
    assert caps["aaaa"]["cap_usd"] == 40.0


def test_caps_for_hints_marks_sibling_cap_approx(tmp_path):
    """Unseen key (user): sibling keys refused at one Max budget -> show that cap
    marked approx (≈)."""
    _transcripts(tmp_path, [
        _rec(KILL_A.format(cost="40.1", cap="40.0"), "2026-09-01T02:00:00.000Z"),
    ])
    ct, _ = _load(tmp_path)
    ct.write_config(ct.learn_cap())
    caps = ct.caps_for_hints(hints_with_spend={"aaaa", "cccc"})
    assert caps["aaaa"] == {"cap_usd": 40.0, "approx": False}
    assert caps["cccc"] == {"cap_usd": 40.0, "approx": True}
    caps = ct.caps_for_hints(hints_with_spend={"aaaa"})
    assert "cccc" not in caps


def test_render_cap_shows_per_key_blocks_with_hints_only(tmp_path):
    _transcripts(tmp_path, [
        _rec(KILL_A.format(cost="40.1", cap="40.0"), "2026-09-01T02:00:00.000Z"),
        _rec(KILL_B.format(cost="12.0", cap="25.0"), "2026-09-02T03:00:00.000Z"),
    ])
    ct, _ = _load(tmp_path)
    cfg = ct.learn_cap()
    out = ct.render_cap(cfg, 25.0)
    assert "Fake-A" in out and "sk-...aaaa" in out
    assert "Fake-B" in out and "sk-...bbbb" in out
    assert "$40.00" in out and "$25.00" in out
