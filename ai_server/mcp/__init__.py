"""MCP (Model Context Protocol) servers for Alfred (migration.md Phase 5, rows 5.2/5.3).

Two standalone servers live here, both stdio, both speaking the 2026-07-28
revision via :mod:`ai_server.mcp.jsonrpc`:

* :mod:`ai_server.mcp.home_assistant_server` — wraps
  :class:`ai_server.integration.home_assistant.HomeAssistantIntegration`.
* :mod:`ai_server.mcp.plugins_server` — wraps every plugin
  :class:`~ai_server.integration.base.DeviceIntegration` discovered by
  :class:`ai_server.plugins.PluginManager`.

Neither touches the existing FastAPI pass-through API (``ai_server/main.py``)
or rewrites anything under ``ai_server/plugins/`` — they are additive
surfaces over the same integration objects.
"""
