"""Home Assistant as an MCP server (migration.md row 5.2).

Wraps :class:`ai_server.integration.home_assistant.HomeAssistantIntegration`
— the class that already does the HTTP work against the HA REST API — and
exposes it as a set of MCP tools. **Nothing in that class is rewritten**:
every tool handler here builds an ``ai_server.models.Command`` and calls
``HomeAssistantIntegration.execute_command`` / ``.get_device_info`` /
``.discover_devices`` / ``.health_check`` exactly as ``ai_server/main.py``'s
``/execute`` endpoint does today.

Action vocabulary
------------------
``turn_on``, ``turn_off``, ``toggle``, ``set_brightness`` and ``get_status``
are ``ai_server.models.Action`` members, used verbatim. ``set_temperature``
is **not** in that enum, and adding it there was out of scope (no plugin or
core rewrite) — but ``HomeAssistantIntegration.execute_command`` already
falls back to using the action string directly as the Home Assistant service
name for anything it does not special-case (``service_map.get(action_lower,
action_lower)``), which is precisely what real Home Assistant needs for
``climate.set_temperature``. So ``"set_temperature"`` is passed straight
through as a plain string, the same way a router or ``AlfredCore`` decision
would if the enum ever grew that member, and no line in ``integration/`` or
``models.py`` changes for it.

Every physical (state-changing) tool below also asks the caller for an
``entity_name`` — a human-readable label — **in addition to** the
machine ``entity_id``. Home Assistant never sees it; it exists purely so
whatever surface renders the permission prompt has something better than a
bare ``entity_id`` to show a human before the action runs. See the module
docstring on grading below for why this is the only lever available on the
server side.

A note on ``Grade.PHYSICAL`` — read before assuming this is graded correctly
------------------------------------------------------------------------------
The task this server was built against says a physical capability should be
graded ``Grade.PHYSICAL`` on the omni-harness side (asks every call, no
standing grant). **As built, ``omni-harness`` does not support that for any
MCP tool**: ``omni/driver/tools/mcp/tool.py`` hardcodes
``McpTool.grade = Grade.EXEC`` for every tool discovered from every MCP
server, unconditionally (task 2.3 / D56), and the MCP tool descriptor this
server returns from ``tools/list`` (``name``/``description``/``inputSchema``)
has no field the client reads to override that. ``Grade.EXEC``'s disposition
is ``ASK`` (a call can be pre-approved once and then runs silently), not
``Grade.PHYSICAL``'s ``ASK_ALWAYS`` (never pre-approvable). So today, a user
who answers "always allow" once to ``mcp__home_assistant__turn_on`` silently
pre-approves every future call to it — lights, locks, everything — with no
further prompt. This is reported in the top-level task report as a finding
against the omni-harness MCP client; it is **not** fixed here, per that
task's own instruction not to modify ``omni-harness``.
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from ..config import settings
from ..integration.home_assistant import HomeAssistantIntegration
from ..models import Action, Command
from .jsonrpc import McpStdioServer, ToolCallResult, ToolSpec

logger = logging.getLogger(__name__)

SERVER_NAME = "alfred-home-assistant"
SERVER_VERSION = "1.0.0"

_KNOWN_DOMAINS = ("light", "switch", "fan", "climate", "cover")


def _entity_property(example: str) -> dict[str, Any]:
    return {
        "type": "string",
        "description": (
            "The Home Assistant entity_id to act on, e.g. "
            f"'{example}'. Get this from list_entities or get_state."
        ),
    }


_ENTITY_NAME_PROPERTY: dict[str, Any] = {
    "type": "string",
    "description": (
        "A short, human-readable name for the device, e.g. 'Bedroom Lamp'. "
        "This is NOT sent to Home Assistant — it exists only so a human "
        "reviewing this call before it runs can tell what physical device "
        "is about to be affected. Always fill this in with the device's "
        "real friendly name (from list_entities/get_state) rather than "
        "repeating the entity_id."
    ),
}


def _command_result(resp: Any) -> ToolCallResult:
    """Render a ``CommandResponse`` (or an error string) as a tool result."""
    if resp.status == "error":
        return ToolCallResult(content_text=resp.error or "unknown error", is_error=True)
    payload = {
        "status": resp.status,
        "message": resp.message,
    }
    if resp.device_state is not None:
        payload["device_state"] = resp.device_state
    return ToolCallResult(content_text=json.dumps(payload, default=str))


def build_tools(ha: HomeAssistantIntegration) -> list[ToolSpec]:
    """The tool table for the Home Assistant MCP server."""

    async def _turn_on(args: dict) -> ToolCallResult:
        entity_id = str(args.get("entity_id", ""))
        command = Command(action=Action.TURN_ON, target=entity_id)
        return _command_result(await ha.execute_command(command))

    async def _turn_off(args: dict) -> ToolCallResult:
        entity_id = str(args.get("entity_id", ""))
        command = Command(action=Action.TURN_OFF, target=entity_id)
        return _command_result(await ha.execute_command(command))

    async def _toggle(args: dict) -> ToolCallResult:
        entity_id = str(args.get("entity_id", ""))
        command = Command(action=Action.TOGGLE, target=entity_id)
        return _command_result(await ha.execute_command(command))

    async def _set_brightness(args: dict) -> ToolCallResult:
        entity_id = str(args.get("entity_id", ""))
        pct = args.get("brightness_pct")
        try:
            pct_int = max(0, min(100, int(pct)))
        except (TypeError, ValueError):
            return ToolCallResult(
                content_text=f"brightness_pct must be an integer 0-100, got {pct!r}",
                is_error=True,
            )
        command = Command(
            action=Action.SET_BRIGHTNESS,
            target=entity_id,
            parameters={"brightness_pct": pct_int},
        )
        return _command_result(await ha.execute_command(command))

    async def _set_temperature(args: dict) -> ToolCallResult:
        entity_id = str(args.get("entity_id", ""))
        temperature = args.get("temperature")
        try:
            temp_val = float(temperature)
        except (TypeError, ValueError):
            return ToolCallResult(
                content_text=f"temperature must be a number, got {temperature!r}",
                is_error=True,
            )
        # Not an Action enum member — see module docstring. Falls through
        # HomeAssistantIntegration.execute_command's own
        # service_map.get(action_lower, action_lower) default, unmodified.
        command = Command(
            action="set_temperature",
            target=entity_id,
            parameters={"temperature": temp_val},
        )
        return _command_result(await ha.execute_command(command))

    async def _get_state(args: dict) -> ToolCallResult:
        entity_id = str(args.get("entity_id", ""))
        info = await ha.get_device_info(entity_id)
        if info is None:
            return ToolCallResult(
                content_text=f"entity {entity_id!r} not found", is_error=True
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

    async def _list_entities(args: dict) -> ToolCallResult:
        domain = args.get("domain")
        devices = await ha.discover_devices()
        if domain:
            devices = [d for d in devices if d.device_type == domain]
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
        healthy = await ha.health_check()
        return ToolCallResult(
            content_text=json.dumps({"healthy": healthy}),
            is_error=not healthy,
        )

    return [
        ToolSpec(
            name="turn_on",
            description=(
                "Physically turn ON a Home Assistant device — a light, "
                "switch, fan, climate entity, or cover — identified by "
                "entity_id. This sends a real command to Home Assistant's "
                "REST API and will have an immediate real-world effect "
                "(e.g. a light turning on) if the entity exists and is "
                "reachable."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "entity_id": _entity_property("light.bedroom_lamp"),
                    "entity_name": _ENTITY_NAME_PROPERTY,
                },
                "required": ["entity_id", "entity_name"],
            },
            handler=_turn_on,
        ),
        ToolSpec(
            name="turn_off",
            description=(
                "Physically turn OFF a Home Assistant device — a light, "
                "switch, fan, climate entity, or cover — identified by "
                "entity_id. This sends a real command to Home Assistant's "
                "REST API and will have an immediate real-world effect "
                "(e.g. a light turning off) if the entity exists and is "
                "reachable."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "entity_id": _entity_property("light.bedroom_lamp"),
                    "entity_name": _ENTITY_NAME_PROPERTY,
                },
                "required": ["entity_id", "entity_name"],
            },
            handler=_turn_off,
        ),
        ToolSpec(
            name="toggle",
            description=(
                "Physically TOGGLE a Home Assistant device — flips a light, "
                "switch, or fan between on and off, or a cover between open "
                "and closed. Sends a real command to the device; the "
                "resulting state depends on whatever state it was already "
                "in, which this tool does not know in advance."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "entity_id": _entity_property("switch.coffee_maker"),
                    "entity_name": _ENTITY_NAME_PROPERTY,
                },
                "required": ["entity_id", "entity_name"],
            },
            handler=_toggle,
        ),
        ToolSpec(
            name="set_brightness",
            description=(
                "Physically set a light's brightness to a percentage. Sends "
                "a real 'light.turn_on' command with brightness_pct to Home "
                "Assistant, which will change how bright the physical bulb "
                "is right now (turning it on first if it was off)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "entity_id": _entity_property("light.living_room"),
                    "entity_name": _ENTITY_NAME_PROPERTY,
                    "brightness_pct": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                        "description": "Target brightness, 0 (off) to 100 (full).",
                    },
                },
                "required": ["entity_id", "entity_name", "brightness_pct"],
            },
            handler=_set_brightness,
        ),
        ToolSpec(
            name="set_temperature",
            description=(
                "Physically set a climate entity's (thermostat's) target "
                "temperature. Sends a real 'climate.set_temperature' "
                "command to Home Assistant, which will change what "
                "temperature the physical HVAC system is heating or "
                "cooling toward."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "entity_id": _entity_property("climate.living_room"),
                    "entity_name": _ENTITY_NAME_PROPERTY,
                    "temperature": {
                        "type": "number",
                        "description": (
                            "Target temperature, in whatever unit the "
                            "Home Assistant climate entity is configured for."
                        ),
                    },
                },
                "required": ["entity_id", "entity_name", "temperature"],
            },
            handler=_set_temperature,
        ),
        ToolSpec(
            name="get_state",
            description=(
                "Read-only. Return the current state and attributes of one "
                "Home Assistant entity (e.g. whether a light is on, its "
                "brightness, a thermostat's current temperature). Makes no "
                "change to anything."
            ),
            input_schema={
                "type": "object",
                "properties": {"entity_id": _entity_property("light.bedroom_lamp")},
                "required": ["entity_id"],
            },
            handler=_get_state,
        ),
        ToolSpec(
            name="list_entities",
            description=(
                "Read-only. List the Home Assistant devices Alfred can see "
                "— lights, switches, fans, climate entities, and covers — "
                "with each one's entity_id, friendly name, and current "
                "state. Optionally filter to one domain. Makes no change to "
                "anything; useful before calling a state-changing tool to "
                "find the right entity_id."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "enum": list(_KNOWN_DOMAINS),
                        "description": "Restrict the listing to one domain. Omit for all.",
                    }
                },
                "required": [],
            },
            handler=_list_entities,
        ),
        ToolSpec(
            name="health_check",
            description=(
                "Read-only. Check whether Home Assistant is reachable at "
                "all right now. Makes no change to anything."
            ),
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=_health_check,
        ),
    ]


def build_server() -> McpStdioServer:
    ha = HomeAssistantIntegration({"url": settings.ha_url, "token": settings.ha_token})
    return McpStdioServer(SERVER_NAME, SERVER_VERSION, build_tools(ha))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Python logging level for stderr diagnostics (default: INFO).",
    )
    args = parser.parse_args()
    logging.getLogger().setLevel(args.log_level)
    build_server().run()


if __name__ == "__main__":
    main()
