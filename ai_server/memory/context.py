"""
Context providers for building conversation context from session history.
"""
from abc import ABC, abstractmethod
from typing import Protocol
import logging

logger = logging.getLogger(__name__)


class ContextProvider(Protocol):
    """
    Protocol for building conversation context from session history.

    This abstraction allows us to swap between different context-building
    strategies (raw messages, summaries, etc.) without changing router/Q&A code.
    """

    def build_context(self, session_id: str) -> str:
        """
        Build conversation context string for a session.

        Args:
            session_id: Session ID

        Returns:
            Formatted context string to inject into prompts
        """
        ...


class MessageHistoryProvider:
    """
    Context provider that formats recent message history as text.

    This is the default implementation - it returns the last N messages
    formatted as a readable conversation history.
    """

    def __init__(self, session_store, limit: int = 10):
        """
        Initialize message history provider.

        Args:
            session_store: SessionStore instance
            limit: Maximum number of messages to include
        """
        self.session_store = session_store
        self.limit = limit

    def build_context(self, session_id: str) -> str:
        """
        Build context from message history.

        Args:
            session_id: Session ID

        Returns:
            Formatted conversation history, or empty string if no history
        """
        if not session_id:
            return ""

        # Check if session exists
        if not self.session_store.session_exists(session_id):
            logger.warning(f"Session {session_id} not found, returning empty context")
            return ""

        # Get message history
        try:
            messages = self.session_store.get_history(session_id, limit=self.limit)
        except ValueError as e:
            logger.error(f"Error fetching history for session {session_id}: {e}")
            return ""

        # If no messages, return empty
        if not messages:
            return ""

        # Format messages
        lines = []
        for msg in messages:
            role_label = "User" if msg.role == "user" else "Assistant"
            lines.append(f"{role_label}: {msg.content}")

        context = "\n".join(lines)
        logger.debug(f"Built context from {len(messages)} messages for session {session_id}")

        return context


class SummaryProvider:
    """
    Context provider that returns a chat summary instead of full history.

    This is a STUB for future implementation. When we want to use LLM-based
    summarization instead of raw message history, we'll implement this.

    For now, it falls back to MessageHistoryProvider behavior.
    """

    def __init__(self, session_store, limit: int = 10):
        """
        Initialize summary provider.

        Args:
            session_store: SessionStore instance
            limit: Maximum number of messages to summarize
        """
        self.session_store = session_store
        self.limit = limit
        logger.warning(
            "SummaryProvider is a stub - falling back to message history. "
            "Implement summarization logic when needed."
        )

    def build_context(self, session_id: str) -> str:
        """
        Build context from chat summary.

        Currently falls back to message history. Implement summarization logic here.

        Args:
            session_id: Session ID

        Returns:
            Summary of conversation, or empty string if no history
        """
        # TODO: Implement LLM-based summarization
        # For now, fall back to message history
        provider = MessageHistoryProvider(self.session_store, self.limit)
        return provider.build_context(session_id)
