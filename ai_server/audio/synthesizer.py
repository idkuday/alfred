"""
Audio synthesis module using Piper TTS.
"""
import logging
import asyncio
import io
import wave
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class Synthesizer:
    """
    Handles offline text-to-speech using Piper TTS.

    The voice model is loaded once upon initialization.
    Synthesis is CPU-intensive and runs in a thread pool.
    """

    def __init__(
        self,
        voice_model: str = "ai_server/audio/voices/en_GB-alan-medium.onnx",
        speaker_id: Optional[int] = None,
    ):
        self.voice_model = voice_model
        self.speaker_id = speaker_id
        self.voice = None

        # Thread pool for blocking synthesis operations
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="piper")

    def load_model(self):
        """
        Load the Piper voice model. This catches and logs errors but propagates them
        so the caller knows if initialization failed.
        """
        logger.info(f"Loading Piper voice model: {self.voice_model}")

        # Check if model file exists
        model_path = Path(self.voice_model)
        if not model_path.exists():
            raise FileNotFoundError(
                f"Piper voice model not found at: {self.voice_model}\n"
                f"Please download the voice model. See ai_server/audio/voices/README.md"
            )

        try:
            from piper import PiperVoice
            self.voice = PiperVoice.load(str(model_path), use_cuda=False)
            logger.info("Piper voice model loaded successfully")
        except ImportError:
            raise ImportError(
                "piper-tts not installed. Install with: pip install piper-tts"
            )

    def synthesize_sync(self, text: str) -> bytes:
        """
        Synthesize text to speech.
        BLOCKING method - run in executor.

        Returns:
            WAV audio data as bytes
        """
        if not self.voice:
            raise RuntimeError("Synthesizer voice model not loaded")

        if not text or not text.strip():
            logger.warning("Empty text provided for synthesis")
            # Return silent WAV (empty audio)
            return self._create_silent_wav()

        # Synthesize audio using Piper
        # PiperVoice.synthesize_stream_raw returns PCM audio samples
        audio_buffer = io.BytesIO()

        # Collect audio samples from generator
        audio_samples = []
        for audio_bytes in self.voice.synthesize_stream_raw(text, speaker_id=self.speaker_id):
            audio_samples.append(audio_bytes)

        # Concatenate all audio bytes
        pcm_audio = b''.join(audio_samples)

        # Wrap PCM in WAV format
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wav_file:
            # Piper outputs 16-bit mono PCM at 22050 Hz (default)
            wav_file.setnchannels(1)  # Mono
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(22050)  # 22050 Hz sample rate
            wav_file.writeframes(pcm_audio)

        wav_buffer.seek(0)
        return wav_buffer.read()

    def _create_silent_wav(self) -> bytes:
        """
        Create a minimal silent WAV file (for empty text).
        """
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
            wav_file.writeframes(b'')  # Empty audio

        wav_buffer.seek(0)
        return wav_buffer.read()

    async def synthesize(self, text: str) -> bytes:
        """
        Async wrapper for text-to-speech synthesis.

        Args:
            text: Text to synthesize

        Returns:
            WAV audio data as bytes
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self.synthesize_sync,
            text
        )
