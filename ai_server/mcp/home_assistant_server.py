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

The LLM-free device path (omni-jev plan, J4, section 6.2)
------------------------------------------------------------
``resolve_entity`` (read) -> ``apply`` (physical) / ``describe`` (read) let a
graph act on "turn on the bedroom lamp" without an LLM agent. ``resolve_entity``
narrows Home Assistant's devices to the ones that support the action and, when
more than one remains, asks Jev through :mod:`ai_server.decisions` (J5). Its
result is a JSON object ``{"outcome": ..., "output": {...}}`` that the kernel's
tool-outcome routing reads. A tool node's output cannot be indexed, so the
resolved ``output`` is passed **whole** as the ``target`` string of ``apply`` /
``describe``, which parse it. The permission prompt for ``apply`` therefore
shows the resolved device's ``entity_name``.

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
from datetime import datetime
from typing import Any

from .. import decisions
from ..config import settings
from ..integration.home_assistant import HomeAssistantIntegration
from ..models import Action, Command, DeviceInfo
from .jsonrpc import McpStdioServer, ToolCallResult, ToolSpec

logger = logging.getLogger(__name__)

SERVER_NAME = "alfred-home-assistant"
SERVER_VERSION = "1.0.0"

_KNOWN_DOMAINS = ("light", "switch", "fan", "climate", "cover")

#: What ``resolve_entity`` may be asked to do (omni-jev plan, section 6.2).
_RESOLVE_ACTIONS = ("turn-on", "turn-off", "toggle", "status")

#: ``status`` can also read these (a motion sensor or lock has a state worth
#: asking about even though it cannot be switched).
_STATUS_EXTRA_DOMAINS = ("sensor", "binary_sensor", "lock")

#: What ``list_entities`` reports (A2b): the integration's own domains plus
#: sensors, locks, media players and vacuums. Before A2b it listed only
#: ``_KNOWN_DOMAINS``, so the agent could not see any sensor and said so.
_LIST_EXTRA_DOMAINS = ("sensor", "binary_sensor", "lock", "media_player", "vacuum")
_LISTABLE_DOMAINS = _KNOWN_DOMAINS + _LIST_EXTRA_DOMAINS

#: The state-changing ``resolve_entity`` actions ``apply`` accepts.
_APPLY_ACTIONS: dict[str, Action] = {
    "turn-on": Action.TURN_ON,
    "turn-off": Action.TURN_OFF,
    "toggle": Action.TOGGLE,
}

#: How many entity ids an ``ambiguous`` outcome lists for the fallback agent.
_AMBIGUOUS_CANDIDATES = 5


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


def _json_result(outcome: str, output: dict[str, Any]) -> ToolCallResult:
    """The kernel's tool-outcome shape: ``{"outcome": ..., "output": ...}``."""
    return ToolCallResult(content_text=json.dumps({"outcome": outcome, "output": output}))


def _resolved_output(device: DeviceInfo, confidence: float) -> dict[str, Any]:
    return {
        "entity_id": device.entity_id,
        "entity_name": device.name,
        "domain": device.device_type,
        "confidence": round(confidence, 3),
    }


def _parse_target(args: Any) -> tuple[dict[str, str] | None, str | None]:
    """Parse the ``target`` string ``resolve_entity`` produced.

    Returns ``(target, None)`` with ``entity_id`` and ``entity_name`` strings, or
    ``(None, reason)``.
    """
    raw = args.get("target")
    if not isinstance(raw, str) or not raw.strip():
        return None, "target must be the JSON string resolve_entity produced"
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None, "target is not valid JSON"
    if not isinstance(parsed, dict):
        return None, "target must be a JSON object"
    entity_id, entity_name = parsed.get("entity_id"), parsed.get("entity_name")
    if not isinstance(entity_id, str) or "." not in entity_id:
        return None, "target has no valid entity_id"
    if not isinstance(entity_name, str) or not entity_name.strip():
        return None, "target has no entity_name"
    return {"entity_id": entity_id, "entity_name": entity_name}, None


def _clock(value: Any) -> str | None:
    """``value`` (an ISO timestamp) as local ``HH:MM``; the raw text if unparsable."""
    if not value:
        return None
    text = str(value)
    try:
        return datetime.fromisoformat(text).astimezone().strftime("%H:%M")
    except ValueError:
        return text


async def _state_devices(
    ha: HomeAssistantIntegration, domains: tuple[str, ...]
) -> list[DeviceInfo] | None:
    """The entities in ``domains`` from Home Assistant's state list, or ``None``
    when Home Assistant could not be read (so callers can tell "none exposed"
    from "unreachable").

    ``discover_devices`` is hard-wired to the integration's own domains, and the
    integration is not edited for this work, so this reads ``/api/states`` itself.
    """
    try:
        session = await ha._get_session()
        async with session.get("/api/states") as response:
            if response.status != 200:
                logger.error("Home Assistant state list failed: %s", response.status)
                return None
            states = await response.json()
    except Exception as exc:
        logger.error("Home Assistant state list failed: %s", type(exc).__name__)
        return None
    out: list[DeviceInfo] = []
    for state in states:
        entity_id = str(state.get("entity_id", ""))
        domain = entity_id.split(".")[0] if "." in entity_id else ""
        if domain in domains:
            attributes = state.get("attributes", {})
            out.append(
                DeviceInfo(
                    entity_id=entity_id,
                    name=attributes.get("friendly_name", entity_id),
                    device_type=domain,
                    state=state.get("state"),
                    attributes=attributes,
                )
            )
    return out


async def _candidates(ha: HomeAssistantIntegration, action: str) -> list[DeviceInfo]:
    """Devices that support ``action``: the integration's own domains, plus
    sensors and locks when the action is ``status``."""
    if action != "status":
        devices = await ha.discover_devices()
        return [d for d in devices if d.device_type in _KNOWN_DOMAINS]
    found = await _state_devices(ha, _KNOWN_DOMAINS + _STATUS_EXTRA_DOMAINS)
    return found or []


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
        devices = await _state_devices(ha, _LISTABLE_DOMAINS)
        if devices is None:
            return ToolCallResult(
                content_text="could not read the entity list from Home Assistant",
                is_error=True,
            )
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

    async def _resolve_entity(args: dict) -> ToolCallResult:
        request = str(args.get("request", ""))
        action = str(args.get("action", ""))
        if action not in _RESOLVE_ACTIONS:
            return ToolCallResult(
                content_text=f"action must be one of {', '.join(_RESOLVE_ACTIONS)}",
                is_error=True,
            )
        devices = await _candidates(ha, action)
        if not devices:
            return _json_result("none", {"reason": "no matching devices"})
        if len(devices) == 1:
            return _json_result("resolved", _resolved_output(devices[0], 1.0))
        first_few = [d.entity_id for d in devices[:_AMBIGUOUS_CANDIDATES]]
        if len(devices) > settings.resolve_max_candidates:
            return _json_result(
                "ambiguous", {"reason": "too many devices", "candidates": first_few}
            )
        options = {
            d.entity_id: f"{d.name} — {d.device_type}, currently {d.state}"
            for d in devices
        }
        answer, confidence = await decisions.choose(
            "Which device does the person mean?",
            options,
            {"request": request},
            threshold=settings.resolve_threshold,
        )
        chosen = next((d for d in devices if d.entity_id == answer), None)
        if chosen is None:
            return _json_result(
                "ambiguous", {"reason": "no confident match", "candidates": first_few}
            )
        return _json_result("resolved", _resolved_output(chosen, confidence))

    async def _apply(args: dict) -> ToolCallResult:
        action = str(args.get("action", ""))
        if action not in _APPLY_ACTIONS:
            return ToolCallResult(
                content_text=f"action must be one of {', '.join(_APPLY_ACTIONS)}",
                is_error=True,
            )
        target, problem = _parse_target(args)
        if target is None:
            return ToolCallResult(content_text=problem or "bad target", is_error=True)
        command = Command(action=_APPLY_ACTIONS[action], target=target["entity_id"])
        resp = await ha.execute_command(command)
        if resp.status == "error":
            return ToolCallResult(content_text=resp.error or "unknown error", is_error=True)
        state = (resp.device_state or {}).get("state")
        if state is None:
            return ToolCallResult(
                content_text=f"could not read back the state of {target['entity_name']}",
                is_error=True,
            )
        return ToolCallResult(content_text=f"{target['entity_name']} is {state}.")

    async def _describe(args: dict) -> ToolCallResult:
        target, problem = _parse_target(args)
        if target is None:
            return ToolCallResult(content_text=problem or "bad target", is_error=True)
        info = await ha.get_device_info(target["entity_id"])
        if info is None:
            return ToolCallResult(
                content_text=f"entity {target['entity_id']!r} not found", is_error=True
            )
        sentence = f"{target['entity_name']} is {info.state}"
        changed = _clock((info.attributes or {}).get("last_changed"))
        if changed:
            sentence += f" (last changed {changed})"
        return ToolCallResult(content_text=sentence + ".")

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
                "Read-only. List the Home Assistant entities Alfred can see: "
                "lights, switches, fans, covers, climate entities, sensors, "
                "binary sensors (motion, door and window contacts and the "
                "like), locks, media players and vacuums. Each item gives the "
                "entity_id, friendly name, device_type (the domain, e.g. "
                "'sensor') and current state. Optionally filter to one domain "
                "with the 'domain' argument, e.g. domain='sensor' for every "
                "sensor; a house can have many sensors, so filter when you "
                "know what kind of entity is wanted. Makes no change to "
                "anything; useful for answering 'what sensors/locks/...' "
                "questions and before calling a state-changing tool to find "
                "the right entity_id."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "enum": list(_LISTABLE_DOMAINS),
                        "description": (
                            "Restrict the listing to one domain, e.g. 'sensor', "
                            "'binary_sensor', 'lock', 'light'. Omit for all."
                        ),
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
        ToolSpec(
            name="resolve_entity",
            description=(
                "Read-only. Work out WHICH Home Assistant device a person "
                "means by their words, for one of the actions turn-on, "
                "turn-off, toggle or status. Returns a JSON object "
                '{"outcome": "resolved" | "ambiguous" | "none", "output": {...}}. '
                'On "resolved" the output holds entity_id, entity_name, domain '
                "and confidence; pass that whole output, as a string, to apply "
                "or describe as their 'target'. Makes no change to anything."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "request": {
                        "type": "string",
                        "description": "The person's words, e.g. 'turn on the bedroom lamp'.",
                    },
                    "action": {
                        "type": "string",
                        "enum": list(_RESOLVE_ACTIONS),
                        "description": "What the person wants done to the device.",
                    },
                },
                "required": ["request", "action"],
            },
            handler=_resolve_entity,
        ),
        ToolSpec(
            name="apply",
            description=(
                "Physically turn a Home Assistant device ON or OFF, or TOGGLE "
                "it. 'target' is the JSON string resolve_entity produced as "
                "its output; it names the device (entity_id and entity_name), "
                "and the entity_name is what a human sees when asked to "
                "approve this call. Sends a real command to Home Assistant "
                "with an immediate real-world effect. Returns one sentence "
                "such as 'Bedroom Lamp is on.'"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": list(_APPLY_ACTIONS),
                        "description": "What to do to the device.",
                    },
                    "target": {
                        "type": "string",
                        "description": (
                            "The JSON string resolve_entity produced as its "
                            "output: an object with entity_id and entity_name "
                            "(the human-readable device name shown to whoever "
                            "approves this call), e.g. "
                            '\'{"entity_id": "light.bedroom_lamp", '
                            '"entity_name": "Bedroom Lamp"}\'.'
                        ),
                    },
                    "request": {
                        "type": "string",
                        "description": "The person's original words, for the record.",
                    },
                },
                "required": ["action", "target", "request"],
            },
            handler=_apply,
        ),
        ToolSpec(
            name="describe",
            description=(
                "Read-only. Say in one sentence what state a device is in, "
                "e.g. 'Bedroom Lamp is off (last changed 14:02).' 'target' is "
                "the JSON string resolve_entity produced as its output. Makes "
                "no change to anything."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": (
                            "The JSON string resolve_entity produced as its "
                            "output: an object with entity_id and entity_name."
                        ),
                    }
                },
                "required": ["target"],
            },
            handler=_describe,
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
    try:
        build_server().run()
    finally:
        decisions.close()


if __name__ == "__main__":
    main()
