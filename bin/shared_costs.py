#!/usr/bin/env python3
"""Shared Claude Code/Codex accounting, imported by cost-tracker.

Run cost-tracker report --combined [--refresh-prices] [--json]. No inference.
Environment: COST_TRACKER_CODEX_HOME (default ~/.codex), COST_TRACKER_LEDGER_DIR
(default ~/.claude/cost-ledger), COST_TRACKER_CONFIG_DIR, COST_TRACKER_CODEX_TOKEN
(optional price-route credential; otherwise Codex provider env_key).
"""
import hashlib
import json
import math
import os
import pathlib
import re
import sqlite3
import time
import tomllib
from datetime import datetime, timezone
from urllib.parse import urlparse

FIELDS = {'input': 'input_cost_per_token', 'output': 'output_cost_per_token',
          'cache_read': 'cache_read_input_token_cost',
          'cache_write': 'cache_creation_input_token_cost'}


def parse_prices(doc):
    """Retain published token rates, context tiers and service tiers verbatim."""
    out = {}
    if not isinstance(doc, dict) or not isinstance(doc.get('data'), list):
        return out
    for row in doc['data']:
        if not isinstance(row, dict):
            continue
        name, info = row.get('model_name'), row.get('model_info')
        if not isinstance(name, str) or not name.startswith('gpt-') or not isinstance(info, dict):
            continue
        out[name] = {k: v for k, v in info.items()
                     if any(k.startswith(f) for f in FIELDS.values())
                     and isinstance(v, (float, int)) and not isinstance(v, bool)
                     and math.isfinite(v) and v >= 0}
    return out


def _root():
    override = os.environ.get('COST_TRACKER_CODEX_HOME')
    if override is not None and not override.strip():
        raise ValueError('COST_TRACKER_CODEX_HOME must not be empty')
    root = pathlib.Path(override).expanduser() if override is not None else pathlib.Path.home() / '.codex'
    if not root.is_dir():
        if override is not None:
            raise ValueError('COST_TRACKER_CODEX_HOME is not an existing directory')
        return root, {}, ['Codex home unavailable']
    try:
        config = tomllib.loads((root / 'config.toml').read_text())
    except (OSError, ValueError):
        config = {}
    return root, config, []


def _db():
    ledger = pathlib.Path(os.environ.get('COST_TRACKER_LEDGER_DIR', '~/.claude/cost-ledger')).expanduser()
    directory = ledger / 'codex'
    if directory.is_symlink():
        raise ValueError('shared usage directory must not be a symlink')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    dbpath = directory / 'usage.sqlite3'
    if dbpath.is_symlink():
        raise ValueError('shared usage database must not be a symlink')
    # Exclusive creation keeps usage private before SQLite first opens the file.
    try:
        fd = os.open(dbpath, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        pass
    conn = sqlite3.connect(dbpath, timeout=10)
    conn.execute('CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, inode INTEGER, offset INTEGER, context TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS responses (id TEXT PRIMARY KEY, day TEXT, data TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS legacy (id TEXT PRIMARY KEY, day TEXT, data TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS issues (source TEXT, day TEXT, reason TEXT, UNIQUE(source, day, reason))')
    conn.commit()
    return conn, str(dbpath)


def _day(stamp):
    dt = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('usage timestamp lacks timezone')
    return dt.astimezone(timezone.utc).date().isoformat()


def _usage(value):
    if not isinstance(value, dict):
        raise ValueError('invalid usage object')
    out = {}
    for field in ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens', 'output_tokens'):
        v = value.get(field, 0 if field.startswith(('cached_', 'cache_write_')) else None)
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ValueError('invalid usage token count')
        out[field] = v
    if out['cached_input_tokens'] + out['cache_write_input_tokens'] > out['input_tokens']:
        raise ValueError('invalid usage: cache tokens exceed input')
    return out


def _import(conn, root, config):
    contexts = []
    providers = config.get('model_providers', {})
    files = sorted(set(root.glob('sessions/**/*.jsonl')) | set(root.glob('archived_sessions/**/*.jsonl')))
    deadline = time.monotonic() + 20
    for p in files:
        if time.monotonic() > deadline:
            raise TimeoutError('Codex import exceeded 20 seconds; rerun to continue')
        stat = p.stat()
        old = conn.execute('SELECT inode, offset, context FROM files WHERE path=?', (str(p),)).fetchone()
        offset = old[1] if old and old[0] == stat.st_ino and old[1] <= stat.st_size else 0
        ctx = json.loads(old[2]) if offset and old else {}
        if ctx.get('reader_version') != 3:
            offset = 0
            ctx = {'provider': '', 'model': '', 'session': '', 'native_days': [],
                   'event_days': [], 'reader_version': 3, 'generation': 0}
        with p.open('rb') as f:
            f.seek(offset)
            while True:
                line = f.readline()
                if not line or not line.endswith(b'\n'):
                    break  # Never consume a partial record written by an active session.
                offset = f.tell()
                if time.monotonic() > deadline:
                    raise TimeoutError('Codex import exceeded 20 seconds; no partial transaction committed')
                r = None
                try:
                    r = json.loads(line)
                    if not isinstance(r, dict):
                        raise ValueError('invalid native record')
                    payload = r.get('payload') or {}
                    if not isinstance(payload, dict):
                        continue
                    kind = r.get('type')
                    if kind == 'session_meta':
                        ctx['session'] = payload.get('session_id') or payload.get('id') or ctx['session']
                        ctx['provider'] = payload.get('model_provider') or ctx['provider']
                    elif kind == 'turn_context':
                        ctx['model'] = payload.get('model') or ctx['model']
                        ctx['provider'] = payload.get('model_provider') or ctx['provider']
                        ctx['tier'] = payload.get('service_tier') or 'default'
                    elif kind == 'event_msg' and payload.get('type') == 'token_count' and payload.get('info'):
                        day = _day(r['timestamp'])
                        info = payload['info']
                        cumulative = info.get('total_token_usage') or {}
                        # Imported history stubs have zero billable categories and an informational
                        # total_tokens count. They are not a newly billed response.
                        visible_usage = cumulative or info.get('last_token_usage') or {}
                        if not any(isinstance(visible_usage.get(k), int) and visible_usage[k] > 0
                                   for k in ('input_tokens', 'output_tokens')):
                            if any((ctx.get('previous_usage') or {}).get(k, 0) > 0
                                   for k in ('input_tokens', 'output_tokens')):
                                ctx['generation'] += 1
                            ctx['previous_usage'] = cumulative
                            continue
                        if day not in ctx['event_days']: ctx['event_days'].append(day)
                        try:
                            cumulative = _usage(cumulative)
                            previous = ctx.get('previous_usage') or {k: 0 for k in cumulative}
                            reset = any(cumulative[k] < previous.get(k, 0) for k in cumulative)
                            if reset:
                                ctx['generation'] += 1
                                usage = _usage(info.get('last_token_usage'))
                            else:
                                usage = {k: cumulative[k] - previous.get(k, 0) for k in cumulative}
                                usage = _usage(usage)
                            ctx['previous_usage'] = cumulative
                            if not usage['input_tokens'] and not usage['output_tokens']:
                                continue
                            provider = ctx['provider']
                            endpoint = (providers.get(provider) or {}).get('base_url') or (
                                'https://api.openai.com/v1' if provider == 'openai' else '')
                            signature = json.dumps([ctx['session'], ctx['generation'], cumulative], sort_keys=True)
                            rid = 'legacy:' + hashlib.sha256(signature.encode()).hexdigest()
                            data = dict(usage=usage, model=ctx['model'], provider=provider,
                                        endpoint=endpoint, tier=ctx.get('tier', 'default'), session=ctx['session'])
                            conn.execute('INSERT OR IGNORE INTO legacy VALUES (?,?,?)',
                                         (rid, day, json.dumps(data, sort_keys=True)))
                        except (TypeError, ValueError):
                            # Missing categories are not zero; preserve the visible coverage warning.
                            pass
                    elif kind == 'token_usage_record':
                        day = _day(r['timestamp'])
                        if day not in ctx['native_days']: ctx['native_days'].append(day)
                        rid = payload.get('response_id')
                        if not isinstance(rid, str) or not rid:
                            raise ValueError('invalid usage: response_id missing')
                        usage = _usage(payload.get('usage'))
                        provider = ctx['provider']
                        provider_config = providers.get(provider) or {}
                        endpoint = provider_config.get('base_url') or ('https://api.openai.com/v1' if provider == 'openai' else '')
                        data = dict(usage=usage, model=payload.get('model') or ctx['model'],
                                    provider=provider, endpoint=endpoint,
                                    tier=payload.get('service_tier') or ctx.get('tier', 'default'),
                                    session=payload.get('session_id') or ctx['session'])
                        raw = json.dumps(data, sort_keys=True)
                        existing = conn.execute('SELECT day, data FROM responses WHERE id=?', (rid,)).fetchone()
                        previous = json.loads(existing[1]) if existing else None
                        conflict = existing and (existing[0] != day or any(
                            previous.get(k) != data.get(k) for k in ('usage', 'model', 'tier')))
                        if conflict:
                            conn.execute('INSERT OR IGNORE INTO issues VALUES (?,?,?)',
                                         (str(p), day, 'conflicting response id'))
                        else:
                            conn.execute('INSERT OR IGNORE INTO responses VALUES (?,?,?)', (rid, day, raw))
                except (ValueError, KeyError, TypeError, UnicodeError):
                    stamp = r.get('timestamp', '') if isinstance(r, dict) else ''
                    try: day = _day(stamp)
                    except (ValueError, TypeError): day = '*'
                    reason = 'invalid usage or malformed native record'
                    conn.execute('INSERT OR IGNORE INTO issues VALUES (?,?,?)', (str(p), day, reason))
        conn.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?)',
                     (str(p), stat.st_ino, offset, json.dumps(ctx)))
        contexts.append(ctx)
    return contexts, len(files)


def _cost(data, rates):
    u, tier = data['usage'], data['tier']
    if tier not in ('default', 'auto', 'standard', 'flex', 'priority', 'balanced', 'ultrafast'):
        raise ValueError('unknown service tier')
    suffix = '' if tier in ('default', 'auto', 'standard') else '_' + tier
    counts = dict(input=u['input_tokens']-u['cached_input_tokens']-u['cache_write_input_tokens'],
                  output=u['output_tokens'], cache_read=u['cached_input_tokens'],
                  cache_write=u['cache_write_input_tokens'])
    total = 0.0
    for category, count in counts.items():
        if not count:
            continue
        field = FIELDS[category]
        thresholds = sorted(int(n) for k in rates for n in re.findall(
            '^' + re.escape(field) + r'_above_(\d+)k_tokens' + re.escape(suffix) + '$', k))
        applicable = [n for n in thresholds if u['input_tokens'] > n * 1000]
        key = field + ('_above_' + str(max(applicable)) + 'k_tokens' if applicable else '') + suffix
        if key not in rates:
            raise ValueError('missing published rate for ' + category + '/' + tier)
        total += count * rates[key]
    return total


def _gateway_markup(entry):
    """GPT base rates use the same learned regional premium as Claude on the gateway."""
    if not entry.get('proxy'):
        return 1.0
    value = entry.get('markup')
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or value <= 0):
        raise ValueError('invalid gateway markup')
    return float(value)


def collect(days, price_tables=None):
    """Refresh append-only usage, then price unique responses for requested UTC dates."""
    root, config, diagnostics = _root()
    days = sorted(set(days))
    if not days:
        raise ValueError('combined report requires UTC dates')
    conn, dbpath = _db()
    try:
        conn.execute('BEGIN IMMEDIATE')
        contexts, file_count = _import(conn, root, config)
        conn.commit()
        if not file_count:
            diagnostics.append('no Codex transcripts available')
        for ctx in contexts:
            if set(ctx['event_days']).intersection(days) - set(ctx['native_days']):
                diagnostics.append('no native response records for some Codex usage')
        placeholders = ','.join('?' for _ in days)
        rows = conn.execute('SELECT id, day, data FROM responses WHERE day IN (' + placeholders + ')', days).fetchall()
        native_sessions = {json.loads(raw)['session'] for (raw,) in conn.execute('SELECT data FROM responses')}
        legacy_rows = conn.execute('SELECT id, day, data FROM legacy WHERE day IN (' + placeholders + ')', days).fetchall()
        legacy_rows = [r for r in legacy_rows if json.loads(r[2])['session'] not in native_sessions]
        rows.extend(legacy_rows)
        diagnostics.extend(row[0] for row in conn.execute(
            'SELECT DISTINCT reason FROM issues WHERE day IN (' + placeholders + ') OR day="*"', days))
    finally:
        conn.close()
    cache = {}
    if price_tables is None:
        try:
            cache = json.loads((pathlib.Path(os.environ.get('COST_TRACKER_CONFIG_DIR', '~/.claude/cost-tracker')).expanduser() / 'proxy-prices.json').read_text())
        except (OSError, ValueError):
            cache = {}
        price_tables = {host: entry.get('response_models', {}) for host, entry in cache.items()
                        if isinstance(entry, dict)}
        stamps = {host: entry.get('fetched_at') for host, entry in cache.items() if isinstance(entry, dict)}
    else:
        stamps = {}
    total, unpriced, endpoints = 0.0, 0, set()
    gateway_markups = {}
    by_day, by_session = {}, {}
    for rid, day, raw in rows:
        data = json.loads(raw)
        endpoint = data['endpoint']
        host = urlparse(endpoint).hostname or ''
        endpoints.add(endpoint or 'unresolved provider:' + data['provider'])
        if host in ('localhost', '127.0.0.1', '::1'):
            cost = 0.0
        else:
            rates = price_tables.get(host, {}).get(data['model'])
            try:
                if not rates: raise ValueError('missing published model price')
                factor = _gateway_markup(cache.get(host, {}))
                cost = _cost(data, rates) * factor
                if cache.get(host, {}).get('proxy'):
                    gateway_markups[host] = factor
            except ValueError as e:
                unpriced += 1
                diagnostics.append(str(e) + ': ' + data['model'])
                continue
        total += cost
        by_day[day] = by_day.get(day, 0.0) + cost
        sid = data['session']
        by_session[sid] = by_session.get(sid, 0.0) + cost
    return dict(codex_usd=round(total, 6), responses=len(rows), unpriced_responses=unpriced,
                legacy_estimated_responses=len(legacy_rows), gateway_markups=gateway_markups,
                days_utc=days, by_day=by_day, by_session=by_session,
                complete=not diagnostics, diagnostics=sorted(set(diagnostics)),
                refreshed_at=datetime.now(timezone.utc).isoformat(), price_fetched_at=stamps,
                ledger=dbpath, endpoints=sorted(endpoints), key_scope_verified=False,
                scope='all locally available Codex responses; historical endpoints inferred from current provider config; key membership unverified',
                basis='native response tokens x published GPT base rates x learned gateway regional markup; legacy cumulative deltas separately estimated', gateway_confirmed_usd=None,
                reserved_spend_usd=None)


def refresh_prices(proxy_prices):
    """Extend the existing per-host price cache; credentials never enter argv."""
    root, config, diagnostics = _root()
    provider = config.get('model_provider', 'openai')
    settings = (config.get('model_providers') or {}).get(provider) or {}
    base = settings.get('base_url') or ('https://api.openai.com/v1' if provider == 'openai' else '')
    if not base:
        raise ValueError('Codex provider endpoint unresolved')
    env = dict(os.environ)
    token = env.get('COST_TRACKER_CODEX_TOKEN') or env.get(settings.get('env_key') or 'OPENAI_API_KEY', '')
    env['ANTHROPIC_BASE_URL'] = base
    env['ANTHROPIC_AUTH_TOKEN'] = token
    env.pop('ANTHROPIC_API_KEY', None)
    env['COST_TRACKER_PROXY_PRICES_URL'] = base.rstrip('/').removesuffix('/v1') + '/model/info'
    entry = proxy_prices.refresh(env, force=True)
    if not entry or entry.get('last_error') or not entry.get('response_models'):
        raise ValueError('Codex price refresh failed; prior cache retained')
    return entry


def combine(claude, codex):
    result = dict(codex)
    result['claude_code_usd'] = claude['billed_usd']
    result['claude_gateway_factor'] = claude.get('markup', 1.0)
    result['claude_gateway_inferred'] = False
    factors = codex.get('gateway_markups', {})
    if (len(factors) == 1 and claude.get('connection') == 'first-party'
            and not any(os.environ.get(k) for k in
                        ('ANTHROPIC_BASE_URL', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN'))):
        # Codex does not inherit Claude's launcher env. Infer the common gateway only
        # when the available Codex records resolve to one priced gateway; expose the inference.
        factor = next(iter(factors.values()))
        result['claude_code_usd'] = round(claude['billed_usd'] / claude.get('markup', 1.0) * factor, 6)
        result['claude_gateway_factor'] = factor
        result['claude_gateway_inferred'] = True
        result['complete'] = False
        result['diagnostics'] = list(result['diagnostics']) + ['Claude gateway inferred from sole priced Codex gateway']
    result['combined_usd'] = round(result['claude_code_usd'] + result['codex_usd'], 6)
    result['claude_basis'] = 'existing Claude ledger + uncovered estimates, gateway factor applied'
    if claude.get('quarantined') or claude.get('reset_anchored_sessions'):
        result['complete'] = False
        result['diagnostics'].append('Claude records quarantined or resume floor present')
    result['scope'] += '; Claude ledger has no endpoint/key attribution'
    return result


def render_table(data):
    days = ', '.join(data['days_utc'])
    lines = ['Combined token-derived estimate (UTC ' + days + ')',
             'Claude Code USD | Codex USD | Combined USD',
             f"{data['claude_code_usd']:.6f} | {data['codex_usd']:.6f} | {data['combined_usd']:.6f}",
             'Refreshed: ' + data['refreshed_at'],
             'Gateway factors: Claude ' + str(data['claude_gateway_factor']) + '; Codex ' + str(data.get('gateway_markups', {})),
             'Coverage: ' + ('available records priced' if data['complete'] else 'INCOMPLETE'),
             'Scope: ' + data['scope'],
             'Gateway-confirmed billing and reserved spend: unknown']
    lines.extend('Warning: ' + d for d in data['diagnostics'])
    return '\n'.join(lines)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args(argv)
    ap.error('use cost-tracker report --combined to include both hosts')


if __name__ == '__main__':
    main()
