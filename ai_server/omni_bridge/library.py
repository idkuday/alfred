"""The graph library the bridge hands to ``GraphPolicy``, with the house constraints injected.

Why injection happens here and not in the graph files (J6): machine paths must never enter a
committed file, ``omni graph validate`` should still validate the files exactly as written,
and there should be one place to change node defaults. So the bridge reads each
``.omni/graphs/*.json`` as a plain JSON document, ``setdefault``s the house keys on every
``agent`` and ``tool`` node (inline subgraphs included), and only then loads the document
into a ``GraphLibrary``. A node that already states a key keeps its own value. ``decide`` and
``human`` nodes are never touched.

Named subgraphs (``"graph": "device-troubleshoot"``) live in their own files in the same
directory, so loading every file through :func:`inject_constraints` covers them: the library
the policy resolves through never holds an un-injected definition.

``write_local_mcp_config`` is J7: the daemon spawns MCP servers itself, with whatever
``python`` is on *its* PATH and its own working directory, so the bridge writes
``.omni/mcp.local.json`` (git-ignored) with Alfred's interpreter and the absolute repo root.

This module reads and writes files and does nothing else: no network, no ``await``.
``omni`` is imported lazily so the rest of Alfred imports without it.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from omni.graph.schema import Graph, GraphLibrary

logger = logging.getLogger(__name__)

#: Node kinds that run a session and so receive the house constraints (plan section 5.1).
INJECTED_KINDS = frozenset({"agent", "tool"})

GRAPHS_SUBDIR = Path(".omni") / "graphs"
MCP_CONFIG = Path(".omni") / "mcp.json"
MCP_LOCAL_CONFIG = Path(".omni") / "mcp.local.json"


class LibraryError(ValueError):
    """A graph file that cannot be read as a graph document."""


def local_mcp_config_path(repo_root: Path) -> Path:
    """Where the generated MCP config goes for ``repo_root`` (a join; no I/O)."""
    return Path(repo_root) / MCP_LOCAL_CONFIG


def house_constraints(repo_root: Path, settings: Any) -> dict[str, Any]:
    """The constraint defaults of plan section 5.1, for ``repo_root`` and ``settings``.

    ``provider``/``model`` appear only when ``settings.omni_node_provider`` /
    ``omni_node_model`` are set.
    """
    root = Path(repo_root).resolve()
    house: dict[str, Any] = {
        "cwd": str(root),
        "mcp_enabled": True,
        "mcp_config_path": str(local_mcp_config_path(root)),
        "on_ask": "prompt",
        "ask_timeout": settings.omni_ask_timeout,
    }
    provider = getattr(settings, "omni_node_provider", None)
    model = getattr(settings, "omni_node_model", None)
    if provider:
        house["provider"] = provider
    if model:
        house["model"] = model
    return house


def inject_constraints(document: Mapping[str, Any], house: Mapping[str, Any]) -> dict[str, Any]:
    """A deep copy of ``document`` with ``house`` set as defaults on every agent/tool node.

    Recurses into inline subgraphs (a ``graph`` node whose ``graph`` is an object). Does not
    mutate its input.
    """
    out = copy.deepcopy(dict(document))
    _inject(out, house)
    return out


def _inject(document: dict[str, Any], house: Mapping[str, Any]) -> None:
    nodes = document.get("nodes")
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if not isinstance(node, dict):
            continue
        kind = node.get("kind")
        if kind in INJECTED_KINDS:
            constraints = node.get("constraints")
            if not isinstance(constraints, dict):
                constraints = {}
                node["constraints"] = constraints
            for key, value in house.items():
                constraints.setdefault(key, value)
        elif kind == "graph" and isinstance(node.get("graph"), dict):
            _inject(node["graph"], house)


def load_library(repo_root: Path, settings: Any) -> "tuple[GraphLibrary, dict[str, Graph]]":
    """Load every ``<repo_root>/.omni/graphs/*.json`` into a library, constraints injected.

    Returns ``(library, graphs)`` where ``graphs`` maps graph name to the loaded ``Graph``.
    A file without a ``name`` is named after its stem, so ``"graph": "<stem>"`` references
    resolve. Raises :class:`LibraryError` for a directory with no graphs or a file that is
    not a JSON object; whether the graphs are *valid* is ``omni graph validate``'s job.
    """
    from omni.graph.schema import GraphLibrary

    root = Path(repo_root)
    graphs_dir = root / GRAPHS_SUBDIR
    files = sorted(graphs_dir.glob("*.json"))
    if not files:
        raise LibraryError(f"no graphs found in {graphs_dir}")
    house = house_constraints(root, settings)
    library = GraphLibrary()
    graphs: dict[str, "Graph"] = {}
    for path in files:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LibraryError(f"cannot read graph {path.name}: {exc}") from exc
        if not isinstance(document, dict):
            raise LibraryError(f"graph {path.name} is not a JSON object")
        document.setdefault("name", path.stem)
        graph = library.load(inject_constraints(document, house))
        graphs[graph.name] = graph
    logger.info("loaded %d graph(s): %s", len(graphs), ", ".join(sorted(graphs)))
    return library, graphs


def write_local_mcp_config(repo_root: Path) -> Path:
    """Generate ``.omni/mcp.local.json`` from ``.omni/mcp.json`` (J7) and return its path.

    Each server whose ``command`` is ``"python"`` gets ``sys.executable`` instead (Alfred's
    own interpreter), and every server's ``cwd`` becomes the absolute repo root. Everything
    else (args, grades, comments) is carried through unchanged. With no ``mcp.json`` the
    result has no servers. The write is atomic (temp file then replace).
    """
    root = Path(repo_root).resolve()
    source = root / MCP_CONFIG
    target = root / MCP_LOCAL_CONFIG
    config: dict[str, Any] = {}
    if source.exists():
        try:
            loaded = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LibraryError(f"cannot read {source}: {exc}") from exc
        if isinstance(loaded, dict):
            config = loaded
    else:
        logger.warning("%s not found; writing a local MCP config with no servers", source)
    servers = config.get("mcpServers")
    config["mcpServers"] = {}
    if isinstance(servers, dict):
        for name, server in servers.items():
            if not isinstance(server, dict):
                continue
            entry = dict(server)
            if entry.get("command") == "python":
                entry["command"] = sys.executable
            entry["cwd"] = str(root)
            config["mcpServers"][name] = entry
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target
