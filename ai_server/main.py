"""
AI Server - Smart Home AI Assistant
Main FastAPI application for processing commands and controlling devices.

Two brains sit behind ``/execute`` and ``/sessions``, chosen by ``settings.alfred_backend``
(omni-jev plan J15, section 8): ``omni`` (default) serves them from the Omni bridge
(``AlfredOmni``, one conversation = one ``front-door`` graph run); ``core`` keeps the older
AlfredCore + SQLite session path. Whisper, Piper and ``/devices`` do not depend on the choice.
"""
import logging
import base64
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from . import decisions
from .config import REPO_ROOT, settings
from .models import Command, CommandResponse, ChatMessage, SessionMeta
from .integration.home_assistant import HomeAssistantIntegration
from .plugins import plugin_manager
from .intent_processor import IntentProcessor
from .alfred_router.schemas import CallToolDecision, ProposeNewToolDecision
from .alfred_router.tool_registry import list_tools
from .core import AlfredCore
from .audio.transcriber import Transcriber
from .audio.synthesizer import Synthesizer
from .memory import SessionStore, MessageHistoryProvider

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(settings.log_file) if settings.log_file else logging.StreamHandler(),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Global integration instance
ha_integration: HomeAssistantIntegration = None
intent_processor: IntentProcessor = None
alfred_core: Optional[AlfredCore] = None
transcriber: Optional[Transcriber] = None
synthesizer: Optional[Synthesizer] = None
session_store: Optional[SessionStore] = None
context_provider: Optional[MessageHistoryProvider] = None
alfred_omni: Optional[Any] = None  # AlfredOmni when alfred_backend == "omni" (imported lazily)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown."""
    global ha_integration, intent_processor, alfred_core, transcriber, synthesizer, session_store, context_provider
    global alfred_omni

    # Startup
    logger.info("Starting AI Server...")
    
    # Initialize Home Assistant integration
    ha_integration = HomeAssistantIntegration({
        "url": settings.ha_url,
        "token": settings.ha_token
    })
    
    # Check HA connection
    if await ha_integration.health_check():
        logger.info("Home Assistant connection successful")
    else:
        logger.warning("Home Assistant connection failed - some features may not work")
    
    # Initialize intent processor (optional helper, not authoritative)
    intent_processor = IntentProcessor(device_mappings=settings.device_mappings)

    # Load plugins
    if settings.auto_load_plugins:
        plugin_manager.load_plugins()
        logger.info(f"Loaded {len(plugin_manager.list_integrations())} integration plugins")

    use_omni = settings.alfred_backend == "omni"

    if use_omni:
        # The Omni bridge: one front-door graph run per conversation (J1). A failure here is
        # logged and Alfred keeps serving; /execute answers 503 until the bridge reconnects
        # (AlfredOmni.say retries start() once).
        try:
            from .omni_bridge import AlfredOmni  # lazy: needs omni-kernel (A5 exports it)

            alfred_omni = AlfredOmni(REPO_ROOT, settings)
        except Exception as exc:
            logger.error(f"Omni bridge unavailable: {exc}", exc_info=True)
            alfred_omni = None
        if alfred_omni is not None:
            # The instance is kept even if start() fails, so a later call can reconnect.
            try:
                await alfred_omni.start()
                logger.info("Omni bridge started")
            except Exception as exc:
                logger.error(f"Failed to start the Omni bridge: {exc}", exc_info=True)
    else:
        # Initialize AlfredCore — single LLM call for all requests
        try:
            alfred_core = AlfredCore(
                model=settings.alfred_core_model,
                prompt_path=settings.alfred_core_prompt_path,
                retry_prompt_path=settings.alfred_core_retry_prompt_path,
                temperature=settings.alfred_core_temperature,
                max_tokens=settings.alfred_core_max_tokens,
            )
            logger.info(f"AlfredCore initialized with model: {settings.alfred_core_model}")
        except Exception as exc:
            logger.error(f"Failed to initialize AlfredCore: {exc}", exc_info=True)
            alfred_core = None

    # Initialize Transcriber (Whisper)
    try:
        transcriber = Transcriber(
            model_size=settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
        )
        # Pre-load model to avoid latency on first request
        # Note: This is blocking, but okay for startup
        transcriber.load_model()
        logger.info(f"Transcriber initialized with model: {settings.whisper_model}")
    except Exception as exc:
        logger.error(f"Failed to initialize Transcriber: {exc}", exc_info=True)
        transcriber = None

    # Initialize Synthesizer (Piper TTS)
    if settings.tts_enabled:
        try:
            synthesizer = Synthesizer(
                voice_model=settings.piper_voice_model,
                speaker_id=settings.piper_speaker_id,
            )
            # Pre-load model to avoid latency on first request
            # Note: This is blocking, but okay for startup
            synthesizer.load_model()
            logger.info(f"Synthesizer initialized with voice: {settings.piper_voice_model}")
        except Exception as exc:
            logger.error(f"Failed to initialize Synthesizer: {exc}", exc_info=True)
            logger.warning("TTS will be disabled. Voice mode will not work.")
            synthesizer = None
    else:
        logger.info("TTS is disabled in config")
        synthesizer = None

    # Initialize Session Store (core backend only: the bridge's conversations are graph runs)
    if not use_omni:
        try:
            session_store = SessionStore(db_path=settings.session_db_path)
            context_provider = MessageHistoryProvider(
                session_store=session_store,
                limit=settings.session_history_limit
            )
            logger.info(f"Session store initialized at {settings.session_db_path}")

            # Cleanup expired sessions on startup
            cleaned = session_store.cleanup_expired(timeout_minutes=settings.session_timeout_minutes)
            if cleaned > 0:
                logger.info(f"Cleaned up {cleaned} expired sessions on startup")
        except Exception as exc:
            logger.error(f"Failed to initialize Session Store: {exc}", exc_info=True)
            session_store = None
            context_provider = None

    yield
    
    # Shutdown
    logger.info("Shutting down AI Server...")
    if alfred_omni:
        try:
            await alfred_omni.close()
        except Exception as exc:
            logger.error(f"Closing the Omni bridge failed: {exc}", exc_info=True)
    decisions.close()
    if ha_integration:
        await ha_integration._close_session()


# Create FastAPI app
app = FastAPI(
    title="Smart Home AI Assistant",
    description="Local-first AI assistant for smart home automation",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ExecuteRequest(BaseModel):
    """Raw user input for Alfred Router."""

    user_input: str
    session_id: Optional[str] = None
    voice_mode: bool = False  # When True, include audio_base64 in response


class ContinueRequest(BaseModel):
    """Load an earlier conversation and say something new in it (J11)."""

    model_config = ConfigDict(extra="forbid")

    user_input: str
    voice_mode: bool = False


class AskAnswerRequest(BaseModel):
    """Answer a pending ask directly, for UIs with buttons (J13)."""

    model_config = ConfigDict(extra="forbid")

    decision: str


def _http_error_from_bridge(exc: Exception, *, ask: bool = False) -> HTTPException:
    """Map a bridge exception to the HTTP error of plan section 8, or re-raise it.

    ``ConversationNotFound`` (a ``LookupError``) -> 404; ``BridgeUnavailable`` (a
    ``RuntimeError``) -> 503; ``ValueError`` -> 400, or 409 for ``answer_ask`` (the ask is not
    pending). Anything else is a bug and propagates as a 500. ``BridgeUnavailable`` is matched
    by class name so that importing it does not drag ``omni`` into the rest of Alfred.
    """
    if any(cls.__name__ == "BridgeUnavailable" for cls in type(exc).__mro__):
        return HTTPException(status_code=503, detail=f"Omni bridge unavailable: {exc}")
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc) or "Conversation not found")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=409 if ask else 400, detail=str(exc))
    raise exc


def _require_omni() -> Any:
    """The bridge, or 503 when it is not configured or did not construct."""
    if alfred_omni is None:
        raise HTTPException(status_code=503, detail="Omni bridge not available")
    return alfred_omni


async def _synthesize_base64(text: str, voice_mode: bool) -> Optional[str]:
    """Speech for ``text`` as base64 WAV when ``voice_mode`` is on; ``None`` on any failure."""
    if not voice_mode:
        return None
    if not synthesizer:
        logger.warning("Voice mode requested but synthesizer not available")
        return None
    try:
        logger.info("Synthesizing response to audio (voice mode enabled)")
        wav_bytes = await synthesizer.synthesize(text)
        logger.debug(f"Synthesized {len(wav_bytes)} bytes of audio")
        return base64.b64encode(wav_bytes).decode('utf-8')
    except Exception as exc:
        logger.error(f"Failed to synthesize audio: {exc}", exc_info=True)
        # Don't fail the request, just log and continue without audio
        return None


async def _reply_payload(reply: Any, voice_mode: bool) -> dict:
    """The ``/execute`` response for a ``ConversationReply`` (plan section 8)."""
    payload: dict[str, Any] = {
        "intent": "conversation",
        "answer": reply.answer,
        "message": reply.answer,
        "session_id": reply.conversation_id,
        "conversation_state": reply.state,
        "pending": reply.pending.model_dump() if reply.pending else None,
        "new_conversation": reply.new_conversation,
        "continued_from": reply.continued_from,
    }
    audio_base64 = await _synthesize_base64(reply.answer, voice_mode) if reply.answer else None
    if audio_base64:
        payload["audio_base64"] = audio_base64
    return payload


def _summary_dict(summary: Any) -> dict:
    """A ``ConversationSummary`` as the UI's session dict (``session_id`` is the run id)."""
    data = summary.model_dump()
    data["session_id"] = summary.conversation_id
    data["created_at"] = summary.started_at  # the UI's name for it (additive)
    return data


def _entry_dict(entry: Any) -> dict:
    """A ``TranscriptEntry`` as dict, with the UI's ``content`` / ``timestamp`` names added."""
    data = entry.model_dump()
    data["content"] = entry.text
    data["timestamp"] = entry.at
    return data


def _build_command_from_parameters(parameters: dict) -> Command:
    """Construct a Command from router parameters."""
    action = parameters.get("action")
    target = parameters.get("target")
    if not action or not target:
        raise HTTPException(
            status_code=400,
            detail="call_tool requires 'action' and 'target' parameters",
        )
    room = parameters.get("room")
    extra_params = {k: v for k, v in parameters.items() if k not in {"action", "target", "room", "intent"}}
    return Command(
        action=action,
        target=target,
        room=room,
        parameters=extra_params or None,
    )


async def _dispatch_command(cmd: Command, tool_name: str) -> CommandResponse:
    """Dispatch a command to the selected integration."""
    if tool_name == "home_assistant":
        if not ha_integration:
            raise HTTPException(status_code=503, detail="Home Assistant not available")
        return await ha_integration.execute_command(cmd)

    plugin_integration = plugin_manager.get_integration(tool_name)
    if plugin_integration:
        return await plugin_integration.execute_command(cmd)

    raise HTTPException(status_code=404, detail=f"Integration '{tool_name}' not found")


async def _handle_call_tool(decision: CallToolDecision) -> CommandResponse:
    """Handle call_tool decisions with strict validation."""
    base_cmd = _build_command_from_parameters(decision.parameters or {})

    # Intent processor is optional and only runs if Core explicitly selects it.
    if decision.tool == "intent_processor":
        if not intent_processor:
            raise HTTPException(status_code=503, detail="Intent processor not available")
        processed = intent_processor.process(
            action=base_cmd.action,
            target=base_cmd.target,
            room=base_cmd.room,
        )
        # Merge parameters, preserving router-provided params
        if base_cmd.parameters:
            if processed.parameters:
                processed.parameters.update(base_cmd.parameters)
            else:
                processed.parameters = base_cmd.parameters
        base_cmd = processed
        target_tool = "home_assistant"
    else:
        target_tool = decision.tool

    return await _dispatch_command(base_cmd, target_tool)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Smart Home AI Assistant",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    ha_healthy = await ha_integration.health_check() if ha_integration else False

    if settings.alfred_backend == "omni":
        omni_ok = False
        if alfred_omni is not None:
            try:
                omni_ok = bool(await alfred_omni.healthy())
            except Exception as exc:
                logger.warning(f"Omni health check failed: {exc}")
        return {
            "status": "healthy" if ha_healthy and omni_ok else "degraded",
            "home_assistant": "connected" if ha_healthy else "disconnected",
            "plugins_loaded": len(plugin_manager.list_integrations()),
            "alfred_backend": "omni",
            "omni": "connected" if omni_ok else "unavailable",
        }

    return {
        "status": "healthy" if ha_healthy else "degraded",
        "home_assistant": "connected" if ha_healthy else "disconnected",
        "plugins_loaded": len(plugin_manager.list_integrations()),
        "alfred_backend": "core",
        "alfred_core": "ready" if alfred_core else "unavailable",
    }


@app.post("/execute")
async def execute_command(request: ExecuteRequest):
    """
    Execute a user request via AlfredCore (single LLM call).

    - Accepts raw user input (text) and optional session_id.
    - Creates a new session if none provided.
    - Injects conversation context from session memory before the LLM call.
    - Saves user message and assistant response to session after completion.
    - Optionally synthesizes response to audio when voice_mode=True.

    With ``alfred_backend == "omni"`` the request is served by the Omni bridge instead
    (plan section 8): ``session_id`` is a front-door run id, and the reply also carries
    ``conversation_state``, ``pending``, ``new_conversation`` and ``continued_from``.
    """
    if settings.alfred_backend == "omni":
        omni = _require_omni()
        try:
            reply = await omni.say(request.user_input, request.session_id)
        except Exception as exc:
            raise _http_error_from_bridge(exc) from exc
        return await _reply_payload(reply, request.voice_mode)

    # Session management (same regardless of mode)
    current_session_id = request.session_id
    conversation_context = ""

    if session_store and context_provider:
        if not current_session_id:
            current_session_id = session_store.create_session()
            logger.info(f"Created new session: {current_session_id}")
        else:
            if not session_store.session_exists(current_session_id):
                logger.warning(f"Session {current_session_id} not found, creating new one")
                current_session_id = session_store.create_session()

        try:
            conversation_context = context_provider.build_context(current_session_id)
        except Exception as exc:
            logger.error(f"Failed to build context: {exc}", exc_info=True)
            conversation_context = ""

    tools = list_tools()
    result = None
    assistant_response = ""

    # ------------------------------------------------------------------ #
    # AlfredCore — single LLM call, plain text or tool JSON              #
    # ------------------------------------------------------------------ #
    if not alfred_core:
        raise HTTPException(status_code=503, detail="AlfredCore not available")

    try:
        core_decision = await alfred_core.process(
            user_input=request.user_input,
            tools=tools,
            conversation_context=conversation_context if conversation_context else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if isinstance(core_decision, str):
        # Plain text = conversational response
        result = {"intent": "conversation", "answer": core_decision}
        assistant_response = core_decision

    elif isinstance(core_decision, CallToolDecision):
        result = await _handle_call_tool(core_decision)
        assistant_response = result.message or f"Executed {result.action} on {result.target}"

    elif isinstance(core_decision, ProposeNewToolDecision):
        result = {
            "intent": "propose_new_tool",
            "name": core_decision.name,
            "description": core_decision.description,
            "executable": False,
        }
        assistant_response = f"I can help you create a new tool: {core_decision.name}"

    else:
        raise HTTPException(status_code=400, detail="Unsupported Core decision")

    # Save messages to session
    if session_store and current_session_id:
        try:
            session_store.save_message(current_session_id, "user", request.user_input)
            session_store.save_message(current_session_id, "assistant", assistant_response)
        except Exception as exc:
            logger.error(f"Failed to save messages to session: {exc}", exc_info=True)

    # Synthesize response to audio if voice_mode is enabled
    audio_base64 = await _synthesize_base64(assistant_response, request.voice_mode)

    # Add session_id and audio to response
    if isinstance(result, dict):
        result["session_id"] = current_session_id
        if audio_base64:
            result["audio_base64"] = audio_base64
    elif hasattr(result, 'model_dump'):
        result_dict = result.model_dump()
        result_dict["session_id"] = current_session_id
        if audio_base64:
            result_dict["audio_base64"] = audio_base64
        return result_dict
    else:
        result_dict = {**result.__dict__, "session_id": current_session_id}
        if audio_base64:
            result_dict["audio_base64"] = audio_base64
        return result_dict

    return result


@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """
    Transcribe an uploaded audio file.

    Returns:
        JSON object with "text".
    """
    if not transcriber:
        raise HTTPException(status_code=503, detail="Transcriber not initialized")

    try:
        # Transcribe directly from the spooled file object
        text = await transcriber.transcribe(file.file)
        return {"text": text}
    except Exception as exc:
        logger.error(f"Transcription failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(exc)}")


class SynthesizeRequest(BaseModel):
    """Request for text-to-speech synthesis."""
    text: str


@app.post("/synthesize")
async def synthesize_text(request: SynthesizeRequest):
    """
    Synthesize text to speech using Piper TTS.

    Args:
        request: JSON with "text" field

    Returns:
        StreamingResponse with audio/wav content
    """
    if not synthesizer:
        raise HTTPException(
            status_code=503,
            detail="Synthesizer not initialized. Check TTS_ENABLED config and voice model."
        )

    if not request.text or not request.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    try:
        wav_bytes = await synthesizer.synthesize(request.text)
        return StreamingResponse(
            iter([wav_bytes]),
            media_type="audio/wav",
            headers={
                "Content-Disposition": "attachment; filename=speech.wav"
            }
        )
    except Exception as exc:
        logger.error(f"Synthesis failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Synthesis failed: {str(exc)}")


@app.get("/devices")
async def list_devices():
    """List all available devices."""
    if not ha_integration:
        raise HTTPException(status_code=503, detail="Home Assistant not available")
    
    devices = await ha_integration.discover_devices()
    return {
        "count": len(devices),
        "devices": [device.dict() for device in devices]
    }


@app.get("/devices/{entity_id}")
async def get_device(entity_id: str):
    """Get information about a specific device."""
    if not ha_integration:
        raise HTTPException(status_code=503, detail="Home Assistant not available")

    device = await ha_integration.get_device_info(entity_id)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device {entity_id} not found")

    return device.dict()


# Session Management Endpoints

@app.get("/sessions")
async def list_sessions():
    """
    List all conversation sessions with metadata.

    Returns:
        List of sessions ordered by last_active (most recent first)
    """
    if settings.alfred_backend == "omni":
        omni = _require_omni()
        try:
            summaries = await omni.list_conversations()
        except Exception as exc:
            raise _http_error_from_bridge(exc) from exc
        return {
            "count": len(summaries),
            "sessions": [_summary_dict(s) for s in summaries],
        }

    if not session_store:
        raise HTTPException(status_code=503, detail="Session store not available")

    sessions = session_store.list_sessions()
    return {
        "count": len(sessions),
        "sessions": [s.to_dict() for s in sessions]
    }


@app.post("/sessions")
async def create_session():
    """
    Explicitly create a new conversation session.

    With the omni backend this returns a placeholder id (``pending-<uuid4>``): the first
    ``/execute`` carrying it starts the run and returns the real id (unknown ids start new
    conversations, plan section 7.8).

    Returns:
        New session_id
    """
    if settings.alfred_backend == "omni":
        _require_omni()
        return {"session_id": f"pending-{uuid.uuid4()}"}

    if not session_store:
        raise HTTPException(status_code=503, detail="Session store not available")

    session_id = session_store.create_session()
    return {"session_id": session_id}


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """
    Get a session with its full message history.

    Args:
        session_id: Session ID

    Returns:
        Session metadata and message history
    """
    if settings.alfred_backend == "omni":
        omni = _require_omni()
        try:
            transcript = await omni.transcript(session_id)
        except Exception as exc:
            raise _http_error_from_bridge(exc) from exc
        return {
            "session": _summary_dict(transcript.conversation),
            "messages": [_entry_dict(e) for e in transcript.entries],
        }

    if not session_store:
        raise HTTPException(status_code=503, detail="Session store not available")

    if not session_store.session_exists(session_id):
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    # Get session metadata
    sessions = session_store.list_sessions()
    session_meta = next((s for s in sessions if s.session_id == session_id), None)

    if not session_meta:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    # Get message history (all messages, not just last N)
    messages = session_store.get_history(session_id, limit=1000)

    return {
        "session": session_meta.to_dict(),
        "messages": [m.to_dict() for m in messages]
    }


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """
    Delete a conversation session and all its messages.

    With the omni backend this cancels the run if it is live and hides it from the list.

    Args:
        session_id: Session ID

    Returns:
        Success status
    """
    if settings.alfred_backend == "omni":
        omni = _require_omni()
        try:
            forgotten = await omni.forget(session_id)
        except Exception as exc:
            raise _http_error_from_bridge(exc) from exc
        if not forgotten:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
        return {"status": "success", "message": f"Session {session_id} deleted"}

    if not session_store:
        raise HTTPException(status_code=503, detail="Session store not available")

    deleted = session_store.delete_session(session_id)

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    return {"status": "success", "message": f"Session {session_id} deleted"}


@app.post("/sessions/{session_id}/continue")
async def continue_session(session_id: str, request: ContinueRequest):
    """
    Load an earlier conversation (J11): start a new run seeded with the old transcript.

    Returns the same shape as ``/execute``; ``session_id`` is the NEW run and
    ``continued_from`` the old one. Omni backend only.
    """
    if settings.alfred_backend != "omni":
        raise HTTPException(status_code=404, detail="Continuing a session needs the omni backend")
    omni = _require_omni()
    try:
        reply = await omni.continue_from(session_id, request.user_input)
    except Exception as exc:
        raise _http_error_from_bridge(exc) from exc
    return await _reply_payload(reply, request.voice_mode)


@app.post("/sessions/{session_id}/asks/{req_id}")
async def answer_session_ask(session_id: str, req_id: str, request: AskAnswerRequest):
    """
    Answer a pending ask directly (J13), for UIs with buttons.

    Returns the same shape as ``/execute``; 409 if ``req_id`` is not the pending ask.
    Omni backend only.
    """
    if settings.alfred_backend != "omni":
        raise HTTPException(status_code=404, detail="Answering an ask needs the omni backend")
    omni = _require_omni()
    try:
        reply = await omni.answer_ask(session_id, req_id, request.decision)
    except Exception as exc:
        raise _http_error_from_bridge(exc, ask=True) from exc
    return await _reply_payload(reply, False)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "ai_server.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload
    )
