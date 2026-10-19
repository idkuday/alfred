"""Who speaks to the person, and what they say (omni-jev plan J8, section 7.5).

A node speaks iff it is an ``agent`` or ``tool`` node that is ``converse: true`` **or** whose
id starts with ``say-``. It is a naming rule on purpose (J8): greppable, no kernel field, no
side file. Evidence-gathering agents stay silent, so the person hears one voice.

This module also holds the one walk over a conversation's journal that both the reply
(``spoken``, section 7.5) and the transcript (``transcript.build``, section 7.7) are made
from, so they cannot disagree about what was said:

* the run stream gives node order, outcomes, and the answers to asks (``Response``);
* a ``graph`` node's ``NodeEntered.session_id`` is the **child run's id** (kernel
  ``_run_subrun``), so the walk descends into ``child_runs``;
* an agent/tool node's ``NodeEntered.session_id`` names the stream in ``session_streams``
  that holds what it said.

Everything here is pure: it takes kernel envelopes that were already read, and does no I/O.
Items are ordered by journal timestamp (stable, so equal timestamps keep walk order), which
puts an ask's answer between the speech before and after it even though they live on
different streams.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from omni.graph.schema import GraphError, Node
from omni.kernel.events import (
    BlockEnded,
    BlockKind,
    BlockStarted,
    CallEnded,
    CallOutcome,
    Envelope,
    MessageStarted,
    NodeEntered,
    NodeExited,
    Response,
    RunStarted,
)

from .models import ConversationState, PendingAsk

if TYPE_CHECKING:  # pragma: no cover - typing only
    from omni.graph.schema import Graph, GraphLibrary

logger = logging.getLogger(__name__)

#: Outcome a converse node emits to say "this is a different request" (J10). The text of a
#: terminal speaker that exits with it is not spoken; the handoff's first reply is.
HANDOFF_OUTCOME = "new-request"

SAY_PREFIX = "say-"

#: ``CallEnded.content`` is trimmed to this many characters in "I couldn't do that" (rule 3).
MAX_ERROR_CHARS = 200

#: Response decisions that mean "not done"; rendered as a refusal, not as an approval.
_REFUSALS = frozenset({"deny", "denied", "decline", "reject", "no", "cancelled", "timeout"})

SPEAKER_KINDS = frozenset({"agent", "tool"})


@dataclass(frozen=True, slots=True)
class Item:
    """One thing said, in the conversation's journal order."""

    role: Literal["user", "assistant"]
    text: str
    node: str | None
    at: float
    #: The stream the text came from and its seq there (what ``spoken(since=...)`` filters on).
    stream_id: str
    seq: int
    #: Handoff suppression (rule 4, J10): said by a terminal speaker that exited ``new-request``.
    #: ``spoken`` leaves it out of the *reply* (the handoff's first reply is what the person
    #: hears); ``walk`` keeps it, so the transcript and the ``earlier`` seed still hold what
    #: Alfred said (A5b).
    suppressed: bool = False


def is_speaker(node: Node) -> bool:
    """J8: whether ``node``'s words reach the person.

    Only ``agent`` and ``tool`` nodes can speak (a ``decide`` or ``human`` node has no
    session text), and they do iff ``converse`` is true or the id starts with ``say-``.
    """
    return node.kind in SPEAKER_KINDS and (node.converse or node.id.startswith(SAY_PREFIX))


def error_phrase(content: str, outcome: CallOutcome) -> str:
    """What Alfred says for a spoken tool call that did not succeed (rule 3).

    ``err`` is the plan's case. A ``denied``/``cancelled``/``timeout`` call is the person
    (or the clock) stopping it, so it is reported as not done rather than as a failure.
    """
    if outcome is CallOutcome.ERR:
        return f"I couldn't do that — {content[:MAX_ERROR_CHARS]}"
    return "Okay, I didn't do that."


def refusal_text(decision: str) -> str:
    """How an answer to an ask reads in a transcript (section 7.7 item 3)."""
    if decision.strip().lower() in _REFUSALS:
        return "(declined)"
    return f"(approved: {decision})"


def _session_items(
    node: Node,
    graph: "Graph | None",
    outcome: str | None,
    stream_id: str,
    envelopes: Sequence[Envelope],
) -> list[Item]:
    """What ``node`` said (and, for converse nodes, what was said to it) on its session."""
    if not is_speaker(node):
        return []
    suppressed = (
        outcome == HANDOFF_OUTCOME and graph is not None and graph.is_terminal(node.id)
    )
    items: list[Item] = []

    if node.kind == "tool":
        for env in envelopes:
            event = env.event
            if isinstance(event, CallEnded):
                if event.outcome is CallOutcome.OK:
                    text = event.content
                else:
                    text = error_phrase(event.content, event.outcome)
                if text.strip():
                    items.append(
                        Item("assistant", text, node.id, env.ts, stream_id, env.seq, suppressed)
                    )
        return items

    roles: dict[str, str] = {}
    kinds: dict[str, BlockKind] = {}
    first_user: str | None = None
    for env in envelopes:
        event = env.event
        if isinstance(event, MessageStarted):
            roles[event.msg_id] = event.role
        elif isinstance(event, BlockStarted):
            kinds[event.block_id] = event.kind
        elif isinstance(event, BlockEnded):
            if kinds.get(event.block_id) is not BlockKind.TEXT or not event.content.strip():
                continue
            role = roles.get(event.msg_id)
            if role == "assistant":
                items.append(
                    Item("assistant", event.content, node.id, env.ts, stream_id, env.seq,
                         suppressed)
                )
            elif role == "user" and node.converse:
                if first_user is None:
                    first_user = event.msg_id  # the kernel's seed prompt, never shown
                if event.msg_id != first_user:
                    items.append(Item("user", event.content, node.id, env.ts, stream_id, env.seq))
    return items


def walk(
    run_envelopes: Sequence[Envelope],
    child_runs: Mapping[str, Sequence[Envelope]],
    session_streams: Mapping[str, Sequence[Envelope]],
    library: "GraphLibrary",
) -> list[Item]:
    """Every user and assistant item of the conversation rooted at ``run_envelopes``.

    ``child_runs`` maps child run id -> that run's envelopes; ``session_streams`` maps node
    session id -> envelopes. A missing child or session simply contributes nothing (the
    bridge may not have attached it yet). A run whose ``definition_ref`` is not in
    ``library`` contributes its user and answer items but no node speech.
    """
    items: list[Item] = []
    _walk_run(run_envelopes, child_runs, session_streams, library, items, root=True)
    items.sort(key=lambda item: item.at)  # stable: ties keep walk order
    return items


def _walk_run(
    envelopes: Sequence[Envelope],
    child_runs: Mapping[str, Sequence[Envelope]],
    session_streams: Mapping[str, Sequence[Envelope]],
    library: "GraphLibrary",
    items: list[Item],
    *,
    root: bool,
) -> None:
    graph: "Graph | None" = None
    open_nodes: dict[str, tuple[Node, str]] = {}

    def flush(node_id: str, outcome: str | None) -> None:
        entered = open_nodes.pop(node_id, None)
        if entered is None:
            return
        node, session_id = entered
        items.extend(
            _session_items(node, graph, outcome, session_id, session_streams.get(session_id, ()))
        )

    for env in envelopes:
        event = env.event
        if isinstance(event, RunStarted):
            try:
                graph = library.resolve(event.definition_ref)
            except GraphError:
                logger.warning(
                    "run %s: graph %s is not in the library", event.run_id, event.definition_ref
                )
            if root:
                message = event.inputs.get("message")
                if isinstance(message, str) and message.strip():
                    items.append(Item("user", message, None, env.ts, env.stream_id, env.seq))
        elif isinstance(event, NodeEntered) and graph is not None:
            node = graph.node(event.node_id)
            if node is None:
                continue
            if node.kind == "graph":
                child = child_runs.get(event.session_id)
                if child:
                    _walk_run(child, child_runs, session_streams, library, items, root=False)
            elif node.kind in SPEAKER_KINDS and event.session_id:
                open_nodes[event.node_id] = (node, event.session_id)
        elif isinstance(event, NodeExited):
            flush(event.node_id, event.outcome)
        elif isinstance(event, Response):
            items.append(
                Item("user", refusal_text(event.decision), None, env.ts, env.stream_id, env.seq)
            )
    for node_id in list(open_nodes):  # still running or parked: no outcome yet
        flush(node_id, None)


def spoken(
    run_envelopes: Sequence[Envelope],
    child_runs: Mapping[str, Sequence[Envelope]],
    session_streams: Mapping[str, Sequence[Envelope]],
    library: "GraphLibrary",
    *,
    since: Mapping[str, int] | None = None,
) -> list[str]:
    """What Alfred says now: assistant texts in journal order (section 7.5).

    Items marked ``suppressed`` (a terminal speaker that handed off) are left out: that is
    rule 4, and it applies to this reply only, never to ``walk`` (the transcript).

    ``since`` maps stream id -> the last seq seen there when ``say()`` submitted its work;
    only text journaled after that is returned, so earlier turns are not repeated.
    """
    cutoff = since or {}
    return [
        item.text
        for item in walk(run_envelopes, child_runs, session_streams, library)
        if item.role == "assistant"
        and not item.suppressed
        and item.seq > cutoff.get(item.stream_id, 0)
    ]


def compose_reply(
    pieces: Sequence[str], state: ConversationState, pending: PendingAsk | None
) -> str:
    """Join spoken pieces into the reply (section 7.5 rule 5).

    Pieces are separated by a blank line. With a pending ask the question is the reply when
    nothing else was said, and is appended to it otherwise.
    """
    texts = [piece.strip() for piece in pieces if piece.strip()]
    if pending is not None and state == "awaiting_approval":
        texts.append(pending.question)
    return "\n\n".join(texts)
