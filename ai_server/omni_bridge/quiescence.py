"""When is it Alfred's turn to speak? (omni-jev plan section 7.4)

``say()`` submits work and then reads streams until the conversation is *quiescent*. The
decision is a fold, never a single event, because the kernel announces the floor once per
node entry (``FloorHeld``) and not once per reply (kernel section 10 T7): a later turn that
again ends without an outcome says nothing new. So "awaiting the user" is: the run that owns
the floor says so (``RunFold.floor``) **and** that session's latest turn has ended.

``state_of`` is the pure part; the timeout (``settings.omni_turn_timeout`` -> ``working``)
and the reading loop are the client's. ``None`` means keep reading.

Rules, first match wins:

1. the root run has ended -> ``ended``;
2. any run (root or child) has an unanswered ``Request`` -> ``awaiting_approval``;
3. any run holds the floor on a session whose latest turn has ended **without leaving**
   -> ``awaiting_user``;
4. otherwise ``None``.

Leaving (fixed in A5c): a floor node's turn that called ``emit_outcome`` (its ``CallEnded``
was ``ok``) is the node saying goodbye, not asking. The kernel journals that turn's
``TurnEnded`` *before* it harvests the outcome and writes ``NodeExited``, and until then
``RunFold.floor`` still names the session, so rule 3 used to see "floor held, turn ended"
and answered ``awaiting_user`` while the walk was about to move on (live: the handoff of
"forget that, turn on the coffee maker" was lost). Such a turn (``TurnState.leaving``) never
makes the conversation ``awaiting_user``: the answer is ``None`` (keep reading) until the
journal says what came next, which is a ``NodeExited`` that clears the floor, then a later
park (another floor, an ask), the run's end, or the client's timeout. This reads the same
facts the kernel's own harvest reads (``supervisor._harvest``): a *failed* ``emit_outcome``
routes nothing, so it does not count and the floor really is still the user's.

Stale turns: right after ``prompt(floor_session, text)`` the session's *previous* turn is
still the latest one in a snapshot, and it has ended. Pass ``since`` (session id -> the
``TurnState.started_seq`` last seen before submitting); a turn only counts if it started
after that.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from omni.kernel.events import (
    CallEnded,
    CallOutcome,
    CallStarted,
    Envelope,
    SessionEnded,
    TurnEnded,
    TurnStarted,
)
from omni.kernel.runs.fold import RunFold

from .models import ConversationState

#: the kernel's tool that ends a node (``omni.driver.tools.emit_outcome.EMIT_OUTCOME``);
#: spelled out so this module stays importable with the events alone.
EMIT_OUTCOME = "emit_outcome"


@dataclass(frozen=True, slots=True)
class TurnState:
    """The latest turn on a node session: ``seq`` of its start and, if over, of its end.

    ``ended_seq`` is ``0`` while the turn is running. A session that has not started a turn
    is ``TurnState(0, 0)``. ``outcome`` is true when the turn called ``emit_outcome``
    successfully, i.e. the node is leaving (its ``NodeExited`` follows in the run stream).
    """

    started_seq: int = 0
    ended_seq: int = 0
    outcome: bool = False

    @property
    def over(self) -> bool:
        return self.started_seq > 0 and self.ended_seq > self.started_seq

    @property
    def leaving(self) -> bool:
        """The turn ended by handing the node's outcome to the graph (A5c)."""
        return self.over and self.outcome


def turn_state(envelopes: Iterable[Envelope]) -> TurnState:
    """The latest turn of a session stream (the same reading the kernel's floor uses).

    A ``SessionEnded`` closes a turn that was still open: a dead session is not "working".
    ``outcome`` is read from the same turn alone (A5c): an ``emit_outcome`` call that ended
    ``ok`` after the turn started; a failed one, or one from an earlier turn, does not count.
    """
    started = ended = 0
    outcome = False
    call_id: str | None = None  # the open emit_outcome call
    for env in envelopes:
        event = env.event
        if isinstance(event, TurnStarted):
            started, ended, outcome, call_id = env.seq, 0, False, None
        elif isinstance(event, CallStarted) and event.name == EMIT_OUTCOME:
            call_id = event.call_id
        elif isinstance(event, CallEnded) and event.call_id == call_id:
            outcome = outcome or event.outcome is CallOutcome.OK
            call_id = None
        elif isinstance(event, TurnEnded):
            ended = env.seq
        elif isinstance(event, SessionEnded) and started and not ended:
            ended = env.seq
    return TurnState(started, ended, outcome)


def state_of(
    run_folds: Sequence[RunFold],
    session_turns: Mapping[str, TurnState],
    since: Mapping[str, int] | None = None,
) -> ConversationState | None:
    """The conversation's state, or ``None`` if more events are needed.

    ``run_folds`` is the root run's fold first, then any child runs' folds.
    ``session_turns`` maps a session id to its :func:`turn_state`. See the module docstring
    for ``since``.
    """
    if not run_folds:
        return None
    root = run_folds[0]
    if root.ended is not None:
        return "ended"
    if any(fold.pending_request is not None for fold in run_folds):
        return "awaiting_approval"
    floor_since = since or {}
    for fold in run_folds:
        if fold.floor is None or fold.ended is not None:
            continue
        session_id = fold.floor[1]
        turn = session_turns.get(session_id)
        if turn is None or not turn.over or turn.leaving:
            continue  # no turn yet, still talking, or leaving: wait for NodeExited (A5c)
        if turn.started_seq > floor_since.get(session_id, 0):
            return "awaiting_user"
    return None
