"""
Unit tests for Piper TTS Synthesizer.
"""
import pytest
import wave
import io
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from ai_server.audio.synthesizer import Synthesizer


class TestSynthesizerInit:
    """Test Synthesizer initialization."""

    def test_init_with_defaults(self):
        """Test synthesizer initializes with default values."""
        synth = Synthesizer()
        assert synth.voice_model == "ai_server/audio/voices/en_GB-alan-medium.onnx"
        assert synth.speaker_id is None
        assert synth.voice is None
        assert synth._executor is not None

    def test_init_with_custom_model(self):
        """Test synthesizer initializes with custom voice model."""
        synth = Synthesizer(voice_model="custom/voice.onnx", speaker_id=0)
        assert synth.voice_model == "custom/voice.onnx"
        assert synth.speaker_id == 0


class TestSynthesizerLoadModel:
    """Test Synthesizer model loading."""

    def test_load_model_file_not_found(self):
        """Test load_model raises FileNotFoundError if voice model doesn't exist."""
        synth = Synthesizer(voice_model="nonexistent/voice.onnx")
        with pytest.raises(FileNotFoundError) as exc_info:
            synth.load_model()
        assert "not found" in str(exc_info.value)

    def test_load_model_success(self):
        """Test load_model successfully loads a voice model."""
        with patch('ai_server.audio.synthesizer.Path.exists', return_value=True):
            # Create a mock PiperVoice class
            mock_voice_instance = Mock()
            mock_piper_class = Mock()
            mock_piper_class.load = Mock(return_value=mock_voice_instance)

            # Mock the import of piper module
            with patch.dict('sys.modules', {'piper': Mock(PiperVoice=mock_piper_class)}):
                synth = Synthesizer(voice_model="test_voice.onnx")
                synth.load_model()

                assert synth.voice == mock_voice_instance
                mock_piper_class.load.assert_called_once_with("test_voice.onnx", use_cuda=False)

    @patch('ai_server.audio.synthesizer.Path.exists')
    def test_load_model_import_error(self, mock_exists):
        """Test load_model raises ImportError if piper-tts not installed."""
        mock_exists.return_value = True

        with patch.dict('sys.modules', {'piper': None}):
            synth = Synthesizer(voice_model="test_voice.onnx")
            with pytest.raises(ImportError) as exc_info:
                synth.load_model()
            assert "piper-tts not installed" in str(exc_info.value)


class TestSynthesizerSynthesize:
    """Test Synthesizer synthesis functionality."""

    def setup_method(self):
        """Set up a mock synthesizer for each test."""
        self.synth = Synthesizer(voice_model="test_voice.onnx")
        self.synth.voice = Mock()

    def test_synthesize_sync_returns_wav_bytes(self):
        """Test synthesize_sync returns valid WAV bytes."""
        # Mock the voice synthesis to return some PCM audio data
        mock_pcm_data = b'\x00\x01' * 100  # Fake PCM samples
        self.synth.voice.synthesize_stream_raw.return_value = iter([mock_pcm_data])

        wav_bytes = self.synth.synthesize_sync("Hello world")

        # Check that we got bytes back
        assert isinstance(wav_bytes, bytes)
        assert len(wav_bytes) > 0

        # Verify it's a valid WAV file by parsing it
        wav_buffer = io.BytesIO(wav_bytes)
        with wave.open(wav_buffer, 'rb') as wav_file:
            assert wav_file.getnchannels() == 1  # Mono
            assert wav_file.getsampwidth() == 2  # 16-bit
            assert wav_file.getframerate() == 22050  # 22050 Hz

        self.synth.voice.synthesize_stream_raw.assert_called_once_with("Hello world", speaker_id=None)

    def test_synthesize_sync_empty_text(self):
        """Test synthesize_sync handles empty text gracefully."""
        wav_bytes = self.synth.synthesize_sync("")

        # Should return a silent WAV (valid but empty)
        assert isinstance(wav_bytes, bytes)
        assert len(wav_bytes) > 0

        # Verify it's a valid (empty) WAV file
        wav_buffer = io.BytesIO(wav_bytes)
        with wave.open(wav_buffer, 'rb') as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
            assert wav_file.getframerate() == 22050
            assert wav_file.getnframes() == 0  # No audio frames

    def test_synthesize_sync_whitespace_text(self):
        """Test synthesize_sync handles whitespace-only text gracefully."""
        wav_bytes = self.synth.synthesize_sync("   \n  \t  ")

        # Should return a silent WAV
        assert isinstance(wav_bytes, bytes)
        wav_buffer = io.BytesIO(wav_bytes)
        with wave.open(wav_buffer, 'rb') as wav_file:
            assert wav_file.getnframes() == 0

    def test_synthesize_sync_model_not_loaded(self):
        """Test synthesize_sync raises error if model not loaded."""
        synth = Synthesizer()
        synth.voice = None

        with pytest.raises(RuntimeError) as exc_info:
            synth.synthesize_sync("test")
        assert "not loaded" in str(exc_info.value)

    def test_synthesize_sync_with_speaker_id(self):
        """Test synthesize_sync passes speaker_id to voice model."""
        self.synth.speaker_id = 2
        mock_pcm_data = b'\x00\x01' * 100
        self.synth.voice.synthesize_stream_raw.return_value = iter([mock_pcm_data])

        self.synth.synthesize_sync("Hello")

        self.synth.voice.synthesize_stream_raw.assert_called_once_with("Hello", speaker_id=2)

    @pytest.mark.asyncio
    async def test_synthesize_async_wrapper(self):
        """Test synthesize async wrapper works correctly."""
        # Mock the voice synthesis
        mock_pcm_data = b'\x00\x01' * 100
        self.synth.voice.synthesize_stream_raw.return_value = iter([mock_pcm_data])

        wav_bytes = await self.synth.synthesize("Async test")

        # Verify we got valid WAV bytes
        assert isinstance(wav_bytes, bytes)
        assert len(wav_bytes) > 0

        wav_buffer = io.BytesIO(wav_bytes)
        with wave.open(wav_buffer, 'rb') as wav_file:
            assert wav_file.getnchannels() == 1


class TestSynthesizerPrivateMethods:
    """Test Synthesizer private helper methods."""

    def test_create_silent_wav(self):
        """Test _create_silent_wav creates a valid empty WAV file."""
        synth = Synthesizer()
        silent_wav = synth._create_silent_wav()

        assert isinstance(silent_wav, bytes)
        assert len(silent_wav) > 0

        # Parse and verify it's a valid empty WAV
        wav_buffer = io.BytesIO(silent_wav)
        with wave.open(wav_buffer, 'rb') as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
            assert wav_file.getframerate() == 22050
            assert wav_file.getnframes() == 0  # No audio
