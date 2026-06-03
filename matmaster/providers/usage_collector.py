"""In-memory per-call LLM usage collector provider wrapper.

Wraps an :class:`LLMProvider` and records one usage snapshot per completed LLM
call. Unlike the online billing wrapper (which reports each call to
tools-server), this collector keeps the per-call usage in memory so offline
flows (evaluation) can attach a per-call breakdown to their run records.

The kernel reuses a single provider instance for the root agent, subagent
child runs (same ``AgentRunContext``) and compaction summary calls, and tags
subagent calls via ``billing_scope(spawn_id=...)`` (see
``matmaster/core/exp.py``). Mirroring that scope here means a single wrapper
captures all three call kinds with the right ``spawn_id``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from matmaster.types.llm_provider import LLMProvider
from matmaster.types.messages import LLMResponse, StreamChunk


@dataclass(frozen=True)
class PerCallUsage:
    """One LLM call's usage snapshot.

    ``spawn_id`` is ``None`` for root-agent and compaction calls and set for
    subagent calls. ``usage`` is the provider-normalized scalar dict, which may
    include ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens`` /
    ``cache_read_tokens`` / ``cache_write_tokens`` / ``reasoning_tokens``.
    """

    call_index: int
    spawn_id: str | None
    model: str
    usage: dict[str, int]

    @property
    def kind(self) -> str:
        return "subagent" if self.spawn_id else "root"

    def to_payload(self) -> dict[str, Any]:
        """JSON-safe dict for run summaries / ingest extra."""
        return {
            "call_index": self.call_index,
            "spawn_id": self.spawn_id,
            "kind": self.kind,
            "model": self.model,
            "usage": dict(self.usage),
        }


def per_call_usage_payload(calls: list[PerCallUsage]) -> list[dict[str, Any]]:
    """Serialize a list of :class:`PerCallUsage` to JSON-safe dicts."""
    return [call.to_payload() for call in calls]


class UsageCollectingProvider:
    """Wrap an LLMProvider and record per-call usage in memory."""

    def __init__(self, inner: LLMProvider, *, model: str) -> None:
        self._inner = inner
        self._model = model
        self._call_index = 0
        self._calls: list[PerCallUsage] = []
        self._spawn_id_var: ContextVar[str | None] = ContextVar(
            "usage_collector_spawn_id",
            default=None,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def __aenter__(self) -> UsageCollectingProvider:
        await self._inner.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        await self._inner.__aexit__(exc_type, exc_val, exc_tb)

    @contextmanager
    def billing_scope(self, *, spawn_id: str | None = None):
        """Tag subsequent calls with ``spawn_id`` (used by subagent runs)."""
        token = self._spawn_id_var.set(spawn_id)
        try:
            yield
        finally:
            self._spawn_id_var.reset(token)

    @property
    def collected_calls(self) -> list[PerCallUsage]:
        return list(self._calls)

    def _record(self, usage: dict[str, int] | None) -> None:
        self._call_index += 1
        if not usage:
            return
        self._calls.append(
            PerCallUsage(
                call_index=self._call_index,
                spawn_id=self._spawn_id_var.get(),
                model=self._model,
                usage=dict(usage),
            )
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        response = await self._inner.chat(messages, tools, tool_choice=tool_choice)
        self._record(response.usage)
        return response

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        last_usage: dict[str, int] | None = None
        async for chunk in self._inner.chat_stream(messages, tools, timeout=timeout):
            if chunk.usage is not None:
                last_usage = dict(chunk.usage)
            yield chunk
        self._record(last_usage)
