# Smart Home AI Assistant

A modular, local-first AI assistant for smart home automation. This system provides a privacy-focused solution for controlling smart home devices via voice or text commands.

## Architecture

```
┌─────────────────┐
│  AI Assistant   │  (Voice/Text Input, NLP)
│     (Client)    │
└────────┬────────┘
         │ HTTP POST
         ▼
┌─────────────────┐
│   AI Server     │  (FastAPI - Command Processing)
│  (FastAPI)      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Integration     │  (Device Abstraction Layer)
│     Layer       │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Home Assistant  │  (Device Management)
│   (Local HA)    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Smart Devices   │  (TP-Link Tapo, Lights, etc.)
└─────────────────┘
```

## Features

- **Modular Architecture**: Easy to extend with new device integrations
- **Privacy-First**: All processing happens locally
- **Intent Processing**: Natural language command understanding
- **Plugin System**: Extensible plugin architecture for custom integrations
- **Home Assistant Integration**: Native support for Home Assistant API
- **Self-Improving AI** (Future): AI can suggest new plugins via Git PRs

## Installation

1. **Clone the repository**:
   ```bash
   cd "Home Ai"
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment** (optional):
   Create a `.env` file in the `ai_server` directory:
   ```env
   HA_URL=http://localhost:8123
   HA_TOKEN=your_home_assistant_token
   LOG_LEVEL=INFO
   ```

## Usage

### Start the AI Server

```bash
cd ai_server
python -m ai_server.main
```

Or using uvicorn directly:
```bash
uvicorn ai_server.main:app --reload --host 0.0.0.0 --port 8000
```

### API Endpoints

#### Execute Command
```bash
POST /execute
Content-Type: application/json

{
  "action": "turn_on",
  "target": "bedroom_lamp",
  "room": "bedroom",
  "parameters": {"brightness": 128}
}
```

#### List Devices
```bash
GET /devices
```

#### Get Device Info
```bash
GET /devices/{entity_id}
```

#### Health Check
```bash
GET /health
```

## Project Structure

```
ai_server/
├── __init__.py
├── main.py                 # FastAPI application
├── config.py              # Configuration management
├── models.py              # Pydantic models
├── intent_processor.py    # Intent processing and normalization
├── integration/
│   ├── __init__.py
│   ├── base.py            # Base integration class
│   └── home_assistant.py  # Home Assistant integration
└── plugins/
    ├── __init__.py        # Plugin manager
    └── .gitkeep          # Place custom plugins here
```

## Creating Custom Plugins

Create a new file in `ai_server/plugins/` that inherits from `DeviceIntegration`:

```python
from ai_server.integration.base import DeviceIntegration
from ai_server.models import Command, CommandResponse, DeviceInfo

class MyCustomIntegration(DeviceIntegration):
    async def execute_command(self, command: Command) -> CommandResponse:
        # Your implementation
        pass
    
    async def get_device_info(self, entity_id: str) -> DeviceInfo:
        # Your implementation
        pass
    
    async def discover_devices(self) -> List[DeviceInfo]:
        # Your implementation
        pass
    
    async def health_check(self) -> bool:
        # Your implementation
        pass
```

The plugin will be automatically loaded on server startup.

## Configuration

Configuration is managed via:
1. Environment variables (`.env` file)
2. `config.py` defaults
3. Pydantic settings

Key settings:
- `HA_URL`: Home Assistant URL (default: http://localhost:8123)
- `HA_TOKEN`: Home Assistant long-lived access token
- `LOG_LEVEL`: Logging level (INFO, DEBUG, etc.)
- `PLUGINS_DIR`: Directory for plugins (default: plugins)

## Intent Processing

The intent processor normalizes natural language commands:

- "turn on bedroom lamp" → `{"action": "turn_on", "target": "bedroom_lamp"}`
- "set brightness to 50%" → `{"action": "set_brightness", "parameters": {"brightness": 128}}`
- "what's the status of kitchen light?" → `{"action": "get_status", "target": "kitchen_light"}`

## Future Enhancements

- [ ] Self-improving AI: Generate plugins via Git PRs
- [ ] Voice input support (Vosk/Whisper integration)
- [ ] Advanced NLP with transformers
- [ ] Dashboard/UI (Streamlit or React)
- [ ] Device discovery and auto-mapping
- [ ] Cloud sync (optional)
- [ ] Multi-room scene support

## Development

### Running Tests
```bash
# Add tests as needed
pytest
```

### Code Style
Follow PEP 8 and use type hints throughout.

## License

[Your License Here]

## Contributing

[Your Contributing Guidelines Here]



