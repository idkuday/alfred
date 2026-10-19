"""Alfred's existing plugins behind one MCP sidecar (migration.md row 5.3).

**No plugin rewrite.** This discovers plugins through
:class:`ai_server.plugins.PluginManager` — the same loader
``ai_server/main.py`` uses — and re-exposes each discovered
:class:`~ai_server.integration.base.DeviceIntegration` as a small family of
MCP tools that call straight through to the plugin's own
``execute_command`` / ``get_device_info`` / ``discover_devices`` /
``health_check`` methods. Not one line under ``ai_server/plugins/`` is
touched.

Every ``DeviceIntegration`` subclass — every plugin, by construction of the
plugin system — already implements the same four-method interface, so this
wrapping never needed a special case for either of the two plugins found in
this tree at the time of writing (``ExampleIntegration``,
``SquareRootCalculatorDeviceIntegration``). If a future plugin cannot be
wrapped through that interface without changes, that is a fact about the
plugin, not about this sidecar, and should be reported rather than patched
around here.

A note on plugins_dir
----------------------
``ai_server.plugins.PluginManager`` now defaults to its own package directory
(``ai_server/plugins/``) rather than the process's working directory, and
``settings.plugins_dir`` is an absolute path to it (omni-jev plan, A1/J21), so
the module-level ``plugin_manager`` ``main.py`` uses finds the same plugins
from any cwd. This server still constructs its **own** ``PluginManager`` — the
same unmodified class — pointed explicitly at ``_DEFAULT_PLUGINS_DIR`` (resolved
relative to this file) or at ``--plugins-dir``, so a stray ``PLUGINS_DIR``
environment variable cannot change which plugins this sidecar exposes.

A generic tool per plugin, because that is what the interface offers
----------------------------------------------------------------------
``DeviceIntegration`` carries no machine-readable list of the actions a
given plugin understands (``ai_server.models.Action`` is Home Assistant's
vocabulary; a plugin is free to invent its own, as
``SquareRootCalculatorDeviceIntegration`` does with ``"square_root"``). The
only description available per plugin is its class docstring (used below
when present) and its name — the same amount of information
``alfred_router/tool_registry.py`` already works with today. So each plugin
gets one ``execute_command`` tool whose schema mirrors
``ai_server.models.Command`` field for field, plus read-only
``get_device_info`` / ``discover_devices`` / ``health_check`` tools. The
``execute_command`` tool's description says plainly that its effect is
whatever the named plugin implements, which may or may not be physical,
because this sidecar has no way to know more than that without the plugin
telling it.
"""

from __future__ import annotations

import argparse
import inspect
import json
import logging
import re
from pathlib import Path
from typing import Any

from ..integration.base import DeviceIntegration
from ..models import Command
from ..plugins import PluginManager
from .jsonrpc import McpStdioServer, ToolCallResult, ToolSpec

logger = logging.getLogger(__name__)

SERVER_NAME = "alfred-plugins"
SERVER_VERSION = "1.0.0"

#: ``ai_server/plugins/`` is a sibling of this file's grandparent
#: (``ai_server/mcp/plugins_server.py`` -> ``ai_server/`` -> ``plugins``).
_DEFAULT_PLUGINS_DIR = Path(__file__).resolve().parent.parent / "plugins"

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_WORD = re.compile(r"\W+")


def _slug(name: str) -> str:
    """A plugin's class name as a lowercase, underscore-separated tool prefix."""
    spaced = _CAMEL_BOUNDARY.sub("_", name)
    return _NON_WORD.sub("_", spaced).strip("_").lower()


def _plugin_summary(integration: DeviceIntegration) -> str:
    doc = inspect.getdoc(integration.__class__)
    if doc:
        # First non-empty line only — the rest is often TODO/implementation
        # commentary aimed at a developer editing the plugin, not at a model
        # deciding whether to call it.
        for line in doc.splitlines():
            line = line.strip()
            if line:
                return line
    return f"Plugin integration '{integration.name}' (no docstring provided)."


def _load_plugins(plugins_dir: Path) -> PluginManager:
    manager = PluginManager(plugins_dir=str(plugins_dir))
    manager.load_plugins()
    return manager


def build_tools(manager: PluginManager) -> list[ToolSpec]:
    tools: list[ToolSpec] = []
    for name, integration in manager.integrations.items():
        tools.extend(_tools_for(name, integration))
    return tools


def _tools_for(name: str, integration: DeviceIntegration) -> list[ToolSpec]:
    slug = _slug(name)
    summary = _plugin_summary(integration)

    async def _execute_command(args: dict) -> ToolCallResult:
        action = str(args.get("action", ""))
        target = str(args.get("target", ""))
        parameters = args.get("parameters")
        parameters = dict(parameters) if isinstance(parameters, dict) else None
        command = Command(
            action=action,
            target=target,
            parameters=parameters,
            room=args.get("room"),
            intent=args.get("intent"),
        )
        response = await integration.execute_command(command)
        if response.status == "error":
            return ToolCallResult(
                content_text=response.error or "unknown error", is_error=True
            )
        payload: dict[str, Any] = {"status": response.status, "message": response.message}
        if response.device_state is not None:
            payload["device_state"] = response.device_state
        return ToolCallResult(content_text=json.dumps(payload, default=str))

    async def _get_device_info(args: dict) -> ToolCallResult:
        entity_id = str(args.get("entity_id", ""))
        info = await integration.get_device_info(entity_id)
        if info is None:
            return ToolCallResult(
                content_text=f"no device info for {entity_id!r}", is_error=True
            )
        return ToolCallResult(
            content_text=json.dumps(
                {
                    "entity_id": info.entity_id,
                    "name": info.name,
                    "device_type": info.device_type,
                    "state": info.state,
                    "attributes": info.attributes,
                },
                default=str,
            )
        )

    async def _discover_devices(_args: dict) -> ToolCallResult:
        devices = await integration.discover_devices()
        payload = [
            {
                "entity_id": d.entity_id,
                "name": d.name,
                "device_type": d.device_type,
                "state": d.state,
            }
            for d in devices
        ]
        return ToolCallResult(content_text=json.dumps(payload, default=str))

    async def _health_check(_args: dict) -> ToolCallResult:
        healthy = await integration.health_check()
        return ToolCallResult(
            content_text=json.dumps({"healthy": healthy}), is_error=not healthy
        )

    return [
        ToolSpec(
            name=f"{slug}_execute_command",
            description=(
                f"Run a command on the '{name}' plugin. {summary} "
                "This calls the plugin's own execute_command() directly — "
                "its real-world effect (if any) is entirely up to that "
                "plugin's implementation, which this server does not "
                "inspect further. 'action' is whatever vocabulary this "
                "specific plugin defines (it is not restricted to Home "
                "Assistant's action names); consult the plugin's "
                "documentation or source for the actions it understands."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": (
                            "The action this plugin should perform, in "
                            "whatever vocabulary the plugin itself defines."
                        ),
                    },
                    "target": {
                        "type": "string",
                        "description": (
                            "The device/target identifier this action applies "
                            "to. Some plugins ignore this field."
                        ),
                    },
                    "parameters": {
                        "type": "object",
                        "description": "Extra arguments the action needs, if any.",
                    },
                    "room": {"type": "string", "description": "Optional room hint."},
                    "intent": {
                        "type": "string",
                        "description": "Optional parsed-intent hint.",
                    },
                },
                "required": ["action", "target"],
            },
            handler=_execute_command,
        ),
        ToolSpec(
            name=f"{slug}_get_device_info",
            description=(
                f"Read-only. Get info about one device/target from the "
                f"'{name}' plugin. Makes no change to anything."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": "The device/target identifier to look up.",
                    }
                },
                "required": ["entity_id"],
            },
            handler=_get_device_info,
        ),
        ToolSpec(
            name=f"{slug}_discover_devices",
            description=(
                f"Read-only. List the devices/targets the '{name}' plugin "
                "knows about. Makes no change to anything."
            ),
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=_discover_devices,
        ),
        ToolSpec(
            name=f"{slug}_health_check",
            description=(
                f"Read-only. Check whether the '{name}' plugin is healthy. "
                "Makes no change to anything."
            ),
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=_health_check,
        ),
    ]


def build_server(plugins_dir: Path | None = None) -> McpStdioServer:
    manager = _load_plugins(plugins_dir or _DEFAULT_PLUGINS_DIR)
    logger.info(
        "loaded %d plugin(s): %s",
        len(manager.list_integrations()),
        ", ".join(manager.list_integrations()) or "(none)",
    )
    return McpStdioServer(SERVER_NAME, SERVER_VERSION, build_tools(manager))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plugins-dir",
        type=Path,
        default=None,
        help="Override the plugins directory (default: ai_server/plugins).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Python logging level for stderr diagnostics (default: INFO).",
    )
    args = parser.parse_args()
    logging.getLogger().setLevel(args.log_level)
    build_server(args.plugins_dir).run()


if __name__ == "__main__":
    main()
