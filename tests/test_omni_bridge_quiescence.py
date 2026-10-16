"""When is it Alfred's turn to speak? (plan section 7.4)."""

import pytest

pytest.importorskip("omni")

from omni.kernel.events import SessionEnded, TurnEnded, TurnStarted  # noqa: E402
from omni.kernel.events import TurnCause  # noqa: E402
from omni.kernel.runs.fold import fold_run  # noqa: E402

from ai_server.omni_bridge import quiescence as q  # noqa: E402
from tests.omni_fixtures import Journal, Story  # noqa: E402


def _state(s: Story, since=None):
    folds = [fold_run(s.run1), fold_run(s.run2)] if s.run2 else [fold_run(s.run1)]
    turns = {sid: q.turn_state(envs) for sid, envs in s.sessions.items()}
    return q.state_of(folds, turns, since)


# -- turn_state --


def test_turn_state_of_an_empty_stream_and_a_running_turn():
    assert q.turn_state([]) == q.TurnState(0, 0)
    assert not q.TurnState(0, 0).over
    j = Journal()
    j.add("s", TurnStarted("t1", TurnCause.USER))
    assert q.turn_state(j.streams["s"]) == q.TurnState(1, 0)
    assert not q.turn_state(j.streams["s"]).over


def test_turn_state_follows_the_latest_turn():
    j = Journal()
    j.add("s", TurnStarted("t1", TurnCause.USER))
    j.add("s", TurnEnded("t1", "end_turn", {}))
    first = q.turn_state(j.streams["s"])
    assert first == q.TurnState(1, 2) and first.over
    j.add("s", TurnStarted("t2", TurnCause.USER))
    assert q.turn_state(j.streams["s"]) == q.TurnState(3, 0)
    j.add("s", TurnEnded("t2", "end_turn", {}))
    assert q.turn_state(j.streams["s"]).over


def test_a_session_end_closes_an_open_turn_but_not_a_missing_one():
    j = Journal()
    j.add("s", SessionEnded("gone"))
    assert q.turn_state(j.streams["s"]) == q.TurnState(0, 0)
    j.add("s", TurnStarted("t1", TurnCause.USER))
    j.add("s", SessionEnded("gone"))
    assert q.turn_state(j.streams["s"]).over


# -- state_of --


def test_no_runs_or_nothing_yet_means_keep_reading():
    assert q.state_of([], {}) is None
    s = Story().start()
    assert _state(s) is None


def test_floor_with_ended_turn_is_awaiting_user():
    s = Story().start().intake_asks()
    assert _state(s) == "awaiting_user"


def test_floor_without_turn_information_keeps_reading():
    s = Story().start().intake_asks()
    folds = [fold_run(s.run1), fold_run(s.run2)]
    assert q.state_of(folds, {}) is None


def test_a_running_turn_after_the_person_spoke_is_not_awaiting_user():
    s = Story().start().intake_asks().user_replies("yes")  # turn started, not ended
    assert _state(s) is None
    s.j.add("sess-intake", TurnEnded("t-next", "end_turn", {}))
    assert _state(s) == "awaiting_user"


def test_stale_ended_turn_does_not_count_after_a_submission():
    s = Story().start().intake_asks()
    seen = q.turn_state(s.sessions["sess-intake"]).started_seq
    # We just prompted; the old turn is still the latest one in the snapshot.
    assert _state(s, since={"sess-intake": seen}) is None
    s.user_replies("yes", reply="Okay.")
    assert _state(s, since={"sess-intake": seen}) == "awaiting_user"


def test_unanswered_request_is_awaiting_approval_and_beats_the_floor():
    s = Story().start().intake_asks().ask_permission()
    assert _state(s) == "awaiting_approval"
    s.answer("once")
    assert _state(s) == "awaiting_user"


def test_request_on_the_root_stream_counts_too():
    from omni.kernel.events import Request, RequestKind

    s = Story().start()
    s.j.add("run-1", Request("req-9", RequestKind.APPROVAL, {"prompt": "ok?"}))
    assert _state(s) == "awaiting_approval"


def test_root_end_wins_over_everything():
    s = Story().start().intake_asks().ask_permission().report()
    assert _state(s) == "ended"
    ended = Story().start().intake_asks().report()
    assert _state(ended) == "ended"


def test_a_child_run_that_ended_does_not_hold_the_floor():
    s = Story().start().intake_asks().intake_exits().report(end=False)
    # intake exited: the floor is gone, and nothing else is parked.
    assert _state(s) is None
