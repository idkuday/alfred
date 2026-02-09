"""
Alfred Router.

Deterministic, JSON-only router implemented as a single LLM invocation.
No agents, chains, memory, callbacks, retries, or heuristic parsing.

Default model: Qwen 2.5 3B (configurable via ALFRED_ROUTER_MODEL)
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_ollama import OllamaLLM

from .schemas import RouterDecision, CallToolDecision


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
            ValueError: if output is non-JSON or fails validation.
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
            raise ValueError(f"Router output failed schema validation: {parsed}. Error: {exc}") from exc

        return decision


def decision_requires_intent_processor(decision: RouterDecision) -> bool:
    """
    Helper to detect if router explicitly requested the intent processor.

    The intent processor is optional and must never run automatically.
    """
    return isinstance(decision, CallToolDecision) and decision.tool == "intent_processor"



