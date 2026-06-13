"""LLMProvider wrapper that reports billing usage to tools-server."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import aiohttp

from clients.billing.client import BillingRunContext, BillingService
from matmaster.types.cancellation import CancellationController
from matmaster.types.llm_provider import LLMProvider
from matmaster.types.messages import LLMResponse, Message, StreamChunk

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 成本熔断触发取消时的原因标记：下游（run_agent 收尾）据此把本轮按「额度耗尽中止」
# 而非「用户取消」对外呈现（语义/文案/运营指标都不同）。
COST_GUARD_CANCEL_REASON = "cost_guard"


class BillingLLMProvider:
    """Wrap an LLM provider and report one usage event per completed LLM call.

    In-run 成本熔断（防线二）：发送前闸口只在 run 启动时拦一次，单个长 run 内的连续
    LLM 调用若不设上界，post-paid 记账可累积出无界欠债。本包装器在每次上报拿回的
    ``total_amount_settle_micro`` 上累加本次 run 已花成本，一旦超过 ``budget_micro``
    （= 启动时可用额度 + 宽限，由调用方算好）就触发
    ``cancel_controller.cancel(reason='cost_guard')``。Exp 内核在每个 turn 开始 / stream
    chunk / 串行 tool 间检查 cancel_token，会在下一个 turn 前优雅收尾（reason='cancelled'），
    故单 run 欠债上界 ≈ 预算 + 一个 turn 成本。取消带 ``cost_guard`` 原因，让 run_agent
    收尾时把本轮按「额度耗尽中止」（失败语义）而非「用户取消」对外呈现。

    仅在同时注入 ``budget_micro`` 与 ``cancel_controller`` 时启用（platform 计费才传；
    byok/eval 不传 → 不熔断）。累加发生在 fire-and-forget 上报回调里，不阻塞主链路。
    """

    def __init__(
        self,
        inner: LLMProvider,
        *,
        run_context: BillingRunContext,
        model: str,
        billing_service: BillingService,
        billing_mode: str = "platform",
        budget_micro: int | None = None,
        cancel_controller: CancellationController | None = None,
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
        # in-run 成本熔断状态：预算 + 取消句柄齐备才启用；spent 在上报回调里累加。
        self._budget_micro = budget_micro
        self._cancel_controller = cancel_controller
        self._spent_micro = 0
        self._guard_tripped = False

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
            # 用 price_llm_usage 同时拿回当次定价结果（含 total_amount_settle_micro），
            # 与 report_llm_usage 共用 POST、副作用一致（platform 模式照常扣费记账），
            # 但多拿成本供 in-run 熔断累加。
            data = await self._billing_service.price_llm_usage(
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
            return
        self._accumulate_cost(data)

    def _accumulate_cost(self, data: dict[str, Any] | None) -> None:
        """累加本次结算成本，超预算则触发 in-run 熔断（取消整个 run）。"""
        if not data:
            return
        try:
            cost_micro = int(data.get("total_amount_settle_micro") or 0)
        except (TypeError, ValueError):
            return
        if cost_micro <= 0:
            return
        self._spent_micro += cost_micro
        self._maybe_trip_guard()

    def _maybe_trip_guard(self) -> None:
        if (
            self._guard_tripped
            or self._budget_micro is None
            or self._cancel_controller is None
        ):
            return
        if self._spent_micro <= self._budget_micro:
            return
        # 只触发一次：set event + fire callbacks，Exp 下个 turn 前见 cancel 优雅收尾。
        # 带 cost_guard 原因，让 run_agent 把本轮按「额度耗尽中止」而非「用户取消」呈现。
        self._guard_tripped = True
        logger.warning(
            "in-run cost guard tripped session_id=%s spent_micro=%s budget_micro=%s, "
            "cancelling run",
            self._run_context.session_id,
            self._spent_micro,
            self._budget_micro,
        )
        try:
            self._cancel_controller.cancel(reason=COST_GUARD_CANCEL_REASON)
        except Exception:
            logger.warning(
                "cost guard cancel failed session_id=%s",
                self._run_context.session_id,
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
        messages: list[Message],
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
        messages: list[Message],
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
