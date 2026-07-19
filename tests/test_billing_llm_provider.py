"""BillingRunState 的 in-run 熔断（预算快照 + 欠费信号）+ call_index 计数，
以及 BillingLLMProvider 的可重入 enter/exit 生命周期。

不触发真实 LLM / HTTP 请求（生命周期用例会创建真实 aiohttp session 但不发请求）。
"""

from __future__ import annotations

from clients.matmaster_platform.billing.client import BillingRunContext
from matmaster.types.cancellation import CancellationController
from src.services.billing_llm_provider import BillingLLMProvider, BillingRunState


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


class TestDebtGuard:
    """欠费熔断：结算响应 uncovered_micro>0 即停，不看预算快照。

    针对并行会话抽干共享余额的场景——每个 run 的预算快照在启动时拍下，
    其它会话花掉的钱对本 run 不可见，池子空了以后本 run 仍会在欠费上跑。
    """

    def test_uncovered_trips_immediately_without_budget(self):
        # 预算快照缺失（旧平台/查询失败）也要能触发：欠费信号不依赖快照。
        ctrl = CancellationController()
        st = _state(None, ctrl)
        st.report_uncovered(1)
        assert ctrl.token.is_cancelled is True
        assert ctrl.token.cancel_reason == "cost_guard"

    def test_uncovered_trips_within_budget(self):
        # 自己的花费远没撞线，但这笔已经没人买单：立即停。
        ctrl = CancellationController()
        st = _state(10**9, ctrl)
        st.accumulate(100)
        assert ctrl.token.is_cancelled is False
        st.report_uncovered(155)
        assert ctrl.token.is_cancelled is True

    def test_zero_uncovered_never_trips(self):
        ctrl = CancellationController()
        st = _state(10**9, ctrl)
        st.report_uncovered(0)
        st.report_uncovered(-5)
        assert ctrl.token.is_cancelled is False

    def test_trips_only_once(self):
        ctrl = CancellationController()
        st = _state(None, ctrl)
        st.report_uncovered(100)
        st.report_uncovered(100)
        assert ctrl.token.is_cancelled is True

    def test_no_controller_never_trips(self):
        st = _state(None, None)
        st.report_uncovered(100)
        assert st._guard_tripped is False


class _FakeInner:
    def __init__(self):
        self.enters = 0
        self.exits = 0

    async def __aenter__(self):
        self.enters += 1
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.exits += 1


def _provider(inner):
    return BillingLLMProvider(
        inner,
        run_context=BillingRunContext(session_id="s", task_id="t", invocation_id="i"),
        model="m",
        billing_service=object(),
        run_state=BillingRunState(session_id="s"),
    )


class TestReentrantLifecycle:
    """可重入 enter/exit：子 exp 无 llm 覆盖时子内核会对父 run 的同一实例再次
    async with，重入不得覆盖外层 session（孤儿泄漏）或提前关闭它。"""

    async def test_nested_enter_reuses_outer_session(self):
        p = _provider(_FakeInner())
        async with p:
            outer_session = p._http_session
            assert outer_session is not None
            async with p:
                assert p._http_session is outer_session
            # 内层退出后 session 仍存活，收尾归最外层
            assert p._http_session is outer_session
            assert not outer_session.closed
        assert outer_session.closed
        assert p._http_session is None

    async def test_inner_provider_enter_exit_balanced(self):
        inner = _FakeInner()
        p = _provider(inner)
        async with p:
            async with p:
                pass
        assert inner.enters == 2
        assert inner.exits == 2

    async def test_single_use_closes_session(self):
        p = _provider(_FakeInner())
        async with p:
            session = p._http_session
        assert session.closed
        assert p._http_session is None
