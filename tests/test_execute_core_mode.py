"""
Unit tests for /execute endpoint in AlfredCore mode (ALFRED_MODE=core).

These tests verify that when ALFRED_MODE is set to "core", the endpoint:
- Calls alfred_core.process() instead of alfred_router.route()
- Correctly handles all three CoreDecision types:
    - str (plain text) → {"intent": "conversation", "answer": ...}
    - CallToolDecision → dispatches to integration, returns CommandResponse
    - ProposeNewToolDecision → {"intent": "propose_new_tool", ...}
- Returns 503 when alfred_core is unavailable
- Returns 400 on ValueError from Core
- Saves messages to session and includes session_id in all responses
- Includes audio_base64 when voice_mode=True

All tests mock alfred_core and settings.alfred_mode — no Ollama required.
"""
import json
import pytest
import base64
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport

from ai_server.main import app
from ai_server.alfred_router.schemas import CallToolDecision, ProposeNewToolDecision
from ai_server.models import CommandResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_call_tool_decision(**params):
    """Build a CallToolDecision for tests."""
    return CallToolDecision(
        intent="call_tool",
        tool="home_assistant",
        parameters={"action": "turn_on", "target": "light", "room": "bedroom", **params},
    )


def _make_propose_decision():
    """Build a ProposeNewToolDecision for tests."""
    return ProposeNewToolDecision(
        intent="propose_new_tool",
        name="garage_control",
        description="Control the garage door opener",
    )


def _make_command_response(message="Light turned on"):
    """Build a CommandResponse mock."""
    return CommandResponse(
        status="success",
        action="turn_on",
        target="light",
        message=message,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_core():
    """Return a mock AlfredCore with a coroutine process() method."""
    core = MagicMock()
    core.process = AsyncMock()
    return core


# ---------------------------------------------------------------------------
# Core mode — plain text (conversational) response
# ---------------------------------------------------------------------------

class TestCoreModeConversation:
    """Tests for plain text (conversation) responses from Core."""

    @pytest.mark.asyncio
    async def test_core_conversation_response(self, mock_core):
        """Plain text Core output is returned as intent=conversation."""
        mock_core.process.return_value = "I'm Alfred, your local AI assistant!"

        with patch("ai_server.main.alfred_core", mock_core), \
             patch("ai_server.main.settings") as mock_settings:
            mock_settings.alfred_mode = "core"

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/execute",
                    json={"user_input": "Who are you?"},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["intent"] == "conversation"
        assert data["answer"] == "I'm Alfred, your local AI assistant!"

    @pytest.mark.asyncio
    async def test_core_conversation_includes_session_id(self, mock_core):
        """Conversation response always includes a session_id."""
        mock_core.process.return_value = "Hello!"

        with patch("ai_server.main.alfred_core", mock_core), \
             patch("ai_server.main.settings") as mock_settings:
            mock_settings.alfred_mode = "core"

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/execute",
                    json={"user_input": "Hi"},
                )

        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        # If session_store is not available, session_id will be None
        # (we're not mocking it here); just check the key exists

    @pytest.mark.asyncio
    async def test_core_conversation_voice_mode_with_audio(self, mock_core):
        """Conversation response in voice_mode includes audio_base64."""
        mock_core.process.return_value = "Here is your answer."
        fake_wav = b"RIFF" + b"\x00" * 40 + b"WAVE"

        with patch("ai_server.main.alfred_core", mock_core), \
             patch("ai_server.main.settings") as mock_settings, \
             patch("ai_server.main.synthesizer") as mock_synth:
            mock_settings.alfred_mode = "core"
            mock_synth.synthesize = AsyncMock(return_value=fake_wav)

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/execute",
                    json={"user_input": "Tell me something", "voice_mode": True},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["intent"] == "conversation"
        assert "audio_base64" in data
        assert base64.b64decode(data["audio_base64"]) == fake_wav
        mock_synth.synthesize.assert_called_once_with("Here is your answer.")

    @pytest.mark.asyncio
    async def test_core_conversation_no_audio_when_voice_mode_false(self, mock_core):
        """No audio_base64 when voice_mode is False."""
        mock_core.process.return_value = "Hi there."

        with patch("ai_server.main.alfred_core", mock_core), \
             patch("ai_server.main.settings") as mock_settings:
            mock_settings.alfred_mode = "core"

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/execute",
                    json={"user_input": "Hello", "voice_mode": False},
                )

        assert response.status_code == 200
        data = response.json()
        assert "audio_base64" not in data


# ---------------------------------------------------------------------------
# Core mode — CallToolDecision
# ---------------------------------------------------------------------------

class TestCoreModeCallTool:
    """Tests for CallToolDecision path in Core mode."""

    @pytest.mark.asyncio
    async def test_core_call_tool_dispatches_to_integration(self, mock_core):
        """CallToolDecision from Core dispatches to the right integration."""
        decision = _make_call_tool_decision()
        mock_core.process.return_value = decision
        mock_result = _make_command_response("Bedroom light turned on")

        with patch("ai_server.main.alfred_core", mock_core), \
             patch("ai_server.main.settings") as mock_settings, \
             patch("ai_server.main._handle_call_tool", new_callable=AsyncMock, return_value=mock_result):
            mock_settings.alfred_mode = "core"

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/execute",
                    json={"user_input": "Turn on the bedroom light"},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["message"] == "Bedroom light turned on"

    @pytest.mark.asyncio
    async def test_core_call_tool_voice_mode_uses_result_message(self, mock_core):
        """In voice_mode, TTS is called with the CommandResponse message."""
        decision = _make_call_tool_decision()
        mock_core.process.return_value = decision
        mock_result = _make_command_response("Lights are on")
        fake_wav = b"RIFF....WAVE"

        with patch("ai_server.main.alfred_core", mock_core), \
             patch("ai_server.main.settings") as mock_settings, \
             patch("ai_server.main._handle_call_tool", new_callable=AsyncMock, return_value=mock_result), \
             patch("ai_server.main.synthesizer") as mock_synth:
            mock_settings.alfred_mode = "core"
            mock_synth.synthesize = AsyncMock(return_value=fake_wav)

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/execute",
                    json={"user_input": "Turn on lights", "voice_mode": True},
                )

        assert response.status_code == 200
        data = response.json()
        assert "audio_base64" in data
        mock_synth.synthesize.assert_called_once_with("Lights are on")


# ---------------------------------------------------------------------------
# Core mode — ProposeNewToolDecision
# ---------------------------------------------------------------------------

class TestCoreModePropose:
    """Tests for ProposeNewToolDecision path in Core mode."""

    @pytest.mark.asyncio
    async def test_core_propose_new_tool_response(self, mock_core):
        """ProposeNewToolDecision returns the expected shape."""
        decision = _make_propose_decision()
        mock_core.process.return_value = decision

        with patch("ai_server.main.alfred_core", mock_core), \
             patch("ai_server.main.settings") as mock_settings:
            mock_settings.alfred_mode = "core"

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/execute",
                    json={"user_input": "Add garage door control"},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["intent"] == "propose_new_tool"
        assert data["name"] == "garage_control"
        assert data["description"] == "Control the garage door opener"
        assert data["executable"] is False

    @pytest.mark.asyncio
    async def test_core_propose_includes_session_id(self, mock_core):
        """ProposeNewToolDecision response includes session_id."""
        decision = _make_propose_decision()
        mock_core.process.return_value = decision

        with patch("ai_server.main.alfred_core", mock_core), \
             patch("ai_server.main.settings") as mock_settings:
            mock_settings.alfred_mode = "core"

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/execute",
                    json={"user_input": "Add garage door control"},
                )

        assert "session_id" in response.json()


# ---------------------------------------------------------------------------
# Core mode — error handling
# ---------------------------------------------------------------------------

class TestCoreModeErrors:
    """Tests for error handling in Core mode."""

    @pytest.mark.asyncio
    async def test_core_unavailable_returns_503(self):
        """503 is returned when alfred_core is None."""
        with patch("ai_server.main.alfred_core", None), \
             patch("ai_server.main.settings") as mock_settings:
            mock_settings.alfred_mode = "core"

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/execute",
                    json={"user_input": "Hello"},
                )

        assert response.status_code == 503
        assert "AlfredCore not available" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_core_value_error_returns_400(self, mock_core):
        """ValueError from Core (e.g. malformed JSON retry failure) returns 400."""
        mock_core.process.side_effect = ValueError("Core returned malformed JSON and retry failed")

        with patch("ai_server.main.alfred_core", mock_core), \
             patch("ai_server.main.settings") as mock_settings:
            mock_settings.alfred_mode = "core"

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/execute",
                    json={"user_input": "Something that breaks the model"},
                )

        assert response.status_code == 400
        assert "malformed JSON" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_core_synthesis_error_does_not_fail_request(self, mock_core):
        """If TTS synthesis fails in voice_mode, request still succeeds without audio."""
        mock_core.process.return_value = "Hello!"

        with patch("ai_server.main.alfred_core", mock_core), \
             patch("ai_server.main.settings") as mock_settings, \
             patch("ai_server.main.synthesizer") as mock_synth:
            mock_settings.alfred_mode = "core"
            mock_synth.synthesize = AsyncMock(side_effect=RuntimeError("TTS model crashed"))

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/execute",
                    json={"user_input": "Hello", "voice_mode": True},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["answer"] == "Hello!"
        assert "audio_base64" not in data


# ---------------------------------------------------------------------------
# Router mode still works (regression guard)
# ---------------------------------------------------------------------------

class TestRouterModeRegression:
    """Ensure existing router-mode behaviour is unchanged by the Core integration."""

    @pytest.mark.asyncio
    async def test_router_mode_qa_response(self):
        """Router mode routes Q&A through qa_handler as before."""
        from ai_server.alfred_router.schemas import RouteToQADecision

        decision = RouteToQADecision(intent="route_to_qa", query="What is 2+2?")

        with patch("ai_server.main.alfred_router") as mock_router, \
             patch("ai_server.main.qa_handler") as mock_qa, \
             patch("ai_server.main.settings") as mock_settings:
            mock_settings.alfred_mode = "router"
            mock_router.route.return_value = decision
            mock_qa.answer = AsyncMock(return_value="4")

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/execute",
                    json={"user_input": "What is 2+2?"},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["intent"] == "route_to_qa"
        assert data["answer"] == "4"

    @pytest.mark.asyncio
    async def test_router_mode_propose_new_tool(self):
        """Router mode returns propose_new_tool shape correctly."""
        decision = ProposeNewToolDecision(
            intent="propose_new_tool",
            name="weather_tool",
            description="Get current weather",
        )

        with patch("ai_server.main.alfred_router") as mock_router, \
             patch("ai_server.main.settings") as mock_settings:
            mock_settings.alfred_mode = "router"
            mock_router.route.return_value = decision

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/execute",
                    json={"user_input": "I want weather info"},
                )

        assert response.status_code == 200
        data = response.json()
        assert data["intent"] == "propose_new_tool"
        assert data["name"] == "weather_tool"
        assert data["executable"] is False

    @pytest.mark.asyncio
    async def test_router_unavailable_returns_503(self):
        """Router mode returns 503 when alfred_router is None."""
        with patch("ai_server.main.alfred_router", None), \
             patch("ai_server.main.settings") as mock_settings:
            mock_settings.alfred_mode = "router"

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/execute",
                    json={"user_input": "Hello"},
                )

        assert response.status_code == 503
        assert "Router not available" in response.json()["detail"]
