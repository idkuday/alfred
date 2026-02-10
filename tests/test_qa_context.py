"""
Unit tests for QA handler context injection.

Tests that conversation context is properly injected into QA prompts
when provided, and that QA handler still works without context (backward compatibility).
"""
import pytest
import asyncio
from unittest.mock import patch, Mock, AsyncMock
from ai_server.alfred_router.qa_handler import OllamaQAHandler


@pytest.fixture
def qa_handler():
    """Create a test QA handler instance."""
    return OllamaQAHandler(
        model="qwen2.5:3b",
        temperature=0.1,
        max_tokens=512,
    )


@pytest.fixture
def sample_context():
    """Sample conversation context."""
    return (
        "User: Hello Alfred\n"
        "Assistant: Hello! I'm Alfred, your smart home AI assistant. How can I help you today?\n"
        "User: What can you do?\n"
        "Assistant: I can help you control your smart home devices and answer questions!"
    )


@pytest.mark.asyncio
async def test_answer_with_context(qa_handler, sample_context):
    """Test that context is injected into QA prompt."""
    query = "What did I ask you before?"

    # Mock the LLM response
    mock_llm_response = "You asked me what I can do!"

    # Mock asyncio.to_thread to avoid actual LLM call
    async def mock_to_thread(func, prompt):
        # Capture the prompt for verification
        mock_to_thread.last_prompt = prompt
        return mock_llm_response

    mock_to_thread.last_prompt = None

    with patch('asyncio.to_thread', side_effect=mock_to_thread):
        response = await qa_handler.answer(
            query=query,
            conversation_context=sample_context
        )

        # Verify response is returned
        assert response == mock_llm_response

        # Verify prompt was built
        prompt = mock_to_thread.last_prompt
        assert prompt is not None

        # Verify context appears in prompt
        assert "## Recent Conversation:" in prompt
        assert sample_context in prompt

        # Verify current query header appears
        assert "## Current Query:" in prompt

        # Verify query appears in prompt
        assert query in prompt


@pytest.mark.asyncio
async def test_answer_without_context(qa_handler):
    """Test that QA handler still works when no context is provided (backward compatibility)."""
    query = "What is your name?"

    # Mock the LLM response
    mock_llm_response = "I'm Alfred, your smart home AI assistant."

    # Mock asyncio.to_thread
    async def mock_to_thread(func, prompt):
        mock_to_thread.last_prompt = prompt
        return mock_llm_response

    mock_to_thread.last_prompt = None

    with patch('asyncio.to_thread', side_effect=mock_to_thread):
        response = await qa_handler.answer(query=query)
        # No conversation_context provided

        # Verify response is returned
        assert response == mock_llm_response

        # Verify prompt was built
        prompt = mock_to_thread.last_prompt
        assert prompt is not None

        # Verify NO context section in prompt
        assert "## Recent Conversation:" not in prompt
        assert "## Current Query:" not in prompt

        # Verify query still appears (in old format)
        assert query in prompt
        assert "User query:" in prompt


@pytest.mark.asyncio
async def test_context_appears_after_system_message(qa_handler, sample_context):
    """Verify that context appears after system message but before query."""
    query = "Turn on the lights"

    # Mock the LLM response
    mock_llm_response = "Sure, which lights?"

    # Mock asyncio.to_thread
    async def mock_to_thread(func, prompt):
        mock_to_thread.last_prompt = prompt
        return mock_llm_response

    mock_to_thread.last_prompt = None

    with patch('asyncio.to_thread', side_effect=mock_to_thread):
        await qa_handler.answer(
            query=query,
            conversation_context=sample_context
        )

        # Get the prompt
        prompt = mock_to_thread.last_prompt

        # Find positions of key sections
        system_msg_pos = prompt.find("You are Alfred")
        context_pos = prompt.find("## Recent Conversation:")
        query_pos = prompt.find(query)

        # Verify order: system message -> context -> query
        assert system_msg_pos < context_pos < query_pos

        # Verify context content appears
        assert context_pos < prompt.find(sample_context) < query_pos


@pytest.mark.asyncio
async def test_response_stripped_correctly(qa_handler):
    """Test that response whitespace is stripped."""
    query = "Hello"

    # Mock LLM response with extra whitespace
    mock_llm_response = "  Hi there!  \n\n  "

    async def mock_to_thread(func, prompt):
        return mock_llm_response

    with patch('asyncio.to_thread', side_effect=mock_to_thread):
        response = await qa_handler.answer(query=query)

        # Verify whitespace is stripped
        assert response == "Hi there!"
        assert response == mock_llm_response.strip()


@pytest.mark.asyncio
async def test_empty_context_treated_as_none(qa_handler):
    """Test that empty string context is treated same as None."""
    query = "What's up?"

    # Mock the LLM response
    mock_llm_response = "Not much!"

    async def mock_to_thread(func, prompt):
        mock_to_thread.last_prompt = prompt
        return mock_llm_response

    mock_to_thread.last_prompt = None

    with patch('asyncio.to_thread', side_effect=mock_to_thread):
        # Call with empty string context
        await qa_handler.answer(
            query=query,
            conversation_context=""
        )

        # Get the prompt
        prompt = mock_to_thread.last_prompt

        # Empty string is falsy, so no context section should appear
        assert "## Recent Conversation:" not in prompt
        assert "## Current Query:" not in prompt


@pytest.mark.asyncio
async def test_model_name_in_system_message(qa_handler, sample_context):
    """Test that model name is correctly inserted into system message."""
    query = "What model are you?"

    mock_llm_response = "I'm powered by qwen2.5:3b"

    async def mock_to_thread(func, prompt):
        mock_to_thread.last_prompt = prompt
        return mock_llm_response

    mock_to_thread.last_prompt = None

    with patch('asyncio.to_thread', side_effect=mock_to_thread):
        await qa_handler.answer(query=query, conversation_context=sample_context)

        prompt = mock_to_thread.last_prompt

        # Verify model name appears in prompt
        assert "qwen2.5:3b" in prompt
        assert qa_handler.model_name in prompt
