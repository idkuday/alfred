"""Live bridge test (omni-jev plan 9.1; coordinator-run, skipped unless ALFRED_LIVE=1).

Runs "turn on the bedroom lamp" through the real front-door graph: real daemon, real Jev decide
nodes, Alfred's Home Assistant MCP tools against the mock Home Assistant. No chat model should
run. The bridge starts with ``client_factory=None``, so it finds (or starts) the daemon itself.

The daemon spawns the MCP servers, so the mock's URL reaches the Home Assistant server through
the ``env`` of its entry in the generated, git-ignored ``.omni/mcp.local.json`` (edited after
``start()`` wrote it and before any node session spawns the server; regenerated afterwards).
"""

import json

import pytest

pytest.importorskip("omni")

from ai_server.config import REPO_ROOT, settings  # noqa: E402
from ai_server.mcp.mock_ha import start_mock_ha  # noqa: E402
from ai_server.omni_bridge import AlfredOmni  # noqa: E402
from ai_server.omni_bridge.library import local_mcp_config_path, write_local_mcp_config  # noqa: E402

pytestmark = [pytest.mark.live, pytest.mark.asyncio]


async def test_turn_on_the_bedroom_lamp_end_to_end():
    url, server = start_mock_ha()
    live = settings.model_copy(update={"ha_url": url, "omni_turn_timeout": 90.0})
    bridge = AlfredOmni(REPO_ROOT, live)
    await bridge.start()
    path = local_mcp_config_path(REPO_ROOT)
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        ha = config["mcpServers"]["home_assistant"]
        ha["env"] = {**ha.get("env", {}), "HA_URL": url, "HA_TOKEN": "mock"}
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")

        assert await bridge.healthy()
        first = await bridge.say("turn on the bedroom lamp", None)
        assert first.state == "awaiting_approval", first
        assert "bedroom" in first.answer.lower()
        second = await bridge.say("yes please", first.conversation_id)
        assert second.state == "ended", second
        assert "on" in second.answer.lower()
        assert server.states["light.bedroom_lamp"]["state"] == "on"
    finally:
        await bridge.close()
        write_local_mcp_config(REPO_ROOT)
        server.shutdown()
        server.server_close()
