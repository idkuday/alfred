"""
AI Server - Smart Home AI Assistant
Main FastAPI application for processing commands and controlling devices.
"""
import logging
import base64
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .config import settings
from .models import Command, CommandResponse, VoiceCommandResponse, ChatMessage, SessionMeta
from .integration.home_assistant import HomeAssistantIntegration
from .plugins import plugin_manager
from .intent_processor import IntentProcessor
from .alfred_router.router import AlfredRouter, RouterRefusalError, decision_requires_intent_processor
from .alfred_router.schemas import (
    RouterDecision,
    CallToolDecision,
    RouteToQADecision,
    ProposeNewToolDecision,
)
from .alfred_router.qa_handler import OllamaQAHandler
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
alfred_router: Optional[AlfredRouter] = None
qa_handler: Optional[OllamaQAHandler] = None
alfred_core: Optional[AlfredCore] = None
transcriber: Optional[Transcriber] = None
synthesizer: Optional[Synthesizer] = None
session_store: Optional[SessionStore] = None
context_provider: Optional[MessageHistoryProvider] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown."""
    global ha_integration, intent_processor, alfred_router, qa_handler, alfred_core, transcriber, synthesizer, session_store, context_provider

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

    # Initialize routing backend (controlled by ALFRED_MODE)
    logger.info(f"Alfred mode: {settings.alfred_mode!r}")

    if settings.alfred_mode == "core":
        # AlfredCore — single LLM call, handles both conversation and tool dispatch
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
    else:
        # Legacy router + QA handler (default)
        try:
            alfred_router = AlfredRouter(
                model=settings.alfred_router_model,
                prompt_path=settings.alfred_router_prompt_path,
                temperature=settings.alfred_router_temperature,
                max_tokens=settings.alfred_router_max_tokens,
            )
            logger.info(f"Alfred Router initialized with model: {settings.alfred_router_model}")
        except Exception as exc:
            logger.error(f"Failed to initialize Alfred Router: {exc}", exc_info=True)
            alfred_router = None

        try:
            qa_handler = OllamaQAHandler(
                model=settings.alfred_qa_model,
                temperature=settings.alfred_qa_temperature,
                max_tokens=settings.alfred_qa_max_tokens,
            )
            logger.info(f"Alfred Q/A handler initialized with model: {settings.alfred_qa_model}")
        except Exception as exc:
            logger.error(f"Failed to initialize Q/A handler: {exc}", exc_info=True)
            qa_handler = None

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

    # Initialize Session Store
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

    # Intent processor is optional and only runs if the router explicitly asks for it.
    if decision_requires_intent_processor(decision):
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

    # Report which backend is active and its init status
    if settings.alfred_mode == "core":
        backend_status = "ready" if alfred_core else "unavailable"
    else:
        backend_status = "ready" if alfred_router else "unavailable"

    return {
        "status": "healthy" if ha_healthy else "degraded",
        "home_assistant": "connected" if ha_healthy else "disconnected",
        "plugins_loaded": len(plugin_manager.list_integrations()),
        "alfred_mode": settings.alfred_mode,
        "alfred_backend": backend_status,
    }


@app.post("/execute")
async def execute_command(request: ExecuteRequest):
    """
    Execute a user request.

    Routing backend is selected by ALFRED_MODE env var:
      - "router" (default): Alfred Router → RouteToQA / CallTool / ProposeNewTool
      - "core": AlfredCore — single LLM call that returns plain text (conversation)
                or tool JSON (call_tool / propose_new_tool)

    - Accepts raw user input (text) and optional session_id.
    - Creates a new session if none provided.
    - Injects conversation context from session memory before the LLM call.
    - Saves user message and assistant response to session after completion.
    - Optionally synthesizes response to audio when voice_mode=True.
    """
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

    if settings.alfred_mode == "core":
        # ------------------------------------------------------------------ #
        # AlfredCore path — single LLM call, plain text or tool JSON          #
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
            # Plain text = conversational response (the normal Q&A path)
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

    else:
        # ------------------------------------------------------------------ #
        # Legacy Alfred Router path (default when ALFRED_MODE=router)         #
        # ------------------------------------------------------------------ #
        if not alfred_router:
            raise HTTPException(status_code=503, detail="Router not available")

        try:
            decision: RouterDecision = alfred_router.route(
                user_input=request.user_input,
                tools=tools,
                conversation_context=conversation_context if conversation_context else None,
            )
        except RouterRefusalError as exc:
            # LLM safety filter activated — return raw refusal text directly.
            # One-LLM-call principle: the model already responded, don't retry.
            logger.info(f"Router refusal for input: {request.user_input[:100]!r}")
            assistant_response = exc.raw_output

            if session_store and current_session_id:
                try:
                    session_store.save_message(current_session_id, "user", request.user_input)
                    session_store.save_message(current_session_id, "assistant", assistant_response)
                except Exception as save_exc:
                    logger.error(f"Failed to save messages to session: {save_exc}", exc_info=True)

            return {
                "intent": "route_to_qa",
                "answer": assistant_response,
                "session_id": current_session_id,
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        if isinstance(decision, CallToolDecision):
            result = await _handle_call_tool(decision)
            assistant_response = result.message or f"Executed {result.action} on {result.target}"

        elif isinstance(decision, RouteToQADecision):
            if not qa_handler:
                raise HTTPException(status_code=503, detail="Q/A handler not available")
            answer = await qa_handler.answer(
                query=decision.query,
                conversation_context=conversation_context if conversation_context else None,
            )
            result = {"intent": "route_to_qa", "answer": answer}
            assistant_response = answer

        elif isinstance(decision, ProposeNewToolDecision):
            result = {
                "intent": "propose_new_tool",
                "name": decision.name,
                "description": decision.description,
                "executable": False,
            }
            assistant_response = f"I can help you create a new tool: {decision.name}"

        else:
            raise HTTPException(status_code=400, detail="Unsupported router decision")

    # Save messages to session
    if session_store and current_session_id:
        try:
            session_store.save_message(current_session_id, "user", request.user_input)
            session_store.save_message(current_session_id, "assistant", assistant_response)
        except Exception as exc:
            logger.error(f"Failed to save messages to session: {exc}", exc_info=True)

    # Synthesize response to audio if voice_mode is enabled
    audio_base64 = None
    if request.voice_mode and synthesizer:
        try:
            logger.info("Synthesizing response to audio (voice mode enabled)")
            wav_bytes = await synthesizer.synthesize(assistant_response)
            audio_base64 = base64.b64encode(wav_bytes).decode('utf-8')
            logger.debug(f"Synthesized {len(wav_bytes)} bytes of audio")
        except Exception as exc:
            logger.error(f"Failed to synthesize audio: {exc}", exc_info=True)
            # Don't fail the request, just log and continue without audio
            audio_base64 = None
    elif request.voice_mode and not synthesizer:
        logger.warning("Voice mode requested but synthesizer not available")

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


@app.post("/voice-command", response_model=VoiceCommandResponse)
async def voice_command(file: UploadFile = File(...)):
    """
    Process a voice command: transcribe audio, route, and execute.

    Combines transcription + routing + execution in a single call.
    Frontend handles VAD (voice activity detection) and sends complete audio.

    Returns:
        VoiceCommandResponse with transcript, intent, result, and status.
    """
    # Step 1: Transcribe audio
    if not transcriber:
        return VoiceCommandResponse(
            transcript="",
            error="Transcriber not initialized",
            processed=False
        )

    try:
        transcript = await transcriber.transcribe(file.file)
    except Exception as exc:
        logger.error(f"Transcription failed: {exc}", exc_info=True)
        return VoiceCommandResponse(
            transcript="",
            error=f"Transcription failed: {str(exc)}",
            processed=False
        )

    # Step 2: Check for empty transcript
    if not transcript or not transcript.strip():
        return VoiceCommandResponse(
            transcript=transcript or "",
            error=None,
            processed=False
        )

    # Step 3: Route through Alfred Router
    if not alfred_router:
        return VoiceCommandResponse(
            transcript=transcript,
            error="Router not available",
            processed=False
        )

    tools = list_tools()
    try:
        decision: RouterDecision = alfred_router.route(
            user_input=transcript,
            tools=tools,
            conversation_context=None  # TODO: Add session support to voice-command endpoint
        )
    except RouterRefusalError as exc:
        # LLM safety filter — return refusal text as a QA answer
        logger.info(f"Router refusal for voice input: {transcript[:100]!r}")
        return VoiceCommandResponse(
            transcript=transcript,
            intent="route_to_qa",
            result={"answer": exc.raw_output},
            processed=True
        )
    except ValueError as exc:
        logger.error(f"Router failed: {exc}", exc_info=True)
        return VoiceCommandResponse(
            transcript=transcript,
            error=f"Router failed: {str(exc)}",
            processed=False
        )

    # Step 4: Execute based on decision type
    try:
        if isinstance(decision, CallToolDecision):
            result = await _handle_call_tool(decision)
            return VoiceCommandResponse(
                transcript=transcript,
                intent="call_tool",
                result=result.model_dump() if hasattr(result, 'model_dump') else result.dict(),
                processed=True
            )

        if isinstance(decision, RouteToQADecision):
            if not qa_handler:
                return VoiceCommandResponse(
                    transcript=transcript,
                    intent="route_to_qa",
                    error="Q/A handler not available",
                    processed=False
                )
            answer = await qa_handler.answer(
                query=decision.query,
                conversation_context=None  # TODO: Add session support to voice-command endpoint
            )
            return VoiceCommandResponse(
                transcript=transcript,
                intent="route_to_qa",
                result={"answer": answer},
                processed=True
            )

        if isinstance(decision, ProposeNewToolDecision):
            return VoiceCommandResponse(
                transcript=transcript,
                intent="propose_new_tool",
                result={
                    "name": decision.name,
                    "description": decision.description,
                    "executable": False,
                },
                processed=False  # Proposals are not executable
            )

        # Unknown decision type
        return VoiceCommandResponse(
            transcript=transcript,
            error="Unsupported router decision",
            processed=False
        )

    except HTTPException as exc:
        return VoiceCommandResponse(
            transcript=transcript,
            intent=decision.intent if hasattr(decision, 'intent') else None,
            error=exc.detail,
            processed=False
        )
    except Exception as exc:
        logger.error(f"Execution failed: {exc}", exc_info=True)
        return VoiceCommandResponse(
            transcript=transcript,
            intent=decision.intent if hasattr(decision, 'intent') else None,
            error=f"Execution failed: {str(exc)}",
            processed=False
        )


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

    Returns:
        New session_id
    """
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

    Args:
        session_id: Session ID

    Returns:
        Success status
    """
    if not session_store:
        raise HTTPException(status_code=503, detail="Session store not available")

    deleted = session_store.delete_session(session_id)

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    return {"status": "success", "message": f"Session {session_id} deleted"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "ai_server.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload
    )
