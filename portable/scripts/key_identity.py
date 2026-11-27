#!/usr/bin/env python3
"""Key identity for per-key budgets: the last-4 hint ONLY, plus read-only probes
that pair each app's live config with the gateway base URL it actually uses.

Rules (spec 2026-11-24):
- A key's identity is its last 4 chars (the gateway's own refusal format
  `sk-...xxxx`). No full key value is ever read into memory beyond the last-4
  slice, printed, logged or stored. On ~/.joyia/joyia.db only
  `substr(key,-4)` is ever selected.
- A key counts toward a GATEWAY budget only when paired with that gateway's
  base URL in the probed config. Pair by URL, never by key shape (gotcha seen
  2026-11-24: a gateway key sitting under ai.openai.api_key next to
  api.openai.com is NOT a gateway key for that app).
- Probing is read-only and best-effort: an unprobeable app yields None, and
  its spend is `unattributed` — never guessed.

Run `key_identity.py --help`.
"""
import argparse
import json
import os
import pathlib
import sqlite3
import sys
import tomllib
from datetime import datetime, timezone
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.realpath(__file__))

_CC = None


def _cc():
    """bin/cost_connection.py loaded once by path (same loader shape as
    cost-tracker._connection). The hint helpers (HINT_RE, hint_of, ...) and the
    host lists live THERE as the single home; this module aliases them so the
    key format cannot drift between the two."""
    global _CC
    if _CC is None:
        import importlib.util
        from importlib.machinery import SourceFileLoader
        path = os.path.join(HERE, "cost_connection.py")
        loader = SourceFileLoader("cost_connection_for_key_identity", path)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        if spec is None or spec.loader is None:
            raise ImportError("cannot load cost_connection.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[loader.name] = mod
        loader.exec_module(mod)
        _CC = mod
    return _CC


HINT_RE = _cc().HINT_RE
DISPLAY_PREFIX = _cc().DISPLAY_PREFIX
hint_of = _cc().hint_of
hint_display = _cc().hint_display
hint_from_display = _cc().hint_from_display


def _connection_hosts():
    return _cc()


# Well-known DIRECT-provider API hosts. Excluded even if a user maps one into
# host_map: a key paired with one of these is never a gateway key (the
# 2026-11-24 gotcha — pair by the gateway's URL, and never let a direct API
# host be classified as a gateway by config accident).
FIRST_PARTY_API_HOSTS = ("api.openai.com",)


def _host(url):
    try:
        return (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""


def known_gateway_hosts(env=None, config_dir=None):
    """ALLOWLIST of gateway hosts: provider-registry hosts (config/providers.json,
    override COST_TRACKER_PROVIDERS), cap.json connection.host_map keys, and the
    hosts in the cached gateway price tables (proxy-prices.json). A host is a
    gateway ONLY if it is in this set — never by shape (the 2026-11-24 gotcha:
    a gateway key under api.openai.com must not be tagged)."""
    env = os.environ if env is None else env
    if config_dir is None:
        config_dir = env.get("COST_TRACKER_CONFIG_DIR") or os.path.expanduser(
            "~/.claude/cost-tracker")
    hosts = set()
    reg = env.get("COST_TRACKER_PROVIDERS") or os.path.join(
        os.path.dirname(HERE), "config", "providers.json")
    try:
        with open(reg) as f:
            doc = json.load(f)
        providers = doc.get("providers") if isinstance(doc, dict) else None
        if isinstance(providers, dict):
            for p in providers.values():
                if isinstance(p, dict):
                    hosts.update(h for h in (p.get("hosts") or [])
                                 if isinstance(h, str))
    except (OSError, ValueError, AttributeError):
        pass
    try:
        with open(os.path.join(config_dir, "cap.json")) as f:
            cap = json.load(f)
        hm = (cap.get("connection") or {}).get("host_map") or {}
        if isinstance(hm, dict):
            hosts.update(h for h in hm if isinstance(h, str))
    except (OSError, ValueError, AttributeError):
        pass
    try:
        with open(os.path.join(config_dir, "proxy-prices.json")) as f:
            pp = json.load(f)
        if isinstance(pp, dict):
            hosts.update(h for h in pp if isinstance(h, str))
    except (OSError, ValueError):
        pass
    return hosts


def is_gateway_host(host, known=None, env=None):
    """True only for a known gateway host (allowlist) that is neither local nor
    first-party Anthropic."""
    if not host:
        return False
    cc = _connection_hosts()
    if host in cc.LOCAL_HOSTS:
        return False
    for p in cc.ANTHROPIC_HOSTS:
        if host == p or host.endswith("." + p):
            return False
    for p in FIRST_PARTY_API_HOSTS:
        if host == p or host.endswith("." + p):
            return False
    if known is None:
        known = known_gateway_hosts(env)
    return host in known


def probe_claude_cli(env):
    """The statusline/capture process is a child of the claude process, so the
    launch env is the probe: token env + ANTHROPIC_BASE_URL."""
    host = _host(env.get("ANTHROPIC_BASE_URL", ""))
    if not is_gateway_host(host, env=env):
        return None
    # Slice at the call site: the env value itself is process memory (os.environ
    # already holds it), but no named local ever carries more than the last 4.
    tok = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY") or ""
    hint = hint_of(tok)
    if not hint:
        return None
    return {"app": "claude-code", "host": host, "hint": hint}


def probe_codex(codex_home=None, env=None):
    """~/.codex/config.toml [model_providers.<active>] base_url + env_key; the
    token comes from that env var, else ~/.codex/auth.json. Override:
    COST_TRACKER_CODEX_HOME (shared with shared_costs.py)."""
    env = os.environ if env is None else env
    override = codex_home or env.get("COST_TRACKER_CODEX_HOME") or os.environ.get(
        "COST_TRACKER_CODEX_HOME")
    if override is not None and not str(override).strip():
        return None
    root = pathlib.Path(override).expanduser() if override else pathlib.Path.home() / ".codex"
    if not root.is_dir():
        return None
    try:
        config = tomllib.loads((root / "config.toml").read_text())
    except (OSError, ValueError):
        return None
    provider = config.get("model_provider")
    providers = config.get("model_providers") or {}
    p = providers.get(provider) or {}
    if not isinstance(p, dict):
        return None
    host = _host(p.get("base_url") or "")
    if not is_gateway_host(host, env=env):
        return None
    env_key = p.get("env_key")
    hint = hint_of(env.get(env_key, "")) if isinstance(env_key, str) and env_key else ""
    if not hint:
        try:
            auth = json.loads((root / "auth.json").read_text())
            hint = hint_of((auth.get("tokens") or {}).get("access_token") or "")
        except (OSError, ValueError, AttributeError):
            hint = ""
    if not hint:
        return None
    return {"app": "codex", "host": host, "hint": hint}


def _desktop_library(env=None):
    env = os.environ if env is None else env
    override = env.get("COST_TRACKER_DESKTOP_CONFIG_LIBRARY")
    if override:
        return pathlib.Path(override).expanduser()
    return (pathlib.Path.home() / "Library" / "Application Support"
            / "Claude-3p" / "configLibrary")


def probe_desktop(config_library=None, env=None):
    """Claude Desktop 3P: configLibrary/<uuid>.json with
    inferenceGatewayBaseUrl + inferenceGatewayApiKey."""
    lib = pathlib.Path(config_library).expanduser() if config_library else _desktop_library(env)
    if not lib.is_dir():
        return None
    for path in sorted(lib.glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict):
            continue
        host = _host(doc.get("inferenceGatewayBaseUrl") or "")
        key = doc.get("inferenceGatewayApiKey")
        if is_gateway_host(host, env=env) and isinstance(key, str):
            hint = hint_of(key)
            if hint:
                return {"app": "desktop", "host": host, "hint": hint}
    return None


def _joyia_home(joyia_home=None, env=None):
    env = os.environ if env is None else env
    override = joyia_home or env.get("COST_TRACKER_JOYIA_HOME")
    if override:
        return pathlib.Path(override).expanduser()
    return pathlib.Path.home() / ".joyia"


def joyia_labels(joyia_home=None, env=None):
    """{hint: name} from joyia's native key store — read-only, last-4 only."""
    home = _joyia_home(joyia_home, env)
    db = home / "joyia.db"
    if not db.is_file():
        return {}
    try:
        conn = sqlite3.connect("file:" + str(db) + "?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT name, substr(key,-4) FROM ai_keys").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    out = {}
    for name, hint in rows:
        if isinstance(name, str) and isinstance(hint, str) and HINT_RE.match(hint):
            out[hint] = name
    return out


def joyia_folder_links(joyia_home=None, env=None):
    """Per-folder key overrides: [{folder_path, key_name}] — a probe INPUT (a
    launch from a linked folder uses that key), not a label."""
    home = _joyia_home(joyia_home, env)
    db = home / "joyia.db"
    if not db.is_file():
        return []
    try:
        conn = sqlite3.connect("file:" + str(db) + "?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT folder_path, key_name FROM ai_key_folder_links").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    return [{"folder_path": r[0], "key_name": r[1]}
            for r in rows if isinstance(r[0], str) and isinstance(r[1], str)]


# --- key registry (keys.json in the config dir) --------------------------------

def registry_path(config_dir):
    return os.path.join(config_dir, "keys.json")


def load_registry(config_dir):
    try:
        with open(registry_path(config_dir)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"keys": {}}
    if not isinstance(data, dict) or not isinstance(data.get("keys"), dict):
        return {"keys": {}}
    return data


def save_registry(config_dir, data):
    os.makedirs(config_dir, exist_ok=True)
    os.chmod(config_dir, 0o700)
    path = registry_path(config_dir)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def register(data, hint, app, now=None, host=None):
    """Register a key seen on an app. `first_seen` is set once; apps dedupe.
    `host` (0.24.0) is the gateway host the probe paired the key with — its LOGIN
    ROUTE; a key's cap is only ever inherited from a key on the same route."""
    if not HINT_RE.match(hint or ""):
        raise ValueError("invalid key hint: " + repr(hint))
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    keys = data.setdefault("keys", {})
    if not isinstance(keys, dict):
        raise ValueError("corrupt registry: keys is not a dict")
    entry = keys.setdefault(hint, {"first_seen": now, "apps": []})
    if not isinstance(entry, dict):
        raise ValueError("corrupt registry entry for " + hint)
    entry.setdefault("first_seen", now)
    apps = entry.setdefault("apps", [])
    if app not in apps:
        apps.append(app)
        apps.sort()
    if host:
        routes = entry.setdefault("routes", [])
        if isinstance(routes, list) and host not in routes:
            routes.append(host)
            routes.sort()
    return data


def resolve_labels(hints, cap_cfg=None, config_dir=None, joyia=None):
    """Display label per hint. Order (spec decision 4): joyia's native key
    store → the refusal's Key= label → key-labels.json → the bare hint."""
    jl = joyia if joyia is not None else {}
    refusal = {}
    for hint, obs in ((cap_cfg or {}).get("keys") or {}).items():
        if isinstance(obs, dict) and isinstance(obs.get("label"), str) \
                and obs["label"] not in ("(set by hand)", "(manual)"):
            refusal[hint] = obs["label"]
    file_labels = {}
    if config_dir:
        try:
            with open(os.path.join(config_dir, "key-labels.json")) as f:
                doc = json.load(f)
            if isinstance(doc, dict):
                file_labels = {str(k): str(v) for k, v in doc.items()}
        except (OSError, ValueError):
            pass
    return {h: (jl.get(h) or refusal.get(h) or file_labels.get(h) or hint_display(h))
            for h in hints}


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="key_identity",
        description="Key identity (last-4 hint) and read-only per-app config probes. "
                    "Never prints or stores a full key. Env: COST_TRACKER_CODEX_HOME, "
                    "COST_TRACKER_DESKTOP_CONFIG_LIBRARY, COST_TRACKER_JOYIA_HOME, "
                    "COST_TRACKER_CONFIG_DIR.")
    ap.add_argument("--probe", action="store_true",
                    help="run all probes against this machine's live config (hints only)")
    ap.add_argument("--probe-claude-cli", action="store_true",
                    help="print '<host> <hint>' for THIS process's env, or an empty line "
                         "when the endpoint is not a known gateway — the capture wrapper "
                         "calls this once per statusline render")
    ap.add_argument("--register", action="store_true",
                    help="with --probe-claude-cli: also record the key's login route "
                         "(its gateway host) in keys.json under COST_TRACKER_CONFIG_DIR; "
                         "written only when something changed")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.probe_claude_cli:
        out = probe_claude_cli(dict(os.environ))
        if out and HINT_RE.match(out["hint"]):
            print(out["host"] + " " + out["hint"])
            if args.register:
                try:
                    cfg = os.environ.get("COST_TRACKER_CONFIG_DIR") or os.path.expanduser(
                        "~/.claude/cost-tracker")
                    reg = load_registry(cfg)
                    before = json.dumps(reg, sort_keys=True)
                    register(reg, out["hint"], out["app"], host=out["host"])
                    if json.dumps(reg, sort_keys=True) != before:
                        save_registry(cfg, reg)
                except (OSError, ValueError):
                    pass    # the render must never break on the registry
        else:
            print("")
        return 0
    if not args.probe:
        ap.print_help()
        return 0
    out = {
        "claude_cli": probe_claude_cli(dict(os.environ)),
        "codex": probe_codex(),
        "desktop": probe_desktop(),
        "joyia_labels": joyia_labels(),
        "joyia_folder_links": joyia_folder_links(),
    }
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        for k, v in out.items():
            print(k + ": " + (json.dumps(v, sort_keys=True) if v else "unprobeable"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
