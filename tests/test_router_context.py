"""
Unit tests for router context injection.

Tests that conversation context is properly injected into router prompts
when provided, and that router still works without context (backward compatibility).
"""
import pytest
from unittest.mock import patch, Mock, MagicMock
from ai_server.alfred_router.router import AlfredRouter
from ai_server.alfred_router.schemas import RouteToQADecision


@pytest.fixture
def mock_llm():
    """Create a mock LLM."""
    mock = MagicMock()
    mock.invoke = MagicMock()
    return mock


@pytest.fixture
def router(mock_llm):
    """Create a test router instance with mocked LLM."""
    with patch('ai_server.alfred_router.router.OllamaLLM', return_value=mock_llm):
        router = AlfredRouter(
            model="qwen2.5:3b",
            prompt_path="ai_server/alfred_router/prompts/router.txt",
            temperature=0.0,
            max_tokens=512,
        )
    return router


@pytest.fixture
def sample_tools():
    """Sample tools list for testing."""
    return [
        {
            "name": "home_assistant",
            "description": "Control smart home devices via Home Assistant"
        }
    ]


@pytest.fixture
def sample_context():
    """Sample conversation context."""
    return (
        "User: Hello Alfred\n"
        "Assistant: Hello! I'm Alfred, your smart home AI assistant. How can I help you today?\n"
        "User: Can you turn on the lights?\n"
        "Assistant: I'd be happy to help! Which lights would you like me to turn on?"
    )


def test_route_with_context(router, mock_llm, sample_tools, sample_context):
    """Test that context is injected into router prompt."""
    user_input = "The bedroom lights please"

    # Mock the LLM response
    mock_llm_response = '{"intent": "route_to_qa", "query": "The bedroom lights please"}'
    mock_llm.invoke.return_value = mock_llm_response

    decision = router.route(
        user_input=user_input,
        tools=sample_tools,
        conversation_context=sample_context
    )

    # Verify LLM was called
    assert mock_llm.invoke.called

    # Get the prompt that was passed to LLM
    call_args = mock_llm.invoke.call_args
    prompt = call_args[0][0]

    # Verify context appears in prompt
    assert "## Recent Conversation:" in prompt
    assert sample_context in prompt

    # Verify current request header appears
    assert "## Current Request:" in prompt

    # Verify user input appears after context
    assert user_input in prompt

    # Verify decision is valid
    assert isinstance(decision, RouteToQADecision)


def test_route_without_context(router, mock_llm, sample_tools):
    """Test that router still works when no context is provided (backward compatibility)."""
    user_input = "What's the weather?"

    # Mock the LLM response
    mock_llm_response = '{"intent": "route_to_qa", "query": "What\'s the weather?"}'
    mock_llm.invoke.return_value = mock_llm_response

    decision = router.route(
        user_input=user_input,
        tools=sample_tools
        # No conversation_context provided
    )

    # Verify LLM was called
    assert mock_llm.invoke.called

    # Get the prompt that was passed to LLM
    call_args = mock_llm.invoke.call_args
    prompt = call_args[0][0]

    # Verify NO context section in prompt
    assert "## Recent Conversation:" not in prompt
    assert "## Current Request:" not in prompt

    # Verify user input still appears
    assert user_input in prompt

    # Verify decision is valid
    assert isinstance(decision, RouteToQADecision)


def test_context_format_in_prompt(router, mock_llm, sample_tools, sample_context):
    """Verify that context appears in correct position in the prompt."""
    user_input = "Turn them on"

    # Mock the LLM response
    mock_llm_response = '{"intent": "route_to_qa", "query": "Turn them on"}'
    mock_llm.invoke.return_value = mock_llm_response

    router.route(
        user_input=user_input,
        tools=sample_tools,
        conversation_context=sample_context
    )

    # Get the prompt
    prompt = mock_llm.invoke.call_args[0][0]

    # Find positions of key sections
    context_pos = prompt.find("## Recent Conversation:")
    current_request_pos = prompt.find("## Current Request:")
    user_input_pos = prompt.find(user_input)

    # Verify order: context section comes before current request, which comes before user input
    assert context_pos < current_request_pos < user_input_pos

    # Verify context content appears between headers
    assert context_pos < prompt.find(sample_context) < current_request_pos


def test_empty_context_treated_as_none(router, mock_llm, sample_tools):
    """Test that empty string context is treated same as None."""
    user_input = "Hello"

    # Mock the LLM response
    mock_llm_response = '{"intent": "route_to_qa", "query": "Hello"}'
    mock_llm.invoke.return_value = mock_llm_response

    # Call with empty string context
    router.route(
        user_input=user_input,
        tools=sample_tools,
        conversation_context=""
    )

    # Get the prompt
    prompt = mock_llm.invoke.call_args[0][0]

    # Empty string is falsy, so no context section should appear
    # (This matches the logic: if conversation_context:)
    assert "## Recent Conversation:" not in prompt
    assert "## Current Request:" not in prompt
