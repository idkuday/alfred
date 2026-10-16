"""A conversation's transcript, rebuilt from the journal (omni-jev plan section 7.7).

Why the journal and not the bridge's memory: after a restart the bridge remembers nothing,
yet ``GET /sessions/{id}`` and "load an earlier conversation" (J11) must still work. So the
transcript is a pure function of the run's envelopes, its child runs' and its node sessions'.
It reuses ``speakers.walk``, so what the transcript says Alfred said is exactly what the reply
said at the time.

Also here, because they read the same journal and are equally pure:

* ``earlier_text`` - the ``inputs.earlier`` seed (section 7.7, J11/J12), bounded to the newest
  ``settings.omni_earlier_chars`` characters;
* ``handoff_text`` - the words a ``new-request`` handoff carries to the next run (J10);
* ``summarize`` - the ``ConversationSummary`` of a run fold.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from omni.kernel.events import BlockEnded, CallEnded, Envelope
from omni.kernel.runs.blackboard import parse_ref
from omni.kernel.runs.fold import RunFold

from . import quiescence, speakers
from .models import (
    TITLE_CHARS,
    ConversationState,
    ConversationSummary,
    TranscriptEntry,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from omni.graph.schema import GraphLibrary


def iso(ts: float | None) -> str | None:
    """``ts`` (epoch seconds) as an ISO 8601 UTC string; ``None`` stays ``None``."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def build(
    run_envelopes: Sequence[Envelope],
    child_runs: Mapping[str, Sequence[Envelope]],
    session_streams: Mapping[str, Sequence[Envelope]],
    graphs: "GraphLibrary",
) -> list[TranscriptEntry]:
    """The transcript of the run whose stream is ``run_envelopes`` (section 7.7).

    First entry: the user's ``inputs.message``. Then, in journal order across the run and
    its child runs: what each speaker node said (``assistant``), what the person said to a
    converse node after its seed prompt (``user``), and the answers to asks
    (``"(approved: <decision>)"``, or ``"(declined)"`` for a refusal).
    """
    return [
        TranscriptEntry(
            role=item.role,
            text=item.text,
            node=item.node if item.role == "assistant" else None,
            at=iso(item.at),
        )
        for item in speakers.walk(run_envelopes, child_runs, session_streams, graphs)
    ]


def earlier_text(entries: Sequence[TranscriptEntry], max_chars: int) -> str:
    """The ``inputs.earlier`` seed: ``"role: text"`` lines, newest ``max_chars`` kept.

    Exactly the last ``max_chars`` characters of the joined text, so a long conversation
    loses its oldest words (and possibly the start of one line) rather than its latest.
    """
    if max_chars <= 0:
        return ""
    text = "\n".join(f"{entry.role}: {entry.text}" for entry in entries)
    return text[-max_chars:]


def ref_text(ref: str | None, session_streams: Mapping[str, Sequence[Envelope]]) -> str | None:
    """The text a kernel ``output_ref`` (``<stream>#<seq>``) names, if its stream is known.

    For an ``emit_outcome`` call that is the ``output`` argument (the capability returns it
    as ``CallEnded.content``); for a plain agent node it is its last text block.
    """
    parsed = parse_ref(ref) if ref else None
    if parsed is None:
        return None
    stream_id, seq = parsed
    for env in session_streams.get(stream_id, ()):
        if env.seq == seq and isinstance(env.event, (CallEnded, BlockEnded)):
            return env.event.content
    return None


def handoff_text(
    fold: RunFold, session_streams: Mapping[str, Sequence[Envelope]]
) -> str | None:
    """The new request a run handed off with, or ``None`` if it did not hand off (J10).

    A run hands off when it has ended and its last node exited with ``new-request``; the
    words are that node's output. ``fold.outputs`` keeps a ref all the way up through
    subgraphs, so the ref may name a node session several runs down - pass every session
    stream the bridge has attached.
    """
    if fold.ended is None:
        return None
    last = fold.last_node
    if last is None or last.outcome != speakers.HANDOFF_OUTCOME:
        return None
    text = ref_text(fold.outputs.get(last.node_id), session_streams)
    return text.strip() if text and text.strip() else None


def summarize(
    fold: RunFold,
    turns: Mapping[str, quiescence.TurnState] | None = None,
) -> ConversationSummary:
    """The listing row for a run (``GET /sessions``).

    ``turns`` (session id -> turn state) decides ``awaiting_user`` exactly as ``say()``
    does. Without it a run that holds the floor is listed as ``awaiting_user`` anyway,
    since for a listing the difference between "just replied" and "about to reply" is not
    worth a stream read.
    """
    state: ConversationState | None = quiescence.state_of([fold], turns or {})
    if state is None:
        state = "awaiting_user" if fold.floor is not None and turns is None else "working"
    message = fold.inputs.get("message")
    title = message.strip()[:TITLE_CHARS] if isinstance(message, str) else ""
    continues = fold.inputs.get("continues")
    return ConversationSummary(
        conversation_id=fold.run_id,
        title=title,
        started_at=iso(fold.started_ts) or "",
        last_active=iso(fold.last_ts) or "",
        state=state,
        continued_from=continues if isinstance(continues, str) and continues else None,
    )
