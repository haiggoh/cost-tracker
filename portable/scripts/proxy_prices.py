#!/usr/bin/env python3
"""proxy_prices.py — learn a gateway's OWN price table, so spend is priced the way it is billed.

A LiteLLM proxy prices every request from its own per-model table and publishes it at
`GET <ANTHROPIC_BASE_URL>/model/info`. Its budget (`Current cost` in a refusal) is that table's
arithmetic, NOT Anthropic list price plus some reseller's published fee. Measured 2026-10-14 on
one such proxy: every Claude row was official x1.10 on every token type, which Anthropic's own
pricing page documents as the cloud regional-endpoint premium. Before this module, cost-tracker
split that one factor into a wrong "fee" (x1.05) and an unexplained "gap" (x1.047).

Detection is by BEHAVIOUR, never by hostname: any non-Anthropic, non-local base URL is asked once
for /model/info, and a LiteLLM-shaped answer makes it a priced proxy. Nothing about a particular
company or host is shipped. The answer is cached per host in <config dir>/proxy-prices.json for
24h; readers (the statusline, hooks) only ever read the cache, and the probe runs detached.

Per-model resolution (see price_for):
  1. the proxy's own row, when it lists the model;
  2. else official price (config/anthropic-prices.json) x the proxy's DERIVED markup — the
     median proxy/official ratio over every model both tables list;
  3. if the proxy cannot be reached, the last cached table and markup keep applying.
Rows off the median by more than CUSTOM_TOL are reported as CUSTOM, so an exception the proxy
makes is visible instead of averaged away.

Usage:
  proxy_prices.py --refresh [--force]   probe this session's gateway now and update the cache
  proxy_prices.py --show [--json]       print the cached table summary for this gateway
  proxy_prices.py --help

Environment:
  ANTHROPIC_BASE_URL               the endpoint to probe (Anthropic's own and localhost are skipped)
  ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY   credential sent to the price route
  COST_TRACKER_PROXY_PRICES_URL    explicit price-table URL (file:// works); `off` disables probing
  COST_TRACKER_CONFIG_DIR          cache location (default ~/.claude/cost-tracker)
  COST_TRACKER_PROXY_PRICES_TTL_S  cache freshness in seconds (default 86400)
"""
import json
import os
import subprocess
import sys
import time
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.realpath(__file__))
OFFICIAL_PATH = os.environ.get("COST_TRACKER_OFFICIAL_PRICES",
                               os.path.join(os.path.dirname(HERE), "config", "anthropic-prices.json"))
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
ANTHROPIC_HOSTS = ("api.anthropic.com",)
CUSTOM_TOL = 0.02
TYPES = ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")
# LiteLLM's per-token field for each of our token types.
LITELLM_FIELDS = {
    "input": "input_cost_per_token",
    "output": "output_cost_per_token",
    "cache_read": "cache_read_input_token_cost",
    "cache_write_5m": "cache_creation_input_token_cost",
    "cache_write_1h": "cache_creation_input_token_cost_above_1hr",
}


def config_dir():
    return os.environ.get("COST_TRACKER_CONFIG_DIR", os.path.expanduser("~/.claude/cost-tracker"))


def cache_path():
    return os.path.join(config_dir(), "proxy-prices.json")


def ttl_s():
    try:
        return float(os.environ.get("COST_TRACKER_PROXY_PRICES_TTL_S") or 86400)
    except ValueError:
        return 86400.0


def host_of(url):
    try:
        return (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""


def probe_target(env):
    """(host, url) to ask for a price table, or (None, None) when there is nothing to ask."""
    override = env.get("COST_TRACKER_PROXY_PRICES_URL")
    if override and override.lower() == "off":
        return None, None
    base = env.get("ANTHROPIC_BASE_URL", "")
    host = host_of(base)
    if override:
        return host or "override", override
    if not host or host in LOCAL_HOSTS or host in ANTHROPIC_HOSTS:
        return None, None
    return host, base.rstrip("/") + "/model/info"


def load_official():
    """{model: {type: $/MTok, ...}} from the bundled official table; {} if unreadable."""
    try:
        with open(OFFICIAL_PATH) as f:
            return (json.load(f) or {}).get("models") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def canonical(model):
    """'claude-opus-5-5[1m]' / 'claude-haiku-4-5-20251001' -> base id."""
    import re
    base = (model or "").partition("[")[0] or (model or "")
    m = re.match(r"^(claude-.+?)-\d{8}$", base)
    return m.group(1) if m else base


def parse_model_info(doc):
    """LiteLLM /model/info JSON -> {model: {type: $/MTok}} for claude-* rows that carry an input
    and output price. Returns None when the document is not LiteLLM-shaped at all."""
    if not isinstance(doc, dict) or not isinstance(doc.get("data"), list):
        return None
    out = {}
    for row in doc["data"]:
        if not isinstance(row, dict):
            continue
        name = canonical(str(row.get("model_name") or ""))
        info = row.get("model_info") or {}
        if not name.startswith("claude-") or not isinstance(info, dict):
            continue
        rates = {}
        for t, field in LITELLM_FIELDS.items():
            v = info.get(field)
            if isinstance(v, (int, float)) and v > 0:
                rates[t] = round(float(v) * 1e6, 6)
        if "input" in rates and "output" in rates:
            out.setdefault(name, rates)
    return out


def derive(models, official):
    """(markup or None, matched_count, custom_rows). markup = median proxy/official ratio over
    every token type of every model both tables list; a model is CUSTOM when any of its
    ratios is off that median by more than CUSTOM_TOL."""
    ratios, per_model = [], {}
    for name, rates in models.items():
        o = official.get(name)
        if not o:
            continue
        rs = {t: rates[t] / o[t] for t in TYPES if t in rates and o.get(t)}
        if rs:
            per_model[name] = rs
            ratios.extend(rs.values())
    if not ratios:
        return None, 0, []
    ratios.sort()
    mid = len(ratios) // 2
    med = ratios[mid] if len(ratios) % 2 else (ratios[mid - 1] + ratios[mid]) / 2
    custom = sorted(n for n, rs in per_model.items()
                    if any(abs(r / med - 1) > CUSTOM_TOL for r in rs.values()))
    return round(med, 4), len(per_model), custom


def read_cache():
    try:
        with open(cache_path()) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def cached(env=None):
    """The cached entry for this session's gateway, or None. Never touches the network, and an
    OLD entry still counts: a proxy that cannot be reached keeps its last known table."""
    env = os.environ if env is None else env
    host = probe_target(env)[0]
    if not host:
        return None
    e = read_cache().get(host)
    return e if isinstance(e, dict) and e.get("proxy") else None


def _stamp(entry):
    """Best available freshness for an entry: a float when the writer recorded one, else 0."""
    for key in ("fetched_at", "checked_at"):
        v = entry.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    return 0.0


def cached_any_host():
    """(host, entry) of the FRESHEST cached gateway price table, or (None, None).

    `cached(env)` can only answer for the session's OWN gateway: it derives the host from
    ANTHROPIC_BASE_URL, so a free/local lane (localhost) has no cache row and gets None even when
    a perfectly good learned table for the real gateway sits in the same file. This accessor
    exists for that case. The per-session cost-ledger records no host attribution, and every
    non-zero dollar in it is gateway spend, so the freshest learned table is the right price for
    it — pricing it at list under-reports by the whole markup (measured 2026-10-23: a $36.33
    list-price day reported as $36.47 where the gateway's own refusal read $40.0265).

    Same contract as `cached()`: a file read only, never the network, and an OLD entry still
    counts.
    """
    best_host, best = None, None
    try:
        cache = read_cache()
    except Exception:
        return None, None
    if not isinstance(cache, dict):
        return None, None
    for host, entry in cache.items():
        if not isinstance(entry, dict) or not entry.get("proxy") or not entry.get("markup"):
            continue
        if best is None or _stamp(entry) > _stamp(best):
            best_host, best = host, entry
    return best_host, best


def price_for(model, entry, official=None):
    """{type: $/MTok} for `model` on the BILLED axis of a proxy `entry`, plus its basis:
    (rates, "proxy table" | "official x markup") or (None, None)."""
    if not entry:
        return None, None
    name = canonical(model)
    row = (entry.get("models") or {}).get(name)
    if row:
        return row, "proxy table"
    official = load_official() if official is None else official
    o = official.get(name)
    if o and entry.get("markup"):
        return {t: o[t] * entry["markup"] for t in TYPES if t in o}, "official x markup"
    return None, None


def _fetch(url, env):
    """Parsed JSON from `url`, or None. The credential travels in a header read from stdin
    (`-H @-`), so it never appears in a process listing."""
    token = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY") or ""
    headers = f"Authorization: Bearer {token}\nx-api-key: {token}\n" if token else ""
    try:
        r = subprocess.run(["curl", "-fsS", "--max-time", "15", "-H", "@-", url],
                           input=headers.encode(), capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def refresh(env=None, force=False):
    """Probe the gateway (unless the cached answer is fresh) and save the result. Returns the
    entry now in the cache for this host, or None when there is no gateway to ask. A failed
    probe NEVER erases a good table: the old one stays, only `last_error` is recorded."""
    env = os.environ if env is None else env
    host, url = probe_target(env)
    if not host:
        return None
    cache = read_cache()
    prev = cache.get(host)
    old: dict = prev if isinstance(prev, dict) else {}
    now = time.time()
    if not force and old and now - float(old.get("checked_at", 0)) < ttl_s():
        return old
    doc = _fetch(url, env)
    models = parse_model_info(doc) if doc is not None else None
    if models is None:
        entry: dict = dict(old) if old.get("proxy") else {"proxy": False}
        entry["checked_at"] = now
        entry["last_error"] = "unreachable" if doc is None else "not a LiteLLM price table"
    else:
        official = load_official()
        markup, matched, custom = derive(models, official)
        entry = {"proxy": bool(models), "checked_at": now, "fetched_at": now,
                 "path": urlparse(url).path or "/", "models": models,
                 "markup": markup, "matched": matched, "custom": custom,
                 "official_verified_at": _official_verified_at()}
    cache[host] = entry
    os.makedirs(config_dir(), exist_ok=True)
    tmp = cache_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
    os.replace(tmp, cache_path())
    return entry


def _official_verified_at():
    try:
        with open(OFFICIAL_PATH) as f:
            return (json.load(f) or {}).get("verified_at")
    except (OSError, ValueError, AttributeError):
        return None


def needs_refresh(env=None):
    env = os.environ if env is None else env
    host, _ = probe_target(env)
    if not host:
        return False
    e = read_cache().get(host)
    return not isinstance(e, dict) or time.time() - float(e.get("checked_at", 0)) >= ttl_s()


def spawn_refresh(env=None):
    """Run --refresh detached, only when the cache is stale. Never waits on the network."""
    env = os.environ if env is None else env
    if os.environ.get("COST_TRACKER_NO_SPAWN") == "1" or not needs_refresh(env):
        return
    try:
        subprocess.Popen([sys.executable, os.path.realpath(__file__), "--refresh"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def summary(entry):
    if not entry:
        return "no proxy price table for this connection — official Anthropic prices apply"
    if not entry.get("proxy"):
        return f"the gateway publishes no LiteLLM price table ({entry.get('last_error') or 'none'})"
    age_h = (time.time() - float(entry.get("fetched_at") or entry.get("checked_at") or 0)) / 3600
    lines = [f"proxy price table: {len(entry.get('models') or {})} Claude model(s), "
             f"fetched {age_h:.1f}h ago from {entry.get('path')}",
             f"  derived markup: ×{entry.get('markup')} (median proxy/official over "
             f"{entry.get('matched')} model(s) both tables list)",
             "  custom rows (off the median >2%): " + (", ".join(entry.get("custom") or []) or "none")]
    if entry.get("last_error"):
        lines.append(f"  last probe: {entry['last_error']} — keeping this table")
    return "\n".join(lines)


def main(argv):
    if not argv or "--help" in argv or "-h" in argv:
        print((__doc__ or "").strip())
        return 0
    known = {"--refresh", "--force", "--show", "--json"}
    bad = [a for a in argv if a not in known]
    if bad:
        print(f"proxy_prices.py: unrecognised argument(s): {' '.join(bad)}", file=sys.stderr)
        print("Try 'proxy_prices.py --help'.", file=sys.stderr)
        return 2
    entry = refresh(force="--force" in argv) if "--refresh" in argv else cached()
    if "--json" in argv:
        print(json.dumps(entry, indent=2, sort_keys=True))
    else:
        print(summary(entry))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
