"""How the paid session connects, and therefore which limit and which fee apply.

Shared by `cost-tracker` and `budget-tally.py`, so both resolve the same answer. A PURE function
of the environment, the statusline payload and one config read: no subprocess, no network,
because the statusline calls it on every render.

Precedence follows Claude Code's documented credential order (code.claude.com/docs/en/
authentication, fetched 2026-10-11): cloud provider env > ANTHROPIC_AUTH_TOKEN > ANTHROPIC_API_KEY >
apiKeyHelper > CLAUDE_CODE_OAUTH_TOKEN > profiles > /login subscription; a signed-in Claude apps
gateway outranks them all. ANTHROPIC_BASE_URL reroutes the endpoint, so a non-Anthropic host is a
gateway whatever credential it carries.

Limits per kind (official):
  subscription    rolling 5-hour + weekly windows, in PERCENT (`rate_limits` in the payload);
                  the dollar figure "isn't relevant for billing" (code.claude.com/docs/en/costs)
  apps-gateway    `rate_limits.spend_limit` (limit_usd, period daily|weekly|monthly)
  api-key         a MONTHLY tier cap (Start $500 / Build $1,000 / Scale $200,000), no daily cap
                  (platform.claude.com/docs/en/api/rate-limits) — set by hand or learned
  cloud-provider  Bedrock / Vertex / Foundry: the cloud console owns spend controls — none here
  gateway         a reseller's own cap, learned from its refusal message (daily per key for
                  llmgateway.io)
  local           free compute, no cap

FEE vs GAP. The fee is a reseller's PUBLISHED, deterministic charge (config/providers.json, cited)
and applies only to sessions routed through that reseller. The gap is what `calibrate` MEASURES on
top of list x fee and cannot yet explain. They are stored and reported separately, and the gap is
never folded into the fee: a fitted constant once hid real unseen spend for weeks.
"""
import json
import os
import sys
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.realpath(__file__))
REGISTRY_PATH = os.environ.get("COST_TRACKER_PROVIDERS",
                               os.path.join(os.path.dirname(HERE), "config", "providers.json"))
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
ANTHROPIC_HOSTS = ("api.anthropic.com",)
IDENTITY = 1.0


def config_path():
    return os.path.join(os.environ.get("COST_TRACKER_CONFIG_DIR",
                                       os.path.expanduser("~/.claude/cost-tracker")), "cap.json")


def read_config():
    try:
        with open(config_path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return migrate_config(data) if isinstance(data, dict) else {}


def read_registry():
    try:
        with open(REGISTRY_PATH) as f:
            return (json.load(f) or {}).get("providers") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def migrate_config(cfg):
    """Legacy single `markup` -> `fee` (when it was set by hand: a stated charge) or `gap` (when
    `calibrate --learn-markup` measured it: an observation). Returns a NEW dict; `markup` is gone,
    so no reader can apply both."""
    m = cfg.get("markup")
    if not isinstance(m, dict) or not m.get("factor"):
        return dict(cfg)
    out = {k: v for k, v in cfg.items() if k != "markup"}
    try:
        f = float(m["factor"])
    except (TypeError, ValueError):
        return out
    if f <= 0:
        return out
    by_hand = "hand" in str(m.get("basis", "")) or not m.get("n_days")
    if by_hand:
        out.setdefault("fee", {"factor": f, "source": "set by hand (migrated from markup)",
                               "set_at": m.get("measured_at")})
    else:
        out.setdefault("gap", {"factor": f, "n_days": m.get("n_days"), "cv": m.get("cv"),
                               "measured_at": m.get("measured_at")})
    return out


def _host(url):
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _host_matches(host, pattern):
    pattern = pattern.lower()
    return host == pattern or host.endswith("." + pattern)


def _provider_for(host, registry, cfg):
    for name, p in registry.items():
        if any(_host_matches(host, h) for h in p.get("hosts") or []):
            return name, "registry host"
    mapped = ((cfg.get("connection") or {}).get("host_map") or {}).get(host)
    if mapped:
        return mapped, "local host map"
    return None, None


def _proxy_entry(env):
    """This gateway's CACHED price table (bin/proxy_prices.py), or None. Reads a file only —
    the probe itself runs detached, so the statusline never waits on the network."""
    try:
        import importlib.util
        from importlib.machinery import SourceFileLoader
        mod = sys.modules.get("proxy_prices")
        if mod is None:
            loader = SourceFileLoader("proxy_prices", os.path.join(HERE, "proxy_prices.py"))
            spec = importlib.util.spec_from_loader(loader.name, loader)
            if spec is None:
                return None
            mod = importlib.util.module_from_spec(spec)
            sys.modules[loader.name] = mod
            loader.exec_module(mod)
        return mod.cached(env)
    except Exception:
        return None


def _proxy_entry_any_host():
    """(host, cached entry) of the freshest learned gateway price table, whatever host it was
    learned from — or (None, None). See proxy_prices.cached_any_host() for why a free lane needs
    a host-agnostic read."""
    try:
        import importlib.util
        from importlib.machinery import SourceFileLoader
        mod = sys.modules.get("proxy_prices")
        if mod is None:
            loader = SourceFileLoader("proxy_prices", os.path.join(HERE, "proxy_prices.py"))
            spec = importlib.util.spec_from_loader(loader.name, loader)
            if spec is None:
                return None, None
            mod = importlib.util.module_from_spec(spec)
            sys.modules[loader.name] = mod
            loader.exec_module(mod)
        return mod.cached_any_host()
    except Exception:
        return None, None


def _env_float(env, *names):
    for n in names:
        raw = env.get(n)
        if raw:
            try:
                v = float(raw)
            except ValueError:
                continue
            if v > 0:
                return v
    return None


def _kind(env, payload):
    if any(env.get(v) for v in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                                "CLAUDE_CODE_USE_FOUNDRY")):
        return "cloud-provider"
    host = _host(env.get("ANTHROPIC_BASE_URL", ""))
    if host in LOCAL_HOSTS:
        return "local"
    if host and host not in ANTHROPIC_HOSTS:
        return "gateway"
    rl = (payload or {}).get("rate_limits") or {}
    if rl.get("spend_limit"):
        return "apps-gateway"
    if rl.get("five_hour") or rl.get("seven_day"):
        return "subscription"
    if env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY"):
        return "api-key"
    # Before the first API response a subscription carries no `rate_limits` yet, and an
    # apiKeyHelper is invisible from here: say so rather than guess.
    return "first-party"


def resolve_connection(env=None, payload=None):
    env = os.environ if env is None else env
    cfg = read_config()
    kind = _kind(env, payload)
    host = _host(env.get("ANTHROPIC_BASE_URL", ""))
    out = {"kind": kind, "host": host or None, "provider": None, "provider_source": None,
           "fee": IDENTITY, "fee_source": "none (list price)",
           "cap_usd": None, "cap_kind": "none", "cap_period": None, "cap_source": None,
           "windows": None, "gap": None, "gap_n_days": None, "gap_cv": None,
           "price_table": None, "gap_superseded": None}

    if kind == "gateway":
        registry = read_registry()
        provider, psrc = _provider_for(host, registry, cfg)
        proxy = _proxy_entry(env)
        p = registry.get(provider) or {}
        env_fee = _env_float(env, "COST_TRACKER_FEE", "COST_TRACKER_MARKUP")
        hand = cfg.get("fee") if isinstance(cfg.get("fee"), dict) else None
        gap = cfg.get("gap") if isinstance(cfg.get("gap"), dict) else None
        if proxy and proxy.get("markup"):
            # 0.15.0: the gateway publishes its OWN price table. That table IS the bill, so it
            # outranks a registry fee matched by host, and it makes a measured gap obsolete —
            # the gap only ever existed because the real table was unknown.
            provider, psrc = "price-table proxy", "the gateway's own /model/info"
            out["price_table"] = {"markup": proxy["markup"], "models": len(proxy.get("models") or {}),
                                  "matched": proxy.get("matched"), "custom": proxy.get("custom") or [],
                                  "fetched_at": proxy.get("fetched_at"),
                                  "last_error": proxy.get("last_error")}
        out["provider"], out["provider_source"] = provider, psrc
        if env_fee:
            out["fee"], out["fee_source"] = env_fee, "env override"
        elif hand and hand.get("factor"):
            out["fee"] = float(hand["factor"])
            out["fee_source"] = hand.get("source") or "set by hand"
        elif out["price_table"]:
            out["fee"] = float(out["price_table"]["markup"])
            out["fee_source"] = (f"gateway price table: median of {out['price_table']['matched']} "
                                 "model(s) vs official Anthropic prices")
        elif p.get("fee"):
            out["fee"], out["fee_source"] = float(p["fee"]), p.get("fee_source") or provider
        out["cap_kind"] = p.get("cap_kind") or "daily-key"
        if gap and gap.get("factor"):
            if out["price_table"]:
                out["gap_superseded"] = float(gap["factor"])
            else:
                out["gap"] = float(gap["factor"])
                out["gap_n_days"], out["gap_cv"] = gap.get("n_days"), gap.get("cv")
    elif kind == "local":
        # 0.16.0. This session's OWN turns cost $0 — but the tally printed from a free lane
        # covers the whole day, including other sessions' real gateway spend, which the GATEWAY
        # billed at its own markup. Resolving the fee from THIS endpoint (localhost -> no branch
        # matched -> fee 1.0) priced that spend at list. Measured 2026-10-23: the day's tally
        # printed $36.47 from a free lane while the gateway's refusal read $40.0265 and the
        # statusline $40.11 (36.4656 x 1.10) — the entire gap was the missing markup, not the
        # ledger's base. So the fee (and the day's cap) come from the learned gateway state, not
        # from this session's connection.
        env_fee = _env_float(env, "COST_TRACKER_FEE", "COST_TRACKER_MARKUP")
        if env_fee:
            out["fee"], out["fee_source"] = env_fee, "env override"
        else:
            host, entry = _proxy_entry_any_host()
            if entry and entry.get("markup"):
                out["fee"] = float(entry["markup"])
                out["fee_source"] = (f"cached gateway price table ({host}), applied to other "
                                     "sessions' gateway spend")
        # The cap is the gateway KEY's; a free lane does not draw against it, so this is
        # reported as context ("of $N cap"), never as this session's own limit.
        k = read_config().get("key") if isinstance(read_config().get("key"), dict) else None
        if k and k.get("cap_usd"):
            out["cap_usd"], out["cap_kind"] = float(k["cap_usd"]), "daily-key"
            out["cap_period"] = "daily"
    elif kind == "subscription":
        rl = (payload or {}).get("rate_limits") or {}
        out["cap_kind"] = "percent-window"
        out["windows"] = {w: (rl.get(w) or {}).get("used_percentage")
                          for w in ("five_hour", "seven_day") if rl.get(w)}
        out["cap_source"] = "Claude Code rate_limits (subscription)"
    elif kind == "apps-gateway":
        sl = ((payload or {}).get("rate_limits") or {}).get("spend_limit") or {}
        out["cap_kind"] = "spend-limit"
        out["cap_usd"] = sl.get("limit_usd")
        out["cap_period"] = sl.get("period")
        out["windows"] = {"spend_limit": sl.get("used_percentage")}
        out["cap_source"] = "Claude apps gateway spend_limit"
    elif kind == "api-key":
        out["cap_kind"] = "monthly"
        m = cfg.get("monthly_cap") if isinstance(cfg.get("monthly_cap"), dict) else None
        if m and m.get("cap_usd"):
            out["cap_usd"], out["cap_source"] = float(m["cap_usd"]), m.get("source") or "set by hand"
        out["cap_period"] = "monthly"
    return out
