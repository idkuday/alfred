"""The omni bridge: Alfred's conversation layer over omni-kernel graph runs (omni-jev plan section 7).

Importing this package does not import ``omni``: the data shapes and the two errors come from
``models`` (no kernel types), and ``AlfredOmni`` is resolved on first access (PEP 562) so that
``from ai_server.omni_bridge import AlfredOmni`` works, yet a deployment on the ``core``
backend, without omni installed, can still import everything else.
"""

from typing import TYPE_CHECKING, Any

from .models import (
    BridgeUnavailable,
    ConversationNotFound,
    ConversationReply,
    ConversationState,
    ConversationSummary,
    Transcript,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .client import AlfredOmni

__all__ = [
    "AlfredOmni",
    "BridgeUnavailable",
    "ConversationNotFound",
    "ConversationReply",
    "ConversationState",
    "ConversationSummary",
    "Transcript",
]


def __getattr__(name: str) -> Any:
    if name == "AlfredOmni":
        from .client import AlfredOmni

        return AlfredOmni
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
