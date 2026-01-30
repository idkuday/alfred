"""
Alfred Router module.

Provides a thin, deterministic routing layer that delegates to integrations
only through validated, structured outputs.
"""

__all__ = ["router", "schemas", "qa_handler", "tool_registry"]


