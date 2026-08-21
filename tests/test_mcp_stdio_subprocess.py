"""Real-process integration tests for the two MCP servers.

Unlike ``tests/test_mcp_jsonrpc.py`` (which drives the dispatcher in-process),
these spawn each server exactly as ``.omni/mcp.json`` configures omni to:
``python -m ai_server.mcp.<module>``, from the repo root, talking newline
JSON-RPC over real stdio. No mocking of ``subprocess``, no import shortcuts.

This is a lighter-weight, self-contained cousin of
``scripts/mcp_smoke_test.py`` (which drives the servers with omni-harness's
actual MCP client) — it needs nothing outside this repo, so it runs as part
of the normal test suite, while the omni-harness client smoke test is a
separate manual/documented step (see README.md).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_PROTOCOL_VERSION = "2026-07-28"


def _meta() -> dict:
    return {
        "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientInfo": {"name": "alfred-test", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }


class _StdioClient:
    """A tiny synchronous JSON-RPC-over-stdio client, for tests only."""

    def __init__(self, module: str):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", module],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(REPO_ROOT),
            text=True,
            bufsize=1,
        )
        self._next_id = 0

    def request(self, method: str, params: dict | None = None, timeout: float = 15.0) -> dict:
        self._next_id += 1
        req_id = self._next_id
        payload = dict(params or {})
        payload["_meta"] = _meta()
        body = json.dumps(
            {"jsonrpc": "2.0", "id": req_id, "method": method, "params": payload}
        )
        assert self.proc.stdin is not None
        self.proc.stdin.write(body + "\n")
        self.proc.stdin.flush()
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        if not line:
            stderr = self.proc.stderr.read() if self.proc.stderr else ""
            raise AssertionError(f"server produced no output; stderr:\n{stderr}")
        message = json.loads(line)
        assert message.get("id") == req_id
        if "error" in message:
            raise AssertionError(f"MCP error: {message['error']}")
        return message["result"]

    def close(self) -> None:
        if self.proc.stdin:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


@pytest.fixture()
def ha_client():
    client = _StdioClient("ai_server.mcp.home_assistant_server")
    try:
        yield client
    finally:
        client.close()


@pytest.fixture()
def plugins_client():
    client = _StdioClient("ai_server.mcp.plugins_server")
    try:
        yield client
    finally:
        client.close()


def test_home_assistant_server_discovers_and_lists_tools(ha_client):
    discovered = ha_client.request("server/discover")
    assert discovered["serverInfo"]["name"] == "alfred-home-assistant"

    listed = ha_client.request("tools/list")
    names = {t["name"] for t in listed["tools"]}
    assert names == {
        "turn_on",
        "turn_off",
        "toggle",
        "set_brightness",
        "set_temperature",
        "get_state",
        "list_entities",
        "health_check",
    }
    turn_on = next(t for t in listed["tools"] if t["name"] == "turn_on")
    assert "entity_id" in turn_on["inputSchema"]["required"]
    assert "entity_name" in turn_on["inputSchema"]["required"]


def test_home_assistant_health_check_round_trips_even_when_unreachable(ha_client):
    # No live Home Assistant is required for this: a request that cannot
    # reach HA still completes as a valid (failed) MCP tool result rather
    # than crashing the server or hanging.
    result = ha_client.request(
        "tools/call", {"name": "health_check", "arguments": {}}
    )
    payload = json.loads(result["content"][0]["text"])
    assert "healthy" in payload
    assert result["isError"] == (not payload["healthy"])


def test_plugins_server_discovers_the_example_plugins(plugins_client):
    listed = plugins_client.request("tools/list")
    names = {t["name"] for t in listed["tools"]}
    assert "square_root_calculator_device_integration_execute_command" in names
    assert "example_integration_execute_command" in names


def test_plugins_server_math_plugin_round_trips_for_real(plugins_client):
    result = plugins_client.request(
        "tools/call",
        {
            "name": "square_root_calculator_device_integration_execute_command",
            "arguments": {
                "action": "square_root",
                "target": "n/a",
                "parameters": {"number": 16},
            },
        },
    )
    assert result["isError"] is False
    assert "4.0" in result["content"][0]["text"]
