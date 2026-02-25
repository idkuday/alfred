"""
Unit tests for /execute endpoint with voice_mode support.
"""
import pytest
import base64
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from ai_server.main import app
from ai_server.alfred_router.schemas import (
    CallToolDecision,
    ProposeNewToolDecision,
)
from ai_server.models import CommandResponse


class TestExecuteVoiceModeDisabled:
    """Test /execute endpoint with voice_mode=False (default)."""

    @pytest.mark.asyncio
    async def test_execute_voice_mode_false_no_audio(self):
        """Test execute with voice_mode=False doesn't include audio_base64."""
        with patch('ai_server.main.alfred_core') as mock_core:
            mock_core.process = AsyncMock(return_value="The weather today is sunny.")

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/execute",
                    json={"user_input": "What is the weather?", "voice_mode": False}
                )

            assert response.status_code == 200
            data = response.json()
            assert "answer" in data
            assert data["answer"] == "The weather today is sunny."
            assert "audio_base64" not in data  # No audio when voice_mode=False

    @pytest.mark.asyncio
    async def test_execute_default_no_voice_mode_no_audio(self):
        """Test execute without voice_mode field (defaults to False)."""
        with patch('ai_server.main.alfred_core') as mock_core:
            mock_core.process = AsyncMock(return_value="The weather today is sunny.")

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/execute",
                    json={"user_input": "What is the weather?"}  # No voice_mode field
                )

            assert response.status_code == 200
            data = response.json()
            assert "audio_base64" not in data  # No audio by default


class TestExecuteVoiceModeEnabled:
    """Test /execute endpoint with voice_mode=True."""

    @pytest.mark.asyncio
    async def test_execute_voice_mode_true_includes_audio(self):
        """Test execute with voice_mode=True includes audio_base64."""
        with patch('ai_server.main.alfred_core') as mock_core:
            mock_core.process = AsyncMock(return_value="Test audio response")

            # Mock the synthesizer
            with patch('ai_server.main.synthesizer') as mock_synth:
                fake_wav = b'RIFF....WAVE....'  # Fake WAV bytes
                mock_synth.synthesize = AsyncMock(return_value=fake_wav)

                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    response = await client.post(
                        "/execute",
                        json={"user_input": "What is the weather?", "voice_mode": True}
                    )

                assert response.status_code == 200
                data = response.json()
                assert "answer" in data
                assert data["answer"] == "Test audio response"
                assert "audio_base64" in data  # Audio included when voice_mode=True

                # Verify audio is base64-encoded
                audio_bytes = base64.b64decode(data["audio_base64"])
                assert audio_bytes == fake_wav

                # Verify synthesizer was called with the response text
                mock_synth.synthesize.assert_called_once_with("Test audio response")

    @pytest.mark.asyncio
    async def test_execute_voice_mode_no_synthesizer(self):
        """Test execute with voice_mode=True gracefully handles missing synthesizer."""
        with patch('ai_server.main.alfred_core') as mock_core:
            mock_core.process = AsyncMock(return_value="Test answer")

            # Set synthesizer to None
            with patch('ai_server.main.synthesizer', None):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    response = await client.post(
                        "/execute",
                        json={"user_input": "What is the weather?", "voice_mode": True}
                    )

                # Should still succeed, just without audio
                assert response.status_code == 200
                data = response.json()
                assert "answer" in data
                assert "audio_base64" not in data  # No audio when synthesizer unavailable

    @pytest.mark.asyncio
    async def test_execute_voice_mode_synthesis_failure(self):
        """Test execute with voice_mode=True handles synthesis errors gracefully."""
        with patch('ai_server.main.alfred_core') as mock_core:
            mock_core.process = AsyncMock(return_value="Test answer")

            # Mock synthesizer to raise an exception
            with patch('ai_server.main.synthesizer') as mock_synth:
                mock_synth.synthesize = AsyncMock(side_effect=RuntimeError("Synthesis failed"))

                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    response = await client.post(
                        "/execute",
                        json={"user_input": "What is the weather?", "voice_mode": True}
                    )

                # Should still succeed, just without audio
                assert response.status_code == 200
                data = response.json()
                assert "answer" in data
                assert "audio_base64" not in data  # No audio on synthesis error


class TestExecuteVoiceModeWithTools:
    """Test /execute with voice_mode for CallToolDecision."""

    @pytest.mark.asyncio
    async def test_execute_voice_mode_with_tool_call(self):
        """Test voice_mode works with tool execution (CallToolDecision)."""
        decision = CallToolDecision(
            intent="call_tool",
            tool="home_assistant",
            parameters={
                "action": "turn_on",
                "target": "light.living_room"
            }
        )

        with patch('ai_server.main.alfred_core') as mock_core:
            mock_core.process = AsyncMock(return_value=decision)

            # Mock the tool execution
            with patch('ai_server.main._handle_call_tool') as mock_handle:
                mock_result = CommandResponse(
                    status="success",
                    action="turn_on",
                    target="light.living_room",
                    message="Light turned on"
                )
                mock_handle.return_value = mock_result

                # Mock the synthesizer
                with patch('ai_server.main.synthesizer') as mock_synth:
                    fake_wav = b'RIFF....WAVE....'
                    mock_synth.synthesize = AsyncMock(return_value=fake_wav)

                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        response = await client.post(
                            "/execute",
                            json={"user_input": "Turn on the light", "voice_mode": True}
                        )

                    assert response.status_code == 200
                    data = response.json()
                    assert "audio_base64" in data

                    # Verify synthesizer was called with the tool result message
                    mock_synth.synthesize.assert_called_once_with("Light turned on")


class TestSynthesizeEndpoint:
    """Test standalone /synthesize endpoint."""

    @pytest.mark.asyncio
    async def test_synthesize_endpoint_success(self):
        """Test /synthesize endpoint returns audio WAV."""
        with patch('ai_server.main.synthesizer') as mock_synth:
            fake_wav = b'RIFF' + b'\x00' * 40 + b'WAVE'  # Minimal WAV header
            mock_synth.synthesize = AsyncMock(return_value=fake_wav)

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/synthesize",
                    json={"text": "Hello world"}
                )

            assert response.status_code == 200
            assert response.headers["content-type"] == "audio/wav"
            assert response.content == fake_wav

            mock_synth.synthesize.assert_called_once_with("Hello world")

    @pytest.mark.asyncio
    async def test_synthesize_endpoint_empty_text(self):
        """Test /synthesize endpoint rejects empty text."""
        with patch('ai_server.main.synthesizer') as mock_synth:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/synthesize",
                    json={"text": ""}
                )

            assert response.status_code == 400
            assert "empty" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_synthesize_endpoint_no_synthesizer(self):
        """Test /synthesize endpoint returns 503 when synthesizer not available."""
        with patch('ai_server.main.synthesizer', None):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/synthesize",
                    json={"text": "Hello"}
                )

            assert response.status_code == 503
            assert "not initialized" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_synthesize_endpoint_synthesis_failure(self):
        """Test /synthesize endpoint handles synthesis errors."""
        with patch('ai_server.main.synthesizer') as mock_synth:
            mock_synth.synthesize = AsyncMock(side_effect=RuntimeError("Model error"))

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/synthesize",
                    json={"text": "Hello"}
                )

            assert response.status_code == 500
            assert "failed" in response.json()["detail"].lower()
