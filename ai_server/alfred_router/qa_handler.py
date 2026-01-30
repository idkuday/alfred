"""
Q/A handler abstraction.

Uses a thin LangChain LLM wrapper for Gemma-2B/7B. This path is read-only and
cannot execute tools or modify state.
"""
from abc import ABC, abstractmethod
from typing import Optional
import asyncio
from langchain_ollama import OllamaLLM


class QAHandler(ABC):
    """Abstract Q/A handler interface."""

    @abstractmethod
    async def answer(self, query: str) -> str:
        """Return a read-only answer for the given query."""
        raise NotImplementedError


class GemmaQAHandler(QAHandler):
    """
    Gemma-based Q/A handler (read-only).

    This wrapper is intentionally minimal: a single LLM invocation with no
    agents, tools, memory, or callbacks.
    """

    def __init__(
        self,
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 512,
    ):
        self.llm = OllamaLLM(
            model=model,
            temperature=temperature,
            num_predict=max_tokens,
        )

    async def answer(self, query: str) -> str:
        prompt = (
            "You are a concise, read-only Q/A model for smart home questions. "
            "Answer briefly and factually.\n\n"
            f"User query:\n{query.strip()}\n"
        )
        # Ollama invoke is sync; run in thread to avoid blocking event loop
        response = await asyncio.to_thread(self.llm.invoke, prompt)
        return response.strip() if isinstance(response, str) else str(response).strip()



