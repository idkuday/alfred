"""front-door's ``say-act`` / ``say-status`` pass ``entity_id`` and ``entity_name`` (pr-review A3, AD9).

Why: ``apply`` / ``describe`` used to take the whole resolve output as a JSON string
``target``. The kernel (pr-review P17) now walks a tool output that is a JSON object, so the
graph names the two fields. These tests pin the graph's args and check that the kernel's
``substitute`` really resolves them against a blackboard.

Needs ``omni`` importable; skipped otherwise.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("omni")

from omni.kernel.runs.blackboard import Blackboard, substitute  # noqa: E402

FRONT_DOOR = Path(__file__).resolve().parents[1] / ".omni" / "graphs" / "front-door.json"

RESOLVED = {"entity_id": "light.x", "entity_name": "X", "domain": "light", "confidence": 1.0}


def _node_args(node_id: str) -> dict[str, Any]:
    graph = json.loads(FRONT_DOOR.read_text(encoding="utf-8"))
    return next(n for n in graph["nodes"] if n["id"] == node_id)["args"]


@pytest.mark.parametrize(
    "node_id,resolver_node",
    [("say-act", "resolve"), ("say-status", "resolve-read")],
)
def test_args_name_the_resolve_fields_and_no_target(node_id, resolver_node):
    args = _node_args(node_id)
    assert "target" not in args
    assert args["entity_id"] == "{{ outputs.%s.entity_id }}" % resolver_node
    assert args["entity_name"] == "{{ outputs.%s.entity_name }}" % resolver_node


@pytest.mark.parametrize(
    "node_id,resolver_node",
    [("say-act", "resolve"), ("say-status", "resolve-read")],
)
def test_substitute_resolves_the_fields_from_a_json_tool_output(node_id, resolver_node):
    board = Blackboard(
        inputs={"message": "turn on x"},
        outputs={resolver_node: "ref", "device-action": "act"},
        resolver={"ref": json.dumps(RESOLVED), "act": json.dumps({"choice": "turn-on"})}.get,
    )
    resolved = substitute(_node_args(node_id), board)
    assert resolved["entity_id"] == "light.x"
    assert resolved["entity_name"] == "X"
