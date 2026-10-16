"""
Forge reviewer verdict and model wiring (omni-jev plan J21).

The LLM is mocked; no Ollama is contacted. Two old bugs are pinned here:
the verdict was a substring test ("NOT APPROVED" passed), and the Forge read the
removed ``settings.alfred_router_model`` at import time.
"""
from unittest.mock import Mock, patch

import pytest

from ai_server.config import settings
from ai_server.forge import agents


def _state() -> dict:
    return {"code_draft": "print('hi')", "iteration_count": 1, "review_comments": []}


def _review_with(reply: str) -> dict:
    fake_llm = Mock()
    fake_llm.invoke.return_value = reply
    with patch.object(agents, "llm", fake_llm):
        return agents.review_node(_state())


@pytest.mark.parametrize("reply", ["APPROVED", "approved", "  APPROVED  \n", "APPROVED\nLooks clean."])
def test_first_line_approved_completes(reply: str):
    result = _review_with(reply)
    assert result["status"] == "complete"
    assert result["review_comments"] == []


@pytest.mark.parametrize(
    "reply",
    ["NOT APPROVED", "Not approved: missing error handling", "", "   ", "Looks fine.\nAPPROVED"],
)
def test_anything_else_loops_back_to_coder(reply: str):
    result = _review_with(reply)
    assert result["status"] == "coding"
    assert result["review_comments"] == [reply]


def test_forge_uses_core_model_setting():
    assert agents.llm.model == settings.alfred_core_model
