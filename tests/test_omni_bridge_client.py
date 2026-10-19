"""AlfredOmni over a fake client (omni-jev plan A5): routing, handoff, asks, re-adoption.

No daemon and no network: ``tests/omni_fake_kernel.py`` plays the kernel and ``GraphHost`` is
replaced by a host that only stays alive. The graphs are the small ones of
``tests/omni_fixtures.py`` written into a temp repo, so node ids (``say-act``, ``chat``,
``troubleshoot``...) are known.
"""

import asyncio
import json
import sys
import types

import pytest
import pytest_asyncio

pytest.importorskip("omni")

from omni.graph.schema import GraphLibrary  # noqa: E402
from omni.kernel.api import UnavailableError  # noqa: E402
from omni.kernel.events import (  # noqa: E402
    CallOutcome,
    FloorHeld,
    NodeEntered,
    NodeExited,
    Request,
    RequestKind,
    Response,
    RunEnded,
    RunStarted,
    TurnCause,
    TurnEnded,
    TurnStarted,
)

from ai_server.config import Settings  # noqa: E402
from ai_server.omni_bridge import (  # noqa: E402
    AlfredOmni,
    BridgeUnavailable,
    ConversationNotFound,
)
from ai_server.omni_bridge import client as client_mod  # noqa: E402
from tests.omni_fake_kernel import FakeHost, FakeKernel  # noqa: E402
from tests.omni_fixtures import FRONT_DOOR, MINI_TROUBLE  # noqa: E402

pytestmark = pytest.mark.asyncio

APPLY = "mcp__home_assistant__apply"
LAMP = {"entity_id": "light.bedroom_lamp", "entity_name": "Bedroom Lamp"}


# ── scaffolding ──────────────────────────────────────────────────────────────


@pytest.fixture
def repo(tmp_path):
    graphs = tmp_path / ".omni" / "graphs"
    graphs.mkdir(parents=True)
    # sorted order matters: the subgraph must load before the graph that names it
    (graphs / "a-mini-trouble.json").write_text(json.dumps(MINI_TROUBLE), encoding="utf-8")
    (graphs / "front-door.json").write_text(json.dumps(FRONT_DOOR), encoding="utf-8")
    (tmp_path / ".omni" / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def fake_host(monkeypatch):
    FakeHost.instances.clear()
    monkeypatch.setattr(client_mod, "GraphHost", FakeHost)


class Decider:
    """A scripted ``decide``: pops answers; records the questions."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def __call__(self, question, options, state, *, threshold):
        self.calls.append((question, dict(options), dict(state), threshold))
        return self.answers.pop(0)


def make(repo, kernel, tmp_path_factory=None, decide=None, **updates):
    base = {"data_dir": str(repo / "data"), "omni_turn_timeout": 1.0,
            "jev_key_file": str(repo / "no-such-key")}
    base.update(updates)
    settings = Settings().model_copy(update=base)

    async def factory():
        return kernel

    return AlfredOmni(repo, settings, decide=decide or Decider(), client_factory=factory)


@pytest_asyncio.fixture
async def started(repo):
    kernel = FakeKernel()
    bridge = make(repo, kernel)
    await bridge.start()
    yield bridge, kernel
    await bridge.close()


def lib_refs(repo, **updates) -> dict:
    """graph name -> ref as the bridge would load them (``updates``: changed house settings)."""
    from ai_server.omni_bridge import library as library_mod

    _, graphs = library_mod.load_library(repo, Settings().model_copy(update=updates))
    return {name: graph.ref for name, graph in graphs.items()}


def lib_front_ref(repo) -> str:
    return lib_refs(repo)["front-door"]


STALE_REF = "0123abcd" * 8


def record(repo, runs: dict) -> None:
    """Write the bridge's own record of the runs it started (A5b)."""
    path = repo / "data" / "bridge_runs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(runs), encoding="utf-8")


def recorded(repo) -> dict:
    path = repo / "data" / "bridge_runs.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# -- scripted behaviours of "the daemon" --


def device_run(k: FakeKernel, run_id: str) -> None:
    """front -> say-act, parked on the physical-tool permission ask."""
    k.add(run_id, NodeEntered(run_id, "front", ""))
    k.add(run_id, NodeExited(run_id, "front", "device"))
    k.stream("sess-act")
    k.add(run_id, NodeEntered(run_id, "say-act", "sess-act"))
    k.add(run_id, Request("req-1", RequestKind.PERMISSION, {
        "tool": APPLY,
        "args": {"action": "turn-on", **LAMP},
        "options": ["once", "deny"],
    }))


def device_answer(k: FakeKernel, run_id: str, req_id: str, decision: str) -> None:
    k.add(run_id, Response(req_id, "alfred", decision, "native:test"))
    if decision == "once":
        env = k.call("sess-act", "Bedroom Lamp is on.", name=APPLY)
        k.add(run_id, NodeExited(run_id, "say-act", "ok", f"sess-act#{env.seq}"))
    else:
        env = k.call("sess-act", "denied", CallOutcome.DENIED, name=APPLY)
        k.add(run_id, NodeExited(run_id, "say-act", "denied", f"sess-act#{env.seq}"))
    k.add(run_id, RunEnded(run_id, "completed"))


def chat_run(k: FakeKernel, run_id: str, hello: str = "Hello, how can I help?") -> None:
    """front -> chat (converse), floor held after its first turn."""
    k.add(run_id, NodeEntered(run_id, "front", ""))
    k.add(run_id, NodeExited(run_id, "front", "chat"))
    k.stream("sess-chat")
    k.add(run_id, NodeEntered(run_id, "chat", "sess-chat"))
    k.message("sess-chat", "user", "SEED: you are chat")
    k.add("sess-chat", TurnStarted("t-seed", TurnCause.USER))
    k.message("sess-chat", "assistant", hello)
    k.add("sess-chat", TurnEnded("t-seed", "end_turn", {}))
    k.add(run_id, FloorHeld(run_id, "chat", "sess-chat"))


def chat_reply(k: FakeKernel, text: str, reply: str) -> None:
    k.message("sess-chat", "user", text)
    k.add("sess-chat", TurnStarted("t-next", TurnCause.USER))
    k.message("sess-chat", "assistant", reply)
    k.add("sess-chat", TurnEnded("t-next", "end_turn", {}))


# ── the five routing rows of section 7.8 ─────────────────────────────────────


async def test_new_conversation_runs_the_front_door_and_parks_on_the_ask(started):
    bridge, k = started
    k.on_start = lambda run_id, inputs: device_run(k, run_id)
    reply = await bridge.say("turn on the bedroom lamp", None)
    assert reply.state == "awaiting_approval"
    assert reply.answer == "Shall I turn on Bedroom Lamp?"
    assert reply.conversation_id == "run-1" and reply.new_conversation
    assert reply.pending is not None and reply.pending.req_id == "req-1"
    assert reply.pending.options == ["allow", "deny"]
    assert k.started[0]["inputs"] == {
        "message": "turn on the bedroom lamp", "earlier": "", "continues": ""}
    assert k.started[0]["module"] == "client:graph"
    assert FakeHost.instances[0].driven == ["run-1"]  # a host drives the run (J2)


async def test_pending_ask_is_answered_from_the_next_message(started):
    bridge, k = started
    decide = Decider(("allow", 0.98))
    bridge._decide = decide
    k.on_start = lambda run_id, inputs: device_run(k, run_id)
    k.on_respond = lambda stream, req, decision: device_answer(k, stream, req, decision)
    await bridge.say("turn on the bedroom lamp", None)
    reply = await bridge.say("yes please", "run-1")
    # allow is sent as "once", never "allow"/"always" (A4, plan 12)
    assert k.responses == [("run-1", "req-1", "once", None)]
    assert decide.calls[0][3] == 0.75 and decide.calls[0][2]["reply"] == "yes please"
    assert reply.state == "ended" and reply.answer == "Bedroom Lamp is on."
    assert not reply.new_conversation and reply.pending is None


async def test_a_refusal_says_it_did_not_do_it_once(started):
    bridge, k = started
    bridge._decide = Decider(("deny", 0.9))
    k.on_start = lambda run_id, inputs: device_run(k, run_id)
    k.on_respond = lambda stream, req, decision: device_answer(k, stream, req, decision)
    await bridge.say("turn on the bedroom lamp", None)
    reply = await bridge.say("no, turn on the kitchen one instead", "run-1")
    assert k.responses[0][2] == "deny"
    assert reply.state == "ended"
    assert reply.answer == "Okay, I didn't do that."  # step.say and the tool's own phrase, once


async def test_an_unclear_answer_is_asked_again_then_denied(started):
    bridge, k = started
    bridge._decide = Decider((None, 0.4), (None, 0.5))
    k.on_start = lambda run_id, inputs: device_run(k, run_id)
    k.on_respond = lambda stream, req, decision: device_answer(k, stream, req, decision)
    await bridge.say("turn on the bedroom lamp", None)
    again = await bridge.say("hmm maybe", "run-1")
    assert again.state == "awaiting_approval" and again.pending.reasks == 1
    assert again.answer == "Sorry — was that a yes or a no?" and not k.responses
    final = await bridge.say("dunno", "run-1")
    assert [r[2] for r in k.responses] == ["deny"]
    assert final.state == "ended" and "I didn't do it" in final.answer


async def test_a_held_floor_gets_the_message_as_a_prompt(started):
    bridge, k = started
    k.on_start = lambda run_id, inputs: chat_run(k, run_id)
    first = await bridge.say("hi", None)
    assert first.state == "awaiting_user" and first.answer == "Hello, how can I help?"
    k.on_prompt = lambda sid, text: chat_reply(k, text, "Why did the lamp cross the road?")
    second = await bridge.say("tell me a joke", "run-1")
    assert k.prompts == [("sess-chat", "tell me a joke")]
    assert second.state == "awaiting_user"
    assert second.answer == "Why did the lamp cross the road?"  # not the greeting again
    assert second.conversation_id == "run-1" and not second.new_conversation


async def test_a_run_that_is_still_working_is_not_queued(repo):
    k = FakeKernel()
    bridge = make(repo, k, omni_turn_timeout=0.2)
    await bridge.start()
    try:
        k.on_start = lambda run_id, inputs: k.add(run_id, NodeEntered(run_id, "front", ""))
        first = await bridge.say("do something slow", None)
        assert first.state == "working" and first.answer == ""
        second = await bridge.say("hello?", "run-1")
        assert second.state == "working" and second.answer == "I'm still on it."
        assert k.prompts == [] and k.responses == [] and len(k.started) == 1
    finally:
        await bridge.close()


async def test_a_late_answer_is_delivered_on_the_next_call(repo):
    k = FakeKernel()
    bridge = make(repo, k, omni_turn_timeout=0.15)
    await bridge.start()
    try:
        k.on_start = lambda run_id, inputs: k.add(run_id, NodeEntered(run_id, "front", ""))
        assert (await bridge.say("slow one", None)).state == "working"
        chat_run(k, "run-1", hello="Sorry for the wait.")  # arrives after the timeout
        await asyncio.sleep(0.05)
        k.on_prompt = lambda sid, text: chat_reply(k, text, "Okay.")
        reply = await bridge.say("anything?", "run-1")
        assert k.prompts == [("sess-chat", "anything?")]
        assert reply.answer == "Sorry for the wait." + chr(10) * 2 + "Okay." and reply.state == "awaiting_user"
    finally:
        await bridge.close()


async def test_events_that_arrive_later_are_waited_for(started):
    bridge, k = started
    loop = asyncio.get_running_loop()

    def on_start(run_id, inputs):
        k.add(run_id, NodeEntered(run_id, "front", ""))
        loop.call_later(0.1, chat_run, k, run_id, "Took a moment.")

    k.on_start = on_start
    reply = await bridge.say("hi", None)
    assert reply.state == "awaiting_user" and reply.answer == "Took a moment."


async def test_an_unknown_or_ended_id_starts_a_fresh_conversation(started):
    bridge, k = started
    k.on_start = lambda run_id, inputs: device_run(k, run_id)
    k.on_respond = lambda stream, req, decision: device_answer(k, stream, req, decision)
    bridge._decide = Decider(("allow", 0.9))
    unknown = await bridge.say("turn on the lamp", "run-999")
    assert unknown.new_conversation and unknown.conversation_id == "run-1"
    ended = await bridge.say("yes", "run-1")
    assert ended.state == "ended"
    fresh = await bridge.say("turn on the lamp again", "run-1")  # J11: ended -> fresh, no seed
    assert fresh.new_conversation and fresh.conversation_id == "run-2"
    assert k.started[1]["inputs"] == {
        "message": "turn on the lamp again", "earlier": "", "continues": ""}


# ── handoff (J10) and loading an earlier conversation (J11) ──────────────────


def handoff(k: FakeKernel, run_id: str, text: str) -> None:
    env = k.call("sess-chat", text, name="emit_outcome")
    k.add(run_id, NodeExited(run_id, "chat", "new-request", f"sess-chat#{env.seq}"))
    k.add(run_id, RunEnded(run_id, "completed"))


async def test_a_new_request_outcome_hands_off_to_a_new_run(started):
    bridge, k = started

    def on_start(run_id, inputs):
        if run_id == "run-1":
            chat_run(k, run_id, "What would you like to do?")
        else:
            device_run(k, run_id)

    k.on_start = on_start
    await bridge.say("hi", None)
    k.on_prompt = lambda sid, text: (
        chat_reply(k, text, "Sure."), handoff(k, "run-1", "turn on the bedroom lamp"))
    reply = await bridge.say("actually, turn on the bedroom lamp", "run-1")
    assert reply.conversation_id == "run-2" and reply.continued_from == "run-1"
    assert reply.new_conversation and reply.state == "awaiting_approval"
    assert reply.answer.endswith("Shall I turn on Bedroom Lamp?")
    second = k.started[1]["inputs"]
    assert second["message"] == "turn on the bedroom lamp" and second["continues"] == "run-1"
    assert "user: hi" in second["earlier"]
    # A5b: rule 4 silences the reply only; what Alfred said in the old run is still in `earlier`
    assert "assistant: What would you like to do?" in second["earlier"]
    assert "assistant: Sure." in second["earlier"]
    assert "run-1" not in bridge._convs and "run-2" in bridge._convs


async def test_at_most_one_automatic_handoff_per_say(started):
    bridge, k = started

    def on_start(run_id, inputs):
        chat_run(k, run_id)
        if run_id == "run-2":
            handoff(k, run_id, "and another thing")  # a second new-request

    k.on_start = on_start
    await bridge.say("hi", None)
    k.on_prompt = lambda sid, text: handoff(k, "run-1", "something else")
    reply = await bridge.say("different topic", "run-1")
    assert len(k.started) == 2
    assert reply.state == "ended" and reply.conversation_id == "run-2"


async def test_a_silent_new_request_hands_off_without_the_apology(started):
    """A5d: a converse node that hands off says nothing; only the new run's reply is shown."""
    bridge, k = started

    def on_start(run_id, inputs):
        if run_id == "run-1":
            chat_run(k, run_id, "What would you like to do?")
        else:
            device_run(k, run_id)

    def silent_handoff(sid, text):
        # the run ends with outcome new-request (the live journal's shape), nothing spoken
        env = k.call("sess-chat", "turn on the bedroom lamp", name="emit_outcome")
        k.add("run-1", NodeExited("run-1", "chat", "new-request", f"sess-chat#{env.seq}"))
        k.add("run-1", RunEnded("run-1", "new-request"))

    k.on_start = on_start
    await bridge.say("hi", None)
    k.on_prompt = silent_handoff
    reply = await bridge.say("turn on the bedroom lamp", "run-1")
    assert reply.conversation_id == "run-2" and reply.continued_from == "run-1"
    assert "something went wrong" not in reply.answer
    assert reply.answer == "Shall I turn on Bedroom Lamp?"


async def test_a_failed_run_that_says_nothing_still_apologises(started):
    bridge, k = started
    k.on_start = lambda run_id, inputs: chat_run(k, run_id, "What would you like to do?")
    await bridge.say("hi", None)
    k.on_prompt = lambda sid, text: k.add("run-1", RunEnded("run-1", "failed"))
    reply = await bridge.say("turn on the bedroom lamp", "run-1")
    assert reply.state == "ended" and reply.answer == client_mod.WENT_WRONG


def troubleshoot_run(k: FakeKernel, run_id: str, repo) -> None:
    """front -> troubleshoot (child run-2, mini-trouble) -> intake holds the floor."""
    refs = lib_refs(repo)
    child = "run-2"
    k.add(run_id, NodeEntered(run_id, "front", ""))
    k.add(run_id, NodeExited(run_id, "front", "troubleshoot"))
    k.add(run_id, NodeEntered(run_id, "troubleshoot", child))
    k.add(child, RunStarted(child, "client:graph", refs["mini-trouble"], {"report": "x"}))
    k.runs = 2  # the child run id is taken; the next start_run is run-3
    k.stream("sess-intake")
    k.add(child, NodeEntered(child, "intake", "sess-intake"))
    k.message("sess-intake", "user", "SEED: you are intake")
    k.add("sess-intake", TurnStarted("t-seed", TurnCause.USER))
    k.message("sess-intake", "assistant", "Is it the motion sensor by the front door?")
    k.add("sess-intake", TurnEnded("t-seed", "end_turn", {}))
    k.add(child, FloorHeld(child, "intake", "sess-intake"))


def topic_change(k: FakeKernel, text: str, new_request: str) -> None:
    """intake emits new-request, say-bye speaks and emits new-request, both runs end."""
    k.message("sess-intake", "user", text)
    k.add("sess-intake", TurnStarted("t-next", TurnCause.USER))
    env = k.call("sess-intake", new_request, name="emit_outcome")
    k.add("run-2", NodeExited("run-2", "intake", "new-request", f"sess-intake#{env.seq}"))
    k.stream("sess-bye")
    k.add("run-2", NodeEntered("run-2", "say-bye", "sess-bye"))
    k.message("sess-bye", "assistant", "Sure, switching.")
    env2 = k.call("sess-bye", new_request, name="emit_outcome")
    k.add("run-2", NodeExited("run-2", "say-bye", "new-request", f"sess-bye#{env2.seq}"))
    k.add("run-2", RunEnded("run-2", "completed"))
    k.add("run-1", NodeExited("run-1", "troubleshoot", "new-request", f"sess-bye#{env2.seq}"))
    k.add("run-1", RunEnded("run-1", "completed"))


async def test_a_topic_change_seeds_the_new_run_with_both_sides_of_the_old_talk(started, repo):
    """Plan 9.3: `earlier` holds the user's AND Alfred's lines; only the reply stays silent."""
    bridge, k = started

    def on_start(run_id, inputs):
        if run_id == "run-1":
            troubleshoot_run(k, run_id, repo)
        else:
            device_run(k, run_id)

    k.on_start = on_start
    first = await bridge.say("the hallway sensor keeps going offline", None)
    assert first.state == "awaiting_user"
    assert first.answer == "Is it the motion sensor by the front door?"
    k.on_prompt = lambda sid, text: topic_change(
        k, text, "turn on the bedroom lamp")
    reply = await bridge.say("actually, forget that - turn on the bedroom lamp", "run-1")
    assert reply.conversation_id == "run-3" and reply.continued_from == "run-1"
    assert reply.new_conversation and reply.state == "awaiting_approval"
    # say-bye's "Sure, switching." is not spoken: the handoff's first reply is all there is
    assert reply.answer == "Shall I turn on Bedroom Lamp?"
    seed = k.started[1]["inputs"]
    assert seed["message"] == "turn on the bedroom lamp" and seed["continues"] == "run-1"
    assert seed["earlier"].splitlines() == [
        "user: the hallway sensor keeps going offline",
        "assistant: Is it the motion sensor by the front door?",
        "user: actually, forget that - turn on the bedroom lamp",
        "assistant: Sure, switching.",
    ]


def outcome_turn(k: FakeKernel, text: str, spoken: str, output: str, outcome: str):
    """The person speaks; intake says goodbye, calls emit_outcome and its turn ENDS.

    The kernel writes `TurnEnded` first and `NodeExited` only when it next looks at the
    session, so the exit is left to the caller (A5c).
    """
    k.message("sess-intake", "user", text)
    k.add("sess-intake", TurnStarted("t-next", TurnCause.USER))
    k.message("sess-intake", "assistant", spoken)
    env = k.call("sess-intake", output, name="emit_outcome")
    k.add("sess-intake", TurnEnded("t-next", "end_turn", {}))
    return env


def intake_exits_to_bye(k: FakeKernel, env, new_request: str) -> None:
    """What the kernel journals later: intake exits, say-bye speaks, both runs end."""
    k.add("run-2", NodeExited("run-2", "intake", "new-request", f"sess-intake#{env.seq}"))
    k.stream("sess-bye")
    k.add("run-2", NodeEntered("run-2", "say-bye", "sess-bye"))
    k.message("sess-bye", "assistant", "Sure, switching.")
    env2 = k.call("sess-bye", new_request, name="emit_outcome")
    k.add("run-2", NodeExited("run-2", "say-bye", "new-request", f"sess-bye#{env2.seq}"))
    k.add("run-2", RunEnded("run-2", "completed"))
    k.add("run-1", NodeExited("run-1", "troubleshoot", "new-request", f"sess-bye#{env2.seq}"))
    k.add("run-1", RunEnded("run-1", "completed"))


async def test_an_outcome_turn_waits_for_the_exit_and_hands_off_in_the_same_say(started, repo):
    """A5c / A7b live bug: intake's turn ends after emit_outcome; NodeExited lands later."""
    bridge, k = started
    loop = asyncio.get_running_loop()

    def on_start(run_id, inputs):
        if run_id == "run-1":
            troubleshoot_run(k, run_id, repo)
        else:
            device_run(k, run_id)

    def on_prompt(sid, text):
        env = outcome_turn(k, text, "Okay, dropping that.", "turn on the bedroom lamp",
                           "new-request")
        loop.call_later(0.15, intake_exits_to_bye, k, env, "turn on the bedroom lamp")

    k.on_start = on_start
    first = await bridge.say("the hallway sensor keeps going offline", None)
    assert first.state == "awaiting_user"
    k.on_prompt = on_prompt
    reply = await bridge.say("actually, forget that - turn on the bedroom lamp", "run-1")
    # not an early awaiting_user with the goodbye: the handoff ran inside this say()
    assert reply.state == "awaiting_approval"
    assert reply.conversation_id == "run-3" and reply.continued_from == "run-1"
    assert reply.new_conversation
    # intake is not terminal, so it still speaks its last words (A4); say-bye is silent
    assert reply.answer == "Okay, dropping that." + chr(10) * 2 + "Shall I turn on Bedroom Lamp?"
    assert k.started[1]["inputs"]["message"] == "turn on the bedroom lamp"
    assert "run-1" not in bridge._convs and "run-3" in bridge._convs


async def test_a_plain_turn_without_an_outcome_is_awaiting_user_at_once(started, repo):
    bridge, k = started
    loop = asyncio.get_running_loop()
    k.on_start = lambda run_id, inputs: troubleshoot_run(k, run_id, repo)
    await bridge.say("the hallway sensor keeps going offline", None)

    def on_prompt(sid, text):
        k.message("sess-intake", "user", text)
        k.add("sess-intake", TurnStarted("t-next", TurnCause.USER))
        k.message("sess-intake", "assistant", "Is it the zigbee one?")
        k.add("sess-intake", TurnEnded("t-next", "end_turn", {}))  # no emit_outcome
        # if the bridge wrongly kept waiting, this would arrive and change the reply
        loop.call_later(0.3, k.add, "run-2", RunEnded("run-2", "completed"))
        loop.call_later(0.3, k.add, "run-1", RunEnded("run-1", "completed"))

    k.on_prompt = on_prompt
    t0 = loop.time()
    reply = await bridge.say("yes, the hallway one", "run-1")
    assert loop.time() - t0 < 0.25
    assert reply.state == "awaiting_user" and reply.answer == "Is it the zigbee one?"
    assert reply.conversation_id == "run-1" and not reply.new_conversation
    assert len(k.started) == 1


async def test_an_outcome_turn_that_moves_on_waits_for_the_next_park(started, repo):
    """9.2: the answer makes intake=identified and inspect starts; not an early reply."""
    bridge, k = started
    loop = asyncio.get_running_loop()
    k.on_start = lambda run_id, inputs: troubleshoot_run(k, run_id, repo)
    await bridge.say("the hallway sensor keeps going offline", None)

    def finish(env):
        k.add("run-2", NodeExited("run-2", "intake", "identified", f"sess-intake#{env.seq}"))
        k.stream("sess-inspect")
        k.add("run-2", NodeEntered("run-2", "inspect", "sess-inspect"))
        loop.call_later(0.15, report_and_end)

    def report_and_end():
        k.add("run-2", NodeExited("run-2", "inspect", "found"))
        k.stream("sess-report")
        k.add("run-2", NodeEntered("run-2", "say-report", "sess-report"))
        env = k.message("sess-report", "assistant", "It was the Zigbee integration.")
        k.add("run-2", NodeExited("run-2", "say-report", "done", f"sess-report#{env.seq}"))
        k.add("run-2", RunEnded("run-2", "completed"))
        k.add("run-1", NodeExited("run-1", "troubleshoot", "done", f"sess-report#{env.seq}"))
        k.add("run-1", RunEnded("run-1", "completed"))

    def on_prompt(sid, text):
        env = outcome_turn(k, text, "Thanks, I'll take a look.", "hallway_motion offline",
                           "identified")
        loop.call_later(0.1, finish, env)

    k.on_prompt = on_prompt
    reply = await bridge.say("yes, the zigbee one", "run-1")
    assert reply.state == "ended"
    assert reply.answer.endswith("It was the Zigbee integration.")


async def test_an_outcome_turn_that_never_parks_again_times_out_as_working(repo, tmp_path):
    k = FakeKernel()
    bridge = make(repo, k, omni_turn_timeout=0.3)
    await bridge.start()
    try:
        k.on_start = lambda run_id, inputs: troubleshoot_run(k, run_id, repo)
        await bridge.say("the hallway sensor keeps going offline", None)

        def on_prompt(sid, text):
            env = outcome_turn(k, text, "Thanks.", "hallway_motion offline", "identified")
            k.add("run-2", NodeExited("run-2", "intake", "identified", f"sess-intake#{env.seq}"))
            k.stream("sess-inspect")
            k.add("run-2", NodeEntered("run-2", "inspect", "sess-inspect"))  # then silence

        k.on_prompt = on_prompt
        reply = await bridge.say("yes", "run-1")
        assert reply.state == "working" and reply.conversation_id == "run-1"
    finally:
        await bridge.close()


async def test_loading_an_earlier_conversation_that_handed_off_keeps_alfreds_words(
    started, repo
):
    """Plan 9.4 + J11: continue_from seeds `earlier` with the whole transcript; /execute does not."""
    bridge, k = started

    def on_start(run_id, inputs):
        if run_id == "run-1":
            troubleshoot_run(k, run_id, repo)
        elif run_id == "run-3":
            device_run(k, run_id)  # the handed-off request waits on an ask
        else:
            chat_run(k, run_id, "Which sensor?")

    k.on_start = on_start
    await bridge.say("the hallway sensor keeps going offline", None)
    # the topic change hands off to run-3, which is left parked on its ask
    k.on_prompt = lambda sid, text: topic_change(k, text, "what is the weather")
    await bridge.say("never mind, what is the weather", "run-1")
    assert "run-1" not in bridge._convs
    rows = await bridge.list_conversations()
    assert {r.conversation_id: r.state for r in rows}["run-1"] == "ended"

    reply = await bridge.continue_from("run-1", "it's offline again")
    assert reply.continued_from == "run-1" and reply.new_conversation
    seed = k.started[-1]["inputs"]
    assert seed["message"] == "it's offline again" and seed["continues"] == "run-1"
    assert "user: the hallway sensor keeps going offline" in seed["earlier"]
    assert "assistant: Is it the motion sensor by the front door?" in seed["earlier"]
    assert "assistant: Sure, switching." in seed["earlier"]

    # the same words to /execute with the ended id: a fresh conversation, no seed (J11)
    fresh = await bridge.say("it's offline again", "run-1")
    assert fresh.new_conversation and fresh.continued_from is None
    assert k.started[-1]["inputs"]["earlier"] == ""


async def test_continue_from_seeds_the_new_run_with_the_old_transcript(started):
    bridge, k = started
    k.on_start = lambda run_id, inputs: device_run(k, run_id)
    k.on_respond = lambda stream, req, decision: device_answer(k, stream, req, decision)
    bridge._decide = Decider(("allow", 0.9))
    await bridge.say("turn on the bedroom lamp", None)
    await bridge.say("yes", "run-1")
    k.on_start = lambda run_id, inputs: chat_run(k, run_id, "Which lamp again?")
    reply = await bridge.continue_from("run-1", "it's off again")
    assert reply.conversation_id == "run-2" and reply.continued_from == "run-1"
    seed = k.started[1]["inputs"]
    assert seed["message"] == "it's off again" and seed["continues"] == "run-1"
    assert "user: turn on the bedroom lamp" in seed["earlier"]
    assert "assistant: Bedroom Lamp is on." in seed["earlier"]
    with pytest.raises(ConversationNotFound):
        await bridge.continue_from("run-404", "hello")


async def test_earlier_is_bounded_to_the_newest_characters(repo):
    k = FakeKernel()
    bridge = make(repo, k, omni_earlier_chars=40)
    await bridge.start()
    try:
        k.on_start = lambda run_id, inputs: device_run(k, run_id)
        await bridge.say("x" * 100 + " the end", None)
        k.on_start = lambda run_id, inputs: chat_run(k, run_id)
        await bridge.continue_from("run-1", "again")
        earlier = k.started[1]["inputs"]["earlier"]
        assert len(earlier) == 40 and "the end" in earlier
    finally:
        await bridge.close()


# ── direct answers (J13) ─────────────────────────────────────────────────────


async def test_answer_ask_responds_directly(started):
    bridge, k = started
    k.on_start = lambda run_id, inputs: device_run(k, run_id)
    k.on_respond = lambda stream, req, decision: device_answer(k, stream, req, decision)
    await bridge.say("turn on the bedroom lamp", None)
    reply = await bridge.answer_ask("run-1", "req-1", "allow")
    assert k.responses == [("run-1", "req-1", "once", None)]
    assert reply.state == "ended" and reply.answer == "Bedroom Lamp is on."


async def test_answer_ask_rejects_what_is_not_pending(started):
    bridge, k = started
    k.on_start = lambda run_id, inputs: device_run(k, run_id)
    await bridge.say("turn on the bedroom lamp", None)
    with pytest.raises(ValueError):
        await bridge.answer_ask("run-1", "req-OTHER", "allow")
    with pytest.raises(ValueError):
        await bridge.answer_ask("run-1", "req-1", "maybe")
    with pytest.raises(ConversationNotFound):
        await bridge.answer_ask("run-77", "req-1", "allow")
    assert k.responses == []


# ── re-adoption (J18) ────────────────────────────────────────────────────────


async def test_restart_readopts_only_the_runs_it_started(repo):
    k = FakeKernel()
    refs = lib_refs(repo)
    inputs = {"message": "hi", "earlier": "", "continues": ""}
    # run-1: ours, live, parked on a held floor
    k.add("run-1", RunStarted("run-1", "client:graph", refs["front-door"], inputs))
    chat_run(k, "run-1", "Hello again.")
    # run-2: another client's run on a graph we do not know -> NOT ours to cancel (A5b)
    k.add("run-2", RunStarted("run-2", "client:graph", STALE_REF, inputs))
    # run-3: another client running our front-door graph -> NOT ours to adopt
    k.add("run-3", RunStarted("run-3", "client:graph", refs["front-door"], inputs))
    k.add("run-3", NodeEntered("run-3", "front", ""))
    # run-4: a subgraph run -> left to its parent's host
    k.add("run-4", RunStarted("run-4", "client:graph", refs["mini-trouble"], {"report": "x"}))
    # run-5: ours once, finished since -> pruned from the record
    k.add("run-5", RunStarted("run-5", "client:graph", refs["front-door"], inputs))
    k.add("run-5", RunEnded("run-5", "completed"))
    k.runs = 5
    record(repo, {"run-1": "front-door", "run-5": "front-door"})
    bridge = make(repo, k)
    await bridge.start()
    try:
        assert k.cancelled == []
        assert set(bridge._convs) == {"run-1"}
        assert FakeHost.instances[0].driven == ["run-1"]
        assert recorded(repo) == {"run-1": "front-door"}
        # the history was seen before the restart as far as anyone can tell: no repeat
        k.on_prompt = lambda sid, text: chat_reply(k, text, "Back with you.")
        reply = await bridge.say("are you there?", "run-1")
        assert reply.conversation_id == "run-1" and reply.answer == "Back with you."
        assert k.prompts == [("sess-chat", "are you there?")]
    finally:
        await bridge.close()


async def test_without_a_record_nothing_is_adopted_or_cancelled(repo):
    k = FakeKernel()
    refs = lib_refs(repo)
    inputs = {"message": "hi", "earlier": "", "continues": ""}
    k.add("run-1", RunStarted("run-1", "client:graph", refs["front-door"], inputs))
    chat_run(k, "run-1")
    k.add("run-2", RunStarted("run-2", "client:graph", STALE_REF, inputs))
    k.runs = 2
    (repo / "data").mkdir()
    (repo / "data" / "bridge_runs.json").write_text("{not json", encoding="utf-8")
    bridge = make(repo, k)
    await bridge.start()
    try:
        assert k.cancelled == [] and bridge._convs == {}
    finally:
        await bridge.close()


async def test_a_changed_setting_re_drives_our_run_with_the_graph_of_the_same_name(repo):
    """The ref is a content hash that covers the injected constraints (A5, J18)."""
    k = FakeKernel()
    old = lib_refs(repo, omni_ask_timeout=7.0)  # what the run was started with
    assert old["front-door"] != lib_refs(repo)["front-door"]
    inputs = {"message": "hi", "earlier": "", "continues": ""}
    k.add("run-1", RunStarted("run-1", "client:graph", old["front-door"], inputs))
    chat_run(k, "run-1", "Hello again.")
    k.runs = 1
    record(repo, {"run-1": "front-door"})
    bridge = make(repo, k)
    await bridge.start()
    try:
        assert k.cancelled == []
        assert set(bridge._convs) == {"run-1"}
        # the policy can now resolve the run's old ref to the graph of the same name
        assert bridge._library.resolve(old["front-door"]) is bridge._graphs["front-door"]
        assert recorded(repo) == {"run-1": "front-door"}
        k.on_prompt = lambda sid, text: chat_reply(k, text, "Still here.")
        assert (await bridge.say("hello?", "run-1")).answer == "Still here."
    finally:
        await bridge.close()


async def test_a_changed_setting_also_heals_the_subgraph_run(repo):
    k = FakeKernel()
    old = lib_refs(repo, omni_ask_timeout=7.0)
    inputs = {"message": "sensor", "earlier": "", "continues": ""}
    k.add("run-1", RunStarted("run-1", "client:graph", old["front-door"], inputs))
    k.add("run-1", NodeEntered("run-1", "front", ""))
    k.add("run-1", NodeExited("run-1", "front", "troubleshoot"))
    k.add("run-1", NodeEntered("run-1", "troubleshoot", "run-2"))
    k.add("run-2", RunStarted("run-2", "client:graph", old["mini-trouble"], {"report": "x"}))
    k.runs = 2
    record(repo, {"run-1": "front-door"})  # run-2 is the host's own child run: not recorded
    bridge = make(repo, k)
    await bridge.start()
    try:
        assert k.cancelled == [] and set(bridge._convs) == {"run-1"}
        assert bridge._library.resolve(old["mini-trouble"]) is bridge._graphs["mini-trouble"]
        assert FakeHost.instances[0].driven == ["run-1"]  # the child is the host's to adopt
    finally:
        await bridge.close()


async def test_our_run_is_cancelled_when_its_graph_name_is_gone_or_no_longer_fits(repo):
    k = FakeKernel()
    old = lib_refs(repo, omni_ask_timeout=7.0)
    inputs = {"message": "hi", "earlier": "", "continues": ""}
    # run-1: recorded under a name the library no longer has
    k.add("run-1", RunStarted("run-1", "client:graph", STALE_REF, inputs))
    # run-2: same name, but it entered a node the graph has since lost
    k.add("run-2", RunStarted("run-2", "client:graph", old["front-door"], inputs))
    k.add("run-2", NodeEntered("run-2", "old-node", "sess-x"))
    # run-3: a node that was an agent session is now a graph node
    k.add("run-3", RunStarted("run-3", "client:graph", old["front-door"], inputs))
    k.add("run-3", NodeEntered("run-3", "troubleshoot", "sess-y"))
    # run-4: untouched foreign run, as ever
    k.add("run-4", RunStarted("run-4", "client:graph", STALE_REF, inputs))
    k.runs = 4
    record(repo, {"run-1": "retired-graph", "run-2": "front-door", "run-3": "front-door"})
    bridge = make(repo, k)
    await bridge.start()
    try:
        assert sorted(k.cancelled) == [
            ("run-1", "graph changed"), ("run-2", "graph changed"), ("run-3", "graph changed")]
        assert bridge._convs == {} and recorded(repo) == {}
        assert old["front-door"] not in bridge._library  # nothing was aliased for the misfits
    finally:
        await bridge.close()


# ── the record of runs this bridge starts (A5b) ──────────────────────────────


async def test_started_runs_are_recorded_and_dropped_when_they_end(started, repo):
    bridge, k = started
    k.on_start = lambda run_id, inputs: device_run(k, run_id)
    k.on_respond = lambda stream, req, decision: device_answer(k, stream, req, decision)
    bridge._decide = Decider(("allow", 0.9))
    await bridge.say("turn on the bedroom lamp", None)
    assert recorded(repo) == {"run-1": "front-door"}
    await bridge.say("yes", "run-1")  # ended
    assert recorded(repo) == {}


async def test_a_handoff_moves_the_record_to_the_new_run_and_forget_drops_it(started, repo):
    bridge, k = started
    k.on_start = lambda run_id, inputs: (
        chat_run(k, run_id) if run_id == "run-1" else device_run(k, run_id))
    await bridge.say("hi", None)
    assert recorded(repo) == {"run-1": "front-door"}
    k.on_prompt = lambda sid, text: handoff(k, "run-1", "turn on the bedroom lamp")
    await bridge.say("different topic", "run-1")
    assert recorded(repo) == {"run-2": "front-door"}
    assert await bridge.forget("run-2") is True
    assert recorded(repo) == {}


async def test_restart_resurfaces_a_pending_ask(repo):
    k = FakeKernel()
    ref = lib_front_ref(repo)
    k.add("run-1", RunStarted("run-1", "client:graph", ref,
                              {"message": "lamp", "earlier": "", "continues": ""}))
    device_run(k, "run-1")
    k.runs = 1
    record(repo, {"run-1": "front-door"})
    bridge = make(repo, k, decide=Decider(("allow", 0.9)))
    k.on_respond = lambda stream, req, decision: device_answer(k, stream, req, decision)
    await bridge.start()
    try:
        reply = await bridge.say("yes", "run-1")
        assert k.responses[0][:3] == ("run-1", "req-1", "once")
        assert reply.state == "ended"
    finally:
        await bridge.close()


# ── listing, transcript, forgetting (7.9) ────────────────────────────────────


async def test_list_transcript_and_forget(started, repo):
    bridge, k = started
    k.on_start = lambda run_id, inputs: chat_run(k, run_id, "Hello there.")
    await bridge.say("hi alfred", None)
    rows = await bridge.list_conversations()
    assert [r.conversation_id for r in rows] == ["run-1"]
    assert rows[0].title == "hi alfred" and rows[0].state == "awaiting_user"
    tr = await bridge.transcript("run-1")
    assert [(e.role, e.text) for e in tr.entries] == [("user", "hi alfred"),
                                                     ("assistant", "Hello there.")]
    with pytest.raises(ConversationNotFound):
        await bridge.transcript("run-404")
    with pytest.raises(ConversationNotFound):
        await bridge.transcript("sess-chat")

    assert await bridge.forget("run-1") is True
    assert k.cancelled == [("run-1", "forgotten")]
    assert await bridge.list_conversations() == []
    assert [r.conversation_id for r in await bridge.list_conversations(include_hidden=True)] == [
        "run-1"]
    hidden = json.loads((repo / "data" / "hidden_conversations.json").read_text())
    assert hidden == ["run-1"]
    with pytest.raises(ConversationNotFound):
        await bridge.transcript("run-1")
    assert await bridge.forget("run-404") is False
    fresh = await bridge.say("hi again", "run-1")  # a hidden id starts a new conversation
    assert fresh.new_conversation and fresh.conversation_id == "run-2"


async def test_ended_conversations_are_listed_and_have_transcripts(started):
    bridge, k = started
    k.on_start = lambda run_id, inputs: device_run(k, run_id)
    k.on_respond = lambda stream, req, decision: device_answer(k, stream, req, decision)
    bridge._decide = Decider(("allow", 0.9))
    await bridge.say("turn on the bedroom lamp", None)
    await bridge.say("yes", "run-1")
    assert "run-1" not in bridge._convs  # ended runs are not kept
    (row,) = await bridge.list_conversations()
    assert row.state == "ended" and row.title == "turn on the bedroom lamp"
    tr = await bridge.transcript("run-1")
    assert tr.entries[-1].text == "Bedroom Lamp is on."


# ── failure shapes ───────────────────────────────────────────────────────────


async def test_methods_need_a_started_bridge(repo):
    bridge = make(repo, FakeKernel())
    with pytest.raises(BridgeUnavailable):
        await bridge.say("hi", None)
    assert await bridge.healthy() is False


async def test_an_unreachable_daemon_is_bridge_unavailable(repo):
    async def refuse():
        raise UnavailableError("no daemon")

    settings = Settings().model_copy(update={"data_dir": str(repo / "data")})
    bridge = AlfredOmni(repo, settings, client_factory=refuse)
    with pytest.raises(BridgeUnavailable):
        await bridge.start()


async def test_a_lost_connection_surfaces_as_bridge_unavailable(started):
    bridge, k = started
    k.start_error = UnavailableError("the connection is closed")
    with pytest.raises(BridgeUnavailable):
        await bridge.say("hi", None)
    assert await bridge.healthy() is True
    k.list_error = UnavailableError("gone")
    assert await bridge.healthy() is False


async def test_close_closes_the_client_and_hosts(repo):
    k = FakeKernel()
    bridge = make(repo, k)
    await bridge.start()
    k.on_start = lambda run_id, inputs: chat_run(k, run_id)
    await bridge.say("hi", None)
    await bridge.close()
    assert k.closed and bridge._convs == {}
    assert await bridge.healthy() is False


async def test_start_writes_the_local_mcp_config(started, repo):
    assert (repo / ".omni" / "mcp.local.json").exists()


# ── J16's secondary path ─────────────────────────────────────────────────────


async def test_prestart_gives_the_key_to_the_daemon_spawn_only(repo, monkeypatch, caplog):
    secret = "sk-test-NOT-A-REAL-KEY"
    keyfile = repo / "jev.env"
    keyfile.write_text(secret + "\n", encoding="utf-8")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    seen = {}

    async def ensure_daemon(*, spawn, **kw):
        seen["child"] = spawn(["omnid"], env=None)

    def spawn_detached(argv, env=None):
        seen["argv"], seen["env"] = list(argv), env
        return "child"

    stub = types.ModuleType("omni.cli.connect")
    stub.ensure_daemon = ensure_daemon
    stub.spawn_detached = spawn_detached
    stub.screen_protocol = lambda record: record
    monkeypatch.setitem(sys.modules, "omni.cli", types.ModuleType("omni.cli"))
    monkeypatch.setitem(sys.modules, "omni.cli.connect", stub)
    monkeypatch.setattr(client_mod, "read_discovery", lambda: None)
    bridge = make(repo, FakeKernel(), jev_key_file=str(keyfile))
    with caplog.at_level("DEBUG"):
        await bridge._prestart_daemon()
    assert seen["env"]["TYPESAFE_API_KEY"] == secret and seen["child"] == "child"
    assert "TYPESAFE_API_KEY" not in __import__("os").environ  # this process is untouched
    assert secret not in caplog.text


async def test_prestart_does_nothing_when_the_key_is_already_in_the_environment(
    repo, monkeypatch
):
    monkeypatch.setenv("TYPESAFE_API_KEY", "already-set")
    stub = types.ModuleType("omni.cli.connect")
    stub.ensure_daemon = lambda **kw: pytest.fail("must not spawn")
    monkeypatch.setitem(sys.modules, "omni.cli", types.ModuleType("omni.cli"))
    monkeypatch.setitem(sys.modules, "omni.cli.connect", stub)
    await make(repo, FakeKernel())._prestart_daemon()


async def test_without_the_cli_extra_a_running_daemon_is_joined(repo, monkeypatch):
    k = FakeKernel()
    monkeypatch.setitem(sys.modules, "omni.cli.connect", None)  # ImportError on import
    monkeypatch.setitem(sys.modules, "omni.cli", None)

    async def join():
        return k

    monkeypatch.setattr(client_mod, "_join_running_daemon", join)
    settings = Settings().model_copy(update={"data_dir": str(repo / "data"),
                                             "jev_key_file": str(repo / "none")})
    bridge = AlfredOmni(repo, settings)
    assert await bridge._connect() is k
