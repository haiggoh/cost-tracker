"""A free lane must report the GATEWAY's day, priced the way the gateway bills it.

Measured 2026-10-23, one day, three figures for the same spend:

  gateway refusal (Bedrock, authoritative)   Current cost: 40.02651716249998, Max budget: 40.0
  statusline, in a gateway session           session $35.43 · today: $40.11/$40
  budget-tally, SessionStart on a free lane ≈ $36.47          <- at 16:41 AND again at 18:03

The tally did not move in 94 minutes, so this is not a stale snapshot. The ledger's base is right
($35.43 + $0.90 = $36.33; the day's free-lane sessions are structurally $0 and carry only a phantom
axis) and the arithmetic names the whole gap: 36.4656 x 1.10 = 40.1122 — the statusline's figure to
the cent. `resolve_connection()` has branches for gateway / subscription / apps-gateway / api-key and
NONE for `local`, so a free session's localhost endpoint decides the fee for OTHER sessions' gateway
spend: fee 1.0, list price, under-reporting by exactly the markup.

The phantom "saved with free agents" axis is a separate, deliberate feature (statusline-render.sh,
ledger fields 4-5) and these tests never touch it — only the SPEND axis.
"""
import importlib.machinery
import importlib.util
import json
import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TODAY = "2026-10-23"


def _cache_entry(markup, host="llmgw.joyia.p7s1.io", fetched_at=1792698365.0):
    """A learned gateway price table, in proxy_prices.py's own cache shape."""
    return {host: {"proxy": True, "markup": markup, "matched": 11, "custom": [],
                   "models": {}, "fetched_at": fetched_at, "checked_at": fetched_at,
                   "path": "/model/info", "official_verified_at": TODAY}}


def _load(tmp_path, *, base_url="http://127.0.0.1:4143", cache=None, cap_usd=None,
          ledger=None, session_id=None, transcripts=None, off_network=False, env_extra=None):
    """A budget-tally module bound to throwaway dirs.

    Every path this script and cost_connection.py reads is pinned to tmp_path. Leaving any of them
    to default would make the suite read THIS machine's real learned markup and cap, so it would
    pass here and fail on a stranger's box — the trap conftest.py documents for cost-tracker.
    `cache=None` (no proxy-prices.json at all) and `cap_usd=None` (no cap.json) are deliberate
    distinct states, not "unset".
    """
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    led = tmp_path / "ledger"
    led.mkdir(parents=True, exist_ok=True)
    proj = tmp_path / "projects"
    proj.mkdir(parents=True, exist_ok=True)
    if cap_usd is not None:
        (cfg / "cap.json").write_text(json.dumps(
            {"contract": 1, "learned_at": TODAY,
             "key": {"cap_usd": cap_usd, "scope": "key", "label": "Joyia-Code-M4m",
                     "observed_at": TODAY + "T02:17:19Z"}}))
    if cache is not None:
        (cfg / "proxy-prices.json").write_text(json.dumps(cache))
    if ledger:
        for sid, fields in ledger.items():
            (led / sid).write_text(" ".join(str(f) for f in fields))
    if transcripts:
        for sid, records in transcripts.items():
            d = proj / os.getcwd().replace("/", "-")
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{sid}.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in records))
    for var in ("COST_TRACKER_CAP_USD", "BUDGET_TALLY_CAP_USD", "COST_TRACKER_FEE",
                "COST_TRACKER_MARKUP", "COST_TRACKER_APPLY_GAP", "COST_TRACKER_PROVIDERS",
                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        os.environ.pop(var, None)
    os.environ["ANTHROPIC_BASE_URL"] = base_url
    os.environ["BUDGET_TALLY_TODAY"] = TODAY
    os.environ["BUDGET_TALLY_LEDGER_DIR"] = str(led)
    os.environ["COST_TRACKER_CONFIG_DIR"] = str(cfg)
    os.environ["COST_TRACKER_HISTORY"] = str(tmp_path / "history.log")
    # The variable budget-tally.py ACTUALLY reads (line 78) is BUDGET_TALLY_PROJECTS_DIR;
    # --help advertises COST_TRACKER_PROJECTS_DIR, which the code never looks at -- corrected in
    # the same change. A fixture that sets the documented name silently reads the real
    # ~/.claude/projects, which is how this test went vacuously green on the first run.
    os.environ["BUDGET_TALLY_PROJECTS_DIR"] = str(proj)
    os.environ.pop("COST_TRACKER_PROJECTS_DIR", None)
    os.environ["COST_TRACKER_NO_PRICE_SYNC"] = "1"
    # "off" only where this test never needs the cached table to be reachable: it makes
    # probe_target() -> (None, None), which would also hide a perfectly good learned table from
    # the gateway branch and turn a real assertion into a vacuous one.
    if off_network:
        os.environ["COST_TRACKER_PROXY_PRICES_URL"] = "off"
    else:
        os.environ.pop("COST_TRACKER_PROXY_PRICES_URL", None)
    os.environ["CLAUDE_CODE_SESSION_ID"] = session_id or ""
    # AFTER the inherited-value pops above, BEFORE the import below: the module reads its config at
    # import time, so an override set earlier is wiped by the cleanup and a later one is inert.
    for k, v in (env_extra or {}).items():
        os.environ[k] = v
    loader = importlib.machinery.SourceFileLoader("bt_free", str(ROOT / "bin" / "budget-tally.py"))
    spec = importlib.util.spec_from_loader("bt_free", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_free_session_prices_gateway_spend_at_the_cached_markup(tmp_path):
    """The drift fix: a free lane takes the fee from the cached gateway table, not from its own
    localhost endpoint. 36.33 x 1.10 = 39.96 -- without the table it reports 36.33, the $3.63
    shortfall that made today's tally disagree with the gateway's own refusal."""
    bt = _load(tmp_path, cache=_cache_entry(1.1), cap_usd=40.0)
    assert bt.CONNECTION["kind"] == "local"
    assert bt.is_free_session() is True
    assert bt.MARKUP == pytest.approx(1.1)
    assert "cached gateway price table" in bt.CONNECTION["fee_source"]


def test_free_session_gets_the_learned_cap_as_denominator(tmp_path):
    """Same fall-through one axis over: `_resolve()` reads cap.json only when kind == "gateway",
    so a free lane had no denominator at all and could not say "of $40 cap"."""
    bt = _load(tmp_path, cache=_cache_entry(1.1), cap_usd=40.0)
    assert bt.CAP_USD == pytest.approx(40.0)


def test_free_lane_line_attributes_the_spend_to_other_sessions(tmp_path, monkeypatch, capsys):
    """The reported line must say WHO owns the money: this session costs $0, the figure is the
    day's gateway spend from other sessions, and it carries no warning prefix."""
    monkeypatch.setattr(bt := _load(
        tmp_path, cache=_cache_entry(1.1), cap_usd=40.0, off_network=True,
        ledger={"a57e8a32": [TODAY, 35.43, 0],
                "free-session": [TODAY, 0, 0, 5.56, 0]}),
        "spawn_proxy_price_refresh", lambda: None)
    monkeypatch.setattr(bt, "spawn_price_sync", lambda *a, **k: None)
    bt.main_session_start()
    line = capsys.readouterr().out
    assert "this session costs $0" in line
    assert "other sessions" in line
    # The BILLED axis, derived from the module's own figures rather than hard-coded: the side
    # spend slice feeds recon_total too, so the number is not simply 35.43 x 1.10 (an earlier
    # version of this test asserted that and was wrong by $0.10).
    assert f"${bt.compute()[0] * bt.MARKUP:.2f}" in line
    assert "of $40 cap" in line
    assert "⚠️" not in line
    # The phantom savings axis must never be folded into the spend axis
    assert "saved" not in line


def test_free_session_with_no_ledger_entry_reports_no_spend_of_its_own(tmp_path):
    """A free session with usage today and no ledger entry must not reconstruct at Opus rates.
    Before the fix this printed `this session ≈ $X` for a session that costs $0 -- the per-session
    clause was computed OUTSIDE the local-lane skip that protects the aggregate."""
    records = [{"type": "assistant", "timestamp": TODAY + "T18:00:00Z",
                # A GATEWAY-shaped response id, not `msg_<24-hex>`: the per-message local regex
                # already catches this lane's `msg_<uuid>` ids (verified against this session's
                # own transcript), so using one here would test a path that is already protected and
                # never go red. `msg_bdrk_` is the shape a free lane whose proxy fronts Bedrock
                # returns -- invisible to the per-message heuristic, so only a session-level guard
                # catches it.
                "message": {"id": "msg_bdrk_01FREEHEADLESS",
                            "model": "claude-opus-5-5",   # spoofed: free compute, not Opus
                            "usage": {"input_tokens": 400000, "output_tokens": 200000,
                                      "cache_read_input_tokens": 250000,
                                      "cache_creation_input_tokens": 250000}}}]
    bt = _load(tmp_path, cache=_cache_entry(1.1), cap_usd=40.0,
               session_id="11111111-2222-3333-4444-555555555555",
               transcripts={"11111111-2222-3333-4444-555555555555": records})
    total, _, _, _, _, current, _, _ = bt.compute()
    assert current == 0.0, "a free lane invented its own spend from a spoofed cloud id"
    assert total == 0.0 or current == 0.0  # the guard, not the aggregate, is what is under test


def test_the_same_fixture_on_a_gateway_does_reconstruct(tmp_path):
    """PLANTED POSITIVE. The test above can only be trusted if the identical fixture, on a gateway
    endpoint, DOES produce spend -- otherwise "0.0" would be indistinguishable from a fixture that
    never priced anything at all."""
    records = [{"type": "assistant", "timestamp": TODAY + "T18:00:00Z",
                "message": {"id": "msg_bdrk_01FREEHEADLESS",
                            "model": "claude-opus-5-5",
                            "usage": {"input_tokens": 400000, "output_tokens": 200000,
                                      "cache_read_input_tokens": 250000,
                                      "cache_creation_input_tokens": 250000}}}]
    bt = _load(tmp_path, base_url="https://llmgw.joyia.p7s1.io", cache=_cache_entry(1.1),
               cap_usd=40.0, session_id="11111111-2222-3333-4444-555555555555",
               transcripts={"11111111-2222-3333-4444-555555555555": records})
    assert bt.CONNECTION["kind"] == "gateway"
    current = bt.compute()[5]
    assert current > 0.0, "the fixture must price spend on a gateway, or the free-lane test is vacuous"


def test_fee_override_is_honoured_off_the_gateway(tmp_path):
    """COST_TRACKER_FEE / COST_TRACKER_MARKUP are documented in --help and README as overrides,
    but were read only inside the `gateway` branch, so on a free lane they silently did nothing
    (measured: COST_TRACKER_MARKUP=1.10 printed an unchanged $36.47). Doc and code now agree."""
    bt = _load(tmp_path, cache=_cache_entry(1.1), cap_usd=40.0,
               env_extra={"COST_TRACKER_FEE": "1.05"})
    os.environ.pop("COST_TRACKER_FEE", None)
    assert bt.MARKUP == pytest.approx(1.05)
    assert bt.CONNECTION["fee_source"] == "env override"


def test_gateway_session_pricing_is_unchanged(tmp_path):
    """Regression guard: the gateway branch's own precedence (env > hand > price table > registry)
    must survive the new local branch."""
    bt = _load(tmp_path, base_url="https://llmgw.joyia.p7s1.io", cache=_cache_entry(1.1),
               cap_usd=40.0)
    assert bt.CONNECTION["kind"] == "gateway"
    assert bt.MARKUP == pytest.approx(1.1)
    assert bt.CAP_USD == pytest.approx(40.0)
    assert "gateway price table" in bt.CONNECTION["fee_source"]
