#!/usr/bin/env python3
"""Real-process smoke test: drive both Alfred MCP servers with
omni-harness's own MCP client.

This is the "Definition of done" check for migration.md rows 5.2/5.3 — it
does not use any part of omni-harness's test suite or mocks; it imports the
actual client shipped in ``omni/driver/tools/mcp/`` from the sibling
``omni-harness`` checkout, starts both Alfred MCP servers as real
subprocesses exactly the way ``omni --mcp`` would (via ``.omni/mcp.json``'s
own command/args), and drives at least one real tool call per server.

No live Home Assistant is assumed. Since none was reachable from this
machine when this was built, the Home Assistant server is pointed (via the
``HA_URL``/``HA_TOKEN`` environment passed to its subprocess — the same
mechanism ``ai_server/config.py`` already reads) at a tiny built-in mock of
the Home Assistant REST API started in this process. Everything from that
mock server's response onward — the subprocess, the stdio JSON-RPC framing,
the omni-harness client's discovery/tools_list/tools_call — is real.

Usage::

    python -m ai_server.mcp.smoke_test [--omni-harness PATH]

``--omni-harness`` defaults to ``../omni-harness`` relative to this repo,
matching the sibling-checkout layout the migration doc assumes. This script
is intentionally **not** part of the pytest suite: it requires that sibling
checkout to be present, which the self-contained tests in ``tests/`` do not.
It also lives under ``ai_server/mcp/`` rather than a top-level ``scripts/``
directory — this repo's ``.gitignore`` excludes ``scripts/`` wholesale (it is
where ad hoc local dev scripts live), and this file is a real, checked-in
deliverable.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# -- a tiny mock of the Home Assistant REST API ----------------------------

_MOCK_STATES = {
    "light.bedroom_lamp": {
        "entity_id": "light.bedroom_lamp",
        "state": "off",
        "attributes": {"friendly_name": "Bedroom Lamp"},
    },
    "climate.living_room": {
        "entity_id": "climate.living_room",
        "state": "heat",
        "attributes": {"friendly_name": "Living Room Thermostat", "temperature": 68},
    },
}


class _MockHaHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
        pass  # keep the smoke test's own output readable

    def _json(self, status: int, body) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802 - stdlib method name
        if self.path == "/api/":
            self._json(200, {"message": "API running (mock)."})
        elif self.path == "/api/states":
            self._json(200, list(_MOCK_STATES.values()))
        elif self.path.startswith("/api/states/"):
            entity_id = self.path.removeprefix("/api/states/")
            state = _MOCK_STATES.get(entity_id)
            if state is None:
                self._json(404, {"message": "not found"})
            else:
                self._json(200, state)
        else:
            self._json(404, {"message": "not found"})

    def do_POST(self):  # noqa: N802 - stdlib method name
        if self.path.startswith("/api/services/"):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            entity_id = body.get("entity_id")
            if entity_id in _MOCK_STATES:
                _MOCK_STATES[entity_id]["state"] = "on"
            self._json(200, [_MOCK_STATES.get(entity_id, {})])
        else:
            self._json(404, {"message": "not found"})


def start_mock_ha() -> tuple[http.server.HTTPServer, str]:
    server = http.server.HTTPServer(("127.0.0.1", 0), _MockHaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


# -- the smoke test itself --------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--omni-harness",
        type=Path,
        default=REPO_ROOT.parent / "omni-harness",
        help="Path to the omni-harness checkout (default: ../omni-harness).",
    )
    args = parser.parse_args()

    omni_path = args.omni_harness.resolve()
    if not (omni_path / "omni").is_dir():
        print(f"error: {omni_path} does not look like an omni-harness checkout", file=sys.stderr)
        return 2
    sys.path.insert(0, str(omni_path))

    from omni.driver.tools.mcp.client import McpClient
    from omni.driver.tools.mcp.config import McpServerConfig

    mock_ha, mock_ha_url = start_mock_ha()
    print(f"[smoke] mock Home Assistant listening at {mock_ha_url}")

    # McpTransport's stdio driver inherits *this* process's cwd for the
    # subprocess it spawns (McpServerConfig has no cwd field) — the same
    # thing omni does when a user runs `omni --mcp` from the repo root.
    os.chdir(REPO_ROOT)

    configs = [
        McpServerConfig(
            name="home_assistant",
            command=sys.executable,
            args=["-m", "ai_server.mcp.home_assistant_server"],
            env={"HA_URL": mock_ha_url, "HA_TOKEN": "smoke-test-token"},
        ),
        McpServerConfig(
            name="alfred_plugins",
            command=sys.executable,
            args=["-m", "ai_server.mcp.plugins_server"],
        ),
    ]

    client = McpClient(configs)
    ok = True
    try:
        client.start()

        print("[smoke] server status:")
        for status in client.server_status():
            print(f"  {status}")
            if not status["connected"]:
                ok = False

        tools = client.tools()
        print(f"[smoke] {len(tools)} tool(s) discovered across all servers:")
        for tool in tools:
            print(f"  {tool.name}")

        expected_ha_tools = {
            "mcp__home_assistant__turn_on",
            "mcp__home_assistant__turn_off",
            "mcp__home_assistant__toggle",
            "mcp__home_assistant__set_brightness",
            "mcp__home_assistant__set_temperature",
            "mcp__home_assistant__get_state",
            "mcp__home_assistant__list_entities",
            "mcp__home_assistant__health_check",
        }
        got_ha_tools = {t.name for t in tools if t.server_name == "home_assistant"}
        if got_ha_tools != expected_ha_tools:
            print(f"[smoke] FAIL: home_assistant tool set mismatch: {got_ha_tools}")
            ok = False

        if not any(
            t.server_name == "alfred_plugins"
            and t.mcp_tool_name == "square_root_calculator_device_integration_execute_command"
            for t in tools
        ):
            print("[smoke] FAIL: alfred_plugins did not expose the math plugin")
            ok = False

        # Read-only round trip #1: list_entities against the mocked HA API.
        result = client.call_tool("home_assistant", "list_entities", {})
        text = McpClient.text_of(result)
        print(f"[smoke] home_assistant.list_entities -> {text}")
        if McpClient.failed(result) or "light.bedroom_lamp" not in text:
            print("[smoke] FAIL: list_entities did not round-trip the mocked entity")
            ok = False

        # Read-only round trip #2: get_state against the mocked HA API.
        result = client.call_tool(
            "home_assistant", "get_state", {"entity_id": "climate.living_room"}
        )
        text = McpClient.text_of(result)
        print(f"[smoke] home_assistant.get_state -> {text}")
        if McpClient.failed(result) or "Living Room Thermostat" not in text:
            print("[smoke] FAIL: get_state did not round-trip the mocked entity")
            ok = False

        # Write round trip, to prove the physical-effect path end to end too
        # (against the mock — no real device exists to affect).
        result = client.call_tool(
            "home_assistant",
            "turn_on",
            {"entity_id": "light.bedroom_lamp", "entity_name": "Bedroom Lamp"},
        )
        text = McpClient.text_of(result)
        print(f"[smoke] home_assistant.turn_on -> {text}")
        if McpClient.failed(result):
            print("[smoke] FAIL: turn_on did not succeed against the mock")
            ok = False

        # Real round trip through the plugins sidecar: the math plugin, for
        # real, no mocking involved on that side.
        result = client.call_tool(
            "alfred_plugins",
            "square_root_calculator_device_integration_execute_command",
            {"action": "square_root", "target": "n/a", "parameters": {"number": 16}},
        )
        text = McpClient.text_of(result)
        print(f"[smoke] alfred_plugins.square_root -> {text}")
        if McpClient.failed(result) or "4.0" not in text:
            print("[smoke] FAIL: math plugin round trip did not produce 4.0")
            ok = False

    finally:
        client.close()
        mock_ha.shutdown()

    print("[smoke] RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
