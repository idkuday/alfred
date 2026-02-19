# Alfred - Local-First Smart Home AI Assistant

A modular, privacy-focused AI assistant for smart home automation. Alfred runs entirely on your local network -- all LLM inference, speech-to-text, and text-to-speech happen on-device via [Ollama](https://ollama.com), [Faster Whisper](https://github.com/SYSTRAN/faster-whisper), and [Piper TTS](https://github.com/rhasspy/piper). No cloud dependencies.

## Architecture

```
User (Text / Voice)
        |
        v
 +--------------+      +------------------+
 | React Chat UI| ---> |  FastAPI Server   |
 |  (alfred-ui) |      |    (ai_server)    |
 +--------------+      +--------+---------+
                                |
         +----------------------+----------------------+
         |                      |                      |
  AlfredCore / Router    Session Memory        STT / TTS
  (Ollama LLM)          (SQLite)            Whisper + Piper
         |
    +----+----+
    |         |
call_tool  conversation
    |         |
    v         v
Integration   Direct
   Layer      Response
    |
    +------+------+
    |             |
Home Assistant  Plugins
  (local)      (auto-loaded)
```

### Two Routing Modes (feature flag)

Alfred supports two routing backends, controlled by `ALFRED_MODE`:

| Mode | Env Value | Description |
|------|-----------|-------------|
| **AlfredCore** (new) | `ALFRED_MODE=core` | Single LLM call that both decides and responds. Plain text = conversation, JSON = tool call. |
| **Legacy Router + QA** | `ALFRED_MODE=router` (default) | Two-step: Router decides (call_tool / route_to_qa / propose_new_tool), then QA handler answers questions separately. |

## Features

- **Local-First & Private** -- All processing on your hardware, no cloud APIs
- **AlfredCore** -- Unified LLM brain (single call for both routing and conversation)
- **Semantic Routing** -- Deterministic router with strict Pydantic validation
- **Home Assistant Integration** -- Control lights, switches, fans, climate, covers
- **Plugin System** -- Auto-loaded plugins extend Alfred's capabilities
- **Speech-to-Text** -- Offline transcription via Faster Whisper
- **Text-to-Speech** -- Offline voice output via Piper TTS
- **Session Memory** -- Multi-turn conversations with SQLite-backed history
- **The Forge** (experimental) -- AI that generates new plugins automatically
- **React Chat UI** -- Companion web app ([alfred-ui](https://github.com/idkuday/alfred-ui))

## Prerequisites

- **Python 3.10+**
- **[Ollama](https://ollama.com)** -- local LLM runtime
- **Home Assistant** (optional) -- for smart home device control

## Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/idkuday/alfred.git
   cd alfred
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Install and start Ollama**, then pull the default model:
   ```bash
   ollama pull qwen2.5:3b
   ```

4. **Configure environment** -- create a `.env` file in the project root:
   ```env
   # Home Assistant (optional)
   HA_URL=http://localhost:8123
   HA_TOKEN=your_long_lived_access_token

   # Routing mode ("router" or "core")
   ALFRED_MODE=router

   # LLM model (used by both router and core modes)
   ALFRED_ROUTER_MODEL=qwen2.5:3b
   ALFRED_CORE_MODEL=qwen2.5:3b

   # Speech-to-Text
   WHISPER_MODEL=tiny.en
   WHISPER_DEVICE=cpu

   # Text-to-Speech (set to false to disable)
   TTS_ENABLED=true
   PIPER_VOICE_MODEL=ai_server/audio/voices/en_GB-alan-medium.onnx
   ```

5. **Start the server**:
   ```bash
   python run_server.py
   ```

## Usage

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Root -- version info |
| `GET` | `/health` | Health check (HA connection, backend status) |
| `POST` | `/execute` | Main endpoint -- route and execute user input |
| `POST` | `/transcribe` | Upload audio file, get transcription |
| `POST` | `/synthesize` | Text-to-speech (returns WAV audio) |
| `GET` | `/devices` | List all Home Assistant devices |
| `GET` | `/devices/{entity_id}` | Get specific device info |
| `GET` | `/sessions` | List conversation sessions |
| `POST` | `/sessions` | Create a new session |
| `GET` | `/sessions/{session_id}` | Get session with message history |
| `DELETE` | `/sessions/{session_id}` | Delete a session |

### Execute a Command

```bash
# Natural language input -- Alfred routes automatically
curl -X POST http://localhost:8000/execute \
  -H "Content-Type: application/json" \
  -d '{"user_input": "turn on the bedroom lamp"}'

# With session tracking (multi-turn conversation)
curl -X POST http://localhost:8000/execute \
  -H "Content-Type: application/json" \
  -d '{"user_input": "what did I just ask?", "session_id": "<session_id>"}'

# With voice mode (returns audio_base64 in response)
curl -X POST http://localhost:8000/execute \
  -H "Content-Type: application/json" \
  -d '{"user_input": "tell me a joke", "voice_mode": true}'
```

### Transcribe Audio

```bash
curl -X POST http://localhost:8000/transcribe \
  -F "file=@audio.wav"
```

### Synthesize Speech

```bash
curl -X POST http://localhost:8000/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello, I am Alfred."}' \
  --output speech.wav
```

### List Devices

```bash
curl http://localhost:8000/devices
```

## Project Structure

```
alfred/
├── ai_server/
│   ├── main.py                      # FastAPI app, endpoints, lifespan
│   ├── config.py                    # Pydantic settings (reads .env)
│   ├── models.py                    # Data models (Command, CommandResponse, etc.)
│   ├── intent_processor.py          # Optional NLP helper
│   ├── core/                        # AlfredCore -- unified LLM brain
│   │   ├── core.py                  #   AlfredCore class
│   │   └── prompts/
│   │       ├── core.txt             #   Unified prompt (personality + tools)
│   │       └── retry.txt            #   JSON fix-it retry prompt
│   ├── alfred_router/               # Legacy semantic router
│   │   ├── router.py                #   AlfredRouter class
│   │   ├── schemas.py               #   Decision types (Pydantic)
│   │   ├── tool_registry.py         #   Available tools list
│   │   ├── qa_handler.py            #   Q/A handler (separate LLM call)
│   │   └── prompts/
│   │       └── router.txt           #   Router prompt template
│   ├── audio/
│   │   ├── transcriber.py           # Faster Whisper STT wrapper
│   │   └── synthesizer.py           # Piper TTS wrapper
│   ├── memory/
│   │   ├── store.py                 # SessionStore (SQLite backend)
│   │   └── context.py               # ContextProvider abstraction
│   ├── integration/
│   │   ├── base.py                  # DeviceIntegration base class
│   │   └── home_assistant.py        # Home Assistant API client
│   ├── plugins/
│   │   ├── __init__.py              # PluginManager (auto-loading)
│   │   ├── example_plugin.py        # Template plugin
│   │   └── math_plugin.py           # Forge-generated example
│   └── forge/                       # Self-improving AI (experimental)
│       ├── graph.py                 #   LangGraph workflow
│       ├── agents.py                #   Agent nodes (researcher, coder, etc.)
│       ├── state.py                 #   ForgeState TypedDict
│       └── prompts.py               #   Agent prompt templates
├── tests/                           # pytest test suite
├── run_server.py                    # Server launcher
├── requirements.txt                 # Python dependencies
└── .env                             # Environment config (not committed)
```

## Creating Plugins

Create a file in `ai_server/plugins/` that extends `DeviceIntegration`:

```python
from ai_server.integration.base import DeviceIntegration
from ai_server.models import Command, CommandResponse, DeviceInfo

class MyCustomIntegration(DeviceIntegration):
    def __init__(self):
        super().__init__()  # Required -- sets the name attribute

    async def execute_command(self, command: Command) -> CommandResponse:
        # Your implementation
        pass

    async def get_device_info(self, entity_id: str) -> DeviceInfo:
        pass

    async def discover_devices(self) -> list[DeviceInfo]:
        pass

    async def health_check(self) -> bool:
        return True
```

The plugin is auto-loaded on server startup and automatically appears in the tool registry.

## Configuration

All settings are managed via environment variables (`.env` file) and `ai_server/config.py`.

| Variable | Default | Description |
|----------|---------|-------------|
| `HA_URL` | `http://localhost:8123` | Home Assistant URL |
| `HA_TOKEN` | `None` | HA long-lived access token |
| `ALFRED_MODE` | `router` | `"router"` or `"core"` |
| `ALFRED_ROUTER_MODEL` | `qwen2.5:3b` | LLM model for legacy router |
| `ALFRED_QA_MODEL` | `qwen2.5:3b` | LLM model for Q/A handler |
| `ALFRED_CORE_MODEL` | `qwen2.5:3b` | LLM model for AlfredCore |
| `WHISPER_MODEL` | `tiny.en` | Whisper model size |
| `WHISPER_DEVICE` | `cpu` | `"cpu"` or `"cuda"` |
| `TTS_ENABLED` | `true` | Enable/disable Piper TTS |
| `PIPER_VOICE_MODEL` | `en_GB-alan-medium.onnx` | Piper voice model path |
| `SESSION_DB_PATH` | `alfred_sessions.db` | SQLite DB for sessions |
| `SESSION_TIMEOUT_MINUTES` | `30` | Idle session expiry |
| `SESSION_HISTORY_LIMIT` | `10` | Messages injected as context |
| `LOG_LEVEL` | `INFO` | Logging level |

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test files
pytest tests/test_core.py -v
pytest tests/test_session_store.py -v
```

## Development

```bash
# Start with auto-reload
uvicorn ai_server.main:app --reload --host 0.0.0.0 --port 8000

# Debug router output (check raw LLM response)
cat last_router_output.txt    # Legacy router
cat last_core_output.txt      # AlfredCore

# Pull a different model
ollama pull deepseek-r1:7b
```

## License

MIT
