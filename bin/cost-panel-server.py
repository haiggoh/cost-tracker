#!/usr/bin/env python3
"""cost-panel-server — local MCP server exposing the shared budget snapshot as an
MCP App conversation panel (thread entrypoint).

ARCHIVED VARIANT — abandoned 2026-11-21: the user does not want any plugin to
create an MCP server ("avoid creating any mcps for any of my plugins"), so the
panel shipped as a pane mod (hooks/cost-panel-register.tsx) instead. Kept in git
history for reference only; do not wire up. See the commit that added this file.

Deliverable B of the shared-key budget plan. This server is THIN: it never prices
anything and never creates a second ledger. It reads/writes the ONE cached
snapshot (bin/shared_snapshot.py, schema cost-tracker/shared-snapshot/1) that
the CLI, statusline and warnings already consume, and serves the bundled UI
resource ui://cost-tracker/panel.html (MIME text/html;profile=mcp-app).

Tools (all annotated honestly; no client-supplied paths, shell or credentials):
  get_cost_snapshot(scope_id?)     cached snapshot, read-only
  refresh_cost_snapshot(scope_id?) bounded local refresh via the single-flight
                                   producer path, then the new cached snapshot
  show_cost_panel(scope_id?)       tool result points at the UI resource; hosts
                                   with MCP Apps render it as a thread panel

Run `cost-panel-server.py --help`. Requires the official MCP Python SDK
(pinned MCP_SDK_PIN below); run via `uv run --with mcp==<pin> python cost-panel-server.py`
or a venv that has it (install.sh creates one).

Environment (all optional, shared with cost-tracker):
  COST_TRACKER_CONFIG_DIR   default ~/.claude/cost-tracker (scopes + snapshots)
  COST_TRACKER_CODEX_HOME   for refresh imports
  COST_TRACKER_PANEL_UI     override the built UI html path (tests)
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys

MCP_SDK_PIN = "1.30.0"  # latest 1.x with the stable FastMCP API (2.x renamed it)
PANEL_URI = "ui://cost-tracker/panel.html"
PANEL_MIME = "text/html;profile=mcp-app"
TOOL_META = {
    "ui": {"resourceUri": PANEL_URI},
    "openai/ui": {"entrypoints": [{"type": "thread"}]},
}


def _here():
    return os.path.dirname(os.path.realpath(__file__))


def _shared_snapshot():
    import importlib.util
    path = os.path.join(_here(), "shared_snapshot.py")
    spec = importlib.util.spec_from_file_location("shared_snapshot", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _panel_html():
    override = os.environ.get("COST_TRACKER_PANEL_UI")
    if override is not None:
        if not override.strip():
            raise ValueError("COST_TRACKER_PANEL_UI must not be empty")
        if not os.path.isfile(override):
            raise ValueError("COST_TRACKER_PANEL_UI is not an existing file")
        return pathlib.Path(override).read_text()
    p = pathlib.Path(_here()).parent / "ui" / "dist" / "panel.html"
    if p.is_file():
        return p.read_text()
    # The CLI works without the UI build; the resource then says so honestly.
    return ("<!doctype html><meta charset='utf-8'><title>cost panel</title>"
            "<p>cost-tracker panel UI not built (ui/dist/panel.html missing). "
            "Run ui/build.mjs. Data tools still work.</p>")


def _resolve_scope(scope_id):
    mod = _shared_snapshot()
    return mod.resolve_scope(scope_id)  # raises ValueError loudly on bad/explicit-unknown


def _today():
    return os.environ.get("COST_TRACKER_TODAY") or _shared_snapshot().datetime.now(
        _shared_snapshot().timezone.utc).date().isoformat()


def get_snapshot(scope_id=None):
    scope = _resolve_scope(scope_id)
    if scope is None:
        return {"error": "no shared scope configured (budget-scopes.json)",
                "snapshot": None}
    snap = _shared_snapshot().read_cached_snapshot(scope["id"], _today())
    if snap is None:
        return {"error": "no cached snapshot yet; call refresh_cost_snapshot",
                "snapshot": None, "scope": scope}
    return {"scope": scope, "snapshot": snap, "stale": _shared_snapshot().is_stale(snap)}


def refresh_snapshot(scope_id=None):
    scope = _resolve_scope(scope_id)
    if scope is None:
        return {"error": "no shared scope configured (budget-scopes.json)",
                "snapshot": None}
    # Bounded, single-flight producer: the CLI's detached-refresh entry point does a
    # full import (<=20s) and rewrites the cache; here we call it synchronously.
    cli = os.path.join(_here(), "cost-tracker")
    env = dict(os.environ)
    env.pop("COST_TRACKER_NO_SPAWN", None)
    try:
        subprocess.run([sys.executable, cli, "statusline", "--refresh-shared"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=90, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass  # a failed refresh leaves the previous cache in place; report honestly
    return get_snapshot(scope["id"])


def show_panel(scope_id=None):
    got = get_snapshot(scope_id)
    out = {"panelUri": PANEL_URI, "mimeType": PANEL_MIME}
    out.update(got)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Local MCP server serving the cost-tracker shared budget snapshot "
                    "as an MCP App thread panel. Data source: the cached snapshot only.")
    ap.add_argument("--print-sdk-pin", action="store_true",
                    help="print the pinned MCP Python SDK version and exit")
    ap.add_argument("--check-ui", action="store_true",
                    help="print whether the built UI resource is present and exit")
    args = ap.parse_args(argv)
    if args.print_sdk_pin:
        print(MCP_SDK_PIN)
        return 0
    if args.check_ui:
        try:
            _panel_html()
            print("ui present")
            return 0
        except (OSError, ValueError) as e:
            print("ui missing: " + str(e), file=sys.stderr)
            return 1

    from mcp.server.fastmcp import FastMCP  # official SDK, pinned (see --print-sdk-pin)

    mcp = FastMCP("cost-tracker")

    @mcp.resource(PANEL_URI, mime_type=PANEL_MIME,
                  name="cost-tracker panel", description="Shared budget snapshot panel UI")
    def panel_resource():
        return _panel_html()

    @mcp.tool(meta=TOOL_META,
              description="Read the cached shared-scope budget snapshot for today "
                          "(read-only; never scans or infers).")
    def get_cost_snapshot(scope_id: str = "") -> str:
        return json.dumps(get_snapshot(scope_id or None), sort_keys=True)

    @mcp.tool(meta=TOOL_META,
              description="Refresh the shared-scope snapshot via the bounded single-flight "
                          "producer (local import only, no inference), then return it.")
    def refresh_cost_snapshot(scope_id: str = "") -> str:
        return json.dumps(refresh_snapshot(scope_id or None), sort_keys=True)

    @mcp.tool(meta=TOOL_META,
              description="Open the cost panel: returns the ui:// panel resource URI for "
                          "hosts that render MCP Apps as a thread panel, plus the snapshot.")
    def show_cost_panel(scope_id: str = "") -> str:
        return json.dumps(show_panel(scope_id or None), sort_keys=True)

    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
