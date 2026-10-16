"""Transcript, the `earlier` seed, the handoff text and the listing row (plan section 7.7, J10-J12)."""

import pytest

pytest.importorskip("omni")

from omni.kernel.runs.fold import fold_run  # noqa: E402

from ai_server.omni_bridge import quiescence, transcript  # noqa: E402
from ai_server.omni_bridge.models import TranscriptEntry  # noqa: E402
from tests.omni_fixtures import Story, library  # noqa: E402


def _build(s: Story):
    return transcript.build(s.run1, s.child_runs, s.sessions, library())


def test_build_covers_user_speech_and_answers_in_order():
    s = (
        Story().start().intake_asks()
        .user_replies("yes, the zigbee one", reply="Thanks, looking.").intake_exits()
        .inspect().ask_permission().answer("once").report()
    )
    entries = _build(s)
    assert [(e.role, e.text, e.node) for e in entries] == [
        ("user", "the hallway sensor keeps going offline", None),
        ("assistant", "Is it the motion sensor by the front door?", "intake"),
        ("user", "yes, the zigbee one", None),
        ("assistant", "Thanks, looking.", "intake"),
        ("user", "(approved: once)", None),
        ("assistant", "It was the Zigbee integration.", "say-report"),
    ]
    assert all(e.at and e.at.endswith("+00:00") for e in entries)


def test_build_renders_a_refusal_as_declined():
    s = Story().start().intake_asks().ask_permission().answer("deny")
    assert _build(s)[-1].text == "(declined)"


def test_build_is_deterministic_after_a_restart():
    s = Story().start().intake_asks().intake_exits().report()
    assert _build(s) == _build(s)


def test_earlier_text_format_and_bound():
    entries = [
        TranscriptEntry(role="user", text="hello"),
        TranscriptEntry(role="assistant", text="hi there", node="chat"),
    ]
    assert transcript.earlier_text(entries, 6000) == "user: hello\nassistant: hi there"
    assert transcript.earlier_text([], 6000) == ""
    full = "user: hello\nassistant: hi there"
    assert transcript.earlier_text(entries, 8) == full[-8:] == "hi there"
    assert transcript.earlier_text(entries, 0) == ""
    assert len(transcript.earlier_text(entries * 500, 100)) == 100
    assert transcript.earlier_text(entries, 6000).endswith("hi there")  # newest kept


def test_ref_text_resolves_calls_and_blocks():
    s = Story().start().intake_asks().intake_exits(output="the words")
    ref = s.run2[-1].event.output_ref
    assert transcript.ref_text(ref, s.sessions) == "the words"
    assistant_block = next(
        e for e in s.sessions["sess-intake"] if type(e.event).__name__ == "BlockEnded" and
        e.event.content.startswith("Is it")
    )
    assert transcript.ref_text(f"sess-intake#{assistant_block.seq}", s.sessions).startswith("Is it")
    assert transcript.ref_text("sess-intake#1", s.sessions) is None  # not a text-bearing event
    assert transcript.ref_text("nope#1", s.sessions) is None
    assert transcript.ref_text("garbage", s.sessions) is None
    assert transcript.ref_text(None, s.sessions) is None


def test_handoff_text_from_an_ended_run():
    s = Story().start().intake_asks().bye(output="turn off the kitchen light")
    fold1 = fold_run(s.run1)
    assert transcript.handoff_text(fold1, s.sessions) == "turn off the kitchen light"
    # The same words come out of the child run, whose last node is say-bye.
    assert transcript.handoff_text(fold_run(s.run2), s.sessions) == "turn off the kitchen light"


def test_handoff_text_is_none_without_a_handoff():
    done = Story().start().intake_asks().intake_exits().report()
    assert transcript.handoff_text(fold_run(done.run1), done.sessions) is None
    running = Story().start().intake_asks()
    assert transcript.handoff_text(fold_run(running.run1), running.sessions) is None
    # Handed off, but the session carrying the words is not attached: no guess.
    s = Story().start().intake_asks().bye()
    assert transcript.handoff_text(fold_run(s.run1), {}) is None


def test_a_handoff_runs_transcript_keeps_what_alfred_said_last():
    """A5b: handoff suppression is for the reply only; `earlier` must hold Alfred's words."""
    s = Story().start().intake_asks().bye(spoken="Sure, switching.")
    entries = _build(s)
    assert [(e.role, e.text) for e in entries] == [
        ("user", "the hallway sensor keeps going offline"),
        ("assistant", "Is it the motion sensor by the front door?"),
        ("assistant", "Sure, switching."),
    ]
    earlier = transcript.earlier_text(entries, 6000)
    assert "assistant: Is it the motion sensor by the front door?" in earlier
    assert earlier.endswith("assistant: Sure, switching.")


def test_summarize_running_and_ended():
    s = Story("a" * 100).start().intake_asks()
    fold = fold_run(s.run1)
    summary = transcript.summarize(fold)
    assert summary.conversation_id == "run-1"
    assert summary.title == "a" * 60
    assert summary.started_at < summary.last_active or summary.started_at == summary.last_active
    assert summary.state in ("working", "awaiting_user")
    assert summary.continued_from is None

    ended = Story().start().intake_asks().intake_exits().report()
    assert transcript.summarize(fold_run(ended.run1)).state == "ended"


def test_summarize_reports_continuation_and_awaiting_states():
    s = Story().start().intake_asks()
    # fold of the CHILD run holds the floor
    child = fold_run(s.run2)
    assert child.floor is not None
    assert transcript.summarize(child).state == "awaiting_user"  # no turns given: listing rule
    turns = {"sess-intake": quiescence.turn_state(s.sessions["sess-intake"])}
    assert transcript.summarize(child, turns).state == "awaiting_user"
    # With turns given and the latest turn still running, it is working.
    s.user_replies("yes")
    turns = {"sess-intake": quiescence.turn_state(s.sessions["sess-intake"])}
    assert transcript.summarize(fold_run(s.run2), turns).state == "working"

    pending = Story().start().intake_asks().ask_permission()
    assert transcript.summarize(fold_run(pending.run2)).state == "awaiting_approval"


def test_summarize_continued_from_and_blank_message():
    from omni.kernel.events import RunStarted
    from tests.omni_fixtures import Journal

    j = Journal()
    j.add("run-4", RunStarted("run-4", "graph", "front-door",
                              {"message": "it's offline again", "earlier": "x", "continues": "run-1"}))
    summary = transcript.summarize(fold_run(j.streams["run-4"]))
    assert summary.continued_from == "run-1" and summary.title == "it's offline again"
    j2 = Journal()
    j2.add("run-5", RunStarted("run-5", "graph", "front-door", {}))
    assert transcript.summarize(fold_run(j2.streams["run-5"])).title == ""


def test_iso():
    assert transcript.iso(None) is None
    assert transcript.iso(0) == "1970-01-01T00:00:00+00:00"
