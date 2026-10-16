"""Who speaks, and the one walk over a conversation (omni-jev plan J8, section 7.5)."""

import pytest

pytest.importorskip("omni")

from omni.graph.schema import Node  # noqa: E402
from omni.kernel.events import (  # noqa: E402
    CallOutcome,
    NodeEntered,
    NodeExited,
    RunEnded,
    RunStarted,
)

from ai_server.omni_bridge import speakers  # noqa: E402
from ai_server.omni_bridge.models import PendingAsk  # noqa: E402
from tests.omni_fixtures import Journal, Story, library  # noqa: E402

DASH = "—"


def _walk(story: Story):
    return speakers.walk(story.run1, story.child_runs, story.sessions, library())


def _spoken(story: Story, **kw):
    return speakers.spoken(story.run1, story.child_runs, story.sessions, library(), **kw)


# -- J8 --


@pytest.mark.parametrize(
    "node,expected",
    [
        (Node(id="chat", kind="agent", converse=True), True),
        (Node(id="say-report", kind="agent"), True),
        (Node(id="say-act", kind="tool", capability="x"), True),
        (Node(id="inspect", kind="agent"), False),
        (Node(id="recover", kind="tool", capability="x"), False),
        (Node(id="say-front", kind="decide"), False),
        (Node(id="say-ok", kind="human"), False),
        (Node(id="troubleshoot", kind="graph"), False),
        (Node(id="saying", kind="agent"), False),
    ],
)
def test_is_speaker(node, expected):
    assert speakers.is_speaker(node) is expected


def test_error_phrase_trims_and_distinguishes_refusals():
    text = speakers.error_phrase("x" * 500, CallOutcome.ERR)
    assert text == f"I couldn't do that {DASH} " + "x" * 200
    assert speakers.error_phrase("hint", CallOutcome.DENIED) == "Okay, I didn't do that."
    assert speakers.error_phrase("", CallOutcome.TIMEOUT) == "Okay, I didn't do that."


def test_refusal_text():
    assert speakers.refusal_text("once") == "(approved: once)"
    assert speakers.refusal_text("approved") == "(approved: approved)"
    assert speakers.refusal_text("deny") == "(declined)"
    assert speakers.refusal_text("Timeout") == "(declined)"


# -- what Alfred says --


def test_converse_node_speaks_and_internal_agents_stay_silent():
    s = Story().start().intake_asks().user_replies("yes, the zigbee one").intake_exits().inspect()
    assert _spoken(s) == ["Is it the motion sensor by the front door?"]
    texts = [i.text for i in _walk(s)]
    assert not any("SEED" in t or "INTERNAL" in t for t in texts)
    assert not any("get_state" in t for t in texts)  # tool_use blocks are never text


def test_full_troubleshooting_story_in_order():
    s = (
        Story().start().intake_asks()
        .user_replies("yes, the zigbee one", reply="Thanks, looking.").intake_exits()
        .inspect().ask_permission().answer("once").report()
    )
    items = [(i.role, i.text, i.node) for i in _walk(s)]
    assert items == [
        ("user", "the hallway sensor keeps going offline", None),
        ("assistant", "Is it the motion sensor by the front door?", "intake"),
        ("user", "yes, the zigbee one", "intake"),
        ("assistant", "Thanks, looking.", "intake"),
        ("user", "(approved: once)", None),
        ("assistant", "It was the Zigbee integration.", "say-report"),
    ]


def test_since_returns_only_text_after_submission():
    s = Story().start().intake_asks()
    cutoff = {"sess-intake": s.sessions["sess-intake"][-1].seq}
    assert _spoken(s, since=cutoff) == []
    s.user_replies("yes", reply="Great, thanks.")
    assert _spoken(s, since=cutoff) == ["Great, thanks."]
    # A cutoff on another stream does not hide this one.
    assert len(_spoken(s, since={"sess-report": 99})) == 2


def test_handoff_speakers_are_suppressed_but_a_nonterminal_converse_still_speaks():
    s = Story().start().intake_asks().bye(spoken="Sure, switching.")
    # say-bye is terminal and exited new-request: silent. intake is not terminal: it spoke.
    assert _spoken(s) == ["Is it the motion sensor by the front door?"]
    # ... but only the REPLY is silenced (A5b): the walk (transcript, `earlier`) keeps the words,
    # marked, so the next run knows what Alfred said.
    said = {i.text: i.suppressed for i in _walk(s) if i.role == "assistant"}
    assert said == {"Is it the motion sensor by the front door?": False, "Sure, switching.": True}


def test_a_terminal_converse_nodes_earlier_turns_stay_in_the_walk_but_not_the_reply():
    # chat (terminal, converse) talks for two turns, then exits new-request.
    j = Journal()
    lib = library()
    j.add("run-1", RunStarted("run-1", "graph", "front-door", {"message": "hi"}))
    j.add("run-1", NodeEntered("run-1", "chat", "sess-chat"))
    j.message("sess-chat", "user", "SEED")
    j.message("sess-chat", "assistant", "What would you like to do?")
    j.message("sess-chat", "user", "actually, the lamp")
    j.message("sess-chat", "assistant", "Sure.")
    env = j.call("sess-chat", "turn on the lamp", name="emit_outcome")
    j.add("run-1", NodeExited("run-1", "chat", "new-request", f"sess-chat#{env.seq}"))
    j.add("run-1", RunEnded("run-1", "completed"))
    items = speakers.walk(j.streams["run-1"], {}, j.streams, lib)
    assert [(i.role, i.text) for i in items] == [
        ("user", "hi"),
        ("assistant", "What would you like to do?"),
        ("user", "actually, the lamp"),
        ("assistant", "Sure."),
    ]
    assert speakers.spoken(j.streams["run-1"], {}, j.streams, lib) == []


def test_terminal_speaker_without_handoff_is_not_suppressed():
    s = Story().start().intake_asks().intake_exits().report("Done and dusted.")
    assert _spoken(s)[-1] == "Done and dusted."


def test_parked_converse_node_with_no_exit_still_contributes():
    s = Story().start().intake_asks()
    assert [i.node for i in _walk(s) if i.role == "assistant"] == ["intake"]


def test_tool_speaker_says_its_output_and_trims_errors():
    j = Journal()
    j.add("run-1", RunStarted("run-1", "graph", "front-door", {"message": "turn on the lamp"}))
    j.add("run-1", NodeEntered("run-1", "say-act", "sess-act"))
    j.call("sess-act", "Bedroom Lamp is on.")
    j.add("run-1", NodeExited("run-1", "say-act", "ok"))
    j.add("run-1", RunEnded("run-1", "completed"))
    out = speakers.spoken(j.streams["run-1"], {}, j.streams, library())
    assert out == ["Bedroom Lamp is on."]

    j2 = Journal()
    j2.add("run-1", RunStarted("run-1", "graph", "front-door", {"message": "m"}))
    j2.add("run-1", NodeEntered("run-1", "say-act", "sess-act"))
    j2.call("sess-act", "boom " * 100, CallOutcome.ERR)
    j2.add("run-1", NodeExited("run-1", "say-act", "error"))
    (said,) = speakers.spoken(j2.streams["run-1"], {}, j2.streams, library())
    assert said.startswith(f"I couldn't do that {DASH} boom")
    assert len(said) == len(f"I couldn't do that {DASH} ") + 200


def test_unknown_graph_yields_only_user_and_answers():
    j = Journal()
    j.add("run-1", RunStarted("run-1", "graph", "sha256:nope", {"message": "hi"}))
    items = speakers.walk(j.streams["run-1"], {}, {}, library())
    assert [(i.role, i.text) for i in items] == [("user", "hi")]


def test_missing_child_or_session_contributes_nothing():
    s = Story().start().intake_asks()
    assert speakers.spoken(s.run1, {}, {}, library()) == []


def test_nested_inline_subgraph_is_walked_without_error():
    j = Journal()
    inline_ref = library().resolve("mini-trouble").subgraphs["inline"]
    j.add("run-1", RunStarted("run-1", "graph", "mini-trouble", {"message": "m"}))
    j.add("run-1", NodeEntered("run-1", "inline", "run-9"))
    j.add("run-9", RunStarted("run-9", "graph", inline_ref, {}))
    j.add("run-9", NodeEntered("run-9", "deep", "sess-deep"))
    j.message("sess-deep", "assistant", "silent: deep is not a speaker")
    j.add("run-9", NodeExited("run-9", "deep", "done"))
    out = speakers.spoken(j.streams["run-1"], {"run-9": j.streams["run-9"]}, j.streams, library())
    assert out == []


# -- the reply --


def test_compose_reply_joins_with_blank_lines():
    assert speakers.compose_reply(["a", " b "], "awaiting_user", None) == "a\n\nb"
    assert speakers.compose_reply([], "ended", None) == ""
    assert speakers.compose_reply(["", "  "], "working", None) == ""


def test_compose_reply_ask_question_alone_or_appended():
    pending = PendingAsk(
        req_id="r", stream_id="s", kind="permission",
        question="Shall I turn on Bedroom Lamp?", options=["allow", "deny"],
    )
    assert speakers.compose_reply([], "awaiting_approval", pending) == "Shall I turn on Bedroom Lamp?"
    assert (
        speakers.compose_reply(["On it."], "awaiting_approval", pending)
        == "On it.\n\nShall I turn on Bedroom Lamp?"
    )
    # The question is only spoken while the conversation is actually awaiting approval.
    assert speakers.compose_reply(["On it."], "working", pending) == "On it."
