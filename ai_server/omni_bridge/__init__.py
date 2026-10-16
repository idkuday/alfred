"""The omni bridge: Alfred's conversation layer over omni-kernel graph runs (omni-jev plan section 7).

Importing this package does not import ``omni``: it exports only the data shapes
(``models``). The pure logic modules (``library``, ``speakers``, ``transcript``, ``asks``,
``quiescence``) import ``omni.*`` and are imported by the bridge client (``client.py``, A5,
which will also export ``AlfredOmni`` here) - never by the rest of Alfred.
"""

from .models import ConversationReply, ConversationState

__all__ = ["ConversationReply", "ConversationState"]
