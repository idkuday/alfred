"""
HTTP API served by the Omni bridge (omni-jev plan section 8, task A6).

A FAKE ``AlfredOmni`` implementing the section 7.2 API is injected into ``ai_server.main`` (the
module-level ``alfred_omni`` global, like the other globals), so no daemon, no network and
no ``omni`` import is needed. Also checks that ``alfred_backend="core"`` still serves the old
path and that the lifespan builds / closes the bridge only for the ``omni`` backend.
"""
from __future__ import annotations

import base64
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import ai_server.main as main
from ai_server.config import settings
from ai_server.omni_bridge.models import (
    ConversationReply,
    ConversationSummary,
    PendingAsk,
    Transcript,
    TranscriptEntry,
)

PENDING = PendingAsk(
    req_id="req-1",
    stream_id="run-abc",
    kind="permission",
    question="Turn off the garage door lock?",
    options=["allow", "deny"],
)


class ConversationNotFound(LookupError):
    """Stand-in for the bridge's error (matched by type, not by import)."""


class BridgeUnavailable(RuntimeError):
    """Stand-in for the bridge's error (matched by name)."""


class AskNotPending(ValueError):
    """What ``answer_ask`` raises when the ask is not the pending one."""


def _summary(cid: str = "run-1", state: str = "awaiting_user", **kw: Any) -> ConversationSummary:
    return ConversationSummary(
        conversation_id=cid,
        title=kw.pop("title", "turn on the lights"),
        started_at="2026-10-01T10:00:00Z",
        last_active="2026-10-01T10:05:00Z",
        state=state,
        **kw,
    )


class FakeOmni:
    """Implements section 7.2; records calls; raises whatever the test queues."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.reply = ConversationReply(conversation_id="run-1", answer="Done.", state="awaiting_user")
        self.error: Optional[BaseException] = None
        self.is_healthy = True
        self.conversations = [_summary("run-2"), _summary("run-1", continued_from="run-0")]
        self.forgot = True

    def _maybe_raise(self) -> None:
        if self.error is not None:
            raise self.error

    async def start(self) -> None:
        self.calls.append(("start",))

    async def close(self) -> None:
        self.calls.append(("close",))

    async def healthy(self) -> bool:
        self.calls.append(("healthy",))
        self._maybe_raise()
        return self.is_healthy

    async def say(self, text: str, conversation_id: Optional[str]) -> ConversationReply:
        self.calls.append(("say", text, conversation_id))
        self._maybe_raise()
        return self.reply

    async def continue_from(self, conversation_id: str, text: str) -> ConversationReply:
        self.calls.append(("continue_from", conversation_id, text))
        self._maybe_raise()
        return self.reply

    async def answer_ask(self, conversation_id: str, req_id: str, decision: str) -> ConversationReply:
        self.calls.append(("answer_ask", conversation_id, req_id, decision))
        self._maybe_raise()
        return self.reply

    async def list_conversations(self, *, include_hidden: bool = False) -> list[ConversationSummary]:
        self.calls.append(("list_conversations", include_hidden))
        self._maybe_raise()
        return self.conversations

    async def transcript(self, conversation_id: str) -> Transcript:
        self.calls.append(("transcript", conversation_id))
        self._maybe_raise()
        return Transcript(
            conversation=_summary(conversation_id),
            entries=[
                TranscriptEntry(role="user", text="hi", at="2026-10-01T10:00:00Z"),
                TranscriptEntry(role="assistant", text="Hello.", node="converse", at="2026-10-01T10:00:02Z"),
            ],
        )

    async def forget(self, conversation_id: str) -> bool:
        self.calls.append(("forget", conversation_id))
        self._maybe_raise()
        return self.forgot


@pytest.fixture
def fake(monkeypatch) -> FakeOmni:
    """A fake bridge injected into main, with the omni backend selected."""
    omni = FakeOmni()
    monkeypatch.setattr(settings, "alfred_backend", "omni")
    monkeypatch.setattr(main, "alfred_omni", omni)
    return omni


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# POST /execute
# ---------------------------------------------------------------------------

class TestExecute:
    @pytest.mark.asyncio
    async def test_shape_and_passthrough(self, fake, client):
        resp = await client.post("/execute", json={"user_input": "lights on", "session_id": "run-1"})
        assert resp.status_code == 200
        assert resp.json() == {
            "intent": "conversation",
            "answer": "Done.",
            "message": "Done.",
            "session_id": "run-1",
            "conversation_state": "awaiting_user",
            "pending": None,
            "new_conversation": False,
            "continued_from": None,
        }
        assert fake.calls == [("say", "lights on", "run-1")]

    @pytest.mark.asyncio
    async def test_no_session_id_passes_none(self, fake, client):
        await client.post("/execute", json={"user_input": "hi"})
        assert fake.calls == [("say", "hi", None)]

    @pytest.mark.asyncio
    async def test_new_conversation_gets_the_new_id(self, fake, client):
        fake.reply = ConversationReply(
            conversation_id="run-9", answer="Fresh start.", state="working",
            new_conversation=True, continued_from="run-1",
        )
        data = (await client.post("/execute", json={"user_input": "x", "session_id": "run-1"})).json()
        assert data["session_id"] == "run-9"
        assert data["new_conversation"] is True
        assert data["continued_from"] == "run-1"
        assert data["conversation_state"] == "working"

    @pytest.mark.asyncio
    async def test_pending_ask_is_serialised(self, fake, client):
        fake.reply = ConversationReply(
            conversation_id="run-1", answer=PENDING.question, state="awaiting_approval", pending=PENDING,
        )
        data = (await client.post("/execute", json={"user_input": "lock it"})).json()
        assert data["conversation_state"] == "awaiting_approval"
        assert data["pending"]["req_id"] == "req-1"
        assert data["pending"]["options"] == ["allow", "deny"]
        assert data["answer"] == PENDING.question

    @pytest.mark.asyncio
    async def test_placeholder_id_is_passed_through(self, fake, client):
        await client.post("/execute", json={"user_input": "hi", "session_id": "pending-123"})
        assert fake.calls == [("say", "hi", "pending-123")]

    @pytest.mark.asyncio
    async def test_missing_user_input_is_422(self, fake, client):
        assert (await client.post("/execute", json={})).status_code == 422

    @pytest.mark.asyncio
    async def test_not_found_is_404(self, fake, client):
        fake.error = ConversationNotFound("no such conversation")
        resp = await client.post("/execute", json={"user_input": "hi", "session_id": "run-x"})
        assert resp.status_code == 404
        assert "no such conversation" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_bridge_unavailable_is_503(self, fake, client):
        fake.error = BridgeUnavailable("daemon down")
        resp = await client.post("/execute", json={"user_input": "hi"})
        assert resp.status_code == 503
        assert "daemon down" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_no_bridge_at_all_is_503(self, monkeypatch, client):
        monkeypatch.setattr(settings, "alfred_backend", "omni")
        monkeypatch.setattr(main, "alfred_omni", None)
        assert (await client.post("/execute", json={"user_input": "hi"})).status_code == 503

    @pytest.mark.asyncio
    async def test_unexpected_runtime_error_is_not_masked_as_503(self, fake, client):
        fake.error = RuntimeError("a bug")
        async with AsyncClient(
            transport=ASGITransport(app=main.app, raise_app_exceptions=False), base_url="http://test"
        ) as c:
            assert (await c.post("/execute", json={"user_input": "hi"})).status_code == 500

    @pytest.mark.asyncio
    async def test_core_is_not_consulted(self, fake, client, monkeypatch):
        core = MagicMock()
        core.process = AsyncMock(side_effect=AssertionError("AlfredCore must not run"))
        monkeypatch.setattr(main, "alfred_core", core)
        assert (await client.post("/execute", json={"user_input": "hi"})).status_code == 200


# ---------------------------------------------------------------------------
# voice_mode
# ---------------------------------------------------------------------------

class TestVoiceMode:
    @pytest.mark.asyncio
    async def test_audio_is_synthesised_from_the_answer(self, fake, client, monkeypatch):
        synth = MagicMock()
        synth.synthesize = AsyncMock(return_value=b"RIFFfake")
        monkeypatch.setattr(main, "synthesizer", synth)
        data = (await client.post("/execute", json={"user_input": "hi", "voice_mode": True})).json()
        assert base64.b64decode(data["audio_base64"]) == b"RIFFfake"
        synth.synthesize.assert_awaited_once_with("Done.")

    @pytest.mark.asyncio
    async def test_no_audio_without_voice_mode(self, fake, client, monkeypatch):
        synth = MagicMock()
        synth.synthesize = AsyncMock(return_value=b"x")
        monkeypatch.setattr(main, "synthesizer", synth)
        data = (await client.post("/execute", json={"user_input": "hi"})).json()
        assert "audio_base64" not in data
        synth.synthesize.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_synthesizer_still_succeeds(self, fake, client, monkeypatch):
        monkeypatch.setattr(main, "synthesizer", None)
        resp = await client.post("/execute", json={"user_input": "hi", "voice_mode": True})
        assert resp.status_code == 200
        assert "audio_base64" not in resp.json()

    @pytest.mark.asyncio
    async def test_synthesis_failure_still_succeeds(self, fake, client, monkeypatch):
        synth = MagicMock()
        synth.synthesize = AsyncMock(side_effect=RuntimeError("piper died"))
        monkeypatch.setattr(main, "synthesizer", synth)
        resp = await client.post("/execute", json={"user_input": "hi", "voice_mode": True})
        assert resp.status_code == 200
        assert "audio_base64" not in resp.json()

    @pytest.mark.asyncio
    async def test_empty_answer_is_not_synthesised(self, fake, client, monkeypatch):
        fake.reply = ConversationReply(conversation_id="run-1", answer="", state="working")
        synth = MagicMock()
        synth.synthesize = AsyncMock(return_value=b"x")
        monkeypatch.setattr(main, "synthesizer", synth)
        data = (await client.post("/execute", json={"user_input": "hi", "voice_mode": True})).json()
        assert "audio_base64" not in data
        synth.synthesize.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_continue_honours_voice_mode(self, fake, client, monkeypatch):
        synth = MagicMock()
        synth.synthesize = AsyncMock(return_value=b"wav")
        monkeypatch.setattr(main, "synthesizer", synth)
        data = (
            await client.post("/sessions/run-1/continue", json={"user_input": "and?", "voice_mode": True})
        ).json()
        assert base64.b64decode(data["audio_base64"]) == b"wav"


# ---------------------------------------------------------------------------
# /sessions
# ---------------------------------------------------------------------------

class TestSessions:
    @pytest.mark.asyncio
    async def test_list(self, fake, client):
        data = (await client.get("/sessions")).json()
        assert data["count"] == 2
        first, second = data["sessions"]
        assert first["session_id"] == first["conversation_id"] == "run-2"
        assert second["continued_from"] == "run-0"
        assert second["title"] == "turn on the lights"
        assert second["state"] == "awaiting_user"
        assert second["created_at"] == second["started_at"]
        assert fake.calls == [("list_conversations", False)]

    @pytest.mark.asyncio
    async def test_list_empty(self, fake, client):
        fake.conversations = []
        assert (await client.get("/sessions")).json() == {"count": 0, "sessions": []}

    @pytest.mark.asyncio
    async def test_create_returns_a_placeholder_without_touching_the_bridge(self, fake, client):
        a = (await client.post("/sessions")).json()["session_id"]
        b = (await client.post("/sessions")).json()["session_id"]
        assert a.startswith("pending-") and b.startswith("pending-") and a != b
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_get_transcript(self, fake, client):
        resp = await client.get("/sessions/run-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session"]["session_id"] == "run-1"
        msgs = data["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[1]["text"] == "Hello." and msgs[1]["node"] == "converse"
        # the UI's names, additive
        assert msgs[1]["content"] == "Hello." and msgs[1]["timestamp"] == "2026-10-01T10:00:02Z"
        assert fake.calls == [("transcript", "run-1")]

    @pytest.mark.asyncio
    async def test_get_unknown_is_404(self, fake, client):
        fake.error = ConversationNotFound("run-x")
        assert (await client.get("/sessions/run-x")).status_code == 404

    @pytest.mark.asyncio
    async def test_delete(self, fake, client):
        resp = await client.delete("/sessions/run-1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        assert fake.calls == [("forget", "run-1")]

    @pytest.mark.asyncio
    async def test_delete_false_is_404(self, fake, client):
        fake.forgot = False
        assert (await client.delete("/sessions/run-x")).status_code == 404

    @pytest.mark.asyncio
    async def test_delete_not_found_raised_is_404(self, fake, client):
        fake.error = ConversationNotFound("run-x")
        assert (await client.delete("/sessions/run-x")).status_code == 404

    @pytest.mark.asyncio
    async def test_unavailable_is_503_on_every_session_route(self, fake, client):
        fake.error = BridgeUnavailable("down")
        assert (await client.get("/sessions")).status_code == 503
        assert (await client.get("/sessions/run-1")).status_code == 503
        assert (await client.delete("/sessions/run-1")).status_code == 503
        assert (await client.post("/sessions/run-1/continue", json={"user_input": "x"})).status_code == 503
        assert (
            await client.post("/sessions/run-1/asks/req-1", json={"decision": "allow"})
        ).status_code == 503

    @pytest.mark.asyncio
    async def test_no_bridge_is_503(self, monkeypatch, client):
        monkeypatch.setattr(settings, "alfred_backend", "omni")
        monkeypatch.setattr(main, "alfred_omni", None)
        assert (await client.get("/sessions")).status_code == 503
        assert (await client.post("/sessions")).status_code == 503


# ---------------------------------------------------------------------------
# /sessions/{id}/continue and /asks/{req_id}
# ---------------------------------------------------------------------------

class TestContinue:
    @pytest.mark.asyncio
    async def test_continue(self, fake, client):
        fake.reply = ConversationReply(
            conversation_id="run-7", answer="Where were we.", state="awaiting_user",
            new_conversation=True, continued_from="run-1",
        )
        resp = await client.post("/sessions/run-1/continue", json={"user_input": "carry on"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "run-7"
        assert data["continued_from"] == "run-1"
        assert data["new_conversation"] is True
        assert data["message"] == data["answer"] == "Where were we."
        assert fake.calls == [("continue_from", "run-1", "carry on")]

    @pytest.mark.asyncio
    async def test_unknown_is_404(self, fake, client):
        fake.error = ConversationNotFound("run-x")
        assert (await client.post("/sessions/run-x/continue", json={"user_input": "x"})).status_code == 404

    @pytest.mark.asyncio
    async def test_body_is_strict(self, fake, client):
        assert (await client.post("/sessions/run-1/continue", json={"user_input": "x", "nope": 1})).status_code == 422
        assert (await client.post("/sessions/run-1/continue", json={})).status_code == 422


class TestAsks:
    @pytest.mark.asyncio
    async def test_answer(self, fake, client):
        fake.reply = ConversationReply(conversation_id="run-1", answer="Locked.", state="working")
        resp = await client.post("/sessions/run-1/asks/req-1", json={"decision": "allow"})
        assert resp.status_code == 200
        assert resp.json()["answer"] == "Locked."
        assert resp.json()["session_id"] == "run-1"
        assert fake.calls == [("answer_ask", "run-1", "req-1", "allow")]

    @pytest.mark.asyncio
    async def test_not_pending_is_409(self, fake, client):
        fake.error = AskNotPending("req-9 is not the pending ask")
        resp = await client.post("/sessions/run-1/asks/req-9", json={"decision": "allow"})
        assert resp.status_code == 409
        assert "req-9" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_unknown_conversation_is_404(self, fake, client):
        fake.error = ConversationNotFound("run-x")
        assert (await client.post("/sessions/run-x/asks/req-1", json={"decision": "deny"})).status_code == 404

    @pytest.mark.asyncio
    async def test_body_is_strict(self, fake, client):
        assert (await client.post("/sessions/run-1/asks/req-1", json={})).status_code == 422
        assert (
            await client.post("/sessions/run-1/asks/req-1", json={"decision": "allow", "x": 1})
        ).status_code == 422


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class TestHealth:
    @pytest.fixture(autouse=True)
    def _ha(self, monkeypatch):
        ha = MagicMock()
        ha.health_check = AsyncMock(return_value=True)
        monkeypatch.setattr(main, "ha_integration", ha)

    @pytest.mark.asyncio
    async def test_connected(self, fake, client):
        data = (await client.get("/health")).json()
        assert data["omni"] == "connected"
        assert data["alfred_backend"] == "omni"
        assert data["status"] == "healthy"
        assert data["home_assistant"] == "connected"

    @pytest.mark.asyncio
    async def test_daemon_not_answering(self, fake, client):
        fake.is_healthy = False
        data = (await client.get("/health")).json()
        assert data["omni"] == "unavailable"
        assert data["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_healthy_raising_is_unavailable(self, fake, client):
        fake.error = BridgeUnavailable("down")
        assert (await client.get("/health")).json()["omni"] == "unavailable"

    @pytest.mark.asyncio
    async def test_no_bridge(self, monkeypatch, client):
        monkeypatch.setattr(settings, "alfred_backend", "omni")
        monkeypatch.setattr(main, "alfred_omni", None)
        assert (await client.get("/health")).json()["omni"] == "unavailable"

    @pytest.mark.asyncio
    async def test_core_backend_reports_core(self, client, monkeypatch):
        monkeypatch.setattr(main, "alfred_core", object())
        data = (await client.get("/health")).json()
        assert data["alfred_backend"] == "core"
        assert data["alfred_core"] == "ready"
        assert "omni" not in data


# ---------------------------------------------------------------------------
# backend switch: core keeps the old path
# ---------------------------------------------------------------------------

class TestCoreBackendStillServes:
    @pytest.mark.asyncio
    async def test_execute_uses_alfred_core_not_the_bridge(self, client, monkeypatch):
        omni = FakeOmni()
        monkeypatch.setattr(settings, "alfred_backend", "core")
        monkeypatch.setattr(main, "alfred_omni", omni)
        core = MagicMock()
        core.process = AsyncMock(return_value="plain answer")
        monkeypatch.setattr(main, "alfred_core", core)
        monkeypatch.setattr(main, "session_store", None)
        monkeypatch.setattr(main, "context_provider", None)
        data = (await client.post("/execute", json={"user_input": "hello"})).json()
        assert data["answer"] == "plain answer"
        assert "conversation_state" not in data
        assert omni.calls == []

    @pytest.mark.asyncio
    async def test_session_routes_use_the_store(self, client, monkeypatch):
        omni = FakeOmni()
        monkeypatch.setattr(settings, "alfred_backend", "core")
        monkeypatch.setattr(main, "alfred_omni", omni)
        store = MagicMock()
        store.create_session.return_value = "sess-1"
        monkeypatch.setattr(main, "session_store", store)
        assert (await client.post("/sessions")).json() == {"session_id": "sess-1"}
        assert omni.calls == []

    @pytest.mark.asyncio
    async def test_omni_only_routes_are_404(self, client, monkeypatch):
        monkeypatch.setattr(settings, "alfred_backend", "core")
        assert (await client.post("/sessions/s/continue", json={"user_input": "x"})).status_code == 404
        assert (await client.post("/sessions/s/asks/r", json={"decision": "allow"})).status_code == 404


# ---------------------------------------------------------------------------
# lifespan
# ---------------------------------------------------------------------------

class _LifespanFake(FakeOmni):
    instances: list["_LifespanFake"] = []
    fail_start = False

    def __init__(self, repo_root, cfg) -> None:
        super().__init__()
        self.repo_root, self.cfg = repo_root, cfg
        _LifespanFake.instances.append(self)

    async def start(self) -> None:
        await super().start()
        if _LifespanFake.fail_start:
            raise BridgeUnavailable("no daemon")


@pytest.fixture
def lifespan_env(monkeypatch):
    """Everything heavy in the lifespan replaced; AlfredOmni replaced by a recording fake."""
    import ai_server.omni_bridge as bridge_pkg

    _LifespanFake.instances = []
    _LifespanFake.fail_start = False
    monkeypatch.setattr(bridge_pkg, "AlfredOmni", _LifespanFake, raising=False)

    ha = MagicMock()
    ha.health_check = AsyncMock(return_value=False)
    ha._close_session = AsyncMock()
    monkeypatch.setattr(main, "HomeAssistantIntegration", lambda cfg: ha)
    monkeypatch.setattr(main, "Transcriber", lambda **kw: MagicMock())
    monkeypatch.setattr(main, "AlfredCore", MagicMock(side_effect=AssertionError("core must not start")))
    monkeypatch.setattr(main, "SessionStore", MagicMock(side_effect=AssertionError("no store")))
    monkeypatch.setattr(settings, "tts_enabled", False)
    monkeypatch.setattr(settings, "auto_load_plugins", False)
    closed = MagicMock()
    monkeypatch.setattr(main.decisions, "close", closed)
    # the lifespan assigns these module globals; have monkeypatch restore them afterwards
    for name in ("alfred_omni", "alfred_core", "ha_integration", "intent_processor",
                 "transcriber", "synthesizer", "session_store", "context_provider"):
        monkeypatch.setattr(main, name, None)
    return closed


class TestLifespan:
    @pytest.mark.asyncio
    async def test_omni_backend_builds_starts_and_closes_the_bridge(self, lifespan_env, monkeypatch):
        monkeypatch.setattr(settings, "alfred_backend", "omni")
        async with main.lifespan(main.app):
            (omni,) = _LifespanFake.instances
            assert omni.repo_root == main.REPO_ROOT
            assert omni.cfg is settings
            assert main.alfred_omni is omni
            assert omni.calls == [("start",)]
        assert omni.calls == [("start",), ("close",)]
        lifespan_env.assert_called_once_with()  # decisions.close()

    @pytest.mark.asyncio
    async def test_start_failure_keeps_serving_with_the_instance(self, lifespan_env, monkeypatch):
        monkeypatch.setattr(settings, "alfred_backend", "omni")
        _LifespanFake.fail_start = True
        async with main.lifespan(main.app):
            assert main.alfred_omni is _LifespanFake.instances[0]
        assert ("close",) in _LifespanFake.instances[0].calls

    @pytest.mark.asyncio
    async def test_missing_bridge_class_keeps_serving(self, lifespan_env, monkeypatch):
        import sys

        import ai_server.omni_bridge as bridge_pkg

        monkeypatch.setattr(settings, "alfred_backend", "omni")
        # Simulate a deployment where the bridge cannot be imported (omni missing): the package
        # resolves AlfredOmni lazily from ``.client``, so make that import raise ImportError.
        monkeypatch.delattr(bridge_pkg, "AlfredOmni", raising=False)
        monkeypatch.setitem(sys.modules, "ai_server.omni_bridge.client", None)
        with pytest.raises(ImportError):
            from ai_server.omni_bridge import AlfredOmni  # noqa: F401 - the premise holds
        async with main.lifespan(main.app):
            assert main.alfred_omni is None

    @pytest.mark.asyncio
    async def test_core_backend_does_not_build_the_bridge(self, lifespan_env, monkeypatch):
        monkeypatch.setattr(settings, "alfred_backend", "core")
        monkeypatch.setattr(main, "AlfredCore", MagicMock(return_value=object()))
        monkeypatch.setattr(main, "SessionStore", MagicMock())
        monkeypatch.setattr(main, "MessageHistoryProvider", MagicMock())
        async with main.lifespan(main.app):
            assert _LifespanFake.instances == []
            assert main.alfred_omni is None
            assert main.alfred_core is not None
