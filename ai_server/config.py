"""
Configuration management for AI Server.
Supports environment variables and config files.
"""
import os
from pathlib import Path
from typing import Literal, Optional
from pydantic_settings import BaseSettings

# Absolute repo root, so defaults that point at repo files (plugins, the Jev key,
# data/) resolve the same from any working directory (omni-jev plan, J21).
REPO_ROOT = Path(__file__).resolve().parents[1]


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
    
    # Plugin settings (absolute default: plugins load from any cwd, J21)
    plugins_dir: str = str(REPO_ROOT / "ai_server" / "plugins")
    auto_load_plugins: bool = True
    
    # Device mapping (room -> device mappings)
    device_mappings: dict = {}

    # Alfred Core — unified brain (single LLM call for all requests)
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

    # Omni / Jev (omni-jev plan). Settings only in A1; consumers arrive in later tasks.
    # Jev key: raw key, no KEY= prefix. Read only through KeyFileCredential (J4/secrets).
    jev_key_file: str = str(REPO_ROOT / "jev.env")
    decision_provider: str = "typesafe"  # Omni decision provider
    decision_model: str = "jev-latest"  # model id passed to the decision provider
    resolve_threshold: float = 0.6  # minimum score for an entity resolve match
    resolve_max_candidates: int = 200  # cap on candidates scored per resolve call
    alfred_backend: Literal["omni", "core"] = "omni"  # which brain serves /execute
    omni_front_graph: str = "front-door"  # graph under .omni/graphs/ the bridge runs
    omni_node_provider: Optional[str] = None  # None = provider from the graph/node default
    omni_node_model: Optional[str] = None  # None = model from the graph/node default
    omni_ask_timeout: float = 300.0  # seconds a pending ask may wait for the user
    omni_turn_timeout: float = 120.0  # seconds one turn may run before it is cut off
    omni_earlier_chars: int = 6000  # char budget for earlier-turn context
    data_dir: str = str(REPO_ROOT / "data")  # runtime state (sessions, bridge stores)

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


