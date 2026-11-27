"""Key identity = last-4 hint only, and per-app probing pairs a key with the
base URL of the config it was found in — never by key shape.

The load-bearing test here is the URL-pairing gotcha (seen live 2026-11-24):
joyia's config.json held a gateway key under ai.openai.api_key paired with
api.openai.com — pairing by key shape would wrongly tag it as a gateway key.

All secrets in this file are FAKE ("sk-fake-..."). No real key value is ever
read, printed or asserted on; the tests assert the hint is exactly 4 chars and
never carries more of the secret.
"""
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_ki():
    spec = importlib.util.spec_from_loader(
        "key_identity",
        importlib.machinery.SourceFileLoader("key_identity", str(ROOT / "bin" / "key_identity.py")),
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


K1 = "sk-fake-00000000aaaa"
K2 = "sk-fake-00000000bbbb"
K3 = "sk-fake-00000000cccc"
GW = "gw.example.test"


def _cfg(tmp_path, host=GW):
    """A throwaway config dir whose cap.json host_map names `host` as a known
    gateway — the allowlist the probes check against."""
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "cap.json").write_text(json.dumps(
        {"contract": 2, "connection": {"host_map": {host: "fake-gw"}}}))
    return cfg


def _env(tmp_path, **kw):
    e = {"COST_TRACKER_CONFIG_DIR": str(_cfg(tmp_path))}
    e.update(kw)
    return e


# --- hint_of / display -------------------------------------------------------

def test_hint_of_returns_exactly_the_last_four_chars():
    ki = load_ki()
    h = ki.hint_of(K1)
    assert h == "aaaa"
    assert len(h) == 4
    # the hint must never carry more of the secret than those 4 chars
    assert K1[:-4] not in h


def test_hint_display_and_parse_round_trip():
    ki = load_ki()
    assert ki.hint_display("aaaa") == "sk-...aaaa"
    assert ki.hint_from_display("sk-...aaaa") == "aaaa"
    assert ki.hint_from_display("aaaa") == "aaaa"  # bare hint tolerated
    assert not ki.HINT_RE.match("toolong")
    assert ki.HINT_RE.match("aA9_")


def test_is_gateway_host_is_an_allowlist_not_a_shape():
    ki = load_ki()
    known = {GW, "llmgw.joyia.example.io"}
    assert ki.is_gateway_host(GW, known=known) is True
    assert ki.is_gateway_host("llmgw.joyia.example.io", known=known) is True
    # a direct-provider API host is NEVER a gateway, even gateway-shaped keys aside
    assert ki.is_gateway_host("api.openai.com", known=known) is False
    assert ki.is_gateway_host("api.openai.com", known=set()) is False
    # ...and NOT even if a user maps it into host_map by accident (dispatch finding 2026-11-24)
    assert ki.is_gateway_host("api.openai.com", known={"api.openai.com"}) is False
    # local and first-party Anthropic are excluded even if listed
    assert ki.is_gateway_host("localhost", known={"localhost"}) is False
    assert ki.is_gateway_host("127.0.0.1", known=set()) is False
    assert ki.is_gateway_host("api.anthropic.com", known={"api.anthropic.com"}) is False
    assert ki.is_gateway_host("", known=known) is False


def test_malformed_providers_registry_never_raises(tmp_path):
    """Dispatch finding: providers.json = [] or {"providers": null} must not
    crash the probe (best-effort: None, never raise)."""
    ki = load_ki()
    cfg = _cfg(tmp_path)
    reg = tmp_path / "providers.json"
    reg.write_text("[]")
    env = {"COST_TRACKER_CONFIG_DIR": str(cfg), "COST_TRACKER_PROVIDERS": str(reg)}
    assert GW in ki.known_gateway_hosts(env)          # host_map still read
    reg.write_text('{"providers": null}')
    assert GW in ki.known_gateway_hosts(env)
    reg.write_text('{"providers": ["not", "a", "dict"]}')
    assert GW in ki.known_gateway_hosts(env)
    reg.write_text("{ not json")
    assert GW in ki.known_gateway_hosts(env)


def test_known_gateway_hosts_unions_registry_hostmap_and_price_cache(tmp_path):
    ki = load_ki()
    cfg = _cfg(tmp_path, host=GW)
    (cfg / "proxy-prices.json").write_text(json.dumps({"priced.example.test": {"markup": 1.1}}))
    reg = tmp_path / "providers.json"
    reg.write_text(json.dumps({"providers": {"reg-gw": {"hosts": ["reg.example.test"]}}}))
    env = {"COST_TRACKER_CONFIG_DIR": str(cfg), "COST_TRACKER_PROVIDERS": str(reg)}
    known = ki.known_gateway_hosts(env)
    assert {GW, "priced.example.test", "reg.example.test"} <= known


# --- probe_claude_cli ---------------------------------------------------------

def test_probe_claude_cli_pairs_token_with_gateway_base_url(tmp_path):
    ki = load_ki()
    env = _env(tmp_path, ANTHROPIC_BASE_URL="https://" + GW + "/v1",
               ANTHROPIC_AUTH_TOKEN=K1)
    out = ki.probe_claude_cli(env)
    assert out == {"app": "claude-code", "host": GW, "hint": "aaaa"}


def test_probe_claude_cli_auth_token_beats_api_key(tmp_path):
    ki = load_ki()
    env = _env(tmp_path, ANTHROPIC_BASE_URL="https://" + GW,
               ANTHROPIC_AUTH_TOKEN=K1, ANTHROPIC_API_KEY=K2)
    assert ki.probe_claude_cli(env)["hint"] == "aaaa"
    env2 = _env(tmp_path, ANTHROPIC_BASE_URL="https://" + GW, ANTHROPIC_API_KEY=K2)
    assert ki.probe_claude_cli(env2)["hint"] == "bbbb"


def test_probe_claude_cli_never_tags_a_non_gateway_url(tmp_path):
    """The 2026-11-24 gotcha: a gateway-shaped key under api.openai.com (or a
    local lane, or an UNKNOWN host) is NOT tagged — the allowlist decides."""
    ki = load_ki()
    assert ki.probe_claude_cli(_env(tmp_path, ANTHROPIC_BASE_URL="https://api.openai.com/v1",
                                   ANTHROPIC_AUTH_TOKEN=K1)) is None
    assert ki.probe_claude_cli(_env(tmp_path, ANTHROPIC_BASE_URL="http://127.0.0.1:4141",
                                   ANTHROPIC_AUTH_TOKEN=K1)) is None
    assert ki.probe_claude_cli(_env(tmp_path, ANTHROPIC_BASE_URL="https://unknown.example.net",
                                   ANTHROPIC_AUTH_TOKEN=K1)) is None
    assert ki.probe_claude_cli(_env(tmp_path, ANTHROPIC_BASE_URL="https://" + GW)) is None
    assert ki.probe_claude_cli(_env(tmp_path)) is None


# --- probe_codex --------------------------------------------------------------

def _codex_home(tmp_path, base_url, env_key="FAKE_JOYIA_KEY", auth=None):
    home = tmp_path / "codex"
    home.mkdir()
    (home / "config.toml").write_text(
        'model_provider = "joyia"\n\n[model_providers.joyia]\n'
        f'name = "Joyia"\nbase_url = "{base_url}"\nenv_key = "{env_key}"\n')
    if auth is not None:
        (home / "auth.json").write_text(json.dumps({"tokens": {"access_token": auth}}))
    return home


def test_probe_codex_reads_provider_base_url_and_env_key(tmp_path):
    ki = load_ki()
    home = _codex_home(tmp_path, "https://" + GW + "/v1")
    env = _env(tmp_path, FAKE_JOYIA_KEY=K3)
    out = ki.probe_codex(codex_home=home, env=env)
    assert out == {"app": "codex", "host": GW, "hint": "cccc"}


def test_probe_codex_openai_url_is_not_a_gateway_key(tmp_path):
    ki = load_ki()
    home = _codex_home(tmp_path, "https://api.openai.com/v1")
    assert ki.probe_codex(codex_home=home, env=_env(tmp_path, FAKE_JOYIA_KEY=K1)) is None


def test_probe_codex_falls_back_to_auth_json(tmp_path):
    ki = load_ki()
    home = _codex_home(tmp_path, "https://" + GW + "/v1", auth=K2)
    out = ki.probe_codex(codex_home=home, env=_env(tmp_path))
    assert out["hint"] == "bbbb"


def test_probe_codex_home_override_and_missing(tmp_path):
    ki = load_ki()
    home = _codex_home(tmp_path, "https://" + GW + "/v1")
    os.environ["COST_TRACKER_CODEX_HOME"] = str(home)
    try:
        assert ki.probe_codex(env=_env(tmp_path, FAKE_JOYIA_KEY=K1))["hint"] == "aaaa"
    finally:
        os.environ.pop("COST_TRACKER_CODEX_HOME", None)
    assert ki.probe_codex(codex_home=tmp_path / "nope", env=_env(tmp_path)) is None


# --- probe_desktop ------------------------------------------------------------

def test_probe_desktop_reads_config_library(tmp_path):
    ki = load_ki()
    lib = tmp_path / "configLibrary"
    lib.mkdir()
    (lib / "aaaa.json").write_text(json.dumps({
        "inferenceGatewayBaseUrl": "https://" + GW,
        "inferenceGatewayApiKey": K1,
        "inferenceCredentialKind": "apiKey",
    }))
    out = ki.probe_desktop(config_library=lib, env=_env(tmp_path))
    assert out == {"app": "desktop", "host": GW, "hint": "aaaa"}


def test_probe_desktop_non_gateway_or_missing_is_none(tmp_path):
    ki = load_ki()
    lib = tmp_path / "configLibrary"
    lib.mkdir()
    (lib / "b.json").write_text(json.dumps({
        "inferenceGatewayBaseUrl": "https://api.anthropic.com",
        "inferenceGatewayApiKey": K1}))
    assert ki.probe_desktop(config_library=lib, env=_env(tmp_path)) is None
    assert ki.probe_desktop(config_library=tmp_path / "missing", env=_env(tmp_path)) is None


# --- joyia label store (last-4 only) ------------------------------------------

def _joyia_db(tmp_path):
    db = tmp_path / ".joyia" / "joyia.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE ai_keys (id INTEGER PRIMARY KEY, name TEXT, key TEXT)")
    conn.execute("CREATE TABLE ai_key_folder_links (id INTEGER PRIMARY KEY, folder_path TEXT, key_name TEXT, linked_at TEXT)")
    conn.execute("INSERT INTO ai_keys (name, key) VALUES (?, ?)", ("Fake-One", K1))
    conn.execute("INSERT INTO ai_keys (name, key) VALUES (?, ?)", ("Fake-Two", K2))
    conn.execute("INSERT INTO ai_key_folder_links (folder_path, key_name, linked_at) VALUES (?, ?, ?)",
                 ("/tmp/some-project", "Fake-Two", "2026-11-24"))
    conn.commit()
    conn.close()
    return db.parent


def test_joyia_labels_maps_last4_to_name_and_never_exposes_the_key(tmp_path):
    ki = load_ki()
    home = _joyia_db(tmp_path)
    labels = ki.joyia_labels(joyia_home=home)
    assert labels == {"aaaa": "Fake-One", "bbbb": "Fake-Two"}
    blob = repr(labels) + repr(ki.joyia_folder_links(joyia_home=home))
    assert K1 not in blob and K2 not in blob and "00000000" not in blob


def test_joyia_folder_links_are_a_probe_input(tmp_path):
    ki = load_ki()
    home = _joyia_db(tmp_path)
    links = ki.joyia_folder_links(joyia_home=home)
    assert links == [{"folder_path": "/tmp/some-project", "key_name": "Fake-Two"}]


# --- registry -----------------------------------------------------------------

def test_registry_round_trip_and_register(tmp_path):
    ki = load_ki()
    cfg = tmp_path / "config"
    cfg.mkdir()
    data = ki.load_registry(cfg)
    assert data == {"keys": {}}
    data = ki.register(data, "aaaa", "claude-code", now="2026-11-24T00:00:00Z")
    data = ki.register(data, "aaaa", "codex", now="2026-11-24T01:00:00Z")
    data = ki.register(data, "aaaa", "codex", now="2026-11-24T02:00:00Z")  # no dup
    ki.save_registry(cfg, data)
    again = ki.load_registry(cfg)
    assert again["keys"]["aaaa"]["apps"] == ["claude-code", "codex"]
    assert again["keys"]["aaaa"]["first_seen"] == "2026-11-24T00:00:00Z"
    mode = (cfg / "keys.json").stat().st_mode & 0o777
    assert mode == 0o600


def test_registry_save_is_atomic_and_private(tmp_path):
    ki = load_ki()
    cfg = tmp_path / "config"
    cfg.mkdir()
    ki.save_registry(cfg, {"keys": {"aaaa": {"first_seen": "t", "apps": []}}})
    assert not (cfg / "keys.json.tmp").exists()
    assert json.loads((cfg / "keys.json").read_text())["keys"]["aaaa"]["apps"] == []


# --- label resolution precedence ----------------------------------------------

def test_probe_claude_cli_subcommand_prints_host_and_hint_only(tmp_path, monkeypatch, capsys):
    ki = load_ki()
    monkeypatch.setenv("COST_TRACKER_CONFIG_DIR", str(_cfg(tmp_path)))
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://" + GW)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", K1)
    assert ki.main(["--probe-claude-cli"]) == 0
    out = capsys.readouterr().out.strip()
    assert out == GW + " aaaa"
    assert K1 not in out and "00000000" not in out
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.openai.com/v1")
    assert ki.main(["--probe-claude-cli"]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_resolve_labels_precedence_joyia_then_refusal_then_file(tmp_path):
    ki = load_ki()
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "key-labels.json").write_text(json.dumps({"cccc": "File-Label"}))
    cap_cfg = {"keys": {"bbbb": {"label": "Refusal-Label"}}}
    labels = ki.resolve_labels(["aaaa", "bbbb", "cccc", "dddd"],
                               cap_cfg=cap_cfg, config_dir=cfg,
                               joyia={"aaaa": "Joyia-Name"})
    assert labels == {"aaaa": "Joyia-Name", "bbbb": "Refusal-Label",
                      "cccc": "File-Label", "dddd": "sk-...dddd"}


def test_register_records_route():
    ki = load_ki()
    data = ki.register({"keys": {}}, "abcd", "claude-code", host="gw-a.example")
    ki.register(data, "abcd", "claude-code", host="gw-a.example")
    assert data["keys"]["abcd"]["routes"] == ["gw-a.example"]


def test_probe_register_writes_route_to_registry(tmp_path, capsys):
    ki = load_ki()
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "cap.json").write_text(json.dumps(
        {"contract": 2, "connection": {"host_map": {"gw-a.example": "fake"}}}))
    env = {"ANTHROPIC_BASE_URL": "https://gw-a.example", "ANTHROPIC_AUTH_TOKEN": "sk-xxxxabcd",
           "COST_TRACKER_CONFIG_DIR": str(cfg)}
    old = dict(os.environ)
    try:
        os.environ.update(env)
        assert ki.main(["--probe-claude-cli", "--register"]) == 0
    finally:
        os.environ.clear()
        os.environ.update(old)
    assert capsys.readouterr().out.strip() == "gw-a.example abcd"
    reg = json.loads((cfg / "keys.json").read_text())
    assert reg["keys"]["abcd"]["routes"] == ["gw-a.example"]
