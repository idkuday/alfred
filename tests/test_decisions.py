"""Tests for ai_server/decisions.py (omni-jev plan, J5, section 6.1).

Never calls TypeSafe: ``get_decision_model`` is patched with a fake
``DecisionModel`` wherever a model is needed. Key files hold a fake key.
"""
import logging

import pytest

pytest.importorskip("omni")

from omni.driver.provider import Decision, DecisionResult, TypeSafeDecision  # noqa: E402
from omni.driver.provider.decision import validate_questions  # noqa: E402

from ai_server import decisions  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_cache():
    decisions._model = None
    yield
    decisions._model = None


class FakeModel:
    """Answers one question with a canned choice and confidence."""

    def __init__(self, choice="a", confidence=0.9, raises=None):
        self.choice, self.confidence, self.raises = choice, confidence, raises
        self.calls = []
        self.closed = False

    async def decide(self, state, questions):
        self.calls.append((state, list(questions)))
        validate_questions(questions)  # as every real DecisionModel does
        if self.raises is not None:
            raise self.raises
        q = questions[0]
        return DecisionResult(
            decisions={
                q.name: Decision(
                    name=q.name,
                    type=q.type,
                    choice=self.choice,
                    confidence=self.confidence,
                    probabilities={k: 0.0 for k in q.criteria},
                )
            },
            model="fake",
            latency_ms=1,
        )

    def close(self):
        self.closed = True


OPTIONS = {"a": "first", "b": "second"}


# ── get_decision_model ───────────────────────────────────────────────────


def test_model_is_none_when_key_file_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(decisions.settings, "jev_key_file", str(tmp_path / "nope.env"))
    assert decisions.get_decision_model() is None


def test_model_is_none_when_key_file_is_empty(monkeypatch, tmp_path):
    key = tmp_path / "empty.env"
    key.write_text("  \n", encoding="utf-8")
    monkeypatch.setattr(decisions.settings, "jev_key_file", str(key))
    assert decisions.get_decision_model() is None


def test_model_is_none_for_other_provider(monkeypatch, tmp_path):
    key = tmp_path / "jev.env"
    key.write_text("fake-key-not-real", encoding="utf-8")
    monkeypatch.setattr(decisions.settings, "jev_key_file", str(key))
    monkeypatch.setattr(decisions.settings, "decision_provider", "other")
    assert decisions.get_decision_model() is None


def test_model_is_a_cached_typesafe_decision_when_key_exists(monkeypatch, tmp_path):
    key = tmp_path / "jev.env"
    key.write_text("fake-key-not-real", encoding="utf-8")
    monkeypatch.setattr(decisions.settings, "jev_key_file", str(key))
    monkeypatch.setattr(decisions.settings, "decision_provider", "typesafe")
    monkeypatch.setattr(decisions.settings, "decision_model", "jev-latest")
    model = decisions.get_decision_model()
    assert isinstance(model, TypeSafeDecision)
    assert model.model == "jev-latest"
    assert decisions.get_decision_model() is model
    assert "fake-key-not-real" not in repr(model)
    decisions.close()
    assert decisions._model is None


# ── choose ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_choose_without_a_model_degrades(monkeypatch):
    monkeypatch.setattr(decisions, "get_decision_model", lambda: None)
    assert await decisions.choose("which?", OPTIONS, {}, threshold=0.5) == (None, 0.0)


@pytest.mark.asyncio
async def test_choose_above_threshold_returns_the_answer(monkeypatch):
    model = FakeModel(choice="b", confidence=0.8)
    monkeypatch.setattr(decisions, "get_decision_model", lambda: model)
    answer = await decisions.choose("which?", OPTIONS, {"request": "hi"}, threshold=0.6)
    assert answer == ("b", 0.8)
    state, questions = model.calls[0]
    assert state == {"request": "hi"}
    assert questions[0].type == "choice"
    assert questions[0].instructions == "which?"
    assert dict(questions[0].criteria) == OPTIONS


@pytest.mark.asyncio
async def test_choose_below_threshold_returns_no_answer(monkeypatch):
    monkeypatch.setattr(
        decisions, "get_decision_model", lambda: FakeModel(choice="a", confidence=0.4)
    )
    assert await decisions.choose("which?", OPTIONS, {}, threshold=0.6) == (None, 0.4)


@pytest.mark.asyncio
async def test_choose_at_threshold_is_accepted(monkeypatch):
    monkeypatch.setattr(
        decisions, "get_decision_model", lambda: FakeModel(choice="a", confidence=0.6)
    )
    assert await decisions.choose("which?", OPTIONS, {}, threshold=0.6) == ("a", 0.6)


@pytest.mark.asyncio
async def test_choose_never_raises_and_logs_only_the_class(monkeypatch, caplog):
    boom = RuntimeError("secret-state-leak")
    monkeypatch.setattr(decisions, "get_decision_model", lambda: FakeModel(raises=boom))
    with caplog.at_level(logging.DEBUG, logger="ai_server.decisions"):
        result = await decisions.choose("which?", OPTIONS, {"request": "x"}, threshold=0.5)
    assert result == (None, 0.0)
    assert "RuntimeError" in caplog.text
    assert "secret-state-leak" not in caplog.text


@pytest.mark.asyncio
async def test_choose_with_one_option_degrades_instead_of_raising(monkeypatch):
    monkeypatch.setattr(decisions, "get_decision_model", lambda: FakeModel())
    assert await decisions.choose("which?", {"a": "only"}, {}, threshold=0.5) == (None, 0.0)


# ── close ────────────────────────────────────────────────────────────────


def test_close_closes_and_forgets_the_cached_model():
    model = FakeModel()
    decisions._model = model
    decisions.close()
    assert model.closed and decisions._model is None
    decisions.close()  # idempotent
