"""In-memory per-call LLM usage collector provider wrapper.

Wraps an :class:`LLMProvider` and records one usage snapshot per completed LLM
call. The kernel reuses a single provider instance for the root agent, subagent
child runs (same ``AgentRunContext``) and compaction summary calls, and tags
subagent calls via ``billing_scope(spawn_id=...)`` (see
``matmaster/core/exp.py``). Mirroring that scope here means a single wrapper
captures all three call kinds with the right ``spawn_id``.

When an optional :class:`UsageReporter` is injected, each completed call is
reported inline (awaited right after the call returns) and its returned cost is
back-filled onto the matching :class:`PerCallUsage` before the next call runs.
Reporting inline — rather than via fire-and-forget background tasks — keeps cost
back-fill deterministic across the root agent, subagent child runs (whose
context/generator lifecycles otherwise race the drain) and the final call of a
run. This wrapper is only used for evaluation runs, where the small per-call
report latency is acceptable.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from matmaster.types.llm_provider import LLMProvider
from matmaster.types.messages import LLMResponse, Message, StreamChunk
from matmaster.types.usage_reporter import UsageReporter

logger = logging.getLogger(__name__)


@dataclass
class PerCallUsage:
    """One LLM call's usage snapshot (and optional cost).

    ``spawn_id`` is ``None`` for root-agent and compaction calls and set for
    subagent calls. ``usage`` is the provider-normalized scalar dict, which may
    include ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens`` /
    ``cache_read_tokens`` / ``cache_write_tokens`` / ``reasoning_tokens``.
    ``cost`` is filled from the reporter's response when available (tools-server
    ``UsageIngestData``: ``total_amount_micro`` etc.).
    """

    call_index: int
    spawn_id: str | None
    model: str
    usage: dict[str, int]
    cost: dict[str, Any] | None = field(default=None)

    @property
    def kind(self) -> str:
        return "subagent" if self.spawn_id else "root"

    def to_payload(self) -> dict[str, Any]:
        """JSON-safe dict for run summaries / ingest extra."""
        payload: dict[str, Any] = {
            "call_index": self.call_index,
            "spawn_id": self.spawn_id,
            "kind": self.kind,
            "model": self.model,
            "usage": dict(self.usage),
        }
        if self.cost is not None:
            payload["cost"] = dict(self.cost)
        return payload


def per_call_usage_payload(calls: list[PerCallUsage]) -> list[dict[str, Any]]:
    """Serialize a list of :class:`PerCallUsage` to JSON-safe dicts."""
    return [call.to_payload() for call in calls]


class UsageCollectingProvider:
    """Wrap an LLMProvider, record per-call usage, optionally report cost."""

    def __init__(
        self,
        inner: LLMProvider,
        *,
        model: str,
        reporter: UsageReporter | None = None,
    ) -> None:
        self._inner = inner
        self._model = model
        self._reporter = reporter
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
        await self._close_reporter()
        await self._inner.__aexit__(exc_type, exc_val, exc_tb)

    @contextmanager
    def billing_scope(self, *, spawn_id: str | None = None):
        """Tag subsequent calls with ``spawn_id`` (used by subagent runs)."""
        token = self._spawn_id_var.set(spawn_id)
        try:
            yield
        finally:
            # ``runtime_scope`` (exp.py) wraps this sync contextmanager around an
            # async generator's ``yield``. When that generator is resumed/closed
            # in a different context (e.g. GeneratorExit on subagent teardown),
            # ``reset(token)`` raises ``ValueError: Token created in a different
            # Context``. Fall back to clearing the value directly so teardown does
            # not crash and downstream cleanup (e.g. reporter close) still runs.
            try:
                self._spawn_id_var.reset(token)
            except ValueError:
                self._spawn_id_var.set(None)

    @property
    def collected_calls(self) -> list[PerCallUsage]:
        return list(self._calls)

    async def _close_reporter(self) -> None:
        aclose = getattr(self._reporter, "aclose", None)
        if aclose is None:
            return
        try:
            await aclose()
        except Exception:
            logger.warning("closing usage reporter failed", exc_info=True)

    async def _record(self, usage: dict[str, int] | None) -> None:
        self._call_index += 1
        if not usage:
            return
        call = PerCallUsage(
            call_index=self._call_index,
            spawn_id=self._spawn_id_var.get(),
            model=self._model,
            usage=dict(usage),
        )
        self._calls.append(call)
        await self._report(call)

    async def _report(self, call: PerCallUsage) -> None:
        if self._reporter is None:
            return
        logger.info(
            "usage report start call=%s spawn=%s",
            call.call_index,
            call.spawn_id,
        )
        try:
            cost = await self._reporter.report_call(
                call_index=call.call_index,
                spawn_id=call.spawn_id,
                model=call.model,
                usage=call.usage,
            )
            if cost is not None:
                call.cost = cost
                logger.info(
                    "usage report done call=%s spawn=%s amt=%s",
                    call.call_index,
                    call.spawn_id,
                    cost.get("total_amount_micro"),
                )
            else:
                logger.warning(
                    "usage report returned no cost call=%s spawn=%s",
                    call.call_index,
                    call.spawn_id,
                )
        except asyncio.CancelledError:
            logger.warning(
                "usage report CANCELLED call=%s spawn=%s",
                call.call_index,
                call.spawn_id,
            )
            raise
        except Exception:
            logger.warning(
                "usage report failed call=%s spawn=%s",
                call.call_index,
                call.spawn_id,
                exc_info=True,
            )

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        response = await self._inner.chat(messages, tools, tool_choice=tool_choice)
        await self._record(response.usage)
        return response

    async def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        last_usage: dict[str, int] | None = None
        async for chunk in self._inner.chat_stream(messages, tools, timeout=timeout):
            if chunk.usage is not None:
                last_usage = dict(chunk.usage)
            yield chunk
        await self._record(last_usage)
