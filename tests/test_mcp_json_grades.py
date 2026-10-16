"""`.omni/mcp.json` must grade every tool the two MCP servers expose
(omni-jev plan, section 6.3).

Reads are graded ``read`` by name; anything not listed takes the server grade,
which for ``home_assistant`` is ``physical``. ``alfred_plugins`` has no server
grade, so its ``*_execute_command`` tools must be listed as ``physical``. Both
servers carry ``"cwd": ".."`` (resolved relative to the file: the repo root).
"""
import json
from pathlib import Path

import pytest

from ai_server.integration.home_assistant import HomeAssistantIntegration
from ai_server.mcp import home_assistant_server, plugins_server

MCP_JSON = Path(__file__).resolve().parent.parent / ".omni" / "mcp.json"
VALID_GRADES = {"read", "write", "exec", "physical"}

HA_READS = {"get_state", "list_entities", "health_check", "resolve_entity", "describe"}
HA_PHYSICAL = {"apply", "turn_on", "turn_off", "toggle", "set_brightness", "set_temperature"}


@pytest.fixture(scope="module")
def servers() -> dict:
    return json.loads(MCP_JSON.read_text(encoding="utf-8"))["mcpServers"]


@pytest.fixture(scope="module")
def ha_tool_names() -> set[str]:
    ha = HomeAssistantIntegration({"url": "http://127.0.0.1:9", "token": "t"})
    return {t.name for t in home_assistant_server.build_tools(ha)}


@pytest.fixture(scope="module")
def plugin_tool_names() -> set[str]:
    manager = plugins_server._load_plugins(plugins_server._DEFAULT_PLUGINS_DIR)
    return {t.name for t in plugins_server.build_tools(manager)}


def test_both_servers_run_from_the_repo_root(servers):
    assert set(servers) == {"home_assistant", "alfred_plugins"}
    for name, cfg in servers.items():
        assert cfg["cwd"] == "..", name
        assert (MCP_JSON.parent / cfg["cwd"]).resolve() == MCP_JSON.parent.parent


def test_every_grade_is_a_known_grade(servers):
    for cfg in servers.values():
        for grade in [cfg.get("grade"), *cfg["tool_grades"].values()]:
            assert grade is None or grade in VALID_GRADES


def test_home_assistant_tool_set_is_the_one_graded(ha_tool_names):
    assert ha_tool_names == HA_READS | HA_PHYSICAL


def test_home_assistant_reads_are_read_and_the_rest_are_physical(servers, ha_tool_names):
    cfg = servers["home_assistant"]
    assert cfg["grade"] == "physical"
    grades = cfg["tool_grades"]
    for tool in ha_tool_names:
        effective = grades.get(tool, cfg["grade"])
        assert effective == ("read" if tool in HA_READS else "physical"), tool
    # nothing in tool_grades names a tool the server does not expose
    assert set(grades) <= ha_tool_names


def test_plugin_tools_all_have_a_grade(servers, plugin_tool_names):
    grades = servers["alfred_plugins"]["tool_grades"]
    assert plugin_tool_names, "no plugins loaded"
    assert plugin_tool_names == set(grades), "mcp.json and the plugin tools drifted"
    for tool in plugin_tool_names:
        if tool.endswith("_execute_command"):
            assert grades[tool] == "physical", tool
        else:
            assert grades[tool] == "read", tool
