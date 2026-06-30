"""BillingRunState 的 in-run 成本熔断（防线二）+ call_index 计数。

只测同步的成本累加 + 熔断触发逻辑，不触发真实 LLM / HTTP。
"""

from __future__ import annotations

from matmaster.types.cancellation import CancellationController
from src.services.billing_llm_provider import BillingRunState


def _state(budget_micro, controller):
    return BillingRunState(
        session_id="s", budget_micro=budget_micro, cancel_controller=controller
    )


class TestCostGuard:
    def test_trips_when_cumulative_over_budget(self):
        ctrl = CancellationController()
        st = _state(1000, ctrl)
        st.accumulate(600)
        assert ctrl.token.is_cancelled is False
        st.accumulate(600)
        assert ctrl.token.is_cancelled is True

    def test_trip_marks_cost_guard_cancel_reason(self):
        ctrl = CancellationController()
        st = _state(100, ctrl)
        st.accumulate(200)
        assert ctrl.token.is_cancelled is True
        assert ctrl.token.cancel_reason == "cost_guard"

    def test_no_trip_within_budget(self):
        ctrl = CancellationController()
        st = _state(1000, ctrl)
        st.accumulate(1000)
        assert ctrl.token.is_cancelled is False

    def test_no_budget_never_trips(self):
        ctrl = CancellationController()
        st = _state(None, ctrl)
        st.accumulate(10**9)
        assert ctrl.token.is_cancelled is False

    def test_no_controller_never_trips(self):
        st = _state(100, None)
        st.accumulate(10**9)
        assert st._guard_tripped is False

    def test_trips_only_once_keeps_accumulating(self):
        ctrl = CancellationController()
        st = _state(100, ctrl)
        st.accumulate(200)
        assert ctrl.token.is_cancelled is True
        st.accumulate(200)
        assert st._spent_micro == 400

    def test_ignores_non_positive(self):
        ctrl = CancellationController()
        st = _state(100, ctrl)
        st.accumulate(0)
        st.accumulate(-5)
        assert st._spent_micro == 0
        assert ctrl.token.is_cancelled is False

    def test_call_index_monotonic_shared(self):
        # 共享 state：两个 wrapper 取到的 call_index 全 run 单调
        st = _state(None, None)
        assert [st.next_call_index() for _ in range(3)] == [1, 2, 3]
