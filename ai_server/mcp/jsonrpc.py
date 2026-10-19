"""A minimal MCP (Model Context Protocol) stdio server, speaking the 2026-07-28
revision, with no SDK.

This is the server-side mirror of ``omni-harness``'s
``omni/driver/tools/mcp/`` client (task 2.3 there; row 5.2/5.3 here). It is
deliberately small and dependency-free: stdlib only. The wire shapes below
were read directly off that client's ``protocol.py``/``transport.py`` rather
than assumed, per the migration brief — "what you build must be what this
client can actually talk to".

What "stateless" means for a server (mirrors the client's own docstrings)
---------------------------------------------------------------------------
There is no ``initialize`` handshake and no session. Every request carries a
``_meta`` block with the caller's protocol version and capabilities, and this
server answers each request independently — nothing here remembers a caller
between calls except the process itself (started once, kept warm, exactly
like the client's own transports keep a process warm rather than one per
call).

Transport: stdio only. One JSON object per line on stdin, one JSON object
per line on stdout, ``jsonrpc: "2.0"`` envelopes. **stdout carries only
protocol frames** — all logging goes to stderr, because the client's
``StdioTransport`` reads stdout line by line and silently drops anything
that doesn't parse as JSON (which would hide a real bug rather than surface
it, so log noise on stdout is worse than useless here).

Concurrency: each incoming request is dispatched as its own ``asyncio`` task,
so a slow tool call does not block a concurrent one — matching the client's
own note that "clients may interleave unrelated requests on the same
transport". Writes to stdout are serialised with a lock so replies never
interleave mid-line.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "MCP_PROTOCOL_VERSION",
    "META_PROTOCOL_VERSION",
    "RESULT_COMPLETE",
    "UNSUPPORTED_PROTOCOL_VERSION",
    "METHOD_NOT_FOUND",
    "INVALID_PARAMS",
    "ToolCallResult",
    "ToolSpec",
    "McpStdioServer",
]

#: The revision this server speaks — must match the client's
#: ``omni.driver.tools.mcp.protocol.MCP_PROTOCOL_VERSION`` exactly, since the
#: client's discovery probe treats any other answer (or an error) as "this is
#: a pre-2026-07-28 server" and skips it.
MCP_PROTOCOL_VERSION = "2026-07-28"

#: The ``_meta`` keys the client writes, read verbatim off
#: ``omni/driver/tools/mcp/protocol.py`` so a typo here can't produce a
#: plausible-looking key the client never actually sends.
META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

RESULT_COMPLETE = "complete"

# JSON-RPC / MCP reserved error codes (specification "Error Codes", mirrored
# from the client's protocol.py).
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
UNSUPPORTED_PROTOCOL_VERSION = -32022

METHOD_DISCOVER = "server/discover"
METHOD_TOOLS_LIST = "tools/list"
METHOD_TOOLS_CALL = "tools/call"
NOTIFICATION_CANCELLED = "notifications/cancelled"


@dataclass(frozen=True)
class ToolCallResult:
    """What a tool handler returns: the text a model reads, and whether it
    is a *tool execution* failure (``isError``) as opposed to a protocol
    failure (which is a raised exception instead — see ``ToolSpec``).
    """

    content_text: str
    is_error: bool = False


ToolHandler = Callable[[Mapping[str, Any]], Awaitable[ToolCallResult]]


@dataclass
class ToolSpec:
    """One MCP tool: what ``tools/list`` advertises and what ``tools/call``
    runs.

    ``description`` is the single most load-bearing string here for any
    state-changing tool — it is what a human approving the call reads, per
    the migration brief. It should say, in plain language, what physically
    happens and to what, not just name the HTTP verb underneath it.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler

    def descriptor(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class McpStdioServer:
    """Serves ``server/discover``, ``tools/list`` and ``tools/call`` over stdio.

    Args:
        server_name: reported in ``server/discover``'s ``serverInfo``.
        server_version: ditto.
        tools: the tools this server exposes, in the order ``tools/list``
            reports them (the specification's own advice: deterministic
            order helps prompt-cache hit rates).
    """

    def __init__(
        self,
        server_name: str,
        server_version: str,
        tools: list[ToolSpec],
    ) -> None:
        self._server_name = server_name
        self._server_version = server_version
        self._tools = {t.name: t for t in tools}
        self._tool_order = [t.name for t in tools]
        self._write_lock: asyncio.Lock | None = None
        self._tasks: dict[Any, asyncio.Task[None]] = {}
        self._tasks_lock: asyncio.Lock | None = None

    # -- entry point --------------------------------------------------

    def run(self) -> None:
        """Configure stderr-only logging and block running the server."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            stream=sys.stderr,
        )
        try:
            asyncio.run(self.serve())
        except KeyboardInterrupt:  # pragma: no cover - interactive only
            pass

    async def serve(self) -> None:
        self._write_lock = asyncio.Lock()
        self._tasks_lock = asyncio.Lock()
        logger.info(
            "MCP server %r starting (%d tool(s)): %s",
            self._server_name,
            len(self._tool_order),
            ", ".join(self._tool_order),
        )
        loop = asyncio.get_running_loop()
        try:
            while True:
                line = await loop.run_in_executor(None, sys.stdin.buffer.readline)
                if not line:
                    logger.info("stdin closed; MCP server %r exiting", self._server_name)
                    break
                line = line.strip()
                if not line:
                    continue
                await self._dispatch_line(line)
        finally:
            async with self._tasks_lock:
                tasks = list(self._tasks.values())
            for task in tasks:
                task.cancel()

    # -- dispatch -------------------------------------------------------

    async def _dispatch_line(self, line: bytes) -> None:
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.warning("unparseable line on stdin, dropped")
            return
        if not isinstance(message, dict):
            logger.warning("non-object message on stdin, dropped")
            return

        request_id = message.get("id")
        method = message.get("method")

        if request_id is None:
            # A notification: no reply is possible or expected.
            if method == NOTIFICATION_CANCELLED:
                await self._handle_cancel(message)
            else:
                logger.debug("ignoring notification %r", method)
            return

        task = asyncio.ensure_future(self._handle_request(request_id, method, message))
        async with self._tasks_lock:
            self._tasks[request_id] = task
        task.add_done_callback(lambda _t, rid=request_id: self._tasks.pop(rid, None))

    async def _handle_cancel(self, message: Mapping[str, Any]) -> None:
        params = message.get("params")
        target_id = params.get("requestId") if isinstance(params, Mapping) else None
        async with self._tasks_lock:
            task = self._tasks.get(target_id)
        if task is not None and not task.done():
            logger.info("cancelling request id=%r", target_id)
            task.cancel()

    async def _handle_request(
        self, request_id: Any, method: Any, message: Mapping[str, Any]
    ) -> None:
        try:
            result = await self._route(method, message)
        except asyncio.CancelledError:
            # No reply for a cancelled call: the client already stopped
            # waiting (TransportCancelled on its side) and a stray reply to
            # a dead request id would just be logged and dropped there.
            return
        except _McpError as exc:
            await self._write_error(request_id, exc.code, str(exc), exc.data)
            return
        except Exception as exc:  # noqa: BLE001 - a broken handler is a protocol error
            logger.exception("unhandled error in %r", method)
            await self._write_error(request_id, INVALID_PARAMS, str(exc), None)
            return
        await self._write_result(request_id, result)

    async def _route(self, method: Any, message: Mapping[str, Any]) -> dict[str, Any]:
        params = message.get("params")
        params = params if isinstance(params, Mapping) else {}
        _check_meta(params)

        if method == METHOD_DISCOVER:
            return self._discover_result()
        if method == METHOD_TOOLS_LIST:
            return self._tools_list_result()
        if method == METHOD_TOOLS_CALL:
            return await self._tools_call_result(params)
        raise _McpError(METHOD_NOT_FOUND, f"unknown method {method!r}")

    # -- method implementations -----------------------------------------

    def _discover_result(self) -> dict[str, Any]:
        return {
            "serverInfo": {
                "name": self._server_name,
                "version": self._server_version,
            },
            "capabilities": {"tools": {}},
        }

    def _tools_list_result(self) -> dict[str, Any]:
        return {
            "resultType": RESULT_COMPLETE,
            "tools": [self._tools[name].descriptor() for name in self._tool_order],
        }

    async def _tools_call_result(self, params: Mapping[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or name not in self._tools:
            raise _McpError(INVALID_PARAMS, f"unknown tool {name!r}")
        arguments = params.get("arguments")
        arguments = arguments if isinstance(arguments, Mapping) else {}
        tool = self._tools[name]
        try:
            outcome = await tool.handler(arguments)
        except Exception as exc:  # noqa: BLE001 - a tool failure, not a protocol one
            logger.exception("tool %r raised", name)
            outcome = ToolCallResult(content_text=str(exc), is_error=True)
        return {
            "resultType": RESULT_COMPLETE,
            "content": [{"type": "text", "text": outcome.content_text}],
            "isError": outcome.is_error,
        }

    # -- writing ----------------------------------------------------------

    async def _write_result(self, request_id: Any, result: Mapping[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "id": request_id, "result": dict(result)})

    async def _write_error(
        self, request_id: Any, code: int, message: str, data: Any
    ) -> None:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        await self._write({"jsonrpc": "2.0", "id": request_id, "error": error})

    async def _write(self, message: Mapping[str, Any]) -> None:
        line = json.dumps(message, separators=(",", ":")) + "\n"
        assert self._write_lock is not None
        async with self._write_lock:
            sys.stdout.write(line)
            sys.stdout.flush()


class _McpError(RuntimeError):
    """A JSON-RPC error frame to send back, raised internally by dispatch."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


def _check_meta(params: Mapping[str, Any]) -> None:
    """Validate the caller's ``_meta`` block.

    Missing ``_meta`` (or a missing protocol version inside it) is a
    malformed request under the specification, which requires ``_meta`` on
    every call — refused with ``INVALID_PARAMS``. A *present but different*
    protocol version is not malformed, it is a caller this server does not
    speak to, refused with ``UnsupportedProtocolVersionError`` naming the one
    version this server supports — exactly the shape the client's own
    ``supported_versions()`` reads.
    """
    meta = params.get("_meta")
    if not isinstance(meta, Mapping):
        raise _McpError(INVALID_PARAMS, "request is missing the required '_meta' block")
    version = meta.get(META_PROTOCOL_VERSION)
    if version != MCP_PROTOCOL_VERSION:
        raise _McpError(
            UNSUPPORTED_PROTOCOL_VERSION,
            f"this server speaks {MCP_PROTOCOL_VERSION!r}, not {version!r}",
            data={"supported": [MCP_PROTOCOL_VERSION]},
        )
