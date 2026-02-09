"""
Data models for commands, responses, and device states.
"""
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum


class Action(str, Enum):
    """Supported actions."""
    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"
    TOGGLE = "toggle"
    SET_BRIGHTNESS = "set_brightness"
    SET_COLOR = "set_color"
    GET_STATUS = "get_status"


class Command(BaseModel):
    """Command from AI Assistant."""
    action: str
    target: str
    parameters: Optional[Dict[str, Any]] = None
    room: Optional[str] = None
    intent: Optional[str] = None  # Parsed intent from NLP


class CommandResponse(BaseModel):
    """Response from command execution."""
    status: str  # "success", "error", "pending"
    action: str
    target: str
    message: Optional[str] = None
    device_state: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class DeviceInfo(BaseModel):
    """Device information."""
    entity_id: str
    name: str
    device_type: str
    room: Optional[str] = None
    state: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None


class VoiceCommandResponse(BaseModel):
    """Response from voice command (transcribe + route + execute)."""
    transcript: str
    intent: Optional[str] = None  # "call_tool", "route_to_qa", "propose_new_tool"
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    processed: bool = False  # True if transcript was routed/executed


class ChatMessage(BaseModel):
    """A single message in a conversation session."""
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime


class SessionMeta(BaseModel):
    """Metadata about a conversation session."""
    session_id: str
    created_at: datetime
    last_active: datetime
    message_count: int



