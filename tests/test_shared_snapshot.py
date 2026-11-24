"""Deliverable-A statusline hotfix: ONE cached shared-snapshot consumed by the ordinary
statusline, the CLI and (later) the panel.

Named discriminants (plan §Named contract discriminants):
  test_normal_statusline_uses_shared_scope
  test_warning_cli_report_share_snapshot_generation
  test_fast_snapshot_does_not_scan_or_infer
"""
import importlib.util
import io
import json
import pathlib
import time

import pytest
from conftest import load_ct

ROOT = pathlib.Path(__file__).resolve().parents[1]

SCHEMA = "cost-tracker/shared-snapshot/1"
SCOPE = {"id": "test-shared", "label": "JoyIA shared key"}


def snapshot_mod():
    spec = importlib.util.spec_from_file_location(
        'shared_snapshot', ROOT / 'bin' / 'shared_snapshot.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def codex_fixture(tmp_path):
    """One native Codex response worth $0.000467 on the canonical gpt-test row."""
    root = tmp_path / 'codex'
    (root / 'sessions').mkdir(parents=True)
    (root / 'config.toml').write_text(
        'model_provider = "gateway"\n[model_providers.gateway]\n'
        'base_url = "https://example.test/v1"\n')
    usage = dict(input_tokens=50000, cached_input_tokens=0, cache_write_input_tokens=0,
                 output_tokens=0, reasoning_output_tokens=0)
    rows = [dict(type='session_meta', payload=dict(id='s', model_provider='gateway')),
            dict(type='turn_context', payload=dict(model='gpt-test')),
            dict(type='token_usage_record', timestamp='2026-11-18T01:00:00Z',
                 payload=dict(response_id='one', usage=usage))]
    (root / 'sessions' / 'a.jsonl').write_text(
        ''.join(json.dumps(r) + '\n' for r in rows))
    return root


def prices():
    return {'gpt-test': dict(
        input_cost_per_token=2e-6, output_cost_per_token=10e-6,
        cache_read_input_token_cost=0.1e-6, cache_creation_input_token_cost=2.5e-6,
        input_cost_per_token_above_272k_tokens=4e-6,
        output_cost_per_token_above_272k_tokens=15e-6,
        cache_read_input_token_cost_above_272k_tokens=0.2e-6,
        cache_creation_input_token_cost_above_272k_tokens=5e-6)}


def setup(tmp_path, monkeypatch):
    ct, ledger = load_ct(tmp_path, today='2026-11-18')
    monkeypatch.setenv('COST_TRACKER_LEDGER_DIR', str(ledger))
    config = tmp_path / 'config'
    config.mkdir(exist_ok=True)
    (config / 'proxy-prices.json').write_text(
        json.dumps({'example.test': {'response_models': prices()}}))
    (config / 'budget-scopes.json').write_text(json.dumps({'scopes': [SCOPE]}))
    monkeypatch.setenv('COST_TRACKER_CODEX_HOME', str(codex_fixture(tmp_path)))
    pathlib.Path(ct.HISTORY_PATH).write_text('')
    return ct, config


def snapshot_path(config, scope_id='test-shared', day='2026-11-18'):
    return config / 'shared-snapshots' / (scope_id + '-' + day + '.json')


def feed(monkeypatch, payload):
    monkeypatch.setattr('sys.stdin', io.StringIO(payload))


def test_snapshot_schema_contract(tmp_path, monkeypatch):
    """build_snapshot carries every contract field; gateway evidence wins the primary
    amount only when it exists, and headroom stays null without it."""
    m = snapshot_mod()
    snap = m.build_snapshot(
        SCOPE, '2026-11-18', claude_billed=1.25,
        shared_result=dict(codex_usd=0.000467, responses=1, unpriced_responses=0,
                           diagnostics=[], complete=True, price_sources={'example.test': 'x'},
                           gateway_confirmed_usd=None, reserved_spend_usd=None),
        cap_usd=40.0)
    assert snap['schema'] == SCHEMA
    assert snap['scope_id'] == 'test-shared' and snap['day_utc'] == '2026-11-18'
    assert snap['estimated_usd'] == '1.250467'
    assert snap['by_host']['claude_code'] == pytest.approx(1.25)
    assert snap['by_host']['codex'] == pytest.approx(0.000467)
    assert snap['by_host']['claude_desktop'] is None
    assert snap['gateway_confirmed_usd'] is None and snap['reserved_usd'] is None
    assert snap['headroom_usd'] is None and snap['discrepancy_usd'] is None
    assert snap['budget_state'] == 'unknown'
    assert snap['primary_basis'].startswith('local estimate')
    assert snap['coverage']['complete'] is True
    # A same-day gateway observation at/over the cap flips budget_state and the primary
    # basis, and still implies NO reassuring headroom.
    confirmed = dict(snap, gateway_confirmed_usd='40.03006327', gateway_observed_at='x',
                     gateway_source='native refusal')
    s2 = m.build_snapshot(
        SCOPE, '2026-11-18', claude_billed=1.25,
        shared_result=dict(codex_usd=0.000467, responses=1, unpriced_responses=0,
                           diagnostics=[], complete=True, price_sources={},
                           gateway_confirmed_usd='40.03006327',
                           reserved_spend_usd=None, gateway_observed_at='x',
                           gateway_source='native refusal'),
        cap_usd=40.0)
    assert s2['budget_state'] == 'cap-reached'
    assert s2['headroom_usd'] is None
    assert s2['primary_basis'].startswith('gateway observation')
    assert s2['discrepancy_usd'] is None  # no matched-cutoff comparison evidence
    del confirmed


def test_producer_writes_cache_and_statusline_reads_it(tmp_path, monkeypatch, capsys):
    ct, config = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(ct, 'collect', lambda *a, **kw: dict(
        billed_usd=1.25, period_days_utc=['2026-11-18'], quarantined=[],
        reset_anchored_sessions=[], cap_usd=40.0, local_saved_usd=0.0,
        connection='gateway', projected_usd=None))
    assert ct.main(['report', '--combined', '--json']) == 0
    p = snapshot_path(config)
    assert p.is_file()
    snap = json.loads(p.read_text())
    assert snap['schema'] == SCHEMA and snap['scope_id'] == 'test-shared'
    assert snap['estimated_usd'] == '1.350000'
    assert snap['cap_usd'] == 40.0


def test_normal_statusline_uses_shared_scope(tmp_path, monkeypatch, capsys):
    """The DEFAULT ordinary fast render (no --combined anywhere) shows the shared-scope
    day amount from the cached snapshot, not the Claude-only figure."""
    ct, config = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(ct, 'collect', lambda *a, **kw: dict(
        billed_usd=1.25, period_days_utc=['2026-11-18'], quarantined=[],
        reset_anchored_sessions=[], cap_usd=40.0, local_saved_usd=0.0,
        connection='gateway', projected_usd=None))
    assert ct.main(['report', '--combined', '--json']) == 0
    capsys.readouterr()
    payload = json.dumps({'model': {'display_name': 'Opus 5'},
                          'cost': {'total_cost_usd': 30.9}})
    feed(monkeypatch, payload)
    assert ct.main(['statusline', '--fast', '--payload', '-']) == 0
    out = capsys.readouterr().out
    assert 'today: $1.35/$40' in out          # combined figure, not 30.90
    assert 'shared' in out                    # labelled: it is the shared-access amount


def test_warning_cli_report_share_snapshot_generation(tmp_path, monkeypatch, capsys):
    """Report and the ordinary statusline consume the SAME cache generation; the fast
    read never rewrites the snapshot."""
    ct, config = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(ct, 'collect', lambda *a, **kw: dict(
        billed_usd=1.25, period_days_utc=['2026-11-18'], quarantined=[],
        reset_anchored_sessions=[], cap_usd=40.0, local_saved_usd=0.0,
        connection='gateway', projected_usd=None))
    assert ct.main(['report', '--combined', '--json']) == 0
    p = snapshot_path(config)
    before = json.loads(p.read_text())
    capsys.readouterr()
    payload = json.dumps({'model': {'display_name': 'Opus 5'},
                          'cost': {'total_cost_usd': 30.9}})
    feed(monkeypatch, payload)
    assert ct.main(['statusline', '--fast', '--payload', '-']) == 0
    capsys.readouterr()
    after = json.loads(p.read_text())
    assert after['snapshot_id'] == before['snapshot_id']
    assert after['last_refreshed_at'] == before['last_refreshed_at']


def test_fast_snapshot_does_not_scan_or_infer(tmp_path, monkeypatch, capsys):
    """--combined --fast answers from cache only: with the Codex home GONE it still
    prints the cached combined figure and exits 0 (a live import would fail). Bounded:
    the whole call returns in well under a full import."""
    ct, config = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(ct, 'collect', lambda *a, **kw: dict(
        billed_usd=1.25, period_days_utc=['2026-11-18'], quarantined=[],
        reset_anchored_sessions=[], cap_usd=40.0, local_saved_usd=0.0,
        connection='gateway', projected_usd=None))
    assert ct.main(['report', '--combined', '--json']) == 0
    capsys.readouterr()
    monkeypatch.setenv('COST_TRACKER_CODEX_HOME', str(tmp_path / 'codex-gone'))
    t0 = time.monotonic()
    assert ct.main(['statusline', '--combined', '--fast']) == 0
    elapsed = time.monotonic() - t0
    out = capsys.readouterr().out
    assert 'today combined estimate: $1.35' in out
    # Bounded well under the 20 s full import this path must never trigger; the
    # plan's 400 ms target is met in isolation (measured ~0.1 s), but the full
    # gate runs this file under `parallel -j 12` + `pytest -n 8`, where CPU
    # contention alone pushed a cache-only read to 0.68 s — so the assertion
    # guards "no import" (20 s), not scheduler noise. The no-scan proof above
    # (deleted Codex home still exits 0 with the cached figure) is structural.
    assert elapsed < 2.0, f'fast combined took {elapsed:.3f}s'


def test_fast_combined_without_cache_is_incomplete_not_zero(tmp_path, monkeypatch, capsys):
    ct, config = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(ct, 'collect', lambda *a, **kw: dict(
        billed_usd=1.25, period_days_utc=['2026-11-18'], quarantined=[],
        reset_anchored_sessions=[], cap_usd=40.0, local_saved_usd=0.0,
        connection='gateway', projected_usd=None))
    assert ct.main(['statusline', '--combined', '--fast']) == 2
    out = capsys.readouterr().out
    assert 'INCOMPLETE' in out
    assert '$0.00' not in out.split('\n')[0]


def test_invalid_cache_is_visible_not_crash(tmp_path, monkeypatch, capsys):
    ct, config = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(ct, 'collect', lambda *a, **kw: dict(
        billed_usd=1.25, period_days_utc=['2026-11-18'], quarantined=[],
        reset_anchored_sessions=[], cap_usd=40.0, local_saved_usd=0.0,
        connection='gateway', projected_usd=None))
    p = snapshot_path(config)
    p.parent.mkdir(parents=True)
    p.write_text('{corrupt')
    assert ct.main(['statusline', '--combined', '--fast']) == 2
    assert 'INCOMPLETE' in capsys.readouterr().out


def test_explicit_unknown_scope_fails_loudly(tmp_path, monkeypatch, capsys):
    ct, config = setup(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as exc:
        ct.main(['report', '--combined', '--scope', 'nope', '--json'])
    assert exc.value.code == 2


def test_stale_snapshot_is_labelled_not_dropped(tmp_path, monkeypatch, capsys):
    """A snapshot older than the TTL is still shown (labelled stale) and arms a
    refresh — it never disappears and is never presented as fresh."""
    ct, config = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(ct, 'collect', lambda *a, **kw: dict(
        billed_usd=1.25, period_days_utc=['2026-11-18'], quarantined=[],
        reset_anchored_sessions=[], cap_usd=40.0, local_saved_usd=0.0,
        connection='gateway', projected_usd=None))
    assert ct.main(['report', '--combined', '--json']) == 0
    p = snapshot_path(config)
    snap = json.loads(p.read_text())
    snap['last_refreshed_at'] = '2020-01-01T00:00:00+00:00'
    p.write_text(json.dumps(snap))
    capsys.readouterr()
    assert ct.main(['statusline', '--combined', '--fast']) == 0
    out = capsys.readouterr().out
    assert 'today combined estimate: $1.35' in out
    assert '(stale)' in out
    m = snapshot_mod()
    assert m.is_stale(snap) is True
    snap['last_refreshed_at'] = '2999-01-01T00:00:00+00:00'
    assert m.is_stale(snap) is False
