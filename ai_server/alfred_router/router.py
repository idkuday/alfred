"""
Alfred Router (Gemma-3 270M).

Deterministic, JSON-only router implemented as a single LLM invocation.
No agents, chains, memory, callbacks, retries, or heuristic parsing.
"""
import json
from pathlib import Path
from typing import Any, Dict, List

from langchain_ollama import OllamaLLM

from .schemas import RouterDecision, CallToolDecision


class AlfredRouter:
    """Thin routing layer that delegates decisions to Gemma-3 270M."""

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

    def _render_prompt(self, user_input: str, tools: List[Dict[str, str]]) -> str:
        tools_json = json.dumps(tools, indent=2)
        return self.prompt_template.format(user_input=user_input.strip(), tools=tools_json)

    def route(self, user_input: str, tools: List[Dict[str, str]]) -> RouterDecision:
        """
        Invoke the router model exactly once and validate the structured output.

        Raises:
            ValueError: if output is non-JSON or fails validation.
        """
        prompt = self._render_prompt(user_input=user_input, tools=tools)

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



