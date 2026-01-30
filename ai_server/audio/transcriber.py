"""
Audio transcription module using Faster Whisper.
"""
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import BinaryIO, Union
from pathlib import Path

from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


class Transcriber:
    """
    Handles offline speech-to-text using Faster Whisper.
    
    The model is loaded once upon initialization.
    Transcriptions are CPU-intensive and run in a thread pool.
    """

    def __init__(
        self,
        model_size: str = "tiny.en",
        device: str = "cpu",
        compute_type: str = "int8",
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.model = None
        
        # Thread pool for blocking model operations
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")

    def load_model(self):
        """
        Load the model. This catches and logs errors but propagates them
        so the caller knows if initialization failed.
        """
        logger.info(f"Loading Whisper model: {self.model_size} ({self.device}/{self.compute_type})")
        # faster-whisper downloads the model automatically if not present in cache
        self.model = WhisperModel(
            self.model_size, 
            device=self.device, 
            compute_type=self.compute_type
        )
        logger.info("Whisper model loaded successfully")

    def transcribe_file(self, file_path_or_obj: Union[str, BinaryIO]) -> str:
        """
        Transcribe an audio file or file-like object.
        BLOCKING method - run in executor.
        """
        if not self.model:
            raise RuntimeError("Transcriber model not loaded")

        segments, info = self.model.transcribe(
            file_path_or_obj, 
            beam_size=5,
            vad_filter=True, # Filter out silence
            vad_parameters=dict(min_silence_duration_ms=500)
        )
        
        logger.debug(f"Detected language: {info.language} with probability {info.language_probability}")

        # Segments is a generator, so we must iterate to actually run inference
        text_segments = [segment.text for segment in segments]
        full_text = " ".join(text_segments).strip()
        
        return full_text

    async def transcribe(self, file_path_or_obj: Union[str, BinaryIO]) -> str:
        """
        Async wrapper for transcription.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, 
            self.transcribe_file, 
            file_path_or_obj
        )
