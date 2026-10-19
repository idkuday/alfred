# Quick Start Guide

## Running on Omni (default)

Alfred runs its conversations on omni-kernel by default (`ALFRED_BACKEND=omni`). Details, and the graphs node by node, are in [docs/omni.md](docs/omni.md) and [docs/graphs.md](docs/graphs.md).

1. **Install omni-kernel into Alfred's venv** (from its main checkout, on `feat/model-kinds`):
   ```bash
   C:\Users\udayr\Documents\Projects\alfred\.venv\Scripts\python.exe -m pip install -e C:\Users\udayr\Documents\Projects\omni-kernel
   ```
   `prompt_toolkit` (the kernel's `[cli]` extra) must also be installed in that venv, so the bridge can start the daemon.
2. **Check the install** prints the kernel's main checkout path and protocol `8`. Stop any daemon left from before with `omni daemon stop`:
   ```bash
   C:\Users\udayr\Documents\Projects\alfred\.venv\Scripts\python.exe -c "import omni, omni.codec.client, omni.graph.host; from omni.kernel.api import PROTOCOL_VERSION; print(omni.__file__, PROTOCOL_VERSION)"
   ```
3. **One-time Jev key step** (you create this file; agents may not): `C:\Users\udayr\.omni\providers\typesafe\config.yml` with one line, `api_key_file: <path to jev.env>`. It holds the path to the key file, never the key. Alternative: set `TYPESAFE_API_KEY` in the daemon's environment. Until one of these is in place, the Jev decide nodes fail and each graph takes its LLM fallback.
4. **Home Assistant** (optional): set `HA_URL` and `HA_TOKEN` in `.env`, as in the older steps below.
5. **Start the server**: `python run_server.py`. The bridge starts the omni daemon on demand.
6. **Smoke test** (mock Home Assistant, no real house, no model call), from the repo root:
   ```bash
   C:\Users\udayr\Documents\Projects\alfred\.venv\Scripts\python.exe -m ai_server.omni_bridge.smoke --dry-run
   ```

The sections below this one are the `core` path (`ALFRED_BACKEND=core`, AlfredCore on Ollama), kept until the user retires it.

## Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com) installed and running

## Setup (5 minutes)

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Pull the default LLM model**:
   ```bash
   ollama pull qwen2.5:3b
   ```

3. **Configure Home Assistant** (optional):
   Create a `.env` file in the project root:
   ```env
   HA_URL=http://localhost:8123
   HA_TOKEN=your_long_lived_access_token
   ```

4. **Start the server**:
   ```bash
   python run_server.py
   ```
   Or with auto-reload:
   ```bash
   uvicorn ai_server.main:app --reload --host 0.0.0.0 --port 8000
   ```

## Test the API

### Health check
```bash
curl http://localhost:8000/health
```

### Send a command (natural language)
```bash
curl -X POST http://localhost:8000/execute \
  -H "Content-Type: application/json" \
  -d '{"user_input": "turn on the bedroom lamp"}'
```

### Ask a question
```bash
curl -X POST http://localhost:8000/execute \
  -H "Content-Type: application/json" \
  -d '{"user_input": "what can you do?"}'
```

### Multi-turn conversation
```bash
# First message -- creates a session automatically
curl -X POST http://localhost:8000/execute \
  -H "Content-Type: application/json" \
  -d '{"user_input": "Hello Alfred"}'
# Response includes "session_id": "abc-123-..."

# Follow-up -- pass session_id for context
curl -X POST http://localhost:8000/execute \
  -H "Content-Type: application/json" \
  -d '{"user_input": "What did I just say?", "session_id": "abc-123-..."}'
```

### Transcribe audio
```bash
curl -X POST http://localhost:8000/transcribe \
  -F "file=@audio.wav"
```

### Text-to-speech
```bash
curl -X POST http://localhost:8000/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello, I am Alfred."}' \
  --output speech.wav
```

### List devices
```bash
curl http://localhost:8000/devices
```

## Optional Configuration

Add these to your `.env` to customize:

```env
# Routing mode (default: "router", try "core" for unified brain)
ALFRED_MODE=router

# LLM models
ALFRED_ROUTER_MODEL=qwen2.5:3b
ALFRED_CORE_MODEL=qwen2.5:3b

# Speech-to-Text
WHISPER_MODEL=tiny.en          # tiny.en, base, small, medium, large
WHISPER_DEVICE=cpu             # cpu or cuda

# Text-to-Speech (disable with TTS_ENABLED=false)
TTS_ENABLED=true
PIPER_VOICE_MODEL=ai_server/audio/voices/en_GB-alan-medium.onnx

# Session memory
SESSION_TIMEOUT_MINUTES=30
SESSION_HISTORY_LIMIT=10
```

## Next Steps

1. **Try AlfredCore**: Set `ALFRED_MODE=core` in `.env` for the unified single-call brain
2. **Connect the Chat UI**: See [alfred-ui](https://github.com/idkuday/alfred-ui) for the React frontend
3. **Add device mappings** in `.env` for friendly device names
4. **Create custom plugins** in `ai_server/plugins/` (auto-loaded on startup)

## Troubleshooting

- **Ollama not running?** Start it: `ollama serve`
- **Model not found?** Pull it: `ollama pull qwen2.5:3b`
- **HA not connecting?** Check `HA_URL` and `HA_TOKEN` in `.env`
- **Import errors?** Run from project root: `python run_server.py`
- **TTS not working?** Check voice model exists at the configured path
- **Debug LLM output**: Check `last_router_output.txt` or `last_core_output.txt`
