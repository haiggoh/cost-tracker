"""Unknown Claude models: looked up once, assumed meanwhile, never priced at $0.

claude-sonnet-5-5 priced at $0 on 2026-10-03 because the rate table was hand-edited and nobody
had added it. These pin the replacement: a lookup that runs only for an id that is not already
known, and a family-based assumption until it lands.
"""
import importlib.machinery
import importlib.util
import json
import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(tmp_path, table=None):
    cfg = tmp_path / "config"
    os.environ["COST_TRACKER_CONFIG_DIR"] = str(cfg)
    os.environ["BUDGET_TALLY_LEDGER_DIR"] = str(tmp_path / "ledger")
    os.environ["COST_TRACKER_NO_PRICE_SYNC"] = "1"
    src = tmp_path / "prices-upstream.json"
    if table is not None:
        src.write_text(json.dumps(table))
    # A file:// URL keeps the test off the network; curl reads it like any other URL.
    os.environ["COST_TRACKER_PRICES_URL"] = src.as_uri()
    loader = importlib.machinery.SourceFileLoader("bt_prices", str(ROOT / "bin" / "budget-tally.py"))
    spec = importlib.util.spec_from_loader("bt_prices", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod, src


UPSTREAM = {
    "claude-opus-6": {"litellm_provider": "anthropic", "input_cost_per_token": 6e-06,
                      "output_cost_per_token": 3e-05, "cache_read_input_token_cost": 6e-07,
                      "cache_creation_input_token_cost": 7.5e-06,
                      "cache_creation_input_token_cost_above_1hr": 1.2e-05},
    # Same id on a reseller must NOT be taken: its price is not Anthropic's list price.
    "claude-sonnet-6": {"litellm_provider": "bedrock", "input_cost_per_token": 9e-06,
                        "output_cost_per_token": 9e-05},
}


def test_sonnet_5_5_is_priced_at_its_list_rate(tmp_path):
    bt, _ = _load(tmp_path)
    r = bt.rate_for("claude-sonnet-5-5")
    assert r["input"] == pytest.approx(2e-6)
    assert r["output"] == pytest.approx(10e-6)
    assert r["cache_read"] == pytest.approx(2e-7)
    assert bt.rate_for("claude-sonnet-5-5[1m]") == r


def test_an_unknown_version_is_assumed_at_the_newest_known_in_its_family(tmp_path):
    bt, _ = _load(tmp_path)
    r = bt.rate_for("claude-opus-6")
    assert r["input"] == pytest.approx(4e-6)       # Opus 5.5, the newest known Opus
    assert "claude-opus-6" in bt.ASSUMED_SEEN
    assert bt.rate_for("claude-sonnet-7-1")["input"] == pytest.approx(2e-6)


def test_a_non_claude_or_unrecognisable_id_is_not_assumed(tmp_path):
    bt, _ = _load(tmp_path)
    assert bt.rate_for("gpt-6-sol") is None
    assert bt.rate_for("claude-mystery") is None


def test_sync_saves_the_upstream_anthropic_rate_and_it_wins_over_the_assumption(tmp_path):
    bt, _ = _load(tmp_path, UPSTREAM)
    assert bt.sync_prices(["claude-opus-6"]) == {"claude-opus-6": "litellm"}
    saved = json.loads((tmp_path / "config" / "prices.json").read_text())["models"]
    assert saved["claude-opus-6"]["input"] == pytest.approx(6e-6)
    assert saved["claude-opus-6"]["cache_read_mult"] == pytest.approx(0.1)
    bt2, _ = _load(tmp_path, UPSTREAM)            # a fresh process reads the saved file
    assert bt2.rate_for("claude-opus-6")["output"] == pytest.approx(3e-5)
    assert "claude-opus-6" not in bt2.ASSUMED_SEEN


def test_a_reseller_row_is_ignored_and_the_assumption_is_saved(tmp_path):
    bt, _ = _load(tmp_path, UPSTREAM)
    assert bt.sync_prices(["claude-sonnet-6"]) == {"claude-sonnet-6": "assumed"}
    assert bt.rate_for("claude-sonnet-6")["input"] == pytest.approx(2e-6)


def test_known_models_trigger_no_lookup_at_all(tmp_path):
    bt, src = _load(tmp_path)                     # no upstream file: any fetch would fail
    assert bt.models_needing_prices(["claude-opus-5-5", "claude-sonnet-5-5[1m]",
                                     "claude-haiku-4-5-20251001", "gpt-6-sol"]) == []
    assert bt.sync_prices(["claude-opus-5-5"]) == {}
    assert not (tmp_path / "config" / "prices.json").exists()


def test_a_saved_model_is_not_looked_up_again(tmp_path):
    bt, src = _load(tmp_path, UPSTREAM)
    bt.sync_prices(["claude-opus-6"])
    src.unlink()                                   # upstream gone: a re-fetch would change nothing
    assert bt.models_needing_prices(["claude-opus-6"]) == []


def test_an_assumption_is_rechecked_only_after_a_day(tmp_path):
    bt, src = _load(tmp_path, UPSTREAM)
    bt.sync_prices(["claude-sonnet-6"])            # saved as "assumed"
    assert bt.models_needing_prices(["claude-sonnet-6"]) == []
    path = tmp_path / "config" / "prices.json"
    d = json.loads(path.read_text())
    d["models"]["claude-sonnet-6"]["checked_at"] -= bt.PRICE_RECHECK_S + 1
    path.write_text(json.dumps(d))
    bt2, _ = _load(tmp_path, UPSTREAM)
    assert bt2.models_needing_prices(["claude-sonnet-6"]) == ["claude-sonnet-6"]


def test_an_unreachable_table_still_saves_the_assumption(tmp_path):
    bt, src = _load(tmp_path)                      # upstream file never written
    assert bt.sync_prices(["claude-fable-6"]) == {"claude-fable-6": "assumed"}
    assert bt.rate_for("claude-fable-6")["input"] == pytest.approx(10e-6)
