"""
AI Server - Smart Home AI Assistant
Main FastAPI application for processing commands and controlling devices.
"""
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import settings
from .models import Command, CommandResponse, VoiceCommandResponse, ChatMessage, SessionMeta
from .integration.home_assistant import HomeAssistantIntegration
from .plugins import plugin_manager
from .intent_processor import IntentProcessor
from .alfred_router.router import AlfredRouter, decision_requires_intent_processor
from .alfred_router.schemas import (
    RouterDecision,
    CallToolDecision,
    RouteToQADecision,
    ProposeNewToolDecision,
)
from .alfred_router.qa_handler import OllamaQAHandler
from .alfred_router.tool_registry import list_tools
from .audio.transcriber import Transcriber
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
transcriber: Optional[Transcriber] = None
session_store: Optional[SessionStore] = None
context_provider: Optional[MessageHistoryProvider] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown."""
    global ha_integration, intent_processor, alfred_router, qa_handler, transcriber, session_store, context_provider

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

    # Initialize Alfred Router
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

    # Initialize Q/A handler (read-only)
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
    return {
        "status": "healthy" if ha_healthy else "degraded",
        "home_assistant": "connected" if ha_healthy else "disconnected",
        "plugins_loaded": len(plugin_manager.list_integrations())
    }


@app.post("/execute")
async def execute_command(request: ExecuteRequest):
    """
    Execute a request via Alfred Router.

    - Accepts raw user input (text) and optional session_id.
    - If no session_id provided, creates a new session.
    - Injects conversation context before routing.
    - Saves user message and assistant response to session.
    - Invokes router once (JSON-only).
    - Dispatches to integrations/Q&A based on validated router decision.
    """
    if not alfred_router:
        raise HTTPException(status_code=503, detail="Router not available")

    # Session management
    current_session_id = request.session_id
    conversation_context = ""

    if session_store and context_provider:
        # Create session if needed
        if not current_session_id:
            current_session_id = session_store.create_session()
            logger.info(f"Created new session: {current_session_id}")
        else:
            # Verify session exists, create if not
            if not session_store.session_exists(current_session_id):
                logger.warning(f"Session {current_session_id} not found, creating new one")
                current_session_id = session_store.create_session()

        # Build conversation context
        try:
            conversation_context = context_provider.build_context(current_session_id)
        except Exception as exc:
            logger.error(f"Failed to build context: {exc}", exc_info=True)
            conversation_context = ""

    # Route the request (context will be passed to router in future - router agent's task)
    tools = list_tools()
    try:
        decision: RouterDecision = alfred_router.route(
            user_input=request.user_input, tools=tools
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Execute based on decision
    result = None
    assistant_response = ""

    if isinstance(decision, CallToolDecision):
        result = await _handle_call_tool(decision)
        assistant_response = result.message or f"Executed {result.action} on {result.target}"

    elif isinstance(decision, RouteToQADecision):
        if not qa_handler:
            raise HTTPException(status_code=503, detail="Q/A handler not available")
        answer = await qa_handler.answer(decision.query)
        result = {"intent": "route_to_qa", "answer": answer}
        assistant_response = answer

    elif isinstance(decision, ProposeNewToolDecision):
        # Non-executable proposal only
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

    # Add session_id to response
    if isinstance(result, dict):
        result["session_id"] = current_session_id
    elif hasattr(result, 'model_dump'):
        result_dict = result.model_dump()
        result_dict["session_id"] = current_session_id
        return result_dict
    else:
        return {**result.__dict__, "session_id": current_session_id}

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
            user_input=transcript, tools=tools
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
            answer = await qa_handler.answer(decision.query)
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
