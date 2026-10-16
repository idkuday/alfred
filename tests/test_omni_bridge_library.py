"""Graph library loading and house-constraint injection (omni-jev plan J6, J7, section 5.1)."""

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("omni")

from omni.graph.schema import graph_ref  # noqa: E402

from ai_server.omni_bridge import library as lib  # noqa: E402
from tests.omni_fixtures import FRONT_DOOR, MINI_TROUBLE  # noqa: E402

SETTINGS = SimpleNamespace(omni_ask_timeout=300.0, omni_node_provider=None, omni_node_model=None)


def _repo(tmp_path: Path, **graphs: dict) -> Path:
    graphs_dir = tmp_path / ".omni" / "graphs"
    graphs_dir.mkdir(parents=True)
    for name, doc in graphs.items():
        (graphs_dir / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")
    return tmp_path


def _node(graph, node_id):
    return graph.node(node_id)


def test_house_constraints_defaults(tmp_path):
    house = lib.house_constraints(tmp_path, SETTINGS)
    assert house == {
        "cwd": str(tmp_path.resolve()),
        "mcp_enabled": True,
        "mcp_config_path": str(tmp_path.resolve() / ".omni" / "mcp.local.json"),
        "on_ask": "prompt",
        "ask_timeout": 300.0,
    }


def test_house_constraints_provider_and_model_only_when_set(tmp_path):
    settings = SimpleNamespace(
        omni_ask_timeout=60.0, omni_node_provider="anthropic", omni_node_model="m1"
    )
    house = lib.house_constraints(tmp_path, settings)
    assert house["provider"] == "anthropic" and house["model"] == "m1"
    assert house["ask_timeout"] == 60.0
    only_model = SimpleNamespace(omni_ask_timeout=1.0, omni_node_provider=None, omni_node_model="m")
    assert "provider" not in lib.house_constraints(tmp_path, only_model)


def test_inject_touches_agent_and_tool_only():
    out = lib.inject_constraints(FRONT_DOOR, {"cwd": "/r", "on_ask": "prompt"})
    by_id = {n["id"]: n for n in out["nodes"]}
    assert by_id["say-act"]["constraints"] == {"cwd": "/r", "on_ask": "prompt"}
    assert by_id["chat"]["constraints"] == {"cwd": "/r", "on_ask": "prompt"}
    # decide keeps exactly its own constraints; human and graph nodes get none.
    assert by_id["front"]["constraints"] == {"provider": "typesafe", "model": "jev"}
    assert "constraints" not in by_id["ask-ok"]
    assert "constraints" not in by_id["troubleshoot"]


def test_inject_keeps_a_nodes_own_values_and_does_not_mutate_input():
    doc = {"nodes": [{"id": "a", "kind": "agent", "constraints": {"on_ask": "deny", "tools": ["x"]}}]}
    before = copy.deepcopy(doc)
    out = lib.inject_constraints(doc, {"on_ask": "prompt", "cwd": "/r"})
    assert out["nodes"][0]["constraints"] == {"on_ask": "deny", "tools": ["x"], "cwd": "/r"}
    assert doc == before


def test_inject_recurses_into_inline_subgraphs():
    out = lib.inject_constraints(MINI_TROUBLE, {"cwd": "/r"})
    inline = next(n for n in out["nodes"] if n["id"] == "inline")["graph"]
    assert inline["nodes"][0]["constraints"] == {"cwd": "/r"}


def test_load_library_injects_into_every_graph_including_named_subgraphs(tmp_path):
    repo = _repo(tmp_path, **{"front-door": FRONT_DOOR, "mini-trouble": MINI_TROUBLE})
    library, graphs = lib.load_library(repo, SETTINGS)
    assert set(graphs) == {"front-door", "mini-trouble"}
    root = str(repo.resolve())
    for graph_name, node_id in [("front-door", "chat"), ("front-door", "say-act"),
                                ("mini-trouble", "intake"), ("mini-trouble", "say-bye")]:
        c = _node(graphs[graph_name], node_id).constraints
        assert c["cwd"] == root and c["mcp_enabled"] is True and c["on_ask"] == "prompt"
        assert c["ask_timeout"] == 300.0
    assert "cwd" not in _node(graphs["front-door"], "front").constraints
    # The policy resolves the *injected* definition, by name or by ref.
    assert library.resolve("mini-trouble") is graphs["mini-trouble"]
    assert library.resolve(graphs["front-door"].ref) is graphs["front-door"]
    assert graphs["front-door"].ref != graph_ref(FRONT_DOOR)
    # The inline subgraph was registered too, already injected.
    inline_ref = graphs["mini-trouble"].subgraphs["inline"]
    assert library.resolve(inline_ref).node("deep").constraints["cwd"] == root


def test_load_library_names_an_unnamed_graph_after_its_file(tmp_path):
    doc = {k: v for k, v in MINI_TROUBLE.items() if k != "name"}
    repo = _repo(tmp_path, **{"troubles": doc})
    library, graphs = lib.load_library(repo, SETTINGS)
    assert list(graphs) == ["troubles"] and "troubles" in library


def test_load_library_errors(tmp_path):
    with pytest.raises(lib.LibraryError, match="no graphs"):
        lib.load_library(tmp_path, SETTINGS)
    repo = _repo(tmp_path / "bad")
    (repo / ".omni" / "graphs" / "x.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(lib.LibraryError, match="x.json"):
        lib.load_library(repo, SETTINGS)
    repo2 = _repo(tmp_path / "list")
    (repo2 / ".omni" / "graphs" / "y.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(lib.LibraryError, match="not a JSON object"):
        lib.load_library(repo2, SETTINGS)


def test_write_local_mcp_config(tmp_path):
    (tmp_path / ".omni").mkdir()
    (tmp_path / ".omni" / "mcp.json").write_text(
        json.dumps(
            {
                "_comment": ["keep me"],
                "mcpServers": {
                    "home_assistant": {
                        "command": "python",
                        "args": ["-m", "ai_server.mcp.home_assistant_server"],
                        "grade": "physical",
                        "cwd": "..",
                    },
                    "other": {"command": "node", "args": ["x.js"]},
                },
            }
        ),
        encoding="utf-8",
    )
    path = lib.write_local_mcp_config(tmp_path)
    assert path == tmp_path.resolve() / ".omni" / "mcp.local.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    ha = data["mcpServers"]["home_assistant"]
    assert ha["command"] == sys.executable
    assert ha["cwd"] == str(tmp_path.resolve())
    assert ha["args"] == ["-m", "ai_server.mcp.home_assistant_server"]
    assert ha["grade"] == "physical"
    assert data["mcpServers"]["other"]["command"] == "node"
    assert data["mcpServers"]["other"]["cwd"] == str(tmp_path.resolve())
    assert data["_comment"] == ["keep me"]
    assert not list((tmp_path / ".omni").glob("*.tmp"))
    # Running it again is stable, and the committed file is untouched.
    assert lib.write_local_mcp_config(tmp_path) == path
    assert json.loads((tmp_path / ".omni" / "mcp.json").read_text())["mcpServers"][
        "home_assistant"]["command"] == "python"


def test_write_local_mcp_config_without_a_source_writes_no_servers(tmp_path):
    path = lib.write_local_mcp_config(tmp_path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"mcpServers": {}}


def test_write_local_mcp_config_rejects_unreadable_source(tmp_path):
    (tmp_path / ".omni").mkdir()
    (tmp_path / ".omni" / "mcp.json").write_text("{", encoding="utf-8")
    with pytest.raises(lib.LibraryError):
        lib.write_local_mcp_config(tmp_path)


def test_local_mcp_config_path_is_a_join(tmp_path):
    assert lib.local_mcp_config_path(tmp_path) == tmp_path / ".omni" / "mcp.local.json"
