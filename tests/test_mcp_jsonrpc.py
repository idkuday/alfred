"""Unit tests for the generic MCP stdio JSON-RPC engine (ai_server/mcp/jsonrpc.py).

These drive the server's dispatch/routing directly (bypassing the real
stdin/stdout loop) by feeding raw JSON-RPC lines through ``_dispatch_line``
and capturing what would have been written, which keeps the tests fast and
independent of any real subprocess or the omni-harness client. The real
client round trip is covered separately (see the module docstring in
``scripts/mcp_smoke_test.py`` and ``tests/test_mcp_stdio_subprocess.py``).
"""
import asyncio
import json

import pytest

from ai_server.mcp.jsonrpc import (
    INVALID_PARAMS,
    MCP_PROTOCOL_VERSION,
    META_CLIENT_CAPABILITIES,
    META_CLIENT_INFO,
    META_PROTOCOL_VERSION,
    METHOD_NOT_FOUND,
    RESULT_COMPLETE,
    UNSUPPORTED_PROTOCOL_VERSION,
    McpStdioServer,
    ToolCallResult,
    ToolSpec,
)


def _meta(version: str = MCP_PROTOCOL_VERSION) -> dict:
    return {
        META_PROTOCOL_VERSION: version,
        META_CLIENT_INFO: {"name": "test-client", "version": "1"},
        META_CLIENT_CAPABILITIES: {},
    }


async def _drive(server: McpStdioServer, message: dict) -> list[dict]:
    """Feed one JSON-RPC message through the server and return what it wrote."""
    server._write_lock = asyncio.Lock()
    server._tasks_lock = asyncio.Lock()
    written: list[dict] = []

    async def fake_write(msg):
        written.append(dict(msg))

    server._write = fake_write  # type: ignore[assignment]
    line = json.dumps(message).encode("utf-8")
    await server._dispatch_line(line)
    # Wait for the request's task (if any) to finish before inspecting output.
    for task in list(server._tasks.values()):
        await task
    return written


def _echo_tool(calls: list) -> ToolSpec:
    async def handler(args: dict) -> ToolCallResult:
        calls.append(dict(args))
        return ToolCallResult(content_text=f"echo:{args.get('value')}")

    return ToolSpec(
        name="echo",
        description="Echoes its 'value' argument back.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        handler=handler,
    )


def _failing_tool() -> ToolSpec:
    async def handler(_args: dict) -> ToolCallResult:
        return ToolCallResult(content_text="boom", is_error=True)

    return ToolSpec(
        name="fail",
        description="Always reports a tool-execution failure.",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )


def _raising_tool() -> ToolSpec:
    async def handler(_args: dict) -> ToolCallResult:
        raise RuntimeError("kaboom")

    return ToolSpec(
        name="raise",
        description="Always raises.",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )


@pytest.mark.asyncio
async def test_discover_returns_server_info():
    server = McpStdioServer("test-server", "9.9", [])
    written = await _drive(
        server,
        {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": _meta()}},
    )
    assert len(written) == 1
    result = written[0]["result"]
    assert result["serverInfo"]["name"] == "test-server"
    assert result["serverInfo"]["version"] == "9.9"


@pytest.mark.asyncio
async def test_tools_list_reports_registered_tools_in_order():
    calls: list = []
    server = McpStdioServer("test-server", "1", [_echo_tool(calls), _failing_tool()])
    written = await _drive(
        server,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"_meta": _meta()}},
    )
    result = written[0]["result"]
    assert result["resultType"] == RESULT_COMPLETE
    names = [t["name"] for t in result["tools"]]
    assert names == ["echo", "fail"]
    assert result["tools"][0]["description"].startswith("Echoes")
    assert result["tools"][0]["inputSchema"]["required"] == ["value"]


@pytest.mark.asyncio
async def test_tools_call_success_round_trip():
    calls: list = []
    server = McpStdioServer("test-server", "1", [_echo_tool(calls)])
    written = await _drive(
        server,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"value": "hi"}, "_meta": _meta()},
        },
    )
    result = written[0]["result"]
    assert result["isError"] is False
    assert result["content"] == [{"type": "text", "text": "echo:hi"}]
    assert calls == [{"value": "hi"}]


@pytest.mark.asyncio
async def test_tools_call_reports_tool_execution_failure_not_protocol_error():
    server = McpStdioServer("test-server", "1", [_failing_tool()])
    written = await _drive(
        server,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "fail", "arguments": {}, "_meta": _meta()},
        },
    )
    result = written[0]["result"]
    assert "error" not in written[0]
    assert result["isError"] is True
    assert result["content"][0]["text"] == "boom"


@pytest.mark.asyncio
async def test_tools_call_handler_exception_becomes_failed_result():
    server = McpStdioServer("test-server", "1", [_raising_tool()])
    written = await _drive(
        server,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "raise", "arguments": {}, "_meta": _meta()},
        },
    )
    result = written[0]["result"]
    assert result["isError"] is True
    assert "kaboom" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_tools_call_unknown_tool_is_a_protocol_error():
    server = McpStdioServer("test-server", "1", [])
    written = await _drive(
        server,
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "nope", "arguments": {}, "_meta": _meta()},
        },
    )
    assert written[0]["error"]["code"] == INVALID_PARAMS


@pytest.mark.asyncio
async def test_unknown_method_is_method_not_found():
    server = McpStdioServer("test-server", "1", [])
    written = await _drive(
        server,
        {"jsonrpc": "2.0", "id": 7, "method": "nope/nope", "params": {"_meta": _meta()}},
    )
    assert written[0]["error"]["code"] == METHOD_NOT_FOUND


@pytest.mark.asyncio
async def test_missing_meta_is_invalid_params():
    server = McpStdioServer("test-server", "1", [])
    written = await _drive(
        server,
        {"jsonrpc": "2.0", "id": 8, "method": "server/discover", "params": {}},
    )
    assert written[0]["error"]["code"] == INVALID_PARAMS


@pytest.mark.asyncio
async def test_wrong_protocol_version_is_unsupported_protocol_version():
    server = McpStdioServer("test-server", "1", [])
    written = await _drive(
        server,
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "server/discover",
            "params": {"_meta": _meta(version="2024-01-01")},
        },
    )
    error = written[0]["error"]
    assert error["code"] == UNSUPPORTED_PROTOCOL_VERSION
    assert error["data"]["supported"] == [MCP_PROTOCOL_VERSION]


@pytest.mark.asyncio
async def test_notification_gets_no_reply():
    server = McpStdioServer("test-server", "1", [])
    written = await _drive(
        server,
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}},
    )
    assert written == []
