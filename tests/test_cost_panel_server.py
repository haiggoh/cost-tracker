"""Deliverable B tests: bin/cost-panel-server.py over real stdio JSON-RPC.

ARCHIVED with the abandoned MCP-server variant — kept for reference, not run
by the ship gate (the pane mod replaced this route).

The server runs under the pinned official MCP SDK via `uv run --with mcp==<pin>`
(uv is installed; the pin is read from the server itself with --print-sdk-pin),
so the suite needs no pre-made venv. Named discriminants (plan §B1/B2):
  test_thread_entrypoint_declared_on_tools      (registration shape)
  test_ui_resource_served_with_mcp_app_mime
  test_snapshot_unknown_and_stale_states_are_visible (tool results, not decoration)
"""
import json
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER = ROOT / "bin" / "cost-panel-server.py"


def sdk_pin():
    out = subprocess.run([sys.executable, str(SERVER), "--print-sdk-pin"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def server_cmd():
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv not installed; cannot run the pinned MCP SDK")
    return [uv, "run", "--quiet", "--with", "mcp==" + sdk_pin(), "--no-project",
            sys.executable, str(SERVER)]


class Mcp:
    def __init__(self):
        self.p = subprocess.Popen(server_cmd(), stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, bufsize=1)
        self._id = 0

    def call(self, method, params=None):
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            req["params"] = params
        self.p.stdin.write(json.dumps(req) + "\n")
        self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("server exited")
            msg = json.loads(line)
            if msg.get("id") == self._id:
                return msg

    def notify(self, method):
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.p.stdin.flush()

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


@pytest.fixture
def mcp():
    c = Mcp()
    c.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                          "clientInfo": {"name": "test", "version": "0"}})
    c.notify("notifications/initialized")
    yield c
    c.close()


@pytest.fixture
def scope_config(tmp_path, monkeypatch):
    config = tmp_path / "config"
    (config / "shared-snapshots").mkdir(parents=True)
    (config / "budget-scopes.json").write_text(
        json.dumps({"scopes": [{"id": "joyia-shared", "label": "JoyIA shared key"}]}))
    monkeypatch.setenv("COST_TRACKER_CONFIG_DIR", str(config))
    monkeypatch.setenv("COST_TRACKER_TODAY", "2026-11-21")
    monkeypatch.setenv("COST_TRACKER_PANEL_UI", str(ROOT / "ui" / "dist" / "panel.html"))
    monkeypatch.setenv("COST_TRACKER_CODEX_HOME", str(tmp_path / "no-codex"))
    return config


def write_snap(config, **over):
    snap = {
        "schema": "cost-tracker/shared-snapshot/1", "snapshot_id": "t1",
        "scope_id": "joyia-shared", "scope_label": "JoyIA shared key",
        "day_utc": "2026-11-21", "estimated_usd": "31.370000", "combined_usd": 31.37,
        "by_host": {"claude_code": 30.9, "codex": 0.47, "claude_desktop": None, "other": None},
        "unassigned_usd": None, "gateway_confirmed_usd": None, "gateway_observed_at": None,
        "gateway_source": None, "reserved_usd": None, "cap_usd": 40.0, "discrepancy_usd": None,
        "last_refreshed_at": "2026-11-21T10:00:00+00:00", "pricing_sources": {},
        "coverage": {"complete": True, "missing_sources": [], "unpriced_requests": 0,
                     "ambiguous_overlap": False, "inferred_bindings": False,
                     "freshness": "live-at-write"},
        "diagnostics": [], "primary_amount_usd": 31.37,
        "primary_basis": "local estimate (coverage complete)",
        "budget_state": "unknown", "headroom_usd": None,
    }
    snap.update(over)
    p = config / "shared-snapshots" / "joyia-shared-2026-11-21.json"
    p.write_text(json.dumps(snap))
    return snap


def tool_names(mcp):
    r = mcp.call("tools/list")
    return {t["name"]: t for t in r["result"]["tools"]}


def test_thread_entrypoint_declared_on_tools(mcp, scope_config):
    tools = tool_names(mcp)
    assert {"get_cost_snapshot", "refresh_cost_snapshot", "show_cost_panel"} <= set(tools)
    for name in ("get_cost_snapshot", "refresh_cost_snapshot", "show_cost_panel"):
        meta = tools[name].get("_meta", {})
        assert meta.get("ui", {}).get("resourceUri") == "ui://cost-tracker/panel.html", name
        eps = meta.get("openai/ui", {}).get("entrypoints", [])
        assert {"type": "thread"} in eps, name


def test_ui_resource_served_with_mcp_app_mime(mcp, scope_config):
    r = mcp.call("resources/list")
    uris = {res["uri"]: res for res in r["result"]["resources"]}
    assert "ui://cost-tracker/panel.html" in uris
    r = mcp.call("resources/read", {"uri": "ui://cost-tracker/panel.html"})
    c = r["result"]["contents"][0]
    assert c["mimeType"] == "text/html;profile=mcp-app"
    assert "<div id=\"root\">" in c["text"] and "cost-tracker" in c["text"]


def test_get_snapshot_returns_cached_generation(mcp, scope_config):
    write_snap(scope_config)
    r = mcp.call("tools/call", {"name": "get_cost_snapshot",
                                "arguments": {}})
    payload = json.loads(r["result"]["content"][0]["text"])
    assert payload["snapshot"]["snapshot_id"] == "t1"
    assert payload["snapshot"]["primary_amount_usd"] == 31.37
    assert payload["stale"] is False
    # no credential-shaped strings in tool-visible output
    assert "sk-" not in r["result"]["content"][0]["text"]


def test_snapshot_unknown_and_stale_states_are_visible(mcp, scope_config):
    # no cache yet -> explicit error, never a fabricated zero
    r = mcp.call("tools/call", {"name": "get_cost_snapshot", "arguments": {}})
    payload = json.loads(r["result"]["content"][0]["text"])
    assert payload["snapshot"] is None and "no cached snapshot" in payload["error"]
    # stale cache -> flagged, value kept
    write_snap(scope_config, last_refreshed_at="2020-01-01T00:00:00+00:00")
    r = mcp.call("tools/call", {"name": "get_cost_snapshot", "arguments": {}})
    payload = json.loads(r["result"]["content"][0]["text"])
    assert payload["stale"] is True and payload["snapshot"]["primary_amount_usd"] == 31.37


def test_show_panel_points_at_resource_and_carries_snapshot(mcp, scope_config):
    write_snap(scope_config)
    r = mcp.call("tools/call", {"name": "show_cost_panel", "arguments": {}})
    payload = json.loads(r["result"]["content"][0]["text"])
    assert payload["panelUri"] == "ui://cost-tracker/panel.html"
    assert payload["mimeType"] == "text/html;profile=mcp-app"
    assert payload["snapshot"]["scope_id"] == "joyia-shared"


def test_unknown_explicit_scope_fails_loudly(mcp, scope_config):
    r = mcp.call("tools/call", {"name": "get_cost_snapshot",
                                "arguments": {"scope_id": "nope"}})
    assert r["result"].get("isError") is True
    assert "unknown scope id" in r["result"]["content"][0]["text"]


def test_help_and_flags():
    for flag, needle in (("--help", "MCP server"), ("--print-sdk-pin", "1."),
                         ("--check-ui", "ui present")):
        r = subprocess.run([sys.executable, str(SERVER), flag],
                           capture_output=True, text=True)
        assert r.returncode == 0 and needle in r.stdout + r.stderr, flag
    r = subprocess.run([sys.executable, str(SERVER), "--bogus"],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "usage" in r.stderr.lower()
