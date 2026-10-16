"""Rendering asks and interpreting the reply (plan J13, J14, section 7.6). No network: a fake `decide`."""

import json

import pytest

pytest.importorskip("omni")

from omni.graph.schema import load_graph  # noqa: E402
from omni.kernel.events import Request, RequestKind  # noqa: E402

from ai_server.omni_bridge import asks  # noqa: E402
from ai_server.omni_bridge.models import PendingAsk  # noqa: E402
from tests.omni_fixtures import FRONT_DOOR  # noqa: E402

DASH = "—"


def _perm(tool, args, **extra):
    payload = {"tool": tool, "args": args, "grade": "physical", "options": ["once", "deny"], **extra}
    return Request("req-1", RequestKind.PERMISSION, payload)


def _apply(action="turn-on", name="Bedroom Lamp"):
    target = json.dumps({"entity_id": "light.bedroom_lamp", "entity_name": name})
    return _perm("mcp__home_assistant__apply", {"action": action, "target": target, "request": "x"})


class FakeDecide:
    """Records calls; returns a scripted `(answer, confidence)`."""

    def __init__(self, answer, confidence=0.9):
        self.answer, self.confidence, self.calls = answer, confidence, []

    async def __call__(self, question, options, state, *, threshold):
        self.calls.append((question, dict(options), dict(state), threshold))
        return self.answer, self.confidence


# -- render: permission --


@pytest.mark.parametrize(
    "action,expected",
    [
        ("turn-on", "Shall I turn on Bedroom Lamp?"),
        ("turn-off", "Shall I turn off Bedroom Lamp?"),
        ("toggle", "Shall I toggle Bedroom Lamp?"),
    ],
)
def test_render_home_assistant_apply(action, expected):
    pending = asks.render(_apply(action), "run-1")
    assert pending.question == expected
    assert pending.kind == "permission" and pending.options == ["allow", "deny"]
    assert pending.req_id == "req-1" and pending.stream_id == "run-1" and pending.reasks == 0


def test_render_apply_accepts_a_target_object_and_falls_back_to_entity_id():
    req = _perm("mcp__home_assistant__apply",
                {"action": "turn-on", "target": {"entity_id": "light.x"}})
    assert asks.render(req, "run-1").question == "Shall I turn on light.x?"


def test_render_apply_with_a_bad_target_falls_back_to_generic():
    req = _perm("mcp__home_assistant__apply", {"action": "turn-on", "target": "not json"})
    assert asks.render(req, "run-1").question == "Alfred wants to use apply. Allow it?"


def test_render_other_tool_with_entity_name():
    req = _perm("mcp__home_assistant__reload_integration", {"entity_name": "Hallway Motion"})
    assert asks.render(req, "run-2").question == "Shall I run reload_integration on Hallway Motion?"


def test_render_generic_tool():
    assert asks.render(_perm("bash", {"command": "ls"}), "run-1").question == (
        "Alfred wants to use bash. Allow it?"
    )
    assert asks.render(_perm("mcp__srv__do_it", {}), "run-1").question == (
        "Alfred wants to use do_it. Allow it?"
    )


def test_permission_decision_words_map_allow_to_once_never_always():
    pending = asks.render(_apply(), "run-1")
    assert asks.decision_word(pending, "allow") == "once"
    assert asks.decision_word(pending, "deny") == "deny"
    non_physical = Request("r", RequestKind.PERMISSION,
                           {"tool": "bash", "args": {}, "options": ["once", "always", "deny"]})
    assert asks.decision_word(asks.render(non_physical, "s"), "allow") == "once"
    bare = Request("r", RequestKind.PERMISSION, {"tool": "bash", "args": {}})
    assert asks.decision_word(asks.render(bare, "s"), "allow") == "once"


def test_sandbox_ask_uses_allow_deny_words():
    req = Request("r", RequestKind.SANDBOX, {"path": "/etc", "options": ["allow", "deny"]})
    pending = asks.render(req, "s")
    assert pending.kind == "permission" and "/etc" in pending.question
    assert asks.decision_word(pending, "allow") == "allow"


def test_secret_ask_is_never_taken_over_chat():
    pending = asks.render(Request("r", RequestKind.SECRET, {}), "s")
    assert pending.options == ["deny"]
    assert asks.decision_word(pending, "deny") == "deny"


def test_short_tool_name():
    assert asks.short_tool_name("mcp__home_assistant__apply") == "apply"
    assert asks.short_tool_name("bash") == "bash"
    assert asks.short_tool_name("mcp__x") == "mcp__x"


# -- render: approval / elicit --


def _lookup(stream_id, request):
    graph = load_graph(FRONT_DOOR)
    return graph.node("ask-ok")


def test_render_approval_with_show_and_options():
    req = Request("req-2", RequestKind.APPROVAL, {
        "prompt": "Apply this plan?",
        "show": {"outputs.plan": "x" * 600, "outputs.n": 3},
        "options": ["approved", "rejected"],
        "on_cancel": "timeout",
    })
    pending = asks.render(req, "run-1", _lookup)
    lines = pending.question.split("\n")
    assert lines[0] == "Apply this plan?"
    assert lines[1].startswith("outputs.plan: " + "x" * 10) and lines[1].endswith("…")
    assert len(lines[1]) == len("outputs.plan: ") + 400
    assert lines[2] == "outputs.n: 3"
    assert lines[3] == "(approved / rejected)"
    assert pending.kind == "approval" and pending.options == ["approved", "rejected"]
    assert pending.descriptions == {"approved": "go ahead", "rejected": "do not"}
    assert pending.fallback == "rejected"  # no on_timeout: the last outcome


def test_render_approval_uses_on_timeout_as_the_fallback():
    graph = load_graph({
        "name": "g", "entry": "h", "terminal": ["h"],
        "nodes": [{"id": "h", "kind": "human", "request": {}, "outcomes": ["yes", "no", "later"],
                   "on_timeout": "later"}],
        "edges": [],
    })
    req = Request("r", RequestKind.APPROVAL, {"prompt": "go?", "options": ["yes", "no", "later"]})
    pending = asks.render(req, "s", lambda stream_id, request: graph.node("h"))
    assert pending.fallback == "later"


def test_render_approval_without_a_node_or_payload_options():
    req = Request("r", RequestKind.APPROVAL, {"prompt": "go?", "options": [{"id": "a", "label": "A"}, "b"]})
    pending = asks.render(req, "s")
    assert pending.options == ["a", "b"] and pending.fallback == "b" and pending.descriptions == {}
    # No options in the payload: the node's declared outcomes (never the implicit two).
    bare = asks.render(Request("r", RequestKind.APPROVAL, {"prompt": "ok?"}), "s", _lookup)
    assert bare.options == ["approved", "rejected"]
    assert asks.render(Request("r", RequestKind.APPROVAL, {}), "s").question == "Do you approve?"


def test_render_elicit():
    req = Request("r", RequestKind.ELICIT, {"message": "What is the Wi-Fi password hint?", "schema": {}})
    pending = asks.render(req, "s")
    assert pending.kind == "elicit" and pending.options == []
    assert pending.question == "What is the Wi-Fi password hint?"
    assert asks.response_data(pending, " hint ") == {"text": "hint"}
    assert asks.response_data(asks.render(_apply(), "s"), "yes") == {}


# -- interpret --


@pytest.mark.asyncio
async def test_interpret_permission_calls_decide_with_the_planned_question_and_threshold():
    pending = asks.render(_apply(), "run-1")
    decide = FakeDecide("allow")
    assert await asks.interpret(pending, "yes please", decide) == "allow"
    question, options, state, threshold = decide.calls[0]
    assert question == "Does this reply give permission?"
    assert options == {
        "allow": "yes, go ahead, do it, sure, please",
        "deny": "no, stop, don't, cancel, or a different request instead",
    }
    assert state == {"question": "Shall I turn on Bedroom Lamp?", "reply": "yes please"}
    assert threshold == 0.75


@pytest.mark.asyncio
async def test_interpret_approval_passes_outcomes_with_their_descriptions():
    req = Request("r", RequestKind.APPROVAL, {"prompt": "ok?", "options": ["approved", "rejected"]})
    pending = asks.render(req, "s", _lookup)
    decide = FakeDecide("approved")
    assert await asks.interpret(pending, "do it", decide) == "approved"
    assert decide.calls[0][1] == {"approved": "go ahead", "rejected": "do not"}
    # Without descriptions the outcome names describe themselves.
    plain = asks.render(req, "s")
    decide2 = FakeDecide("rejected")
    assert await asks.interpret(plain, "no", decide2) == "rejected"
    assert decide2.calls[0][1] == {"approved": "approved", "rejected": "rejected"}


@pytest.mark.asyncio
async def test_interpret_returns_none_when_unsure_or_outside_the_options():
    pending = asks.render(_apply(), "run-1")
    assert await asks.interpret(pending, "hmm", FakeDecide(None, 0.4)) is None
    assert await asks.interpret(pending, "hmm", FakeDecide("maybe")) is None


@pytest.mark.asyncio
async def test_interpret_swallows_a_decide_that_raises():
    async def boom(*a, **k):
        raise RuntimeError("down")

    assert await asks.interpret(asks.render(_apply(), "run-1"), "yes", boom) is None


@pytest.mark.asyncio
async def test_interpret_skips_the_model_when_there_is_nothing_to_choose():
    decide = FakeDecide("deny")
    secret = asks.render(Request("r", RequestKind.SECRET, {}), "s")
    assert await asks.interpret(secret, "whatever", decide) == "deny"
    elicit = asks.render(Request("r", RequestKind.ELICIT, {"message": "m"}), "s")
    assert await asks.interpret(elicit, "a hint", decide) == "accept"
    assert await asks.interpret(elicit, "   ", decide) is None
    no_options = PendingAsk(req_id="r", stream_id="s", kind="approval", question="q", options=[])
    assert await asks.interpret(no_options, "x", decide) is None
    assert decide.calls == []


# -- step: answer, re-ask once, then settle --


def test_step_clear_allow_and_deny():
    pending = asks.render(_apply(), "run-1")
    allow = asks.step(pending, "allow")
    assert (allow.option, allow.word, allow.say, allow.pending) == ("allow", "once", "", None)
    deny = asks.step(pending, "deny")
    assert (deny.option, deny.word) == ("deny", "deny")
    assert deny.say == "Okay, I didn't do that."  # J14: it did not act


def test_step_permission_reasks_once_then_denies():
    pending = asks.render(_apply(), "run-1")
    first = asks.step(pending, None)
    assert first.option is None and first.word is None
    assert first.say == f"Sorry {DASH} was that a yes or a no?"
    assert first.pending.reasks == 1 and pending.reasks == 0  # the original is untouched
    second = asks.step(first.pending, None)
    assert (second.option, second.word) == ("deny", "deny")
    assert "didn't do it" in second.say and second.pending is None


def test_step_approval_reasks_with_options_then_uses_the_fallback():
    req = Request("r", RequestKind.APPROVAL, {"prompt": "ok?", "options": ["approved", "rejected"]})
    pending = asks.render(req, "s", _lookup)
    first = asks.step(pending, None)
    assert first.say == f"Sorry {DASH} which did you mean? (approved / rejected)"
    final = asks.step(first.pending, None)
    assert final.option == "rejected" and final.word == "rejected"
    assert '"rejected"' in final.say


def test_step_approval_answer_uses_the_outcome_name_as_the_word():
    req = Request("r", RequestKind.APPROVAL, {"prompt": "ok?", "options": ["approved", "rejected"]})
    result = asks.step(asks.render(req, "s"), "approved")
    assert result.word == "approved" and result.say == ""


def test_step_elicit_carries_the_text_as_data_and_declines_after_a_reask():
    pending = asks.render(Request("r", RequestKind.ELICIT, {"message": "Hint?"}), "s")
    done = asks.step(pending, "accept", "  the hint ")
    assert (done.option, done.word, dict(done.data)) == ("accept", "accept", {"text": "the hint"})
    again = asks.step(pending, None)
    assert again.say == "Hint?" and again.pending.reasks == 1
    assert asks.step(again.pending, None).word == "decline"
