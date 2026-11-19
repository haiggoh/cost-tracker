import importlib.util
import json
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import load_ct

ROOT = pathlib.Path(__file__).resolve().parents[1]


def module():
    spec = importlib.util.spec_from_file_location('shared_costs', ROOT / 'bin/shared_costs.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def setup(tmp_path, monkeypatch):
    root = tmp_path / 'codex'
    (root / 'sessions').mkdir(parents=True)
    (root / 'config.toml').write_text('model_provider = "gateway"\n[model_providers.gateway]\nbase_url = "https://example.test/v1"\n')
    monkeypatch.setenv('COST_TRACKER_CODEX_HOME', str(root))
    monkeypatch.setenv('COST_TRACKER_LEDGER_DIR', str(tmp_path / 'ledger'))
    monkeypatch.setenv('COST_TRACKER_CONFIG_DIR', str(tmp_path / 'config'))
    return module(), root


def records(rid='one', stamp='2026-11-18T01:00:00Z', **usage):
    u = dict(input_tokens=100, cached_input_tokens=20, cache_write_input_tokens=10,
             output_tokens=30, reasoning_output_tokens=25)
    u.update(usage)
    return [dict(type='session_meta', payload=dict(id='s', model_provider='gateway')),
            dict(type='turn_context', payload=dict(model='gpt-test')),
            dict(type='token_usage_record', timestamp=stamp,
                 payload=dict(response_id=rid, usage=u))]


def write(root, name, rows):
    p = root / 'sessions' / name
    p.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    return p


def prices(mod):
    return mod.parse_prices({'data': [dict(model_name='gpt-test', model_info=dict(
        input_cost_per_token=2e-6, output_cost_per_token=10e-6,
        cache_read_input_token_cost=0.1e-6, cache_creation_input_token_cost=2.5e-6,
        input_cost_per_token_above_272k_tokens=4e-6,
        output_cost_per_token_above_272k_tokens=15e-6,
        cache_read_input_token_cost_above_272k_tokens=0.2e-6,
        cache_creation_input_token_cost_above_272k_tokens=5e-6))]})


def test_native_duplicate_resume_and_utc_days(tmp_path, monkeypatch):
    m, root = setup(tmp_path, monkeypatch)
    rows = records() + records('two', '2026-11-19T00:00:00Z')
    write(root, 'a.jsonl', rows)
    write(root, 'import.jsonl', rows)
    result = m.collect(['2026-11-18'], price_tables={'example.test': prices(m)})
    assert result['responses'] == 1
    assert result['codex_usd'] == pytest.approx(.000467)
    assert result['complete']
    assert m.collect(['2026-11-19'], price_tables={'example.test': prices(m)})['responses'] == 1


def test_reasoning_not_double_charged_and_context_tier(tmp_path, monkeypatch):
    m, root = setup(tmp_path, monkeypatch)
    write(root, 'a.jsonl', records(input_tokens=300000, cached_input_tokens=0,
                                 cache_write_input_tokens=0))
    r = m.collect(['2026-11-18'], price_tables={'example.test': prices(m)})
    assert r['codex_usd'] == pytest.approx(1.20045)


def test_partial_append_retried_and_cumulative_events_ignored(tmp_path, monkeypatch):
    m, root = setup(tmp_path, monkeypatch)
    rows = records()
    p = write(root, 'a.jsonl', rows[:-1])
    raw = json.dumps(rows[-1])
    with p.open('a') as f: f.write(raw[:20])
    assert m.collect(['2026-11-18'], price_tables={})['responses'] == 0
    with p.open('a') as f: f.write(raw[20:] + '\n')
    with p.open('a') as f: f.write(json.dumps(dict(type='event_msg', timestamp=rows[-1]['timestamp'],
        payload=dict(type='token_count', info=dict(total_token_usage=dict(input_tokens=999999)))))+'\n')
    assert m.collect(['2026-11-18'], price_tables={'example.test': prices(m)})['responses'] == 1


def test_missing_prices_and_invalid_usage_visible(tmp_path, monkeypatch):
    m, root = setup(tmp_path, monkeypatch)
    write(root, 'a.jsonl', records() + records('bad', input_tokens=-1))
    r = m.collect(['2026-11-18'], price_tables={})
    assert not r['complete']
    assert r['unpriced_responses'] == 1
    assert any('invalid usage' in x for x in r['diagnostics'])
    assert r['gateway_confirmed_usd'] is None
    assert r['key_scope_verified'] is False


def test_concurrent_importers_and_conflicting_duplicate(tmp_path, monkeypatch):
    m, root = setup(tmp_path, monkeypatch)
    write(root, 'a.jsonl', records())
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: m.collect(['2026-11-18'],
            price_tables={'example.test': prices(m)}), range(2)))
    assert all(r['responses'] == 1 and r['complete'] for r in results)
    write(root, 'conflict.jsonl', records(output_tokens=31))
    r = m.collect(['2026-11-18'], price_tables={'example.test': prices(m)})
    assert not r['complete']
    assert any('conflicting response' in x for x in r['diagnostics'])


def test_missing_explicit_root_fails(tmp_path, monkeypatch):
    m, root = setup(tmp_path, monkeypatch)
    monkeypatch.setenv('COST_TRACKER_CODEX_HOME', str(root / 'missing'))
    with pytest.raises(ValueError, match='COST_TRACKER_CODEX_HOME'):
        m.collect(['2026-11-18'], price_tables={})


def test_host_sum_and_same_store_from_either_host(tmp_path, monkeypatch):
    m, root = setup(tmp_path, monkeypatch)
    write(root, 'a.jsonl', records())
    r = m.collect(['2026-11-18'], price_tables={'example.test': prices(m)})
    claude = dict(billed_usd=1.25, quarantined=[], reset_anchored_sessions=[],
                  estimate_source='test')
    a = m.combine(claude, r)
    monkeypatch.setenv('ANTHROPIC_BASE_URL', 'http://localhost:8000')
    b = m.combine(claude, m.collect(['2026-11-18'], price_tables={'example.test': prices(m)}))
    assert a['combined_usd'] == b['combined_usd'] == round(1.25 + a['codex_usd'], 6)
    assert 'Claude Code' in m.render_table(a) and 'Codex' in m.render_table(a)
    assert a['combined_usd'] == a['claude_code_usd'] + a['codex_usd']


def test_old_native_missing_records_incomplete(tmp_path, monkeypatch):
    m, root = setup(tmp_path, monkeypatch)
    write(root, 'old.jsonl', [dict(type='event_msg', timestamp='2026-11-18T01:00:00Z',
        payload=dict(type='token_count', info=dict(last_token_usage=dict(input_tokens=100))))])
    r = m.collect(['2026-11-18'], price_tables={})
    assert not r['complete']
    assert any('no native response records' in x for x in r['diagnostics'])


def test_service_tier_missing_rate_and_exact_threshold(tmp_path, monkeypatch):
    m, root = setup(tmp_path, monkeypatch)
    rates = prices(m)['gpt-test']
    data = dict(usage=dict(input_tokens=272000, cached_input_tokens=0,
                          cache_write_input_tokens=0, output_tokens=1), tier='default')
    assert m._cost(data, rates) == pytest.approx(.54401)
    data['tier'] = 'priority'
    with pytest.raises(ValueError, match='published rate'):
        m._cost(data, rates)
    data['tier'] = 'flex'
    rates.update(input_cost_per_token_flex=1e-6, output_cost_per_token_flex=5e-6)
    assert m._cost(data, rates) == pytest.approx(.272005)


def test_malformed_record_flags_incomplete_and_store_private(tmp_path, monkeypatch):
    m, root = setup(tmp_path, monkeypatch)
    p = write(root, 'a.jsonl', records())
    with p.open('a') as f: f.write('{invalid json}\n')
    r = m.collect(['2026-11-18'], price_tables={'example.test': prices(m)})
    assert not r['complete'] and r['responses'] == 1
    assert pathlib.Path(r['ledger']).stat().st_mode & 0o777 == 0o600


def test_cli_json_and_statusline_use_shared_total(tmp_path, monkeypatch, capsys):
    ct, _ = load_ct(tmp_path, today='2026-11-18')
    m, root = setup(tmp_path, monkeypatch)
    write(root, 'a.jsonl', records())
    config = tmp_path / 'config'
    config.mkdir(exist_ok=True)
    (config / 'proxy-prices.json').write_text(json.dumps({'example.test': dict(response_models=prices(m))}))
    monkeypatch.setattr(ct, 'collect', lambda *a, **kw: dict(
        billed_usd=1.25, period_days_utc=['2026-11-18'], quarantined=[], reset_anchored_sessions=[]))
    pathlib.Path(ct.HISTORY_PATH).write_text('')
    assert ct.main(['report', '--combined', '--json']) == 0
    result = json.loads(capsys.readouterr().out)['shared']
    assert result['combined_usd'] == pytest.approx(1.250467)
    assert ct.main(['statusline', '--combined', '--fast']) == 0
    assert 'today combined estimate: $1.25' in capsys.readouterr().out
    (config / 'proxy-prices.json').write_text('{}')
    assert ct.main(['report', '--combined', '--json']) == 2
    assert not json.loads(capsys.readouterr().out)['shared']['complete']


def test_cli_invalid_flags_and_root_nonzero(tmp_path, monkeypatch):
    ct, _ = load_ct(tmp_path)
    with pytest.raises(SystemExit) as exc:
        ct.main(['report', '--refresh-prices'])
    assert exc.value.code == 2
    monkeypatch.setenv('COST_TRACKER_CODEX_HOME', '')
    assert ct.main(['report', '--combined']) == 2


def test_price_refresh_secure_adapter_preserves_host_identity(tmp_path, monkeypatch):
    m, root = setup(tmp_path, monkeypatch)
    monkeypatch.setenv('COST_TRACKER_CODEX_TOKEN', 'test-not-a-real-key')
    seen = {}
    class Proxy:
        def refresh(self, env, force=False):
            seen.update(env)
            assert force
            return {'response_models': prices(m)}
    m.refresh_prices(Proxy())
    assert seen['ANTHROPIC_AUTH_TOKEN'] == 'test-not-a-real-key'
    assert seen['ANTHROPIC_BASE_URL'] == 'https://example.test/v1'
    assert seen['COST_TRACKER_PROXY_PRICES_URL'] == 'https://example.test/model/info'


def test_imported_history_placeholder_is_not_new_spend(tmp_path, monkeypatch):
    m, root = setup(tmp_path, monkeypatch)
    write(root, 'history.jsonl', [dict(type='session_meta', payload=dict(id='import', history_mode='legacy')),
        dict(type='event_msg', timestamp='2026-11-18T01:00:00Z', payload=dict(type='token_count',
            info=dict(total_token_usage=dict(input_tokens=0, output_tokens=0,
                cached_input_tokens=0, cache_write_input_tokens=0, total_tokens=64000))))])
    result = m.collect(['2026-11-18'], price_tables={})
    assert result['complete'] and result['codex_usd'] == 0


def test_legacy_probe_delta_deduplicates_and_labels_estimate(tmp_path, monkeypatch):
    m, root = setup(tmp_path, monkeypatch)
    rows = records()[:2]
    token = dict(input_tokens=100, output_tokens=30, cached_input_tokens=20, cache_write_input_tokens=10)
    event = dict(type='event_msg', timestamp='2026-11-18T01:00:00Z',
                 payload=dict(type='token_count', info=dict(total_token_usage=token, last_token_usage=token)))
    rows += [event, event]
    write(root, 'probe.jsonl', rows)
    write(root, 'probe-copy.jsonl', rows)
    result = m.collect(['2026-11-18'], price_tables={'example.test': prices(m)})
    assert result['responses'] == result['legacy_estimated_responses'] == 1
    assert result['codex_usd'] == pytest.approx(.000467)
    assert not result['complete']


def test_native_replaces_legacy_instead_of_adding_it(tmp_path, monkeypatch):
    m, root = setup(tmp_path, monkeypatch)
    rows = records()
    rows += [dict(type='event_msg', timestamp=rows[-1]['timestamp'], payload=dict(type='token_count',
                 info=dict(total_token_usage=rows[-1]['payload']['usage'])))]
    write(root, 'a.jsonl', rows)
    result = m.collect(['2026-11-18'], price_tables={'example.test': prices(m)})
    assert result['responses'] == 1 and result['legacy_estimated_responses'] == 0
    assert result['codex_usd'] == pytest.approx(.000467)


def test_legacy_reset_to_zero_does_not_lose_second_identical_request(tmp_path, monkeypatch):
    m, root = setup(tmp_path, monkeypatch)
    token = dict(input_tokens=100, output_tokens=30, cached_input_tokens=20, cache_write_input_tokens=10)
    def event(value):
        return dict(type='event_msg', timestamp='2026-11-18T01:00:00Z', payload=dict(type='token_count',
            info=dict(total_token_usage=value, last_token_usage=value)))
    write(root, 'a.jsonl', records()[:2] + [event(token), event({k: 0 for k in token}), event(token)])
    result = m.collect(['2026-11-18'], price_tables={'example.test': prices(m)})
    assert result['responses'] == 2
    assert result['codex_usd'] == pytest.approx(.000934)


def test_missing_claude_inputs_cannot_claim_complete_zero(tmp_path, monkeypatch, capsys):
    ct, _ = load_ct(tmp_path, today='2026-11-18', make_dirs=False)
    m, root = setup(tmp_path, monkeypatch)
    write(root, 'placeholder.jsonl', [])
    monkeypatch.setattr(ct, 'collect', lambda *a, **kw: dict(
        billed_usd=0, period_days_utc=['2026-11-18'], quarantined=[], reset_anchored_sessions=[]))
    assert ct.main(['report', '--combined', '--json']) == 2
    result = json.loads(capsys.readouterr().out)['shared']
    assert result['claude_inputs_available'] is False
    assert 'Claude ledger/history/transcript inputs unavailable' in result['diagnostics']


def test_canonical_gpt_rows_ignore_claude_markup_and_keep_claude_inference(tmp_path, monkeypatch):
    m, root = setup(tmp_path, monkeypatch)
    write(root, 'a.jsonl', records())
    cfg = tmp_path / 'config'
    cfg.mkdir()
    (cfg / 'proxy-prices.json').write_text(json.dumps({'example.test': {
        'proxy': True, 'markup': 1.1, 'response_models': prices(m), 'fetched_at': 1}}))
    codex = m.collect(['2026-11-18'])
    assert codex['codex_usd'] == pytest.approx(.000467)
    assert codex['price_sources']['example.test'] == 'canonical model/info table'
    assert 'Codex pricing: canonical model/info table' in m.render_table(m.combine(
        dict(billed_usd=0, markup=1, connection='gateway'), codex))
    for key in ('ANTHROPIC_BASE_URL', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN'):
        monkeypatch.delenv(key, raising=False)
    claude = dict(billed_usd=2.0, markup=1.0, connection='first-party',
                  quarantined=[], reset_anchored_sessions=[])
    combined = m.combine(claude, codex)
    assert combined['claude_code_usd'] == pytest.approx(2.2)
    assert combined['claude_gateway_inferred'] is True
    already_adjusted = dict(claude, billed_usd=2.2, markup=1.1, connection='gateway')
    assert m.combine(already_adjusted, codex)['claude_code_usd'] == pytest.approx(2.2)
    assert combined['combined_usd'] == round(2.2 + codex['codex_usd'], 6)
    for key in ('ANTHROPIC_BASE_URL', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN'):
        with monkeypatch.context() as explicit:
            explicit.setenv(key, 'explicit-connection-fixture')
            direct = m.combine(claude, codex)
            assert direct['claude_code_usd'] == pytest.approx(2.0)
            assert direct['claude_gateway_inferred'] is False


def test_invalid_claude_markup_does_not_discard_canonical_gpt_price(tmp_path, monkeypatch):
    m, root = setup(tmp_path, monkeypatch)
    write(root, 'a.jsonl', records())
    cfg = tmp_path / 'config'; cfg.mkdir()
    (cfg / 'proxy-prices.json').write_text(json.dumps({'example.test': {
        'proxy': True, 'markup': -1, 'response_models': prices(m)}}))
    result = m.collect(['2026-11-18'])
    assert not result['complete']
    assert result['unpriced_responses'] == 0
    assert result['codex_usd'] == pytest.approx(.000467)
    assert any('invalid gateway markup' in d for d in result['diagnostics'])


def test_custom_canonical_gpt_row_and_price_url_are_preserved(tmp_path, monkeypatch):
    m, root = setup(tmp_path, monkeypatch)
    write(root, "a.jsonl", records())
    cfg = tmp_path / "config"; cfg.mkdir()
    rows = prices(m)
    rows["gpt-test"]["input_cost_per_token"] = 9e-6
    (cfg / "proxy-prices.json").write_text(json.dumps({"example.test": {
        "proxy": True, "markup": 1.25, "response_models": rows,
        "source_url": "https://example.test/custom/model/info"}}))
    result = m.collect(["2026-11-18"])
    assert result["codex_usd"] == pytest.approx(.000957)
    assert result["price_urls"] == {"example.test": "https://example.test/custom/model/info"}
    assert result["price_sources"] == {"example.test": "canonical model/info table"}
