# Quick Start Guide

## Setup (5 minutes)

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Home Assistant** (if using):
   - Get a long-lived access token from Home Assistant
   - Create `.env` file:
     ```env
     HA_URL=http://localhost:8123
     HA_TOKEN=your_token_here
     ```

3. **Start the server**:
   ```bash
   python run_server.py
   ```
   
   Or:
   ```bash
   cd ai_server
   python -m ai_server.main
   ```

## Test the API

### Using curl:
```bash
# Health check
curl http://localhost:8000/health

# Execute a command
curl -X POST http://localhost:8000/execute \
  -H "Content-Type: application/json" \
  -d '{"action": "turn_on", "target": "bedroom_lamp"}'

# List devices
curl http://localhost:8000/devices
```

### Using Python:
```python
import requests

# Execute command
response = requests.post(
    "http://localhost:8000/execute",
    json={
        "action": "turn_on",
        "target": "bedroom_lamp",
        "room": "bedroom"
    }
)
print(response.json())
```

## Example Commands

The intent processor understands natural language:

```json
{"action": "turn on", "target": "bedroom lamp"}
{"action": "set brightness to 50%", "target": "kitchen light"}
{"action": "get status", "target": "living room light"}
{"action": "turn off", "target": "bedroom_lamp", "room": "bedroom"}
```

## Next Steps

1. **Connect your AI Assistant client** to POST commands to `/execute`
2. **Add device mappings** in config for friendly names
3. **Create custom plugins** in `ai_server/plugins/` for non-HA devices
4. **Extend intent processor** for your specific use cases

## Troubleshooting

- **Home Assistant not connecting?** Check `HA_URL` and `HA_TOKEN` in `.env`
- **Import errors?** Make sure you're running from project root or have installed the package
- **Devices not found?** Verify entity_ids match your Home Assistant setup



