"""LLMProvider wrapper that records dry-run billing usage."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from matmaster.types.llm_provider import LLMProvider
from matmaster.types.messages import LLMResponse, StreamChunk
from src.services.billing_service import (
    BillingModelIdentity,
    BillingRunContext,
    BillingService,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class BillingLLMProvider:
    """Wrap an LLM provider and write one ledger row per completed LLM call."""

    def __init__(
        self,
        inner: LLMProvider,
        *,
        run_context: BillingRunContext,
        model_identity: BillingModelIdentity,
        billing_service: BillingService,
        billing_mode: str = "dry_run",
    ) -> None:
        self._inner = inner
        self._run_context = run_context
        self._model_identity = model_identity
        self._billing_service = billing_service
        self._billing_mode = billing_mode
        self._call_index = 0
        self._spawn_id_var: ContextVar[str | None] = ContextVar(
            "billing_spawn_id",
            default=None,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def __aenter__(self) -> BillingLLMProvider:
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
        token = self._spawn_id_var.set(spawn_id)
        try:
            yield
        finally:
            self._spawn_id_var.reset(token)

    def _next_call_index(self) -> int:
        self._call_index += 1
        return self._call_index

    async def _record(
        self,
        *,
        call_index: int,
        call_kind: str,
        usage: dict[str, Any] | None,
        usage_vendor: dict[str, Any] | None,
    ) -> None:
        try:
            await self._billing_service.record_llm_usage(
                run_context=self._run_context,
                model_identity=self._model_identity,
                call_index=call_index,
                call_kind=call_kind,
                spawn_id=self._spawn_id_var.get(),
                usage=usage,
                usage_vendor=usage_vendor,
                billing_mode=self._billing_mode,
            )
        except Exception:
            logger.warning(
                "dry-run billing record failed session_id=%s call_index=%s",
                self._run_context.session_id,
                call_index,
                exc_info=True,
            )

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
        await self._record(
            call_index=call_index,
            call_kind="chat",
            usage=response.usage,
            usage_vendor=response.usage_vendor,
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
        last_usage_vendor: dict[str, Any] | None = None
        async for chunk in self._inner.chat_stream(messages, tools, timeout=timeout):
            if chunk.usage is not None:
                last_usage = dict(chunk.usage)
            if chunk.usage_vendor is not None:
                last_usage_vendor = dict(chunk.usage_vendor)
            yield chunk
        await self._record(
            call_index=call_index,
            call_kind="chat_stream",
            usage=last_usage,
            usage_vendor=last_usage_vendor,
        )
