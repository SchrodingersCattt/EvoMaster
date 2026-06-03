"""LLMProvider wrapper that reports billing usage to tools-server."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import aiohttp

from matmaster.types.llm_provider import LLMProvider
from matmaster.types.messages import LLMResponse, StreamChunk
from src.services.billing_service import BillingRunContext, BillingService

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class BillingLLMProvider:
    """Wrap an LLM provider and report one usage event per completed LLM call."""

    def __init__(
        self,
        inner: LLMProvider,
        *,
        run_context: BillingRunContext,
        model: str,
        billing_service: BillingService,
        billing_mode: str = "platform",
    ) -> None:
        self._inner = inner
        self._run_context = run_context
        self._model = model
        self._billing_service = billing_service
        self._billing_mode = billing_mode
        self._call_index = 0
        self._pending: set[asyncio.Task] = set()
        self._http_session: aiohttp.ClientSession | None = None
        self._spawn_id_var: ContextVar[str | None] = ContextVar(
            "billing_spawn_id",
            default=None,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def __aenter__(self) -> BillingLLMProvider:
        await self._inner.__aenter__()
        # 一次 run 内复用一个 session 的连接池，避免每次上报重新握手。
        self._http_session = aiohttp.ClientSession()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        # 先 drain 完仍在用 session 的上报任务，再关 session。
        await self._drain_pending()
        if self._http_session is not None:
            await self._http_session.close()
            self._http_session = None
        await self._inner.__aexit__(exc_type, exc_val, exc_tb)

    async def _drain_pending(self) -> None:
        if not self._pending:
            return
        pending = list(self._pending)
        try:
            await asyncio.wait(pending, timeout=10)
        except Exception:
            logger.warning("draining billing usage reports failed", exc_info=True)

    @contextmanager
    def billing_scope(self, *, spawn_id: str | None = None):
        token = self._spawn_id_var.set(spawn_id)
        try:
            yield
        finally:
            self._spawn_id_var.reset(token)

    def _next_call_index(self) -> int:
        self._call_index += 1
        return self._call_index

    async def _report(
        self,
        *,
        call_index: int,
        spawn_id: str | None,
        usage: dict[str, Any] | None,
    ) -> None:
        try:
            await self._billing_service.report_llm_usage(
                run_context=self._run_context,
                model=self._model,
                call_index=call_index,
                spawn_id=spawn_id,
                usage=usage,
                billing_mode=self._billing_mode,
                session=self._http_session,
            )
        except Exception:
            logger.warning(
                "billing report failed session_id=%s call_index=%s",
                self._run_context.session_id,
                call_index,
                exc_info=True,
            )

    def _schedule_report(
        self,
        *,
        call_index: int,
        usage: dict[str, Any] | None,
    ) -> None:
        """非阻塞地上报，避免计费 HTTP 往返拖慢用户主链路。"""
        if not usage:
            return
        task = asyncio.create_task(
            self._report(
                call_index=call_index,
                spawn_id=self._spawn_id_var.get(),
                usage=usage,
            )
        )
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_choice: str | dict | None = None,
    ) -> LLMResponse:
        call_index = self._next_call_index()
        response = await self._inner.chat(
            messages,
            tools,
            tool_choice=tool_choice,
        )
        self._schedule_report(
            call_index=call_index,
            usage=response.usage,
        )
        return response

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        call_index = self._next_call_index()
        last_usage: dict[str, Any] | None = None
        async for chunk in self._inner.chat_stream(messages, tools, timeout=timeout):
            if chunk.usage is not None:
                last_usage = dict(chunk.usage)
            yield chunk
        self._schedule_report(
            call_index=call_index,
            usage=last_usage,
        )
