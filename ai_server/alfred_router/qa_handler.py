"""
Q/A handler abstraction.

Uses a thin LangChain LLM wrapper via Ollama. This path is read-only and
cannot execute tools or modify state.

Default model: Qwen 2.5 3B (configurable via ALFRED_QA_MODEL)
"""
from abc import ABC, abstractmethod
from typing import Optional
import asyncio
from langchain_ollama import OllamaLLM


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
    """

    def __init__(
        self,
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 512,
    ):
        self.model_name = model
        self.llm = OllamaLLM(
            model=model,
            temperature=temperature,
            num_predict=max_tokens,
        )

    async def answer(self, query: str, conversation_context: Optional[str] = None) -> str:
        # Build system message
        system_message = (
            "You are Alfred, a smart home AI assistant.\n\n"
            "About you:\n"
            "- Name: Alfred\n"
            f"- Model: You are powered by {self.model_name} running locally via Ollama\n"
            "- Purpose: Help users control their smart home devices and answer questions\n"
            "- Architecture: Local-first, privacy-focused (running entirely on user's network, no cloud)\n"
            "- Capabilities: Control lights, switches, thermostats, answer questions, integrate with Home Assistant\n"
            "- Personality: Helpful, concise, friendly, slightly witty like Jarvis\n\n"
            "Guidelines:\n"
            "- Be helpful and direct\n"
            "- Keep responses concise (2-3 sentences max)\n"
            "- Be friendly but professional\n"
            "- If asked about your capabilities, mention smart home control and Q&A\n"
            "- If asked what model you are, tell them you run on {model_name} locally\n"
            "- If you don't know something, say so honestly\n"
        ).format(model_name=self.model_name)

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

