"""Thin synchronous LLM wrapper for evaluation judge / simulator calls.

Replaces the evomaster ``create_llm`` + ``Dialog`` pattern with direct
``openai.OpenAI`` SDK calls (all traffic goes through LiteLLM Proxy anyway).

Usage::

    from .llm_utils import SyncLLM

    llm = SyncLLM(
        model="gemini-2.5-flash",
        api_key="...",
        base_url="...",
        temperature=0.0,
        max_tokens=2048,
        timeout=180,
    )
    reply = llm.chat(
        system="You are a judge.",
        user="Evaluate this answer...",
    )
    print(reply)  # str
"""

from __future__ import annotations

from dataclasses import dataclass

import openai


@dataclass
class SyncLLM:
    """Minimal sync chat wrapper around ``openai.OpenAI``."""

    model: str
    api_key: str
    base_url: str | None = None
    temperature: float = 0.7
    max_tokens: int | None = None
    timeout: float = 300.0

    def __post_init__(self) -> None:
        self._client = openai.OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
        )

    def chat(self, *, system: str, user: str) -> str:
        """Send a system+user message pair and return the assistant reply text."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        response = self._client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""
