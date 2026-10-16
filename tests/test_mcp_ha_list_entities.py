"""list_entities on the Home Assistant MCP server (A2b), driven in-process
against the shared mock HA.

Before A2b the tool listed only the integration's own domains (light, switch,
fan, climate, cover), so a sensor was invisible to the agent. These tests pin
the widened domain set, the optional ``domain`` filter, the output format and
the "Home Assistant unreachable" error.
"""
import json

import pytest
import pytest_asyncio

from ai_server.integration.home_assistant import HomeAssistantIntegration
from ai_server.mcp.home_assistant_server import build_tools
from ai_server.mcp.mock_ha import start_mock_ha

LISTED_DOMAINS = {
    "light",
    "switch",
    "fan",
    "climate",
    "cover",
    "sensor",
    "binary_sensor",
    "lock",
    "media_player",
    "vacuum",
}


@pytest_asyncio.fixture
async def house():
    """(list_entities tool, mock server, integration) over a fresh mock house."""
    url, server = start_mock_ha()
    ha = HomeAssistantIntegration({"url": url, "token": "test"})
    tools = {t.name: t for t in build_tools(ha)}
    try:
        yield tools["list_entities"], server, ha
    finally:
        await ha._close_session()
        server.shutdown()
        server.server_close()


async def _list(tool, **args) -> list[dict]:
    result = await tool.handler(args)
    assert not result.is_error, result.content_text
    return json.loads(result.content_text)


@pytest.mark.asyncio
async def test_lists_every_expected_domain(house):
    tool, server, _ = house
    items = await _list(tool)
    domains = {item["device_type"] for item in items}
    assert domains == LISTED_DOMAINS
    # every entity in the mock house in a listed domain is listed, none missing
    expected_ids = {
        eid for eid in server.states if eid.split(".")[0] in LISTED_DOMAINS
    }
    assert {item["entity_id"] for item in items} == expected_ids


@pytest.mark.asyncio
async def test_output_format_is_unchanged(house):
    tool, _, _ = house
    items = await _list(tool)
    assert items, "the mock house should not be empty"
    for item in items:
        assert set(item) == {"entity_id", "name", "device_type", "state"}
    by_id = {item["entity_id"]: item for item in items}
    assert by_id["lock.front_door"] == {
        "entity_id": "lock.front_door",
        "name": "Front Door Lock",
        "device_type": "lock",
        "state": "locked",
    }
    assert by_id["sensor.living_room_temperature"]["state"] == "71.2"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "domain, expected_id",
    [
        ("sensor", "sensor.living_room_temperature"),
        ("binary_sensor", "binary_sensor.hallway_motion"),
        ("lock", "lock.front_door"),
        ("cover", "cover.garage_door"),
        ("fan", "fan.bedroom_fan"),
        ("media_player", "media_player.living_room_speaker"),
        ("vacuum", "vacuum.robot_vacuum"),
        ("light", "light.bedroom_lamp"),
    ],
)
async def test_domain_filter_returns_only_that_domain(house, domain, expected_id):
    tool, _, _ = house
    items = await _list(tool, domain=domain)
    assert items, f"no {domain} entities listed"
    assert {item["device_type"] for item in items} == {domain}
    assert expected_id in {item["entity_id"] for item in items}


@pytest.mark.asyncio
async def test_unknown_domain_lists_nothing(house):
    tool, _, _ = house
    assert await _list(tool, domain="person") == []


@pytest.mark.asyncio
async def test_unreachable_home_assistant_is_an_error_not_an_empty_list(house):
    tool, _, _ = house
    # a closed port: the state list cannot be read at all
    dead = HomeAssistantIntegration({"url": "http://127.0.0.1:9", "token": "test"})
    dead_tool = {t.name: t for t in build_tools(dead)}["list_entities"]
    try:
        result = await dead_tool.handler({})
    finally:
        await dead._close_session()
    assert result.is_error
    assert "could not read" in result.content_text


def test_tool_schema_and_description_name_the_new_domains(house):
    tool, _, _ = house
    spec_enum = tool.input_schema["properties"]["domain"]["enum"]
    assert set(spec_enum) == LISTED_DOMAINS
    for phrase in ("sensors", "binary sensors", "locks", "media players", "vacuums"):
        assert phrase in tool.description
