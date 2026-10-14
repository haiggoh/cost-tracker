"""A gateway's OWN price table outranks a fee guessed from its hostname.

Measured 2026-10-14: a LiteLLM proxy priced every Claude row at official x1.10 (Anthropic's
documented regional-endpoint premium) and its budget refusals tracked that table to 0.4%, while
cost-tracker split the one factor into a wrong registry fee (x1.05) and an unexplained "gap"
(x1.047). These pin the replacement: detection by behaviour (no hostname shipped), per-model
rows first, official x derived markup for unlisted models, and a stale-but-kept table when the
proxy is unreachable.
"""
import importlib.machinery
import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

OFFICIAL = json.loads((ROOT / "config" / "anthropic-prices.json").read_text())["models"]


def _row(name, rates):
    f = {"input": "input_cost_per_token", "output": "output_cost_per_token",
         "cache_read": "cache_read_input_token_cost",
         "cache_write_5m": "cache_creation_input_token_cost",
         "cache_write_1h": "cache_creation_input_token_cost_above_1hr"}
    return {"model_name": name, "model_info": {f[k]: v / 1e6 for k, v in rates.items()}}


def _scaled(name, k):
    return {t: v * k for t, v in OFFICIAL[name].items() if isinstance(v, (int, float))
            and t != "above_tokens"}


def proxy_doc(custom=None):
    """A LiteLLM /model/info document: x1.10 on opus-5-5, sonnet-5-5, haiku-4-5, plus an
    optional CUSTOM row priced off that markup, plus a non-Claude row that must be ignored."""
    rows = [_row(n, _scaled(n, 1.1)) for n in ("claude-opus-5-5", "claude-sonnet-5-5",
                                                "claude-haiku-4-5")]
    rows.append(_row("claude-haiku-4-5-20251001", _scaled("claude-haiku-4-5", 1.1)))
    if custom:
        rows.append(_row(custom, _scaled(custom, 0.5)))
    rows.append({"model_name": "gpt-x", "model_info": {"input_cost_per_token": 1e-6,
                                                       "output_cost_per_token": 2e-6}})
    return {"data": rows}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    monkeypatch.setenv("COST_TRACKER_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("COST_TRACKER_NO_SPAWN", "1")
    for v in ("COST_TRACKER_FEE", "COST_TRACKER_MARKUP", "CLAUDE_CODE_USE_BEDROCK"):
        monkeypatch.delenv(v, raising=False)
    for m in ("proxy_prices", "cost_connection"):
        sys.modules.pop(m, None)
    return tmp_path


def _load(name, file):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / "bin" / file))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def _serve(tmp_path, doc, monkeypatch):
    src = tmp_path / "model-info.json"
    src.write_text(json.dumps(doc))
    monkeypatch.setenv("COST_TRACKER_PROXY_PRICES_URL", src.as_uri())
    return src


GW = {"ANTHROPIC_BASE_URL": "https://gw.example.test"}


def test_markup_is_derived_from_the_table_not_hardcoded(env, monkeypatch):
    _serve(env, proxy_doc(), monkeypatch)
    pp = _load("proxy_prices", "proxy_prices.py")
    e = pp.refresh({**GW, "COST_TRACKER_PROXY_PRICES_URL": str((env / "model-info.json").as_uri())})
    assert e["proxy"] is True and e["markup"] == pytest.approx(1.1)
    assert e["custom"] == []
    assert "gpt-x" not in e["models"]                       # only claude-* rows are kept
    assert "claude-haiku-4-5" in e["models"]                 # dated id folded into its alias


def test_a_row_off_the_markup_is_reported_custom_and_priced_as_listed(env, monkeypatch):
    src = _serve(env, proxy_doc(custom="claude-haiku-5-5"), monkeypatch)
    pp = _load("proxy_prices", "proxy_prices.py")
    e = pp.refresh({**GW, "COST_TRACKER_PROXY_PRICES_URL": src.as_uri()})
    assert e["markup"] == pytest.approx(1.1)                 # the outlier does not move the median
    assert e["custom"] == ["claude-haiku-5-5"]
    rates, basis = pp.price_for("claude-haiku-5-5", e)
    assert basis == "proxy table" and rates["input"] == pytest.approx(0.05)


def test_an_unlisted_model_is_official_times_the_derived_markup(env, monkeypatch):
    src = _serve(env, proxy_doc(), monkeypatch)
    pp = _load("proxy_prices", "proxy_prices.py")
    e = pp.refresh({**GW, "COST_TRACKER_PROXY_PRICES_URL": src.as_uri()})
    rates, basis = pp.price_for("claude-fable-5-1[1m]", e)
    assert basis == "official x markup"
    assert rates["input"] == pytest.approx(11.0) and rates["cache_read"] == pytest.approx(0.275)


def test_an_unreachable_proxy_keeps_its_last_table(env, monkeypatch):
    src = _serve(env, proxy_doc(), monkeypatch)
    pp = _load("proxy_prices", "proxy_prices.py")
    e1 = pp.refresh({**GW, "COST_TRACKER_PROXY_PRICES_URL": src.as_uri()})
    src.unlink()
    e2 = pp.refresh({**GW, "COST_TRACKER_PROXY_PRICES_URL": src.as_uri()}, force=True)
    assert e2["markup"] == e1["markup"] and e2["models"] == e1["models"]
    assert e2["last_error"] == "unreachable"


def test_a_non_litellm_answer_is_not_a_price_table(env, monkeypatch):
    src = _serve(env, {"detail": "Not Found"}, monkeypatch)
    pp = _load("proxy_prices", "proxy_prices.py")
    e = pp.refresh({**GW, "COST_TRACKER_PROXY_PRICES_URL": src.as_uri()})
    assert e["proxy"] is False
    assert pp.cached({**GW, "COST_TRACKER_PROXY_PRICES_URL": src.as_uri()}) is None


@pytest.mark.parametrize("url", ["https://api.anthropic.com", "http://localhost:4142", ""])
def test_anthropic_and_local_endpoints_are_never_probed(env, url):
    pp = _load("proxy_prices", "proxy_prices.py")
    assert pp.probe_target({"ANTHROPIC_BASE_URL": url}) == (None, None)
    assert pp.refresh({"ANTHROPIC_BASE_URL": url}) is None


def test_off_disables_probing(env):
    pp = _load("proxy_prices", "proxy_prices.py")
    assert pp.probe_target({**GW, "COST_TRACKER_PROXY_PRICES_URL": "off"}) == (None, None)


def test_default_probe_is_the_litellm_route_on_the_session_endpoint(env, monkeypatch):
    monkeypatch.delenv("COST_TRACKER_PROXY_PRICES_URL", raising=False)
    pp = _load("proxy_prices", "proxy_prices.py")
    assert pp.probe_target({"ANTHROPIC_BASE_URL": "https://gw.example.test/"}) == (
        "gw.example.test", "https://gw.example.test/model/info")


def test_the_credential_never_appears_on_the_command_line(env, monkeypatch):
    pp = _load("proxy_prices", "proxy_prices.py")
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["input"] = argv, kw.get("input")

        class R:
            returncode, stdout = 1, b""
        return R()
    monkeypatch.setattr(pp.subprocess, "run", fake_run)
    pp._fetch("https://gw.example.test/model/info", {"ANTHROPIC_AUTH_TOKEN": "sk-test-SECRET"})
    assert not any("SECRET" in a for a in seen["argv"])
    assert b"SECRET" in seen["input"]


def test_connection_takes_the_table_markup_and_supersedes_the_gap(env, monkeypatch):
    """The end-to-end shape: a host the registry maps to a 5% reseller, a stored 1.047 gap,
    and a proxy table — the table wins, the gap is shown as superseded and not applied."""
    src = _serve(env, proxy_doc(), monkeypatch)
    pp = _load("proxy_prices", "proxy_prices.py")
    pp.refresh({**GW, "COST_TRACKER_PROXY_PRICES_URL": src.as_uri()})
    (env / "cfg" / "cap.json").write_text(json.dumps({
        "connection": {"host_map": {"gw.example.test": "llmgateway.io"}},
        "gap": {"factor": 1.0472, "n_days": 10, "cv": 0.03}}))
    cc = _load("cost_connection", "cost_connection.py")
    c = cc.resolve_connection({**GW, "COST_TRACKER_PROXY_PRICES_URL": src.as_uri()})
    assert c["fee"] == pytest.approx(1.1)
    assert "price table" in c["fee_source"]
    assert c["gap"] is None and c["gap_superseded"] == pytest.approx(1.0472)
    assert c["price_table"]["models"] == 3


def test_without_a_table_the_old_resolution_is_unchanged(env, monkeypatch):
    monkeypatch.setenv("COST_TRACKER_PROXY_PRICES_URL", "off")
    (env / "cfg").mkdir()
    (env / "cfg" / "cap.json").write_text(json.dumps({
        "connection": {"host_map": {"gw.example.test": "llmgateway.io"}},
        "gap": {"factor": 1.0472, "n_days": 10}}))
    cc = _load("cost_connection", "cost_connection.py")
    c = cc.resolve_connection({**GW, "COST_TRACKER_PROXY_PRICES_URL": "off"})
    assert c["fee"] == 1.05 and c["gap"] == pytest.approx(1.0472)
    assert c["price_table"] is None


def test_reconstruction_uses_the_custom_row_exactly(env, monkeypatch):
    """budget-tally multiplies reconstructed list-axis cost by the fee; for a CUSTOM row the
    product must be the gateway's row, not official x markup."""
    src = _serve(env, proxy_doc(custom="claude-haiku-5-5"), monkeypatch)
    pp = _load("proxy_prices", "proxy_prices.py")
    pp.refresh({**GW, "COST_TRACKER_PROXY_PRICES_URL": src.as_uri()})
    monkeypatch.setenv("ANTHROPIC_BASE_URL", GW["ANTHROPIC_BASE_URL"])
    monkeypatch.setenv("BUDGET_TALLY_LEDGER_DIR", str(env / "ledger"))
    monkeypatch.setenv("COST_TRACKER_NO_PRICE_SYNC", "1")
    bt = _load("bt_proxy", "budget-tally.py")
    assert bt.MARKUP == pytest.approx(1.1)
    r = bt.rate_for("claude-haiku-5-5")
    assert r["input"] * bt.MARKUP == pytest.approx(0.05e-6)  # the gateway's own (custom) row
    r = bt.rate_for("claude-opus-5-5[1m]")
    assert r["input"] * bt.MARKUP == pytest.approx(4.4e-6)


def test_official_table_is_the_budget_tally_source(env, monkeypatch):
    monkeypatch.setenv("COST_TRACKER_PROXY_PRICES_URL", "off")
    monkeypatch.setenv("BUDGET_TALLY_LEDGER_DIR", str(env / "ledger"))
    bt = _load("bt_official", "budget-tally.py")
    for name, o in OFFICIAL.items():
        r = bt.rate_for(name)
        for t in ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h"):
            assert r[t] == pytest.approx(o[t] / 1e6), (name, t)


def test_cli_help_does_not_probe(env, monkeypatch):
    pp = _load("proxy_prices", "proxy_prices.py")
    monkeypatch.setattr(pp, "refresh", lambda *a, **k: pytest.fail("--help probed the network"))
    assert pp.main(["--help"]) == 0
    assert pp.main(["--bogus"]) == 2
