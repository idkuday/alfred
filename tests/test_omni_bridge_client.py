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


def lib_front_ref(repo) -> str:
    from ai_server.omni_bridge import library as library_mod

    _, graphs = library_mod.load_library(repo, Settings().model_copy(update={}))
    return graphs["front-door"].ref


# -- scripted behaviours of "the daemon" --


def device_run(k: FakeKernel, run_id: str) -> None:
    """front -> say-act, parked on the physical-tool permission ask."""
    k.add(run_id, NodeEntered(run_id, "front", ""))
    k.add(run_id, NodeExited(run_id, "front", "device"))
    k.stream("sess-act")
    k.add(run_id, NodeEntered(run_id, "say-act", "sess-act"))
    k.add(run_id, Request("req-1", RequestKind.PERMISSION, {
        "tool": APPLY,
        "args": {"action": "turn-on", "target": json.dumps(LAMP)},
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
    # A4 rule 4 suppresses a terminal speaker that exits new-request, earlier turns included
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


async def test_restart_readopts_live_runs_and_cancels_unknown_graphs(repo):
    k = FakeKernel()
    ref = lib_front_ref(repo)
    inputs = {"message": "hi", "earlier": "", "continues": ""}
    # a live conversation parked on a held floor
    k.add("run-1", RunStarted("run-1", "client:graph", ref, inputs))
    chat_run(k, "run-1", "Hello again.")
    # a conversation whose graph was edited since
    k.add("run-2", RunStarted("run-2", "client:graph", "0123abcd" * 8, inputs))
    # a subgraph's child run: left to its parent's host
    child_ref = GraphLibrary()  # noqa: F841 - just to show intent; the ref is resolved below
    from ai_server.omni_bridge import library as library_mod

    lib, _ = library_mod.load_library(repo, Settings().model_copy(update={}))
    k.add("run-3", RunStarted("run-3", "client:graph", lib.resolve("mini-trouble").ref,
                              {"report": "x"}))
    k.runs = 3
    bridge = make(repo, k)
    await bridge.start()
    try:
        assert k.cancelled == [("run-2", "graph changed")]
        assert set(bridge._convs) == {"run-1"}
        assert FakeHost.instances[0].driven == ["run-1"]
        # the history was seen before the restart as far as anyone can tell: no repeat
        k.on_prompt = lambda sid, text: chat_reply(k, text, "Back with you.")
        reply = await bridge.say("are you there?", "run-1")
        assert reply.conversation_id == "run-1" and reply.answer == "Back with you."
        assert k.prompts == [("sess-chat", "are you there?")]
    finally:
        await bridge.close()


async def test_restart_resurfaces_a_pending_ask(repo):
    k = FakeKernel()
    ref = lib_front_ref(repo)
    k.add("run-1", RunStarted("run-1", "client:graph", ref,
                              {"message": "lamp", "earlier": "", "continues": ""}))
    device_run(k, "run-1")
    k.runs = 1
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
