"""Alfred's one door to a decision model (omni-jev plan, J5).

Lazily imports omni-kernel (``omni.driver.provider``) so the rest of Alfred
imports without it. Used by the Home Assistant MCP server (``resolve_entity``)
and by the bridge's ask interpreter (plan section 7.6).

The model is a ``TypeSafeDecision`` (Jev) whose credential is a
``KeyFileCredential`` on ``settings.jev_key_file``: the key is read from the file
on every call and never copied, logged or printed here. Every failure path
degrades to "no answer" so callers can fall back to an LLM: a missing key file,
an un-importable ``omni``, a different ``decision_provider``, a vendor error.

``state`` may hold the person's words, so it is never logged above DEBUG.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .config import settings

if TYPE_CHECKING:  # pragma: no cover - typing only; omni is imported lazily
    from omni.driver.provider import DecisionModel

logger = logging.getLogger(__name__)

#: The cached model; ``None`` until first built, and again after :func:`close`.
_model: "DecisionModel | None" = None

#: The name of the single question every :func:`choose` call asks.
_QUESTION_NAME = "q"


def get_decision_model() -> "DecisionModel | None":
    """A cached ``TypeSafeDecision(model=settings.decision_model)``, or ``None``.

    ``None`` (callers degrade) when ``settings.decision_provider`` is not
    ``"typesafe"``, ``omni`` is not importable, or the key file does not exist /
    is empty / is unreadable. A ``None`` result is not cached, so a key file
    created later is picked up on the next call.
    """
    global _model
    if _model is not None:
        return _model
    if settings.decision_provider != "typesafe":
        logger.info("decision provider %r is not 'typesafe'; no decision model",
                    settings.decision_provider)
        return None
    try:
        from omni.driver.provider import KeyFileCredential, TypeSafeDecision
    except Exception as exc:  # ImportError, or a kernel that fails to import
        logger.warning("omni is not importable (%s); no decision model", type(exc).__name__)
        return None
    credentials = KeyFileCredential(settings.jev_key_file)
    if credentials.resolve() is None:
        logger.warning("Jev key file is missing or empty; no decision model")
        return None
    _model = TypeSafeDecision(model=settings.decision_model, credentials=credentials)
    return _model


async def choose(
    question: str,
    options: Mapping[str, str],
    state: Mapping[str, Any],
    *,
    threshold: float,
) -> tuple[str | None, float]:
    """Ask one Jev ``choice`` question; return ``(answer, confidence)``.

    ``options`` maps each allowed answer to its description. ``answer`` is
    ``None`` when the model is unavailable, raised, or its confidence is below
    ``threshold`` (the confidence is still returned in that last case). **Never
    raises**: on any exception the result is ``(None, 0.0)`` and a WARNING names
    only the exception class.
    """
    try:
        model = get_decision_model()
        if model is None:
            return None, 0.0
        from omni.driver.provider import Question

        q = Question(
            name=_QUESTION_NAME,
            type="choice",
            instructions=question,
            criteria=dict(options),
        )
        result = await model.decide(state, [q])
        decision = result.decisions[_QUESTION_NAME]
        confidence = float(decision.confidence)
        if confidence < threshold:
            return None, confidence
        return decision.choice, confidence
    except Exception as exc:
        logger.warning("decision call failed: %s", type(exc).__name__)
        return None, 0.0


def close() -> None:
    """Close and forget the cached model (called on shutdown). Never raises."""
    global _model
    model, _model = _model, None
    if model is None:
        return
    try:
        model.close()
    except Exception as exc:
        logger.warning("closing the decision model failed: %s", type(exc).__name__)
