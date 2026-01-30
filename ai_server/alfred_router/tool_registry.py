"""
Tool registry for Alfred Router.

Provides static descriptions for available tools so the router can choose
deterministically. This does not execute tools; execution is handled elsewhere.
"""
from typing import List, Dict
from ..plugins import plugin_manager


def list_tools() -> List[Dict[str, str]]:
    """Return available tools/integrations with brief descriptions."""
    tools: List[Dict[str, str]] = [
        {
            "name": "home_assistant",
            "description": "Control devices via Home Assistant integration (trusted).",
        },
        {
            "name": "intent_processor",
            "description": "Optional helper to normalize NL commands; not authoritative.",
        },
    ]

    # Include loaded integration plugins as selectable tools
    for integration_name in plugin_manager.list_integrations():
        tools.append(
            {
                "name": integration_name,
                "description": f"Plugin integration '{integration_name}'",
            }
        )

    return tools



