"""resolve_entity / apply / describe on the Home Assistant MCP server (omni-jev
plan, J4, section 6.2), driven in-process against the shared mock HA.

``decisions.choose`` is replaced by a fake, so no decision model (and no omni)
is needed here.
"""
import json
from datetime import datetime

import pytest
import pytest_asyncio

from ai_server import decisions
from ai_server.config import settings
from ai_server.integration.home_assistant import HomeAssistantIntegration
from ai_server.mcp.home_assistant_server import build_tools
from ai_server.mcp.mock_ha import start_mock_ha


@pytest_asyncio.fixture
async def house():
    """(tools by name, mock server, integration) over a fresh mock house."""
    url, server = start_mock_ha()
    ha = HomeAssistantIntegration({"url": url, "token": "test"})
    tools = {t.name: t for t in build_tools(ha)}
    try:
        yield tools, server, ha
    finally:
        await ha._close_session()
        server.shutdown()
        server.server_close()


@pytest.fixture
def fake_choose(monkeypatch):
    """Replace decisions.choose; ``calls`` records what it was asked."""

    class Fake:
        def __init__(self):
            self.answer: tuple[str | None, float] = (None, 0.0)
            self.calls: list[dict] = []

        async def __call__(self, question, options, state, *, threshold):
            self.calls.append(
                {"question": question, "options": dict(options), "state": dict(state),
                 "threshold": threshold}
            )
            return self.answer

    fake = Fake()
    monkeypatch.setattr(decisions, "choose", fake)
    return fake


async def _call(tool, **args):
    result = await tool.handler(args)
    return result


def _outcome(result):
    assert not result.is_error, result.content_text
    parsed = json.loads(result.content_text)
    assert set(parsed) == {"outcome", "output"}
    return parsed["outcome"], parsed["output"]


# ── resolve_entity ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_zero_candidates_is_none(house, fake_choose):
    tools, server, _ = house
    server.states.clear()
    outcome, output = _outcome(
        await _call(tools["resolve_entity"], request="the lamp", action="turn-on")
    )
    assert outcome == "none"
    assert output == {"reason": "no matching devices"}
    assert fake_choose.calls == []


@pytest.mark.asyncio
async def test_one_candidate_resolves_without_asking_a_model(house, fake_choose):
    tools, server, _ = house
    for entity_id in list(server.states):
        if entity_id != "light.bedroom_lamp":
            del server.states[entity_id]
    outcome, output = _outcome(
        await _call(tools["resolve_entity"], request="the lamp", action="turn-on")
    )
    assert outcome == "resolved"
    assert output == {
        "entity_id": "light.bedroom_lamp",
        "entity_name": "Bedroom Lamp",
        "domain": "light",
        "confidence": 1.0,
    }
    assert fake_choose.calls == []


@pytest.mark.asyncio
async def test_many_candidates_resolved_by_the_model(house, fake_choose):
    tools, _, _ = house
    fake_choose.answer = ("light.living_room_lamp", 0.93)
    outcome, output = _outcome(
        await _call(
            tools["resolve_entity"], request="turn on the living room lamp", action="turn-on"
        )
    )
    assert outcome == "resolved"
    assert output["entity_id"] == "light.living_room_lamp"
    assert output["entity_name"] == "Living Room Lamp"
    assert output["domain"] == "light"
    assert output["confidence"] == 0.93
    (call,) = fake_choose.calls
    assert call["state"] == {"request": "turn on the living room lamp"}
    assert call["threshold"] == settings.resolve_threshold
    assert call["options"]["light.bedroom_lamp"].startswith("Bedroom Lamp")
    assert "light, currently off" in call["options"]["light.bedroom_lamp"]
    # a sensor cannot be switched, so it is not an option for turn-on
    assert "binary_sensor.hallway_motion" not in call["options"]


@pytest.mark.asyncio
async def test_model_unavailable_or_unsure_is_ambiguous(house, fake_choose):
    tools, _, _ = house
    fake_choose.answer = (None, 0.0)
    outcome, output = _outcome(
        await _call(tools["resolve_entity"], request="the lamp", action="turn-off")
    )
    assert outcome == "ambiguous"
    assert 1 <= len(output["candidates"]) <= 5
    assert all("." in c for c in output["candidates"])


@pytest.mark.asyncio
async def test_an_answer_outside_the_candidates_is_ambiguous(house, fake_choose):
    tools, _, _ = house
    fake_choose.answer = ("light.not_in_the_house", 0.99)
    outcome, _ = _outcome(
        await _call(tools["resolve_entity"], request="x", action="toggle")
    )
    assert outcome == "ambiguous"


@pytest.mark.asyncio
async def test_too_many_candidates_is_ambiguous_without_asking(house, fake_choose, monkeypatch):
    tools, _, _ = house
    monkeypatch.setattr(settings, "resolve_max_candidates", 2)
    outcome, output = _outcome(
        await _call(tools["resolve_entity"], request="x", action="turn-on")
    )
    assert outcome == "ambiguous"
    assert output["reason"] == "too many devices"
    assert fake_choose.calls == []


@pytest.mark.asyncio
async def test_status_also_considers_sensors(house, fake_choose):
    tools, _, _ = house
    fake_choose.answer = ("binary_sensor.hallway_motion", 0.9)
    outcome, output = _outcome(
        await _call(tools["resolve_entity"], request="is there motion", action="status")
    )
    assert outcome == "resolved"
    assert output["entity_id"] == "binary_sensor.hallway_motion"
    assert output["domain"] == "binary_sensor"
    assert "binary_sensor.hallway_motion" in fake_choose.calls[0]["options"]


@pytest.mark.asyncio
async def test_resolve_rejects_an_unknown_action(house, fake_choose):
    tools, _, _ = house
    result = await _call(tools["resolve_entity"], request="x", action="explode")
    assert result.is_error
    assert "action must be one of" in result.content_text


# ── apply ────────────────────────────────────────────────────────────────


def _target(entity_id="light.bedroom_lamp", name="Bedroom Lamp") -> str:
    return json.dumps({"entity_id": entity_id, "entity_name": name})


@pytest.mark.asyncio
async def test_apply_flips_the_mock_and_returns_the_sentence(house):
    tools, server, _ = house
    apply_ = tools["apply"]
    result = await _call(apply_, action="turn-on", target=_target(), request="lamp on")
    assert not result.is_error
    assert result.content_text == "Bedroom Lamp is on."
    assert server.states["light.bedroom_lamp"]["state"] == "on"

    result = await _call(apply_, action="toggle", target=_target(), request="flip it")
    assert result.content_text == "Bedroom Lamp is off."
    assert server.states["light.bedroom_lamp"]["state"] == "off"

    await _call(apply_, action="turn-on", target=_target(), request="lamp on")
    result = await _call(apply_, action="turn-off", target=_target(), request="lamp off")
    assert result.content_text == "Bedroom Lamp is off."
    assert server.calls[-1][:2] == ("light", "turn_off")


@pytest.mark.asyncio
async def test_apply_takes_resolve_entity_output_whole(house, fake_choose):
    tools, server, _ = house
    fake_choose.answer = ("switch.coffee_maker", 0.95)
    _, output = _outcome(
        await _call(tools["resolve_entity"], request="coffee", action="turn-on")
    )
    result = await _call(
        tools["apply"], action="turn-on", target=json.dumps(output), request="coffee"
    )
    assert result.content_text == "Coffee Maker is on."
    assert server.states["switch.coffee_maker"]["state"] == "on"


@pytest.mark.parametrize(
    "target",
    [
        "",
        "not json",
        "[1, 2]",
        json.dumps({"entity_id": "light.bedroom_lamp"}),
        json.dumps({"entity_name": "Bedroom Lamp"}),
        json.dumps({"entity_id": "nodot", "entity_name": "X"}),
        json.dumps({"entity_id": "light.bedroom_lamp", "entity_name": " "}),
        {"entity_id": "light.bedroom_lamp", "entity_name": "not a string target"},
    ],
)
@pytest.mark.asyncio
async def test_apply_bad_target_is_an_error_and_changes_nothing(house, target):
    tools, server, _ = house
    result = await _call(tools["apply"], action="turn-on", target=target, request="x")
    assert result.is_error
    assert server.calls == []
    assert server.states["light.bedroom_lamp"]["state"] == "off"


@pytest.mark.asyncio
async def test_apply_bad_action_is_an_error(house):
    tools, server, _ = house
    for action in ("status", "explode", ""):
        result = await _call(tools["apply"], action=action, target=_target(), request="x")
        assert result.is_error
        assert "action must be one of" in result.content_text
    assert server.calls == []


@pytest.mark.asyncio
async def test_apply_to_an_unknown_entity_is_an_error(house):
    tools, _, _ = house
    result = await _call(
        tools["apply"],
        action="turn-on",
        target=_target("light.ghost", "Ghost Light"),
        request="x",
    )
    assert result.is_error


def test_apply_schema_tells_the_prompt_about_entity_name():
    ha = HomeAssistantIntegration({"url": "http://127.0.0.1:9", "token": "t"})
    tools = {t.name: t for t in build_tools(ha)}
    schema = tools["apply"].input_schema
    assert schema["required"] == ["action", "target", "request"]
    assert "entity_name" in schema["properties"]["target"]["description"]
    assert tools["resolve_entity"].input_schema["required"] == ["request", "action"]
    assert tools["describe"].input_schema["required"] == ["target"]


# ── describe ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_describe_states_the_state(house):
    tools, server, _ = house
    result = await _call(tools["describe"], target=_target())
    assert not result.is_error
    assert result.content_text == "Bedroom Lamp is off."
    server.states["light.bedroom_lamp"]["state"] = "on"
    result = await _call(tools["describe"], target=_target())
    assert result.content_text == "Bedroom Lamp is on."


@pytest.mark.asyncio
async def test_describe_includes_last_changed_when_present(house):
    tools, server, _ = house
    stamp = "2026-07-01T14:02:00+00:00"
    server.states["light.bedroom_lamp"]["attributes"]["last_changed"] = stamp
    expected = datetime.fromisoformat(stamp).astimezone().strftime("%H:%M")
    result = await _call(tools["describe"], target=_target())
    assert result.content_text == f"Bedroom Lamp is off (last changed {expected})."


@pytest.mark.asyncio
async def test_describe_bad_target_and_unknown_entity_are_errors(house):
    tools, _, _ = house
    assert (await _call(tools["describe"], target="nope")).is_error
    ghost = await _call(tools["describe"], target=_target("light.ghost", "Ghost"))
    assert ghost.is_error
