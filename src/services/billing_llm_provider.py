"""LLMProvider wrapper that reports billing usage to MatMaster platform."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import aiohttp

from clients.matmaster_platform.billing.client import BillingRunContext, BillingService
from matmaster.types.cancellation import CancellationController
from matmaster.types.llm_provider import LLMProvider
from matmaster.types.messages import LLMResponse, Message, StreamChunk

logger = logging.getLogger(__name__)

# 成本熔断触发取消时的原因标记：下游（run_agent 收尾）据此把本轮按「额度耗尽中止」
# 而非「用户取消」对外呈现（语义/文案/运营指标都不同）。
COST_GUARD_CANCEL_REASON = "cost_guard"

# 模块级 ContextVar（Python 要求顶层创建：Context 持强引用，动态创建的 var
# 无法回收）。值随 asyncio Task 的 Context 隔离，多实例/多 run 共用一个 var 安全。
_billing_spawn_id_var: ContextVar[str | None] = ContextVar(
    "billing_spawn_id",
    default=None,
)

# 最外层退出时等待在途上报任务收尾的上限；超时残余任务随 session 关闭而丢单。
_DRAIN_TIMEOUT_SECONDS = 10.0


class BillingRunState:
    """一个 run 内共享的计费状态：call_index 计数 + in-run 熔断（预算 / 欠费双触发）。

    root / subagent / compaction 的所有 BillingLLMProvider 共享同一实例，使
    call_index 全 run 单调、熔断按全 run 累计触发。两个触发条件共用一次性
    的 tripped 标记：本 run 累计花费超预算快照（accumulate），或结算响应
    显示本次调用已产生未覆盖欠费（report_uncovered，针对并行会话抽干共享
    余额、快照失真的场景）。asyncio 单线程下计数和累加均为同步原子操作，
    无需锁。
    """

    def __init__(
        self,
        *,
        session_id: str,
        budget_micro: int | None = None,
        cancel_controller: CancellationController | None = None,
    ) -> None:
        self._session_id = session_id
        self._budget_micro = budget_micro
        self._cancel_controller = cancel_controller
        self._call_index = 0
        self._spent_micro = 0
        self._guard_tripped = False

    def next_call_index(self) -> int:
        self._call_index += 1
        return self._call_index

    def accumulate(self, cost_micro: int) -> None:
        """累加本次结算成本，超预算则触发 in-run 熔断。"""
        if cost_micro <= 0:
            return
        self._spent_micro += cost_micro
        self._maybe_trip_guard()

    def report_uncovered(self, uncovered_micro: int) -> None:
        """结算响应显示本次调用产生了未覆盖欠费：立即熔断，不看预算快照。

        预算快照只数本 run 自己的花费：并行会话共享同一余额池，池子被别的
        run 抽干后，本 run 在自己撞线前会一直在欠费上跑。settle 响应的
        ``uncovered_micro > 0`` 是「这笔调用已经没人买单」的精确信号（平台侧
        对幂等重放/结算故障/fail-open 恒返回 0，不会误杀），见到即停，单 run
        欠费上界从「预算快照 + 宽限」收敛到 ≈ 一笔调用。
        """
        if uncovered_micro <= 0:
            return
        if self._guard_tripped or self._cancel_controller is None:
            return
        logger.warning(
            "in-run debt guard tripped session_id=%s uncovered_micro=%s, "
            "cancelling run",
            self._session_id,
            uncovered_micro,
        )
        self._trip_guard()

    def _maybe_trip_guard(self) -> None:
        if (
            self._guard_tripped
            or self._budget_micro is None
            or self._cancel_controller is None
        ):
            return
        if self._spent_micro <= self._budget_micro:
            return
        logger.warning(
            "in-run cost guard tripped session_id=%s spent_micro=%s budget_micro=%s, "
            "cancelling run",
            self._session_id,
            self._spent_micro,
            self._budget_micro,
        )
        self._trip_guard()

    def _trip_guard(self) -> None:
        self._guard_tripped = True
        try:
            self._cancel_controller.cancel(reason=COST_GUARD_CANCEL_REASON)
        except Exception:
            logger.warning(
                "cost guard cancel failed session_id=%s",
                self._session_id,
                exc_info=True,
            )


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

    熔断状态由调用方注入的 ``run_state`` 共享。累加发生在 fire-and-forget 上报回调里，
    不阻塞主链路。
    """

    def __init__(
        self,
        inner: LLMProvider,
        *,
        run_context: BillingRunContext,
        model: str,
        billing_service: BillingService,
        billing_mode: str = "platform",
        run_state: BillingRunState,
    ) -> None:
        self._inner = inner
        self._run_context = run_context
        self._model = model
        self._billing_service = billing_service
        self._billing_mode = billing_mode
        self._run_state = run_state
        self._pending: set[asyncio.Task] = set()
        self._http_session: aiohttp.ClientSession | None = None
        self._enter_count = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def __aenter__(self) -> BillingLLMProvider:
        # 可重入（与 Transport._enter_count 同款）：子 exp 无 llm 覆盖时继承父 run
        # 的同一实例并被子内核再次 async with 进入，若无脑重建 session 会把外层
        # session 覆盖成孤儿（GC 报 Unclosed client session）。
        await self._inner.__aenter__()
        self._enter_count += 1
        if self._http_session is None:
            # 一次 run 内复用一个 session 的连接池，避免每次上报重新握手。
            self._http_session = aiohttp.ClientSession()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        self._enter_count -= 1
        if self._enter_count > 0:
            await self._inner.__aexit__(exc_type, exc_val, exc_tb)
            return
        # 最外层退出：先 drain 完仍在用 session 的上报任务，再关 session。
        # 两层 finally：drain 中被取消（CancelledError 不入 except Exception）
        # 或 close 抛错时，session 关闭与 inner 退出也必须执行，否则重现
        # Unclosed client session 泄漏 / inner（Transport）计数失衡永不关闭。
        try:
            await self._drain_pending()
        finally:
            try:
                if self._http_session is not None:
                    await self._http_session.close()
                    self._http_session = None
            finally:
                await self._inner.__aexit__(exc_type, exc_val, exc_tb)

    async def _drain_pending(self) -> None:
        if not self._pending:
            return
        _done, leftover = await asyncio.wait(
            list(self._pending), timeout=_DRAIN_TIMEOUT_SECONDS
        )
        if leftover:
            # 残余任务将撞上随后关闭的 session 而丢单（fail-open 少收方向）；
            # 记数让丢单可从日志量化。
            logger.warning(
                "billing report drain timed out, %s in-flight reports will be "
                "lost session_id=%s",
                len(leftover),
                self._run_context.session_id,
            )

    @contextmanager
    def billing_scope(self, *, spawn_id: str | None = None):
        token = _billing_spawn_id_var.set(spawn_id)
        try:
            yield
        finally:
            # 与 usage_collector.billing_scope 同款兜底:该 scope 包在 async
            # generator 的 yield 外层,若生成器被遗弃后由事件循环的 GC finalizer
            # 在新 Context 里关闭,reset(token) 会抛 "created in a different
            # Context";此时直接清值,保证 teardown 不中断。
            try:
                _billing_spawn_id_var.reset(token)
            except ValueError:
                _billing_spawn_id_var.set(None)

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
        """从定价响应解析本次成本与欠费信号，委派给共享 run_state 累加 / 熔断。"""
        if not data:
            return
        try:
            cost_micro = int(data.get("total_amount_settle_micro") or 0)
        except (TypeError, ValueError):
            cost_micro = 0
        self._run_state.accumulate(cost_micro)
        # uncovered_micro：平台 settle 后未被任何源覆盖、已挂欠费的残额。
        # 旧平台响应无此字段 -> 0，欠费熔断退化关闭（仍有预算快照兜底）。
        try:
            uncovered_micro = int(data.get("uncovered_micro") or 0)
        except (TypeError, ValueError):
            uncovered_micro = 0
        self._run_state.report_uncovered(uncovered_micro)

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
                spawn_id=_billing_spawn_id_var.get(),
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
        call_index = self._run_state.next_call_index()
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
        call_index = self._run_state.next_call_index()
        last_usage: dict[str, Any] | None = None
        async for chunk in self._inner.chat_stream(messages, tools, timeout=timeout):
            if chunk.usage is not None:
                last_usage = dict(chunk.usage)
            yield chunk
        self._schedule_report(
            call_index=call_index,
            usage=last_usage,
        )
