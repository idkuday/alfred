"""
Q/A handler abstraction.

Uses a thin LangChain LLM wrapper via Ollama. This path is read-only and
cannot execute tools or modify state.

Default model: Qwen 2.5 3B (configurable via ALFRED_QA_MODEL)
"""
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
import asyncio
from langchain_ollama import OllamaLLM

logger = logging.getLogger(__name__)

# Default prompt path (next to this file, in prompts/)
_DEFAULT_QA_PROMPT = Path(__file__).parent / "prompts" / "qa.txt"


class QAHandler(ABC):
    """Abstract Q/A handler interface."""

    @abstractmethod
    async def answer(self, query: str, conversation_context: Optional[str] = None) -> str:
        """
        Return a read-only answer for the given query.

        Args:
            query: The user's question.
            conversation_context: Optional conversation history context (pre-built string).
        """
        raise NotImplementedError


class OllamaQAHandler(QAHandler):
    """
    Ollama-based Q/A handler (read-only).

    This wrapper is intentionally minimal: a single LLM invocation with no
    agents, tools, memory, or callbacks.

    The system prompt is loaded from prompts/qa.txt — kept local and untracked
    by git so personality customizations stay private.
    """

    def __init__(
        self,
        model: str,
        prompt_path: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 512,
    ):
        self.model_name = model
        self.llm = OllamaLLM(
            model=model,
            temperature=temperature,
            num_predict=max_tokens,
        )

        # Load system prompt from file
        prompt_file = Path(prompt_path) if prompt_path else _DEFAULT_QA_PROMPT
        if not prompt_file.exists():
            raise FileNotFoundError(
                f"QA prompt file not found: {prompt_file}\n"
                f"Create this file with your Alfred personality prompt.\n"
                f"This file is intentionally untracked by git — it stays local to your machine."
            )
        self.system_prompt_template = prompt_file.read_text(encoding="utf-8")
        logger.info(f"QA prompt loaded from {prompt_file}")

    def _build_system_message(self) -> str:
        """Render the system prompt, injecting model name."""
        return self.system_prompt_template.format(model_name=self.model_name)

    async def answer(self, query: str, conversation_context: Optional[str] = None) -> str:
        system_message = self._build_system_message()

        # Inject conversation context if provided
        if conversation_context:
            context_section = f"\n## Recent Conversation:\n{conversation_context}\n\n## Current Query:\n"
            full_prompt = system_message + context_section + query.strip() + "\n\nYour response:"
        else:
            full_prompt = system_message + f"\n\nUser query: {query.strip()}\n\nYour response:"

        # Ollama invoke is sync; run in thread to avoid blocking event loop
        response = await asyncio.to_thread(self.llm.invoke, full_prompt)
        return response.strip() if isinstance(response, str) else str(response).strip()


# Backward compatibility alias
GemmaQAHandler = OllamaQAHandler

