"""
Unit tests for AlfredCore.

Tests cover:
- Plain text output → returned as conversational response
- Valid call_tool JSON → parsed and returned as CallToolDecision
- Valid propose_new_tool JSON → parsed and returned as ProposeNewToolDecision
- Malformed JSON (starts with '{') → triggers one retry
- Retry success → returns fixed JSON as decision
- Retry failure → raises ValueError
- Retry returning plain text → raises ValueError (we needed JSON)
- Truncated JSON repaired by appending '}' → succeeds without retry
- Off-script JSON with conversational fallback key → treated as plain text
- With conversation context → context appears in rendered prompt
- Without conversation context → no context section in prompt
- Empty string context → treated same as None (no context section)
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from ai_server.core.core import AlfredCore
from ai_server.alfred_router.schemas import CallToolDecision, ProposeNewToolDecision


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_llm():
    """Create a mock OllamaLLM."""
    mock = MagicMock()
    mock.invoke = MagicMock()
    return mock


@pytest.fixture
def core(mock_llm):
    """
    Create an AlfredCore instance with mocked LLM.

    Uses non-existent prompt paths so the class falls back to built-in defaults.
    This avoids file-system dependencies in tests.
    """
    with patch("ai_server.core.core.OllamaLLM", return_value=mock_llm):
        instance = AlfredCore(
            model="qwen2.5:3b",
            prompt_path="ai_server/core/prompts/core.txt",
            retry_prompt_path="ai_server/core/prompts/retry.txt",
            temperature=0.0,
            max_tokens=2048,
        )
    return instance


@pytest.fixture
def sample_tools():
    """Minimal tool list for testing."""
    return [
        {
            "name": "home_assistant",
            "description": "Control smart home devices via Home Assistant",
        }
    ]


@pytest.fixture
def sample_context():
    """Sample conversation context string."""
    return (
        "User: Hello Alfred\n"
        "Assistant: Hello! How can I help you today?\n"
        "User: Can you turn on the lights?\n"
        "Assistant: Sure, which lights?"
    )


# ---------------------------------------------------------------------------
# _parse_output unit tests (synchronous, test the parser directly)
# ---------------------------------------------------------------------------

class TestParseOutput:
    """Direct unit tests for the _parse_output method."""

    def test_plain_text_returns_string(self, core):
        """Plain text output is returned as-is (normal conversation path)."""
        result = core._parse_output("Hello! I'm Alfred, what can I do for you?")
        assert result == "Hello! I'm Alfred, what can I do for you?"

    def test_plain_text_stripped(self, core):
        """Leading/trailing whitespace is stripped from the output first."""
        result = core._parse_output("  Hello!  ")
        assert result == "Hello!"

    def test_valid_call_tool_json(self, core):
        """Valid call_tool JSON returns a CallToolDecision."""
        raw = json.dumps({
            "intent": "call_tool",
            "tool": "home_assistant",
            "parameters": {"action": "turn_on", "target": "light", "room": "bedroom"},
        })
        result = core._parse_output(raw)
        assert isinstance(result, CallToolDecision)
        assert result.intent == "call_tool"
        assert result.tool == "home_assistant"
        assert result.parameters["action"] == "turn_on"

    def test_valid_propose_new_tool_json(self, core):
        """Valid propose_new_tool JSON returns a ProposeNewToolDecision."""
        raw = json.dumps({
            "intent": "propose_new_tool",
            "name": "garage_control",
            "description": "Control the garage door",
        })
        result = core._parse_output(raw)
        assert isinstance(result, ProposeNewToolDecision)
        assert result.intent == "propose_new_tool"
        assert result.name == "garage_control"

    def test_truncated_json_repaired(self, core):
        """Truncated JSON (missing closing brace) is repaired automatically."""
        # Missing the closing }
        raw = '{"intent": "call_tool", "tool": "home_assistant", "parameters": {"action": "turn_on", "target": "light"}'
        result = core._parse_output(raw)
        assert isinstance(result, CallToolDecision)

    def test_malformed_json_returns_none(self, core):
        """Genuinely malformed JSON (starts with '{') returns None to signal retry."""
        raw = '{"intent": "call_to'  # truncated mid-string, can't repair
        result = core._parse_output(raw)
        assert result is None

    def test_off_script_json_with_response_key(self, core):
        """JSON with a 'response' key (model went off-script) is treated as conversation."""
        raw = json.dumps({"response": "I'm Alfred, your assistant!"})
        result = core._parse_output(raw)
        assert result == "I'm Alfred, your assistant!"

    def test_off_script_json_with_answer_key(self, core):
        """JSON with an 'answer' key is treated as conversational response."""
        raw = json.dumps({"answer": "The thermostat is set to 72 degrees."})
        result = core._parse_output(raw)
        assert result == "The thermostat is set to 72 degrees."

    def test_off_script_json_with_message_key(self, core):
        """JSON with a 'message' key is treated as conversational response."""
        raw = json.dumps({"message": "Done!"})
        result = core._parse_output(raw)
        assert result == "Done!"

    def test_json_unknown_schema_raises_value_error(self, core):
        """Valid JSON that fails schema validation and has no fallback raises ValueError."""
        raw = json.dumps({"intent": "unknown_intent", "data": "something"})
        with pytest.raises(ValueError, match="failed schema validation"):
            core._parse_output(raw)


# ---------------------------------------------------------------------------
# _render_prompt unit tests (synchronous)
# ---------------------------------------------------------------------------

class TestRenderPrompt:
    """Tests for prompt rendering and context injection."""

    def test_tools_injected_into_prompt(self, core, sample_tools):
        """Tool descriptions appear in the rendered prompt."""
        prompt = core._render_prompt("test", sample_tools)
        assert "home_assistant" in prompt
        assert "Control smart home devices" in prompt

    def test_user_input_in_prompt(self, core, sample_tools):
        """User input appears in the rendered prompt."""
        prompt = core._render_prompt("Turn on the light", sample_tools)
        assert "Turn on the light" in prompt

    def test_context_injected_when_provided(self, core, sample_tools, sample_context):
        """Conversation context appears in prompt with correct headers."""
        prompt = core._render_prompt("What did I say?", sample_tools, sample_context)
        assert "## Recent Conversation:" in prompt
        assert sample_context in prompt
        assert "## Current Request:" in prompt
        assert "What did I say?" in prompt

    def test_no_context_section_without_context(self, core, sample_tools):
        """No context headers when conversation_context is not provided."""
        prompt = core._render_prompt("Hello", sample_tools)
        assert "## Recent Conversation:" not in prompt
        assert "## Current Request:" not in prompt

    def test_empty_string_context_omitted(self, core, sample_tools):
        """Empty string context is treated the same as None (falsy)."""
        prompt = core._render_prompt("Hello", sample_tools, conversation_context="")
        assert "## Recent Conversation:" not in prompt
        assert "## Current Request:" not in prompt

    def test_context_order_in_prompt(self, core, sample_tools, sample_context):
        """Context section appears before current request, which appears before user input."""
        user_input = "Turn them on"
        prompt = core._render_prompt(user_input, sample_tools, sample_context)

        context_pos = prompt.find("## Recent Conversation:")
        request_pos = prompt.find("## Current Request:")
        input_pos = prompt.rfind(user_input)

        assert context_pos < request_pos < input_pos

    def test_model_name_in_prompt(self, core, sample_tools):
        """Model name is substituted into the prompt."""
        prompt = core._render_prompt("Hello", sample_tools)
        assert "qwen2.5:3b" in prompt


# ---------------------------------------------------------------------------
# process() async tests (test the full async pipeline with mocked LLM)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_plain_text_response(core, mock_llm, sample_tools):
    """Plain text LLM output is returned as a string (conversational path)."""
    plain_response = "I'm Alfred, your unfiltered local AI assistant!"

    async def mock_to_thread(func, prompt):
        return plain_response

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await core.process(user_input="Who are you?", tools=sample_tools)

    assert result == plain_response


@pytest.mark.asyncio
async def test_process_call_tool_json(core, mock_llm, sample_tools):
    """Valid call_tool JSON output returns a CallToolDecision."""
    tool_response = json.dumps({
        "intent": "call_tool",
        "tool": "home_assistant",
        "parameters": {"action": "turn_on", "target": "light", "room": "bedroom"},
    })

    async def mock_to_thread(func, prompt):
        return tool_response

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await core.process(
            user_input="Turn on the bedroom light", tools=sample_tools
        )

    assert isinstance(result, CallToolDecision)
    assert result.tool == "home_assistant"
    assert result.parameters["room"] == "bedroom"


@pytest.mark.asyncio
async def test_process_propose_new_tool_json(core, mock_llm, sample_tools):
    """Valid propose_new_tool JSON output returns a ProposeNewToolDecision."""
    propose_response = json.dumps({
        "intent": "propose_new_tool",
        "name": "garage_control",
        "description": "Control the garage door",
    })

    async def mock_to_thread(func, prompt):
        return propose_response

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await core.process(
            user_input="Add garage control", tools=sample_tools
        )

    assert isinstance(result, ProposeNewToolDecision)
    assert result.name == "garage_control"


@pytest.mark.asyncio
async def test_process_malformed_json_triggers_retry(core, mock_llm, sample_tools):
    """Malformed JSON output triggers exactly one retry."""
    malformed = '{"intent": "call_to'  # truncated, can't repair
    fixed = json.dumps({
        "intent": "call_tool",
        "tool": "home_assistant",
        "parameters": {"action": "turn_on", "target": "light"},
    })

    calls = []

    async def mock_to_thread(func, prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return malformed  # First call: malformed JSON
        return fixed          # Second call (retry): fixed JSON

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await core.process(
            user_input="Turn on the light", tools=sample_tools
        )

    # Verify retry was triggered (LLM called twice)
    assert len(calls) == 2

    # First call uses the full prompt (has tools in it)
    assert "home_assistant" in calls[0]

    # Retry prompt is minimal — should contain the broken JSON but NOT the tools list
    assert malformed.strip() in calls[1]
    assert "home_assistant" not in calls[1]  # No tools in retry

    # Result is the fixed decision
    assert isinstance(result, CallToolDecision)


@pytest.mark.asyncio
async def test_process_retry_success(core, mock_llm, sample_tools):
    """Successful retry returns the corrected CallToolDecision."""
    malformed = '{"intent": "call_to'
    fixed = json.dumps({
        "intent": "call_tool",
        "tool": "home_assistant",
        "parameters": {"action": "turn_off", "target": "light", "room": "kitchen"},
    })

    call_count = 0

    async def mock_to_thread(func, prompt):
        nonlocal call_count
        call_count += 1
        return malformed if call_count == 1 else fixed

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await core.process(
            user_input="Turn off the kitchen light", tools=sample_tools
        )

    assert isinstance(result, CallToolDecision)
    assert result.parameters["room"] == "kitchen"
    assert result.parameters["action"] == "turn_off"


@pytest.mark.asyncio
async def test_process_retry_failure_raises_value_error(core, mock_llm, sample_tools):
    """If retry also returns malformed JSON, ValueError is raised."""
    malformed = '{"intent": "call_to'

    async def mock_to_thread(func, prompt):
        return malformed  # Both calls return malformed JSON

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        with pytest.raises(ValueError, match="malformed JSON"):
            await core.process(user_input="Turn on lights", tools=sample_tools)


@pytest.mark.asyncio
async def test_process_retry_plain_text_raises_value_error(core, mock_llm, sample_tools):
    """If retry returns plain text (not JSON), ValueError is raised."""
    malformed = '{"intent": "call_to'
    plain_text_retry = "Sorry, I couldn't fix that JSON."

    call_count = 0

    async def mock_to_thread(func, prompt):
        nonlocal call_count
        call_count += 1
        return malformed if call_count == 1 else plain_text_retry

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        with pytest.raises(ValueError, match="retry did not produce valid tool JSON"):
            await core.process(user_input="Turn on lights", tools=sample_tools)


@pytest.mark.asyncio
async def test_process_with_conversation_context(core, mock_llm, sample_tools, sample_context):
    """Conversation context is injected into the prompt passed to the LLM."""
    plain_response = "I remember you asked about the lights!"

    captured_prompts = []

    async def mock_to_thread(func, prompt):
        captured_prompts.append(prompt)
        return plain_response

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await core.process(
            user_input="What did I say?",
            tools=sample_tools,
            conversation_context=sample_context,
        )

    assert result == plain_response
    assert len(captured_prompts) == 1

    prompt = captured_prompts[0]
    assert "## Recent Conversation:" in prompt
    assert sample_context in prompt
    assert "## Current Request:" in prompt
    assert "What did I say?" in prompt


@pytest.mark.asyncio
async def test_process_without_conversation_context(core, mock_llm, sample_tools):
    """Process works without conversation context (backward compatible)."""
    plain_response = "Hello!"

    captured_prompts = []

    async def mock_to_thread(func, prompt):
        captured_prompts.append(prompt)
        return plain_response

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await core.process(
            user_input="Hello",
            tools=sample_tools,
            # No conversation_context
        )

    assert result == plain_response
    assert "## Recent Conversation:" not in captured_prompts[0]
    assert "## Current Request:" not in captured_prompts[0]


@pytest.mark.asyncio
async def test_process_strips_whitespace_from_plain_text(core, mock_llm, sample_tools):
    """Leading/trailing whitespace is stripped from plain text responses."""
    async def mock_to_thread(func, prompt):
        return "  Hello there!  \n\n"

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await core.process(user_input="Hi", tools=sample_tools)

    assert result == "Hello there!"


@pytest.mark.asyncio
async def test_process_valid_json_bad_schema_raises_value_error(core, mock_llm, sample_tools):
    """Valid JSON with unknown schema raises ValueError (no retry, not a JSON parse error)."""
    bad_schema = json.dumps({"intent": "unknown_intent", "data": "something"})

    async def mock_to_thread(func, prompt):
        return bad_schema

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        with pytest.raises(ValueError, match="failed schema validation"):
            await core.process(user_input="Do something", tools=sample_tools)


@pytest.mark.asyncio
async def test_process_plain_text_never_retried(core, mock_llm, sample_tools):
    """Plain text output is NEVER retried — it's a valid conversation response."""
    plain_response = "Here's a joke: Why did the light bulb go to school?"

    call_count = 0

    async def mock_to_thread(func, prompt):
        nonlocal call_count
        call_count += 1
        return plain_response

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await core.process(user_input="Tell me a joke", tools=sample_tools)

    # LLM called exactly once — no retry for plain text
    assert call_count == 1
    assert result == plain_response
