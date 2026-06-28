"""BillingLLMProvider 的 in-run 成本熔断（防线二）。

只测同步的成本累加 + 熔断触发逻辑（_accumulate_cost / _maybe_trip_guard），
不触发真实 LLM / HTTP：构造时注入 MagicMock 的 inner 与 billing_service，
直接喂定价响应 dict，断言是否触发 cancel_controller。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from clients.matmaster_platform.billing.client import BillingRunContext
from matmaster.types.cancellation import CancellationController
from src.services.billing_llm_provider import BillingLLMProvider


def _provider(budget_micro, controller, billing_mode="platform"):
    return BillingLLMProvider(
        MagicMock(),
        run_context=BillingRunContext(session_id="s", task_id="t", invocation_id="i"),
        model="m",
        billing_service=MagicMock(),
        billing_mode=billing_mode,
        budget_micro=budget_micro,
        cancel_controller=controller,
    )


class TestCostGuard:
    def test_trips_when_cumulative_over_budget(self):
        ctrl = CancellationController()
        p = _provider(1000, ctrl)
        p._accumulate_cost({"total_amount_settle_micro": 600})
        assert ctrl.token.is_cancelled is False  # 600 <= 1000
        p._accumulate_cost({"total_amount_settle_micro": 600})
        assert ctrl.token.is_cancelled is True  # 累计 1200 > 1000

    def test_trip_marks_cost_guard_cancel_reason(self):
        ctrl = CancellationController()
        p = _provider(100, ctrl)
        p._accumulate_cost({"total_amount_settle_micro": 200})
        assert ctrl.token.is_cancelled is True
        # 取消带 cost_guard 原因，下游据此按「额度耗尽中止」而非「用户取消」呈现。
        assert ctrl.token.cancel_reason == "cost_guard"

    def test_no_trip_within_budget(self):
        ctrl = CancellationController()
        p = _provider(1000, ctrl)
        p._accumulate_cost({"total_amount_settle_micro": 1000})  # 等于预算不算超
        assert ctrl.token.is_cancelled is False

    def test_no_budget_never_trips(self):
        ctrl = CancellationController()
        p = _provider(None, ctrl)
        p._accumulate_cost({"total_amount_settle_micro": 10**9})
        assert ctrl.token.is_cancelled is False

    def test_no_controller_never_trips(self):
        p = _provider(100, None)
        p._accumulate_cost({"total_amount_settle_micro": 10**9})
        assert p._guard_tripped is False

    def test_trips_only_once_keeps_accumulating(self):
        ctrl = CancellationController()
        p = _provider(100, ctrl)
        p._accumulate_cost({"total_amount_settle_micro": 200})
        assert ctrl.token.is_cancelled is True
        assert p._guard_tripped is True
        p._accumulate_cost({"total_amount_settle_micro": 200})  # 不重复触发、不报错
        assert p._spent_micro == 400

    def test_ignores_zero_missing_and_none(self):
        ctrl = CancellationController()
        p = _provider(100, ctrl)
        p._accumulate_cost({"total_amount_settle_micro": 0})
        p._accumulate_cost({})
        p._accumulate_cost(None)
        assert p._spent_micro == 0
        assert ctrl.token.is_cancelled is False
