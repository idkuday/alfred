"""Static checks on Alfred's four agent manifests (pr-review A1).

Why: AD1-AD7 move every manifest to ``claude-sonnet-5-5`` and stop restating kernel
defaults (``max_tokens``, ``budget.max_iterations``, ``mode``, ``time_aware``). Nothing
else fails when someone pastes the old caps back, so this file does.

Needs ``omni`` importable (Alfred's venv); skipped otherwise.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any, Dict

import pytest

pytest.importorskip("omni")
yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[1]
OMNI_DIR = REPO_ROOT / ".omni"
AGENTS = ("alfred", "alfred-builder", "alfred-designer", "alfred-verifier")
MODEL = "claude-sonnet-5-5"
OLD_CLAUDE_ID = re.compile(r"claude-(opus|sonnet|haiku)-4|claude-3")


def _loader() -> Any:
    return importlib.import_module("omni.driver.artifacts.agent")


def _raw(name: str) -> Dict[str, Any]:
    return yaml.safe_load((OMNI_DIR / name / "agent.yml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", AGENTS)
def test_manifest_names_sonnet_5_5_and_restates_no_defaults(name: str) -> None:
    """AD1-AD4: model pinned, output cap and iteration cap left to the kernel, cost kept."""
    models = importlib.import_module("omni.driver.provider.anthropic.models")
    agent = _loader().load_agent(name, cwd=REPO_ROOT, home=REPO_ROOT)
    raw = _raw(name)

    assert agent.provider == "anthropic"
    assert agent.model == MODEL
    meta = models.MODEL_METADATA[MODEL]
    assert meta.price_input > 0, "budget.cost cannot trip on an unpriced model"

    assert agent.max_tokens is None
    assert agent.budget.max_iterations is _loader().ABSENT
    assert agent.budget.cost is not None

    for key in ("max_tokens", "mode", "time_aware"):
        assert key not in raw
    assert "tokens" not in raw["budget"]
    assert "max_iterations" not in raw["budget"]
    assert "cost" in raw["budget"]


def test_alfred_compaction_is_absolute_150k_keeping_six() -> None:
    """AD5: compaction in tokens, not 0.7 of a 1M window; mode and time_aware are defaults (AD6)."""
    agent = _loader().load_agent("alfred", cwd=REPO_ROOT, home=REPO_ROOT)
    assert agent.compaction.threshold == 150000
    assert agent.compaction.keep_recent == 6
    assert agent.mode == "interactive"
    assert agent.time_aware == "basic"


def test_no_old_claude_id_under_dot_omni() -> None:
    """AD1: no file under ``.omni/`` still names a retired Claude model."""
    hits = [
        str(p.relative_to(REPO_ROOT))
        for p in OMNI_DIR.rglob("*")
        if p.is_file()
        and p.suffix in {".yml", ".yaml", ".json", ".md"}
        and OLD_CLAUDE_ID.search(p.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert hits == []
