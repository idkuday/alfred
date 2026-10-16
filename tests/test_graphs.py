"""Static checks on Alfred's graph library (omni-jev A3).

Why: the graphs are code that no unit test exercises until a person talks to Alfred, and
three of the plan's rules (J6, J8, J16) are conventions the kernel's validator cannot see.
This file is where they are enforced, so a graph edit that breaks one fails the gate.

Needs ``omni`` importable (Alfred's venv after A0); skipped otherwise.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Set

import pytest

pytest.importorskip("omni")

REPO_ROOT = Path(__file__).resolve().parents[1]
GRAPH_DIR = REPO_ROOT / ".omni" / "graphs"
GRAPH_FILES = sorted(GRAPH_DIR.glob("*.json"))

# J6: the bridge injects these; a committed file must never carry them.
HOUSE_CONSTRAINT_KEYS = {"cwd", "mcp_enabled", "mcp_config_path", "on_ask", "ask_timeout"}

# The graph files whose nodes must be present (guards against a silent deletion).
EXPECTED_GRAPHS = {"front-door", "device-troubleshoot", "home-routine", "new-capability"}


def _load_raw() -> Dict[str, Dict[str, Any]]:
    return {
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in GRAPH_FILES
    }


RAW = _load_raw()


def _iter_nodes(doc: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    yield from doc.get("nodes", [])


def _outcome_names(node: Dict[str, Any]) -> List[str]:
    outcomes = node.get("outcomes", [])
    return list(outcomes.keys()) if isinstance(outcomes, dict) else list(outcomes)


def _is_speaker(node: Dict[str, Any]) -> bool:
    """J8: a node speaks iff it is converse or its id starts with ``say-``."""
    return bool(node.get("converse")) or node["id"].startswith("say-")


def test_expected_graphs_present() -> None:
    assert EXPECTED_GRAPHS <= set(RAW), f"missing graphs: {EXPECTED_GRAPHS - set(RAW)}"


@pytest.mark.parametrize("path", GRAPH_FILES, ids=lambda p: p.stem)
def test_graph_loads_and_validates(path: Path, capsys: pytest.CaptureFixture) -> None:
    """Test 1: every graph loads and has no error-severity diagnostics."""
    schema = importlib.import_module("omni.graph.schema")
    validate = importlib.import_module("omni.graph.validate")
    tools = importlib.import_module("omni.graph.tools")

    library, load_errors, _names = tools.discover_library(REPO_ROOT)
    assert not load_errors, f"library load errors: {load_errors}"

    graph = schema.load_graph(json.loads(path.read_text(encoding="utf-8")), library=library)
    diagnostics = validate.validate(graph, library=library)
    for d in diagnostics:
        print(f"{path.name}: {d}")
    errors = [d for d in diagnostics if d.level == "error"]
    assert not errors, "\n".join(str(d) for d in errors)


def _terminal_speakers(name: str, seen: Set[str]) -> List[str]:
    """Return the terminal nodes of ``name`` (recursing into graph nodes) that do not speak."""
    if name in seen:
        return []
    seen = seen | {name}
    doc = RAW[name]
    by_id = {n["id"]: n for n in doc["nodes"]}
    bad: List[str] = []
    for tid in doc["terminal"]:
        node = by_id[tid]
        if node["kind"] == "graph":
            bad.extend(_terminal_speakers(node["graph"], seen))
        elif not _is_speaker(node):
            bad.append(f"{name}.{tid}")
    return bad


@pytest.mark.parametrize("name", sorted(RAW))
def test_terminal_nodes_speak(name: str) -> None:
    """Test 2 (J8): every terminal node is a speaker, except graph nodes (checked via subgraph)."""
    assert _terminal_speakers(name, set()) == []


@pytest.mark.parametrize("name", sorted(RAW))
def test_non_terminal_say_nodes_are_agents_or_tools(name: str) -> None:
    """Test 2b (J8): a ``say-*`` node is something that can speak (agent or tool), never decide/human/graph."""
    for node in _iter_nodes(RAW[name]):
        if node["id"].startswith("say-"):
            assert node["kind"] in {"agent", "tool"}, f"{name}.{node['id']} is {node['kind']}"


@pytest.mark.parametrize("name", sorted(RAW))
def test_no_house_constraints_in_files(name: str) -> None:
    """Test 3 (J6): house constraints are injected by the bridge, never written in a graph file."""
    for node in _iter_nodes(RAW[name]):
        leaked = HOUSE_CONSTRAINT_KEYS & set(node.get("constraints", {}))
        assert not leaked, f"{name}.{node['id']} carries {sorted(leaked)}"
        assert not (HOUSE_CONSTRAINT_KEYS & set(node)), f"{name}.{node['id']} has a top-level house key"


@pytest.mark.parametrize("name", sorted(RAW))
def test_decide_nodes_use_jev(name: str) -> None:
    """Test 4 (J16): every decide node names typesafe / jev-latest in the file."""
    for node in _iter_nodes(RAW[name]):
        if node["kind"] == "decide":
            c = node.get("constraints", {})
            assert c.get("provider") == "typesafe", f"{name}.{node['id']}"
            assert c.get("model") == "jev-latest", f"{name}.{node['id']}"


def _references(target: str) -> List[Dict[str, Any]]:
    return [
        n
        for doc in RAW.values()
        for n in _iter_nodes(doc)
        if n["kind"] == "graph" and n["graph"] == target
    ]


@pytest.mark.parametrize("name", sorted(RAW))
def test_earlier_is_received(name: str) -> None:
    """Test 5 (J12): a node that declares ``inputs.earlier`` lives in a graph that receives it.

    front-door is the root and always has ``earlier``; a subgraph receives it only if every
    ``graph`` node that runs it passes ``earlier``.
    """
    declares = [
        n["id"]
        for n in _iter_nodes(RAW[name])
        if "inputs.earlier" in (n.get("inputs") if isinstance(n.get("inputs"), list) else [])
    ]
    if not declares or name == "front-door":
        return
    refs = _references(name)
    assert refs, f"{name} declares inputs.earlier ({declares}) but no graph node runs it"
    for ref in refs:
        assert "earlier" in ref["inputs"], f"graph node {ref['id']} does not pass earlier to {name}"


def test_front_door_passes_earlier_to_subgraphs() -> None:
    for sub in ("device-troubleshoot", "home-routine"):
        refs = [r for r in _references(sub) if r in list(_iter_nodes(RAW["front-door"]))]
        assert refs, f"front-door does not run {sub}"
        assert all(r["inputs"].get("earlier") == "{{ inputs.earlier }}" for r in refs)


@pytest.mark.parametrize("name", sorted(RAW))
def test_graph_node_outcomes_match_subgraph_terminals(name: str) -> None:
    """The validator does not compare a graph node's declared outcomes to its subgraph's terminals
    (plan section 12), so keep them in step here: declared == union of terminal-node outcomes."""
    for node in _iter_nodes(RAW[name]):
        if node["kind"] != "graph":
            continue
        sub = RAW[node["graph"]]
        by_id = {n["id"]: n for n in sub["nodes"]}
        expected: Set[str] = set()
        for tid in sub["terminal"]:
            expected.update(_outcome_names(by_id[tid]))
        assert set(_outcome_names(node)) == expected, f"{name}.{node['id']}"


def test_bye_is_the_only_new_request_exit_in_troubleshoot() -> None:
    """Section 5.3: a terminal cannot also branch, so only ``say-bye`` returns ``new-request``."""
    doc = RAW["device-troubleshoot"]
    for tid in doc["terminal"]:
        node = next(n for n in doc["nodes"] if n["id"] == tid)
        has = "new-request" in _outcome_names(node)
        assert has == (tid == "say-bye"), tid


def test_alfred_agent_manifest_has_decision_and_tools() -> None:
    """Section 5.6 (J17): agent ``alfred`` names Jev for ``decide`` and may call it."""
    loader = importlib.import_module("omni.driver.artifacts.agent")
    agent = loader.load_agent("alfred", cwd=REPO_ROOT, home=REPO_ROOT)
    assert agent.decision.provider == "typesafe"
    assert agent.decision.model == "jev-latest"
    assert {"decide", "run_context"} <= set(agent.tools.allow)
