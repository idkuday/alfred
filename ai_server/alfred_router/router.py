"""
Alfred Router.

Deterministic, JSON-only router implemented as a single LLM invocation.
No agents, chains, memory, callbacks, retries, or heuristic parsing.

Default model: Qwen 2.5 3B (configurable via ALFRED_ROUTER_MODEL)
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from langchain_ollama import OllamaLLM

from .schemas import RouterDecision, CallToolDecision, RouteToQADecision

logger = logging.getLogger(__name__)


class RouterRefusalError(Exception):
    """Raised when the LLM refuses a request instead of returning structured JSON.

    This happens when the model's safety filter activates (e.g., policy-violating
    requests). The raw_output contains the LLM's refusal text which can be
    returned to the user as-is.
    """

    def __init__(self, raw_output: str, user_input: str):
        self.raw_output = raw_output
        self.user_input = user_input
        super().__init__(f"LLM refused to route (safety filter): {raw_output[:200]}")


class AlfredRouter:
    """Thin routing layer that delegates decisions to the configured LLM."""

    def __init__(
        self,
        model: str,
        prompt_path: str,
        temperature: float = 0.0,
        max_tokens: int = 512,
    ):
        self.prompt_template = Path(prompt_path).read_text(encoding="utf-8")
        self.llm = OllamaLLM(
            model=model,
            temperature=temperature,
            num_predict=max_tokens,
        )

    def _render_prompt(
        self,
        user_input: str,
        tools: List[Dict[str, str]],
        conversation_context: Optional[str] = None
    ) -> str:
        tools_json = json.dumps(tools, indent=2)

        # Inject conversation context before user input if provided
        if conversation_context:
            context_section = f"\n## Recent Conversation:\n{conversation_context}\n\n## Current Request:\n"
            user_input_with_context = context_section + user_input.strip()
        else:
            user_input_with_context = user_input.strip()

        return self.prompt_template.format(
            user_input=user_input_with_context,
            tools=tools_json
        )

    def _is_llm_refusal(self, output: str) -> bool:
        """Detect if the LLM output is a safety/policy refusal rather than a routing failure.

        Non-JSON output that contains conversational text (not garbled/truncated)
        is almost certainly the model's safety filter responding directly.
        """
        # If it starts with { it's not a refusal - it's an attempted JSON response
        if output.startswith("{"):
            return False

        # Short garbled output is not a refusal, it's a real error
        if len(output.strip()) < 10:
            return False

        # If the output contains readable sentences, it's a refusal
        # (as opposed to truncated JSON like `{"intent": "call_to`)
        return True

    def route(
        self,
        user_input: str,
        tools: List[Dict[str, str]],
        conversation_context: Optional[str] = None
    ) -> RouterDecision:
        """
        Invoke the router model exactly once and validate the structured output.

        Args:
            user_input: The current user request.
            tools: List of available tools.
            conversation_context: Optional conversation history context (pre-built string).

        Raises:
            RouterRefusalError: if the LLM's safety filter activates and it returns
                plain text instead of JSON. The caller should return raw_output to the user.
            ValueError: if output is malformed JSON or fails schema validation.
        """
        prompt = self._render_prompt(
            user_input=user_input,
            tools=tools,
            conversation_context=conversation_context
        )

        raw_output = self.llm.invoke(prompt)
        if not isinstance(raw_output, str):
            raw_output = str(raw_output)

        with open("last_router_output.txt", "w", encoding="utf-8") as f:
            f.write(raw_output)

        cleaned = raw_output.strip()
        if not cleaned.startswith("{"):
            # Check if this is an LLM refusal (safety filter) vs actual error
            if self._is_llm_refusal(cleaned):
                logger.warning(f"LLM safety filter activated for input: {user_input[:100]!r}")
                raise RouterRefusalError(raw_output=cleaned, user_input=user_input)
            raise ValueError(f"Router output is not valid JSON (missing object wrappers). Output: {cleaned}")

        try:
            parsed: Dict[str, Any] = json.loads(cleaned)
        except json.JSONDecodeError:
            # Attempt to repair truncated JSON (common with some models)
            try:
                parsed = json.loads(cleaned + "}")
            except json.JSONDecodeError as exc:
                raise ValueError(f"Router returned non-JSON output: {repr(cleaned)}") from exc

        try:
            # Use TypeAdapter for Union validation
            from pydantic import TypeAdapter
            adapter = TypeAdapter(RouterDecision)
            decision = adapter.validate_python(parsed)
        except Exception as exc:
            # Check if the LLM returned a conversational response wrapped in JSON
            # e.g. {"response": "Here's your answer..."} or {"answer": "..."}
            # This happens when the model goes off-script but still outputs JSON.
            fallback_text = (
                parsed.get("response")
                or parsed.get("answer")
                or parsed.get("message")
                or parsed.get("text")
                or parsed.get("reply")
            )
            if fallback_text and isinstance(fallback_text, str):
                logger.warning(
                    f"LLM returned non-schema JSON with key "
                    f"{[k for k in parsed.keys()]!r} — treating as refusal/direct answer"
                )
                raise RouterRefusalError(raw_output=fallback_text, user_input=user_input) from exc
            raise ValueError(f"Router output failed schema validation: {parsed}. Error: {exc}") from exc

        return decision


def decision_requires_intent_processor(decision: RouterDecision) -> bool:
    """
    Helper to detect if router explicitly requested the intent processor.

    The intent processor is optional and must never run automatically.
    """
    return isinstance(decision, CallToolDecision) and decision.tool == "intent_processor"



