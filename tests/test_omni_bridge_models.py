"""Data shapes (plan section 7.3) and the hidden-conversations file (section 7.9). No `omni` needed."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_server import omni_bridge
from ai_server.omni_bridge import models


def test_package_exports_the_shapes_without_importing_omni():
    assert omni_bridge.ConversationReply is models.ConversationReply
    assert omni_bridge.ConversationState is models.ConversationState


def test_reply_defaults_and_strictness():
    reply = models.ConversationReply(conversation_id="run-1", answer="hi", state="awaiting_user")
    assert reply.pending is None and reply.new_conversation is False and reply.continued_from is None
    with pytest.raises(ValidationError):
        models.ConversationReply(conversation_id="r", answer="", state="nonsense")
    with pytest.raises(ValidationError):
        models.ConversationReply(conversation_id="r", answer="", state="ended", surprise=1)


def test_pending_ask_round_trips():
    ask = models.PendingAsk(
        req_id="r", stream_id="s", kind="permission", question="Shall I?", options=["allow", "deny"]
    )
    assert ask.reasks == 0 and ask.respond_with == {} and ask.fallback is None
    again = models.ConversationReply.model_validate(
        models.ConversationReply(
            conversation_id="run-1", answer="Shall I?", state="awaiting_approval", pending=ask
        ).model_dump()
    )
    assert again.pending == ask
    with pytest.raises(ValidationError):
        models.PendingAsk(req_id="r", stream_id="s", kind="other", question="q")


def test_transcript_shapes():
    summary = models.ConversationSummary(
        conversation_id="run-1", title="t", started_at="2026-01-01T00:00:00+00:00",
        last_active="2026-01-01T00:00:01+00:00", state="ended",
    )
    entry = models.TranscriptEntry(role="assistant", text="hi", node="chat")
    assert models.Transcript(conversation=summary, entries=[entry]).entries[0].at is None
    with pytest.raises(ValidationError):
        models.TranscriptEntry(role="system", text="x")


def test_hidden_path_is_a_join():
    assert models.hidden_path("data") == Path("data") / "hidden_conversations.json"


def test_parse_hidden_is_lenient():
    assert models.parse_hidden("") == frozenset()
    assert models.parse_hidden("  \n") == frozenset()
    assert models.parse_hidden("{oops") == frozenset()
    assert models.parse_hidden('{"a": 1}') == frozenset()
    assert models.parse_hidden('["run-1", 3, "run-2", null]') == {"run-1", "run-2"}


def test_render_hidden_is_a_sorted_deduplicated_json_list():
    text = models.render_hidden(["run-2", "run-1", "run-2"])
    assert json.loads(text) == ["run-1", "run-2"]
    assert models.parse_hidden(text) == {"run-1", "run-2"}
    assert json.loads(models.render_hidden([])) == []


def test_with_hidden_is_idempotent():
    once = models.with_hidden({"run-1"}, "run-2")
    assert once == {"run-1", "run-2"}
    assert models.with_hidden(once, "run-2") == once
