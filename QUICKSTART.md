# Quick Start Guide

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
