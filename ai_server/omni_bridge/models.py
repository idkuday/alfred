"""Data shapes of the omni bridge (omni-jev plan section 7.3) and the hidden-conversations file.

Why Pydantic with ``extra="forbid"``: these cross the HTTP boundary (``/execute``,
``/sessions``) and a drifting field should fail loudly, as it does for every other new API
body in Alfred. The kernel's own events stay frozen dataclasses; nothing in this module
imports ``omni``.

``PendingAsk`` carries three fields beyond the plan's list (``descriptions``, ``fallback``,
``respond_with``). They are what lets ``asks.interpret`` stay a pure function of
``(pending, text, decide)``: the ask is rendered once from a kernel ``Request`` plus the graph
node that raised it, and everything later needed to answer it travels with it (plan section 12,
task A4).

Hidden conversations (section 7.9): the file is a JSON list of run ids. Only the format lives
here; reading and writing it, and cancelling the run, are the client's (A5).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

ConversationState = Literal["awaiting_user", "awaiting_approval", "working", "ended"]

#: Where hidden conversation ids are kept, relative to ``settings.data_dir`` (section 7.9).
HIDDEN_FILENAME = "hidden_conversations.json"

#: ``ConversationSummary.title`` is the first this-many characters of ``inputs.message``.
TITLE_CHARS = 60


class ConversationNotFound(LookupError):
    """An id that names no conversation the bridge can show (``main.py`` answers 404)."""


class BridgeUnavailable(RuntimeError):
    """The omni daemon cannot be reached (``main.py`` answers 503)."""


class PendingAsk(BaseModel):
    """A question the run is parked on, as Alfred puts it to the person."""

    model_config = ConfigDict(extra="forbid")

    req_id: str
    stream_id: str = Field(description="Run (or child run) stream that carries the Request.")
    kind: Literal["permission", "approval", "elicit"]
    question: str = Field(description="What Alfred says, already rendered.")
    options: list[str] = Field(
        default_factory=list,
        description="permission: ['allow', 'deny']; approval: the request's outcomes.",
    )
    reasks: int = 0
    # --- beyond section 7.3; see the module docstring -------------------------------------
    descriptions: dict[str, str] = Field(
        default_factory=dict,
        description="Option -> what it means, for the decision model (approval asks).",
    )
    fallback: str | None = Field(
        default=None,
        description="Option chosen when the person's answer stays unclear after one re-ask.",
    )
    respond_with: dict[str, str] = Field(
        default_factory=dict,
        description="Option -> the kernel decision word to send; identity when absent.",
    )


class ConversationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    title: str = Field(description="First 60 characters of inputs.message.")
    started_at: str = Field(description="ISO 8601, UTC.")
    last_active: str
    state: ConversationState
    continued_from: str | None = None


class ConversationReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(
        description="The run id (J1); a NEW id after a handoff or a fresh start."
    )
    answer: str = Field(description="What Alfred says now ('' if nothing yet).")
    state: ConversationState
    pending: PendingAsk | None = None
    new_conversation: bool = False
    continued_from: str | None = None


class TranscriptEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    text: str
    node: str | None = Field(default=None, description="Graph node that spoke (assistant only).")
    at: str | None = None


class Transcript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation: ConversationSummary
    entries: list[TranscriptEntry]


# ── hidden conversations (section 7.9) ──────────────────────────────────────


def hidden_path(data_dir: str | Path) -> Path:
    """Where the hidden-conversations file lives under ``data_dir`` (a join; no I/O)."""
    return Path(data_dir) / HIDDEN_FILENAME


def parse_hidden(text: str) -> frozenset[str]:
    """The ids in a hidden-conversations file's text.

    Lenient on purpose: a missing, empty or corrupt file means "nothing hidden", never a
    failure to list conversations. Non-string entries are dropped.
    """
    if not text.strip():
        return frozenset()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("hidden conversations file is not valid JSON; treating as empty")
        return frozenset()
    if not isinstance(data, list):
        logger.warning("hidden conversations file is not a JSON list; treating as empty")
        return frozenset()
    return frozenset(item for item in data if isinstance(item, str))


def render_hidden(ids: Iterable[str]) -> str:
    """The file text for ``ids``: a sorted, de-duplicated JSON list."""
    return json.dumps(sorted(set(ids)), indent=2) + "\n"


def with_hidden(current: Iterable[str], conversation_id: str) -> frozenset[str]:
    """``current`` plus ``conversation_id`` (idempotent)."""
    return frozenset(current) | {conversation_id}
