#!/usr/bin/env python3
"""Real-process smoke test: drive both Alfred MCP servers with
omni-harness's own MCP client.

This is the "Definition of done" check for migration.md rows 5.2/5.3 — it
does not use any part of omni-harness's test suite or mocks; it imports the
actual client shipped in ``omni/driver/tools/mcp/`` from the sibling
``omni-harness`` checkout, starts both Alfred MCP servers as real
subprocesses exactly the way ``omni --mcp`` would (via ``.omni/mcp.json``'s
own command/args), and drives at least one real tool call per server.

No live Home Assistant is assumed. The Home Assistant server is pointed (via
the ``HA_URL``/``HA_TOKEN`` environment passed to its subprocess — the same
mechanism ``ai_server/config.py`` already reads) at the shared mock of the Home
Assistant REST API in ``ai_server/mcp/mock_ha.py``, started in this process.
``JEV_KEY_FILE`` is pointed at a file that does not exist so ``resolve_entity``
never reaches a real decision model (it degrades to ``ambiguous``). Everything from that
mock server's response onward — the subprocess, the stdio JSON-RPC framing,
the omni-harness client's discovery/tools_list/tools_call — is real.

Usage::

    python -m ai_server.mcp.smoke_test [--omni-harness PATH]

The MCP client is the one in the ``omni`` package installed in this
interpreter (omni-kernel; the old ``../omni-harness`` checkout is a husk).
``--omni-harness`` only overrides where ``omni`` is imported from. This script
is intentionally **not** part of the pytest suite: it needs ``omni`` importable,
which the self-contained tests in ``tests/`` do not require.
It also lives under ``ai_server/mcp/`` rather than a top-level ``scripts/``
directory — this repo's ``.gitignore`` excludes ``scripts/`` wholesale (it is
where ad hoc local dev scripts live), and this file is a real, checked-in
deliverable.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from ai_server.mcp.mock_ha import start_mock_ha

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# -- the smoke test itself --------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--omni-harness",
        type=Path,
        default=None,
        help=(
            "Optional path to a checkout whose `omni` package should be used. "
            "Default: the `omni` installed in this interpreter (omni-kernel)."
        ),
    )
    args = parser.parse_args()

    if args.omni_harness is not None:
        omni_path = args.omni_harness.resolve()
        if not (omni_path / "omni").is_dir():
            print(f"error: {omni_path} does not look like an omni checkout", file=sys.stderr)
            return 2
        sys.path.insert(0, str(omni_path))

    try:
        from omni.driver.tools.mcp.client import McpClient
        from omni.driver.tools.mcp.config import McpServerConfig
    except ImportError as exc:
        print(f"error: cannot import omni's MCP client ({exc})", file=sys.stderr)
        return 2

    mock_ha_url, mock_ha = start_mock_ha()
    print(f"[smoke] mock Home Assistant listening at {mock_ha_url}")

    configs = [
        McpServerConfig(
            name="home_assistant",
            command=sys.executable,
            args=["-m", "ai_server.mcp.home_assistant_server"],
            cwd=str(REPO_ROOT),
            env={
                "HA_URL": mock_ha_url,
                "HA_TOKEN": "smoke-test-token",
                "JEV_KEY_FILE": str(Path(tempfile.gettempdir()) / "alfred-smoke-no-such-key"),
            },
        ),
        McpServerConfig(
            name="alfred_plugins",
            command=sys.executable,
            args=["-m", "ai_server.mcp.plugins_server"],
            cwd=str(REPO_ROOT),
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
            "mcp__home_assistant__resolve_entity",
            "mcp__home_assistant__apply",
            "mcp__home_assistant__describe",
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

        # The LLM-free device path (J4): resolve_entity -> apply -> describe.
        # With no Jev key and several devices, resolve_entity is "ambiguous";
        # the target for apply/describe is then built by hand, as resolve_entity's
        # own "resolved" output would be.
        result = client.call_tool(
            "home_assistant",
            "resolve_entity",
            {"request": "turn on the bedroom lamp", "action": "turn-on"},
        )
        text = McpClient.text_of(result)
        print(f"[smoke] home_assistant.resolve_entity -> {text}")
        try:
            outcome = json.loads(text).get("outcome")
        except ValueError:
            outcome = None
        if McpClient.failed(result) or outcome not in ("resolved", "ambiguous", "none"):
            print("[smoke] FAIL: resolve_entity did not return an outcome object")
            ok = False

        target = json.dumps(
            {"entity_id": "switch.coffee_maker", "entity_name": "Coffee Maker"}
        )
        result = client.call_tool(
            "home_assistant",
            "apply",
            {"action": "turn-on", "target": target, "request": "make coffee"},
        )
        text = McpClient.text_of(result)
        print(f"[smoke] home_assistant.apply -> {text}")
        if McpClient.failed(result) or text != "Coffee Maker is on.":
            print("[smoke] FAIL: apply did not report 'Coffee Maker is on.'")
            ok = False

        result = client.call_tool("home_assistant", "describe", {"target": target})
        text = McpClient.text_of(result)
        print(f"[smoke] home_assistant.describe -> {text}")
        if McpClient.failed(result) or not text.startswith("Coffee Maker is on"):
            print("[smoke] FAIL: describe did not see the state apply set")
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
        mock_ha.server_close()

    print("[smoke] RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
