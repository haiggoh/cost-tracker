"""How the paid session connects decides which LIMIT applies and which FEE (if any) is real.

Sources (fetched 2026-10-11): code.claude.com/docs/en/authentication (credential precedence),
/statusline (`rate_limits` appears only for Pro/Max subscribers or behind a Claude apps gateway),
platform.claude.com/docs/en/api/rate-limits (Console keys: MONTHLY tier cap, no daily cap), and
llmgateway.io/pricing (list rates + 5% fee). Before 0.12.0 every user got this machine's $40/day
gateway cap and a single "markup" that mixed a published fee with an unexplained measured gap.
"""
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture()
def cc(tmp_path, monkeypatch):
    monkeypatch.setenv("COST_TRACKER_CONFIG_DIR", str(tmp_path / "cfg"))
    spec = importlib.util.spec_from_file_location("cost_connection", ROOT / "bin" / "cost_connection.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cfg(tmp_path, data):
    d = tmp_path / "cfg"
    d.mkdir(exist_ok=True)
    (d / "cap.json").write_text(json.dumps(data))


SUB = {"rate_limits": {"five_hour": {"used_percentage": 23.5, "resets_at": 1},
                       "seven_day": {"used_percentage": 41.2, "resets_at": 2}}}
APPS_GW = {"rate_limits": {"spend_limit": {"used_percentage": 62.8, "resets_at": 3,
                                           "used_usd": 314.12, "limit_usd": 500, "period": "monthly"}}}


@pytest.mark.parametrize("env,payload,kind", [
    ({"CLAUDE_CODE_USE_BEDROCK": "1", "ANTHROPIC_API_KEY": "k"}, None, "cloud-provider"),
    ({"CLAUDE_CODE_USE_VERTEX": "1"}, None, "cloud-provider"),
    ({"CLAUDE_CODE_USE_FOUNDRY": "1"}, None, "cloud-provider"),
    ({"CLAUDE_CODE_USE_VERTEX": ""}, SUB, "subscription"),          # empty = unset (official)
    ({"ANTHROPIC_BASE_URL": "http://127.0.0.1:4142"}, None, "local"),
    ({"ANTHROPIC_BASE_URL": "http://localhost:8000"}, None, "local"),
    ({"ANTHROPIC_BASE_URL": "https://api.llmgateway.io/v1"}, None, "gateway"),
    ({"ANTHROPIC_BASE_URL": "https://gw.example.test"}, None, "gateway"),
    ({"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}, SUB, "subscription"),
    ({}, APPS_GW, "apps-gateway"),
    ({}, SUB, "subscription"),
    ({"ANTHROPIC_API_KEY": "k"}, None, "api-key"),
    ({"ANTHROPIC_AUTH_TOKEN": "t"}, None, "api-key"),
    ({}, None, "first-party"),
])
def test_connection_kind_follows_official_precedence(cc, env, payload, kind):
    assert cc.resolve_connection(env, payload)["kind"] == kind


def test_known_gateway_gets_the_published_fee_with_its_source(cc):
    c = cc.resolve_connection({"ANTHROPIC_BASE_URL": "https://api.llmgateway.io/v1"})
    assert c["provider"] == "llmgateway.io"
    assert c["fee"] == 1.05
    assert "llmgateway.io/pricing" in c["fee_source"]
    assert c["cap_kind"] == "daily-key"


def test_a_lookalike_host_never_inherits_a_fee(cc):
    """Substring matching would hand a 5% fee to any host that merely CONTAINS the name."""
    for url in ("https://llmgateway.io.evil.test", "https://notllmgateway.io",
                "https://api.llmgateway.io.example.test/v1"):
        c = cc.resolve_connection({"ANTHROPIC_BASE_URL": url})
        assert c["kind"] == "gateway" and c["provider"] is None and c["fee"] == 1.0, (url, c)


def test_a_subdomain_of_a_registered_host_does_match(cc):
    c = cc.resolve_connection({"ANTHROPIC_BASE_URL": "https://eu.api.llmgateway.io"})
    assert c["provider"] == "llmgateway.io" and c["fee"] == 1.05


def test_a_private_host_is_mapped_locally_not_shipped(cc, tmp_path):
    _cfg(tmp_path, {"connection": {"host_map": {"llmgw.corp.example": "llmgateway.io"}}})
    c = cc.resolve_connection({"ANTHROPIC_BASE_URL": "https://llmgw.corp.example"})
    assert c["provider"] == "llmgateway.io" and c["fee"] == 1.05
    assert c["provider_source"] == "local host map"
    shipped = (ROOT / "config" / "providers.json").read_text()
    assert "corp.example" not in shipped


def test_only_a_known_gateway_carries_a_fee(cc):
    for env, payload in [({}, SUB), ({"ANTHROPIC_API_KEY": "k"}, None),
                         ({"CLAUDE_CODE_USE_BEDROCK": "1"}, None),
                         ({"ANTHROPIC_BASE_URL": "https://gw.example.test"}, None), ({}, APPS_GW)]:
        assert cc.resolve_connection(env, payload)["fee"] == 1.0, (env, payload)


def test_limits_per_kind(cc):
    sub = cc.resolve_connection({}, SUB)
    assert sub["cap_kind"] == "percent-window" and sub["cap_usd"] is None
    assert sub["windows"] == {"five_hour": 23.5, "seven_day": 41.2}
    apps = cc.resolve_connection({}, APPS_GW)
    assert apps["cap_kind"] == "spend-limit" and apps["cap_usd"] == 500 and apps["cap_period"] == "monthly"
    key = cc.resolve_connection({"ANTHROPIC_API_KEY": "k"})
    assert key["cap_kind"] == "monthly" and key["cap_usd"] is None      # until set or learned
    assert cc.resolve_connection({"CLAUDE_CODE_USE_BEDROCK": "1"})["cap_kind"] == "none"
    assert cc.resolve_connection({"ANTHROPIC_BASE_URL": "http://127.0.0.1:4142"})["cap_kind"] == "none"


def test_a_hand_set_fee_overrides_the_registry_and_says_so(cc, tmp_path):
    _cfg(tmp_path, {"fee": {"factor": 1.065, "source": "set by hand"}})
    c = cc.resolve_connection({"ANTHROPIC_BASE_URL": "https://api.llmgateway.io"})
    assert c["fee"] == 1.065 and c["fee_source"] == "set by hand"


def test_a_hand_set_fee_never_reaches_a_non_gateway_session(cc, tmp_path):
    """The fee is a property of the reseller. A subscription session must stay at list price
    even on a machine whose config holds a gateway fee."""
    _cfg(tmp_path, {"fee": {"factor": 1.05, "source": "set by hand"}})
    assert cc.resolve_connection({}, SUB)["fee"] == 1.0
    assert cc.resolve_connection({"ANTHROPIC_API_KEY": "k"})["fee"] == 1.0


def test_env_fee_override_wins_and_legacy_markup_env_still_works(cc, monkeypatch):
    env = {"ANTHROPIC_BASE_URL": "https://gw.example.test", "COST_TRACKER_FEE": "1.1"}
    assert cc.resolve_connection(env)["fee"] == 1.1
    env = {"ANTHROPIC_BASE_URL": "https://gw.example.test", "COST_TRACKER_MARKUP": "1.2"}
    assert cc.resolve_connection(env)["fee"] == 1.2


def test_legacy_markup_migrates_hand_set_to_fee_and_learned_to_gap(cc):
    hand = {"markup": {"factor": 1.05, "n_days": 0, "basis": "set by hand with `cost-tracker markup --set`"}}
    out = cc.migrate_config(hand)
    assert out["fee"]["factor"] == 1.05 and "gap" not in out and "markup" not in out
    learned = {"markup": {"factor": 1.2512, "n_days": 6, "cv": 0.057, "basis": "measured"}}
    out = cc.migrate_config(learned)
    assert out["gap"]["factor"] == 1.2512 and "fee" not in out and "markup" not in out


def test_the_gap_is_reported_separately_and_never_becomes_the_fee(cc, tmp_path):
    _cfg(tmp_path, {"fee": {"factor": 1.05, "source": "registry"},
                    "gap": {"factor": 1.047, "n_days": 7, "cv": 0.02}})
    c = cc.resolve_connection({"ANTHROPIC_BASE_URL": "https://api.llmgateway.io"})
    assert c["fee"] == 1.05
    assert c["gap"] == 1.047
    assert c["gap_n_days"] == 7


def test_resolver_is_pure_no_subprocess(cc, monkeypatch):
    import subprocess
    def boom(*a, **k):
        raise AssertionError("the statusline path must never spawn a process")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    cc.resolve_connection({"ANTHROPIC_BASE_URL": "https://api.llmgateway.io"}, SUB)


# --- the CLI and the statusline segment, end to end on the resolver ------------------------

def _ct(tmp_path, monkeypatch, base_url=None, cfg=None, **env):
    import sys
    sys.path.insert(0, str(ROOT / "tests"))
    from conftest import load_ct
    ct, ledger = load_ct(tmp_path)
    if cfg is not None:
        ct.write_config(cfg)
    if base_url is None:
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    (ledger / "ffffffff-0000-0000-0000-000000000001").write_text("2026-09-03 30.00 0")
    return ct


LEARNED_40 = {"contract": 1, "key": {"cap_usd": 40.0, "scope": "key"}}


def test_a_subscription_never_inherits_a_learned_gateway_cap(tmp_path, monkeypatch):
    ct = _ct(tmp_path, monkeypatch, None, cfg=dict(LEARNED_40))
    data = ct.collect("today")
    # no rate_limits payload in the CLI path, no key env: first-party, still no gateway cap
    assert data["cap_usd"] is None
    assert "$40" not in ct.render_statusline(data)


def test_an_api_key_session_gets_no_daily_cap_and_no_fee(tmp_path, monkeypatch):
    ct = _ct(tmp_path, monkeypatch, None, cfg=dict(LEARNED_40, fee={"factor": 1.05}),
             ANTHROPIC_API_KEY="sk-test-not-real")
    data = ct.collect("today")
    assert data["connection"] == "api-key" and data["cap_usd"] is None
    assert data["fee"] == 1.0 and data["billed_usd"] == pytest.approx(30.0)


def test_a_known_gateway_bills_list_times_fee_and_projects_the_gap(tmp_path, monkeypatch):
    ct = _ct(tmp_path, monkeypatch, "https://api.llmgateway.io",
             cfg=dict(LEARNED_40, gap={"factor": 1.05, "n_days": 7}))
    data = ct.collect("today")
    assert data["fee"] == 1.05 and "llmgateway.io/pricing" in data["fee_source"]
    assert data["billed_usd"] == pytest.approx(31.5)           # list x fee only
    assert data["projected_usd"] == pytest.approx(33.075)      # x gap, separately
    assert ct.render_statusline(data) == "today: $31.50/$40 gw (~$33.08 +gap)"


def test_the_gap_projection_can_be_turned_off(tmp_path, monkeypatch):
    ct = _ct(tmp_path, monkeypatch, "https://api.llmgateway.io",
             cfg=dict(LEARNED_40, gap={"factor": 1.05, "n_days": 7}), COST_TRACKER_APPLY_GAP="0")
    data = ct.collect("today")
    assert data["projected_usd"] is None and data["gap"] == 1.05
    assert ct.render_statusline(data) == "today: $31.50/$40 gw"


def test_map_host_writes_locally_and_rejects_an_unknown_provider(tmp_path, monkeypatch, capsys):
    ct = _ct(tmp_path, monkeypatch, "https://llmgw.corp.example")
    assert ct.main(["connection", "--map-host", "llmgw.corp.example", "llmgateway.io"]) == 0
    assert ct.connection()["fee"] == 1.05
    with pytest.raises(SystemExit):
        ct.main(["connection", "--map-host", "x.example", "no-such-provider"])


def test_calibrate_measures_the_gap_on_top_of_the_fee_never_into_it(tmp_path, monkeypatch):
    """10-09 shape: list 36.41, gateway 40.07, published fee 1.05 -> gap 1.0481, fee untouched."""
    ct = _ct(tmp_path, monkeypatch, "https://api.llmgateway.io")
    days = [{"verdict": "undercount", "ours_usd": 36.41, "gateway_usd": 40.07}] * 6
    m = ct.measure_markup({"days": days, "fee": 1.05})
    assert m["factor"] == pytest.approx(1.0481, abs=1e-4)
    assert ct.markup_factor() == 1.05      # the fee did not move
    # without the fee divided out, the same days would read x1.1005 — the conflation 0.12 removes
    assert ct.measure_markup({"days": days})["factor"] == pytest.approx(1.1005, abs=1e-4)


def test_learn_gap_stores_the_gap_and_leaves_the_fee(tmp_path, monkeypatch, capsys):
    ct = _ct(tmp_path, monkeypatch, "https://api.llmgateway.io")
    fake = {"days": [], "undercount_days": 0, "measured_markup":
            {"factor": 1.047, "n_days": 7, "cv": 0.02, "reason": None}}
    monkeypatch.setattr(ct, "calibrate", lambda **k: fake)
    monkeypatch.setattr(ct, "render_calibrate", lambda d: "")
    ct.main(["calibrate", "--learn-gap"])
    cfg = ct.read_config()
    assert cfg["gap"]["factor"] == 1.047 and "fee" not in cfg and "markup" not in cfg
    assert "fee is unchanged" in capsys.readouterr().out
    assert ct.markup_factor() == 1.05 and ct.gap_factor() == 1.047
