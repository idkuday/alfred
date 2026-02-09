"""
Unit tests for ContextProvider implementations.
"""
import pytest
from ai_server.memory import SessionStore, MessageHistoryProvider, SummaryProvider


@pytest.fixture
def store(in_memory_db):
    """Provide a fresh in-memory SessionStore for each test."""
    return SessionStore(db_path=in_memory_db)


@pytest.fixture
def provider(store):
    """Provide a MessageHistoryProvider."""
    return MessageHistoryProvider(session_store=store, limit=10)


def test_message_history_provider_formats_correctly(store, provider):
    """Test that MessageHistoryProvider formats messages correctly."""
    session_id = store.create_session()

    # Add some messages
    store.save_message(session_id, "user", "Hello Alfred")
    store.save_message(session_id, "assistant", "Hello! How can I help you?")
    store.save_message(session_id, "user", "Turn on the lights")

    # Build context
    context = provider.build_context(session_id)

    # Should be formatted as conversation
    assert "User: Hello Alfred" in context
    assert "Assistant: Hello! How can I help you?" in context
    assert "User: Turn on the lights" in context

    # Should be in order (newline separated)
    lines = context.split("\n")
    assert len(lines) == 3
    assert lines[0].startswith("User:")
    assert lines[1].startswith("Assistant:")
    assert lines[2].startswith("User:")


def test_message_history_provider_empty_session(store, provider):
    """Test that provider returns empty string for empty session."""
    session_id = store.create_session()

    # No messages yet
    context = provider.build_context(session_id)

    assert context == ""


def test_message_history_provider_respects_limit(store):
    """Test that provider only includes last N messages."""
    # Create provider with limit of 3
    provider = MessageHistoryProvider(session_store=store, limit=3)

    session_id = store.create_session()

    # Add 5 messages
    for i in range(5):
        store.save_message(session_id, "user", f"Message {i}")

    # Build context
    context = provider.build_context(session_id)

    # Should only have last 3 messages
    lines = context.split("\n")
    assert len(lines) == 3

    # Should be messages 2, 3, 4
    assert "Message 2" in lines[0]
    assert "Message 3" in lines[1]
    assert "Message 4" in lines[2]


def test_message_history_provider_nonexistent_session(store, provider):
    """Test that provider returns empty string for nonexistent session."""
    fake_session = "00000000-0000-0000-0000-000000000000"

    context = provider.build_context(fake_session)

    assert context == ""


def test_message_history_provider_empty_session_id(store, provider):
    """Test that provider handles empty session_id gracefully."""
    context = provider.build_context("")

    assert context == ""


def test_summary_provider_fallback(store):
    """Test that SummaryProvider falls back to message history (stub)."""
    provider = SummaryProvider(session_store=store, limit=10)

    session_id = store.create_session()
    store.save_message(session_id, "user", "Test message")

    # Should fall back to message history for now
    context = provider.build_context(session_id)

    assert "User: Test message" in context


def test_message_history_multiline_content(store, provider):
    """Test handling of messages with newlines."""
    session_id = store.create_session()

    multiline_msg = "This is a message\nwith multiple lines\nof text"
    store.save_message(session_id, "user", multiline_msg)

    context = provider.build_context(session_id)

    # Should preserve newlines in content
    assert multiline_msg in context
