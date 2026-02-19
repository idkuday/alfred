"""
Configuration management for AI Server.
Supports environment variables and config files.
"""
import os
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings with environment variable support."""
    
    # Server settings
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = True
    
    # Home Assistant settings
    ha_url: Optional[str] = os.getenv("HA_URL", "http://localhost:8123")
    ha_token: Optional[str] = os.getenv("HA_TOKEN", None)
    
    # Logging
    log_level: str = "INFO"
    log_file: Optional[str] = "ai_server.log"
    
    # Plugin settings
    plugins_dir: str = "plugins"
    auto_load_plugins: bool = True
    
    # Device mapping (room -> device mappings)
    device_mappings: dict = {}

    # Alfred Router
    alfred_router_model: str = os.getenv("ALFRED_ROUTER_MODEL", "qwen2.5:3b")
    alfred_router_prompt_path: str = os.getenv(
        "ALFRED_ROUTER_PROMPT_PATH", "ai_server/alfred_router/prompts/router.txt"
    )
    alfred_router_temperature: float = float(os.getenv("ALFRED_ROUTER_TEMPERATURE", 0.0))
    alfred_router_max_tokens: int = int(os.getenv("ALFRED_ROUTER_MAX_TOKENS", 1024))

    # Alfred Q/A - read-only
    alfred_qa_model: str = os.getenv("ALFRED_QA_MODEL", "qwen2.5:3b")
    alfred_qa_temperature: float = float(os.getenv("ALFRED_QA_TEMPERATURE", 0.1))
    alfred_qa_max_tokens: int = int(os.getenv("ALFRED_QA_MAX_TOKENS", 2048))

    # Alfred Core — unified brain (replaces router + QA when ALFRED_MODE=core)
    alfred_mode: str = os.getenv("ALFRED_MODE", "router")  # "router" | "core"
    alfred_core_model: str = os.getenv("ALFRED_CORE_MODEL", "qwen2.5:3b")
    alfred_core_prompt_path: str = os.getenv(
        "ALFRED_CORE_PROMPT_PATH", "ai_server/core/prompts/core.txt"
    )
    alfred_core_retry_prompt_path: str = os.getenv(
        "ALFRED_CORE_RETRY_PROMPT_PATH", "ai_server/core/prompts/retry.txt"
    )
    alfred_core_temperature: float = float(os.getenv("ALFRED_CORE_TEMPERATURE", 0.0))
    alfred_core_max_tokens: int = int(os.getenv("ALFRED_CORE_MAX_TOKENS", 2048))

    # Session Memory
    session_db_path: str = os.getenv("SESSION_DB_PATH", "alfred_sessions.db")
    session_timeout_minutes: int = int(os.getenv("SESSION_TIMEOUT_MINUTES", 30))
    session_history_limit: int = int(os.getenv("SESSION_HISTORY_LIMIT", 10))

    # Speech to Text (Faster Whisper)
    whisper_model: str = os.getenv("WHISPER_MODEL", "tiny.en")
    whisper_device: str = os.getenv("WHISPER_DEVICE", "cpu")  # "cuda" or "cpu"
    whisper_compute_type: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")  # "float16" for cuda, "int8" for cpu

    # Text to Speech (Piper)
    piper_voice_model: str = os.getenv("PIPER_VOICE_MODEL", "ai_server/audio/voices/en_GB-alan-medium.onnx")
    piper_speaker_id: Optional[int] = None  # For multi-speaker models
    tts_enabled: bool = os.getenv("TTS_ENABLED", "true").lower() in ("true", "1", "yes")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()


