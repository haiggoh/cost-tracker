"""Per-key aggregation and rendering (spec decisions 6, 7, 8): collect() splits
today's cloud spend by paying-key hint with an explicit unattributed remainder;
the statusline shows every key against its own cap; report shows per-key rows
plus the unattributed line; the legacy joyia-shared scope is retired by a
VISIBLE migration; `cost-tracker keys` exposes the same data as JSON.
"""
import importlib.machinery
import importlib.util
import json
import os
import pathlib

import pytest

from conftest import load_ct

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_ss():
    loader = importlib.machinery.SourceFileLoader(
        "shared_snapshot_under_test", str(ROOT / "bin" / "shared_snapshot.py"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["COST_TRACKER_CONFIG_DIR"] = None  # read via param below
    loader.exec_module(mod)
    return mod


def _load(tmp_path):
    os.environ["COST_TRACKER_PROJECTS_DIR"] = str(tmp_path / "projects")
    os.environ["COST_TRACKER_CONFIG_DIR"] = str(tmp_path / "config")
    os.environ["COST_TRACKER_JOYIA_HOME"] = str(tmp_path / "no-joyia")
    return load_ct(tmp_path)


def _write_cap(ct, keys):
    cfg = {"contract": 2, "found": True,
           "learned_at": "2026-09-03T00:00:00Z",
           "keys": keys,
           "key": max(keys.values(), key=lambda o: o["observed_at"]),
           "team": None}
    ct.write_config(cfg)
    return cfg


def _key_obs(hint, label, cap, cost, ts):
    return {"cap_usd": cap, "cost_at_kill_usd": cost, "scope": "key",
            "label": label, "key_hint": "sk-..." + hint, "observed_at": ts,
            "source_file": "x.jsonl", "raw": "..."}


# honest fixtures: refusal cost ≈ own ledger spend x markup (list vs gateway $)
A = _key_obs("aaaa", "Fake-A", 40.0, 5.5, "2026-09-03T09:00:00Z")
B = _key_obs("bbbb", "Fake-B", 25.0, 12.0, "2026-09-02T03:00:00Z")


def _write_ledger(tmp_path, rows):
    d = pathlib.Path(os.environ["COST_TRACKER_LEDGER_DIR"])
    for name, line in rows.items():
        (d / name).write_text(line + "\n")


def test_collect_splits_by_key_with_unattributed_remainder(tmp_path):
    ct, _ = _load(tmp_path)
    _write_cap(ct, {"aaaa": A, "bbbb": B})
    _write_ledger(tmp_path, {
        "s1": "2026-09-03 7.5 2.5 k=aaaa",
        "s2": "2026-09-03 10 0 k=bbbb",
        "s3": "2026-09-03 3 1",          # legacy row: unattributed
    })
    data = ct.collect("today")
    assert data["by_key"] == {"aaaa": 5.0, "bbbb": 10.0}
    assert data["unattributed_usd"] == 2.0
    assert abs(sum(data["by_key"].values()) + data["unattributed_usd"]
               - data["cloud_usd"]) < 1e-9
    assert data["sessions"]["s1"]["key_hint"] == "aaaa"
    assert "key_hint" not in data["sessions"]["s3"] or data["sessions"]["s3"].get("key_hint") is None
    assert data["key_caps"]["aaaa"] == {"cap_usd": 40.0, "approx": False}
    assert data["key_labels"]["aaaa"] == "Fake-A"
    assert data["shared_budget"]["suspected"] is False


def test_collect_autoregisters_new_keys(tmp_path):
    ct, _ = _load(tmp_path)
    _write_cap(ct, {"aaaa": A})
    _write_ledger(tmp_path, {"s1": "2026-09-03 7.5 2.5 k=aaaa"})
    ct.collect("today")
    ki = ct._key_identity()
    reg = ki.load_registry(ct.CONFIG_DIR)
    assert reg["keys"]["aaaa"]["apps"] == ["claude-code"]
    assert reg["keys"]["aaaa"]["first_seen"]


def test_statusline_per_key_segment(tmp_path):
    ct, _ = _load(tmp_path)
    _write_cap(ct, {"aaaa": A, "bbbb": B})
    _write_ledger(tmp_path, {
        "s1": "2026-09-03 7.5 2.5 k=aaaa",
        "s2": "2026-09-03 10 0 k=bbbb",
        "s3": "2026-09-03 3 1",
    })
    data = ct.collect("today", fast=True)
    # the unattributed remainder ($2.00 from s3) stays visible on the budget line;
    # 0.25.0: the shared label prefix "Fake-" shortens to "key" (no current key -> all)
    assert ct.render_statusline(data) == \
        "today: keyB $10.00/$25 · keyA $5.00/$40 · unattributed $2.00"


def test_statusline_cap_reached_and_approx(tmp_path):
    ct, _ = _load(tmp_path)
    _write_cap(ct, {"aaaa": _key_obs("aaaa", "Fake-A", 4.0, 4.1, "2026-09-03T09:00:00Z"),
                    "bbbb": B})
    _write_ledger(tmp_path, {
        "s1": "2026-09-03 7.5 2.5 k=aaaa",     # 5.00 vs cap 4 -> CAP REACHED
        "s2": "2026-09-03 4 0 k=cccc",         # no refusal of its own -> approx sibling cap
    })
    # 0.24.0: inheritance needs a shared login route (all three on one gateway here)
    (pathlib.Path(ct.CONFIG_DIR) / "keys.json").write_text(json.dumps({"keys": {
        h: {"apps": ["claude-code"], "routes": ["gw-a.example"]}
        for h in ("aaaa", "bbbb", "cccc")}}))
    data = ct.collect("today", fast=True)
    out = ct.render_statusline(data)
    assert "Fake-A $5.00/$4 CAP REACHED" in out
    # cccc has no refusal of its own: it inherits the NEWEST sibling's cap (aaaa,
    # ts 2026-09-03 > bbbb's) marked approx
    assert "sk-...cccc $4.00/≈$4" in out


def test_statusline_shared_budget_suspected_drops_denominators(tmp_path):
    ct, _ = _load(tmp_path)
    pooled = _key_obs("aaaa", "Fake-A", 40.0, 40.11, "2026-09-03T09:00:00Z")
    _write_cap(ct, {"aaaa": pooled})
    _write_ledger(tmp_path, {"s1": "2026-09-03 7.5 2.5 k=aaaa"})   # own spend 5.0 << 40.11
    data = ct.collect("today", fast=True)
    assert data["shared_budget"]["suspected"] is True
    out = ct.render_statusline(data)
    assert "/" not in out.split("·")[0] or "≈" not in out
    assert "$5.00" in out and "shared budget suspected" in out


def test_statusline_legacy_output_unchanged_without_hints(tmp_path):
    ct, _ = _load(tmp_path)
    _write_ledger(tmp_path, {"s1": "2026-09-03 7.5 2.5"})
    data = ct.collect("today", fast=True)
    assert ct.render_statusline(data) == "today: $5.00"


def test_render_table_has_per_key_section_and_unattributed(tmp_path):
    ct, _ = _load(tmp_path)
    _write_cap(ct, {"aaaa": A, "bbbb": B})
    _write_ledger(tmp_path, {
        "s1": "2026-09-03 7.5 2.5 k=aaaa",
        "s2": "2026-09-03 10 0 k=bbbb",
        "s3": "2026-09-03 3 1",
    })
    data = ct.collect("today")
    out = ct.render_table(data)
    assert "per key" in out
    assert "Fake-A" in out and "Fake-B" in out
    assert "$40" in out and "$25" in out
    assert "unattributed" in out and "2.00" in out


def test_budget_scopes_joyia_shared_migrates_visibly(tmp_path):
    ss = _load_ss()
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "budget-scopes.json").write_text(json.dumps(
        {"scopes": [{"id": "joyia-shared", "label": "JoyIA shared key"}]}))
    assert ss.read_scopes(str(cfg)) == []
    doc = json.loads((cfg / "budget-scopes.json").read_text())
    assert doc["scopes"] == []
    assert doc["retired_scopes"][0]["id"] == "joyia-shared"
    assert "per-key" in doc["retired_scopes"][0]["reason"]
    assert (cfg / "migrations.log").exists()
    with pytest.raises(ValueError):
        ss.resolve_scope("joyia-shared", str(cfg))
    # idempotent: second read does not rewrite
    mtime = (cfg / "budget-scopes.json").stat().st_mtime
    assert ss.read_scopes(str(cfg)) == []
    assert (cfg / "budget-scopes.json").stat().st_mtime == mtime


def test_zero_spend_hint_is_not_flagged_as_pooled(tmp_path):
    """Dispatch finding: a $0 hinted row (local render / not-yet-spent cloud) must
    not defeat the date-scope guard and false-alarm 'shared budget'."""
    ct, _ = _load(tmp_path)
    _write_cap(ct, {"bbbb": B})     # B refused at cost 12.0 (last week, say)
    _write_ledger(tmp_path, {"s1": "2026-09-03 5 5 k=bbbb"})   # day_usd == 0
    data = ct.collect("today", fast=True)
    assert data["by_key"] == {"bbbb": 0.0}
    assert data["shared_budget"]["suspected"] is False


def test_multi_day_report_never_says_cap_reached(tmp_path):
    """Caps are daily: a --week sum above the cap must not print CAP REACHED."""
    ct, _ = _load(tmp_path)
    _write_cap(ct, {"aaaa": A})
    _write_ledger(tmp_path, {
        "s1": "2026-09-03 30 0 k=aaaa",
        "s2": "2026-09-02 30 0 k=aaaa",
    })
    # history supplies the older day
    import pathlib as _pl
    _pl.Path(ct.HISTORY_PATH).write_text(
        "2026-09-02T23:00:00Z s2 2026-09-02 30 0 0 k=aaaa\n")
    data = ct.collect("week")
    assert data["per_key_single_day"] is False
    assert data["shared_budget"]["suspected"] is False
    out = ct.render_table(data)
    assert "CAP REACHED" not in out
    assert "Fake-A" in out and "60.00" in out
    # single-day still shows the denominator
    data1 = ct.collect("today")
    assert data1["per_key_single_day"] is True
    assert "30.00/$40" in ct.render_table(data1)


def test_per_key_segment_wins_over_configured_scope(tmp_path):
    """Precedence pinned: hinted ledger rows -> per-key segment even when a
    (non-retired) shared scope is also configured."""
    ct, _ = _load(tmp_path)
    _write_cap(ct, {"aaaa": A})
    _write_ledger(tmp_path, {"s1": "2026-09-03 7.5 2.5 k=aaaa"})
    cfg = pathlib.Path(ct.CONFIG_DIR)
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "budget-scopes.json").write_text(json.dumps(
        {"scopes": [{"id": "test-shared", "label": "Test"}]}))
    data = ct.collect("today", fast=True)
    data["shared_snapshot"] = {"primary_amount_usd": 99.0, "cap_usd": 100.0}
    out = ct.render_statusline(data)
    assert out.startswith("today: Fake-A $5.00/$40")
    assert "99.00" not in out


def test_keys_subcommand_json(tmp_path, capsys):
    ct, _ = _load(tmp_path)
    _write_cap(ct, {"aaaa": A})
    _write_ledger(tmp_path, {"s1": "2026-09-03 7.5 2.5 k=aaaa"})
    assert ct.main(["keys", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["by_key"] == {"aaaa": 5.0}
    assert out["caps"]["aaaa"]["cap_usd"] == 40.0
    assert out["labels"]["aaaa"] == "Fake-A"
    assert out["registry"]["keys"]["aaaa"]["apps"] == ["claude-code"]
    assert out["shared_budget"]["suspected"] is False


def test_fast_path_splits_resumed_session_across_keys(tmp_path):
    """A session resumed under a NEW key: its ledger file holds only the last render
    (k=bbbb), but history shows $10 billed under aaaa before the switch. The fast
    path (keys, statusline) must split the day like the full path, never book the
    whole day to the last key (2026-11-26: Code4 $51.46 vs $24.02 on the full path)."""
    ct, _ = _load(tmp_path)
    pathlib.Path(ct.HISTORY_PATH).write_text(
        "2026-09-03T08:00:00Z s1 2026-09-03 4 0 4 k=aaaa p=1\n"
        "2026-09-03T09:00:00Z s1 2026-09-03 10 0 10 k=aaaa p=1\n"
        "2026-09-03T10:00:00Z s1 2026-09-03 10 0 10 k=bbbb p=2\n"
        "2026-09-03T11:00:00Z s1 2026-09-03 13 0 13 k=bbbb p=2\n")
    _write_ledger(tmp_path, {"s1": "2026-09-03 13 0 k=bbbb"})
    fast = ct.collect("today", fast=True)["by_key"]
    full = ct.collect("today")["by_key"]
    assert fast == pytest.approx({"aaaa": 10.0, "bbbb": 3.0})
    assert fast == pytest.approx(full)


# --- 0.24.0: a cap is inherited only within one login route (user, 2026-11-26) ------
R_A = _key_obs("aaaa", "Fake-A", 25.0, 25.3, "2026-09-03T09:00:00Z")


def test_new_key_inherits_cap_from_same_route_only(tmp_path):
    ct, _ = _load(tmp_path)
    caps = ct.caps_for_hints({"keys": {"aaaa": R_A}}, hints_with_spend={"aaaa", "bbbb", "cccc"},
                             routes={"aaaa": {"gw-a.example"}, "bbbb": {"gw-a.example"},
                                     "cccc": {"gw-b.example"}})
    assert caps["aaaa"] == {"cap_usd": 25.0, "approx": False}
    assert caps["bbbb"]["cap_usd"] == 25.0 and caps["bbbb"]["approx"] is True
    assert "cccc" not in caps


def test_own_refusal_beats_route_sibling(tmp_path):
    ct, _ = _load(tmp_path)
    own = _key_obs("bbbb", "Fake-B", 60.0, 60.2, "2026-09-02T09:00:00Z")
    caps = ct.caps_for_hints({"keys": {"aaaa": R_A, "bbbb": own}}, hints_with_spend={"bbbb"},
                             routes={"aaaa": {"gw-a.example"}, "bbbb": {"gw-a.example"}})
    assert caps["bbbb"] == {"cap_usd": 60.0, "approx": False}


def test_unknown_route_inherits_nothing(tmp_path):
    ct, _ = _load(tmp_path)
    caps = ct.caps_for_hints({"keys": {"aaaa": R_A}}, hints_with_spend={"bbbb"},
                             routes={"aaaa": {"gw-a.example"}})
    assert "bbbb" not in caps


def test_collect_reads_routes_from_the_registry(tmp_path):
    ct, _ = _load(tmp_path)
    _write_cap(ct, {"aaaa": R_A})
    (pathlib.Path(ct.CONFIG_DIR) / "keys.json").write_text(json.dumps({"keys": {
        "aaaa": {"apps": ["claude-code"], "routes": ["gw-a.example"]},
        "bbbb": {"apps": ["claude-code"], "routes": ["gw-a.example"]},
        "cccc": {"apps": ["claude-code"], "routes": ["gw-b.example"]}}}))
    _write_ledger(tmp_path, {"s1": "2026-09-03 3 0 k=bbbb", "s2": "2026-09-03 2 0 k=cccc"})
    out = ct.render_statusline(ct.collect("today", fast=True))
    assert "sk-...bbbb $3.00/≈$25" in out
    assert "sk-...cccc $2.00" in out and "sk-...cccc $2.00/" not in out


def test_no_literal_cap_in_bin():
    """No cap value is ever hardcoded (user, 2026-11-26): caps come from refusals."""
    import ast
    import re
    dollar40 = re.compile(r"\$40\b")
    hits = []
    for p in sorted((ROOT / "bin").iterdir()):
        if not p.is_file() or p.suffix == ".pyc":
            continue
        src = p.read_text(errors="ignore")
        if src.startswith("#!") and "python" in src.splitlines()[0] or p.suffix == ".py":
            tree = ast.parse(src)
            docs = {id(n.body[0].value) for n in ast.walk(tree)
                    if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
                    and n.body and isinstance(n.body[0], ast.Expr)
                    and isinstance(n.body[0].value, ast.Constant)}
            for n in ast.walk(tree):
                if isinstance(n, ast.Constant) and id(n) not in docs:
                    v = n.value
                    if (type(v) in (int, float) and v == 40) or \
                            (isinstance(v, str) and dollar40.search(v)):
                        hits.append(f"{p.name}:{n.lineno}: {v!r}")
        else:
            for i, line in enumerate(src.splitlines(), 1):
                code = re.sub(r"(^|\s)#.*$", "", line)
                # a cap in shell would be written 40.0 / $40 — a bare 40 is a count (tail -n 40)
                if re.search(r"(?<![\d.])40\.0+(?![\d])|\$40\b", code):
                    hits.append(f"{p.name}:{i}: {line.strip()}")
    assert hits == []


def test_statusline_omits_a_zero_spend_key(tmp_path):
    """A key with $0.00 today is noise on the statusline (user, 2026-11-27)."""
    ct, _ = _load(tmp_path)
    _write_ledger(tmp_path, {"s1": "2026-09-03 5 0 k=aaaa", "s2": "2026-09-03 2 2 k=zero"})
    out = ct.render_statusline(ct.collect("today", fast=True))
    assert "sk-...aaaa $5.00" in out
    assert "zero" not in out


# --- 0.25.0: the statusline shows the CURRENT key by default; similar labels shortened ----
def _labelled(ct, tmp_path):
    _write_cap(ct, {
        # yesterday's refusals: they teach the caps without tripping today's pooled check
        "aaaa": _key_obs("aaaa", "Joyia-Code4", 40.0, 40.1, "2026-09-02T09:00:00Z"),
        "bbbb": _key_obs("bbbb", "Joyia-Code-M4m", 40.0, 40.2, "2026-09-02T08:00:00Z"),
        "cccc": _key_obs("cccc", "Joyia-Code", 40.0, 40.3, "2026-09-02T07:00:00Z")})
    _write_ledger(tmp_path, {"s1": "2026-09-03 12 0 k=aaaa", "s2": "2026-09-03 7 0 k=bbbb",
                             "s3": "2026-09-03 3 0 k=cccc"})


def test_statusline_shows_only_the_current_key_by_default(tmp_path, monkeypatch):
    ct, _ = _load(tmp_path)
    _labelled(ct, tmp_path)
    monkeypatch.delenv("COST_TRACKER_STATUSLINE_KEYS", raising=False)
    monkeypatch.setattr(ct, "_current_key_hint", lambda: "bbbb")
    out = ct.render_statusline(ct.collect("today", fast=True))
    assert "key-M4m $7.00/$40" in out
    assert "key4" not in out and "$12.00" not in out


def test_statusline_all_mode_lists_every_key_shortened(tmp_path, monkeypatch):
    ct, _ = _load(tmp_path)
    _labelled(ct, tmp_path)
    monkeypatch.setenv("COST_TRACKER_STATUSLINE_KEYS", "all")
    monkeypatch.setattr(ct, "_current_key_hint", lambda: "bbbb")
    out = ct.render_statusline(ct.collect("today", fast=True))
    assert "key4 $12.00/$40" in out and "key-M4m $7.00/$40" in out and "key $3.00/$40" in out
    assert "Joyia" not in out


def test_statusline_unknown_current_key_falls_back_to_all(tmp_path, monkeypatch):
    ct, _ = _load(tmp_path)
    _labelled(ct, tmp_path)
    monkeypatch.delenv("COST_TRACKER_STATUSLINE_KEYS", raising=False)
    monkeypatch.setattr(ct, "_current_key_hint", lambda: None)
    out = ct.render_statusline(ct.collect("today", fast=True))
    assert "key4 $12.00/$40" in out and "key $3.00/$40" in out


def test_statusline_current_key_shown_even_at_zero(tmp_path, monkeypatch):
    ct, _ = _load(tmp_path)
    _labelled(ct, tmp_path)
    monkeypatch.setattr(ct, "_current_key_hint", lambda: "dddd")   # fresh key, no spend yet
    out = ct.render_statusline(ct.collect("today", fast=True))
    assert "sk-...dddd $0.00" in out


def test_statusline_invalid_keys_setting_is_visible_not_silent(tmp_path, monkeypatch):
    ct, _ = _load(tmp_path)
    _labelled(ct, tmp_path)
    monkeypatch.setenv("COST_TRACKER_STATUSLINE_KEYS", "everything")
    out = ct.render_statusline(ct.collect("today", fast=True))
    assert "COST_TRACKER_STATUSLINE_KEYS" in out and "current|all" in out


@pytest.mark.parametrize("labels,expected", [
    ({"a": "Joyia-Code4", "b": "Joyia-Code-M4m", "c": "Joyia-Code"},
     {"a": "key4", "b": "key-M4m", "c": "key"}),
    ({"a": "Joyia-Code4"}, {"a": "Joyia-Code4"}),                       # one label: unchanged
    ({"a": "Work", "b": "Home"}, {"a": "Work", "b": "Home"}),             # nothing shared
    ({"a": "Joyia-Code4", "b": "sk-...mote"}, {"a": "Joyia-Code4", "b": "sk-...mote"}),
])
def test_short_labels_strip_only_a_shared_prefix(tmp_path, labels, expected):
    ct, _ = _load(tmp_path)
    assert ct.short_key_labels(labels) == expected
