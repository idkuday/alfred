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
from .models import Command, CommandResponse
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
from .alfred_router.qa_handler import GemmaQAHandler
from .alfred_router.tool_registry import list_tools
from .audio.transcriber import Transcriber

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
qa_handler: Optional[GemmaQAHandler] = None
transcriber: Optional[Transcriber] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown."""
    global ha_integration, intent_processor, alfred_router, qa_handler, transcriber
    
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

    # Initialize Alfred Router (Gemma-3 270M)
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

    # Initialize Q/A handler (Gemma-2B/7B, read-only)
    try:
        qa_handler = GemmaQAHandler(
            model=settings.alfred_qa_model,
            temperature=settings.alfred_qa_temperature,
            max_tokens=settings.alfred_qa_max_tokens,
        )
        logger.info(f"Alfred Q/A handler initialized with model: {settings.alfred_qa_model}")
    except Exception as exc:
        logger.error(f"Failed to initialize Q/A handler: {exc}", exc_info=True)
        qa_handler = None

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

    - Accepts raw user input (text).
    - Invokes router once (JSON-only).
    - Dispatches to integrations/Q&A based on validated router decision.
    """
    if not alfred_router:
        raise HTTPException(status_code=503, detail="Router not available")

    tools = list_tools()
    try:
        decision: RouterDecision = alfred_router.route(
            user_input=request.user_input, tools=tools
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if isinstance(decision, CallToolDecision):
        return await _handle_call_tool(decision)

    if isinstance(decision, RouteToQADecision):
        if not qa_handler:
            raise HTTPException(status_code=503, detail="Q/A handler not available")
        answer = await qa_handler.answer(decision.query)
        return {"intent": "route_to_qa", "answer": answer}

    if isinstance(decision, ProposeNewToolDecision):
        # Non-executable proposal only
        return {
            "intent": "propose_new_tool",
            "name": decision.name,
            "description": decision.description,
            "executable": False,
        }

    raise HTTPException(status_code=400, detail="Unsupported router decision")


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "ai_server.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload
    )
