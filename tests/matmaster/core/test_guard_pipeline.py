"""Tests for matmaster.core.guard_pipeline -- LoopDetectionGuard and GuardPipeline."""

from __future__ import annotations

from typing import Any

from matmaster.core.guard_pipeline import GuardPipeline, LoopDetectionGuard
from matmaster.types.guards import GuardContext, GuardResult, RecentCall
from tests.matmaster.core.conftest import make_tool_call


def _make_context(
    tool_name: str = "fn",
    tool_args: dict[str, Any] | None = None,
    recent_calls: list[RecentCall] | None = None,
) -> GuardContext:
    return GuardContext(
        tool_name=tool_name,
        tool_args=tool_args if tool_args is not None else {"a": 1},
        tool_call_id="tc-1",
        current_turn=1,
        max_turns=10,
        recent_calls=recent_calls or [],
    )


def _make_recent(
    tool_name: str = "fn",
    tool_args: dict[str, Any] | None = None,
) -> RecentCall:
    import time

    return RecentCall(
        tool_name=tool_name,
        tool_args=tool_args if tool_args is not None else {"a": 1},
        call_id="rc-1",
        timestamp=time.monotonic(),
    )


class TestLoopDetectionGuard:
    def test_first_call_allowed(self) -> None:
        guard = LoopDetectionGuard()
        result = guard.evaluate(_make_context("fn", {"a": 1}, recent_calls=[]))

        assert result.allowed is True

    def test_repeated_call_at_threshold_denied(self) -> None:
        guard = LoopDetectionGuard()
        recent = [_make_recent("fn", {"a": 1}), _make_recent("fn", {"a": 1})]

        result = guard.evaluate(_make_context("fn", {"a": 1}, recent_calls=recent))

        assert result.allowed is False
        assert "Loop detected" in (result.reason or "")
        assert result.guidance is not None

    def test_different_args_allowed(self) -> None:
        guard = LoopDetectionGuard()
        recent = [_make_recent("fn", {"a": 1}), _make_recent("fn", {"a": 1})]

        result = guard.evaluate(_make_context("fn", {"a": 2}, recent_calls=recent))

        assert result.allowed is True

    def test_different_tool_name_allowed(self) -> None:
        guard = LoopDetectionGuard()
        recent = [_make_recent("fn", {"a": 1}), _make_recent("fn", {"a": 1})]

        result = guard.evaluate(_make_context("other_fn", {"a": 1}, recent_calls=recent))

        assert result.allowed is True

    def test_window_limits_lookup(self) -> None:
        guard = LoopDetectionGuard(window=3, threshold=2)
        recent = [
            _make_recent("fn", {"a": 1}),
            _make_recent("fn", {"a": 1}),
            _make_recent("other", {"b": 2}),
            _make_recent("other", {"b": 2}),
            _make_recent("other", {"b": 2}),
        ]

        result = guard.evaluate(_make_context("fn", {"a": 1}, recent_calls=recent))

        assert result.allowed is True


class AlwaysAllowGuard:
    def evaluate(self, ctx: GuardContext) -> GuardResult:
        return GuardResult(allowed=True)


class AlwaysDenyGuard:
    def evaluate(self, ctx: GuardContext) -> GuardResult:
        return GuardResult(allowed=False, reason="Denied by external guard")


class TrackingGuard:
    def __init__(self, allowed: bool = True) -> None:
        self.called = False
        self._allowed = allowed

    def evaluate(self, ctx: GuardContext) -> GuardResult:
        self.called = True
        return GuardResult(
            allowed=self._allowed,
            reason=None if self._allowed else "Tracking guard denied",
        )


class TestGuardPipeline:
    def test_builtin_not_removable(self) -> None:
        pipeline = GuardPipeline()

        assert len(pipeline._guards) == 1
        assert isinstance(pipeline._guards[0], LoopDetectionGuard)

    def test_pipeline_order(self) -> None:
        ext1 = AlwaysAllowGuard()
        ext2 = AlwaysAllowGuard()
        pipeline = GuardPipeline(external_guards=[ext1, ext2])

        assert len(pipeline._guards) == 3
        assert isinstance(pipeline._guards[0], LoopDetectionGuard)
        assert pipeline._guards[1] is ext1
        assert pipeline._guards[2] is ext2

    def test_all_allow(self) -> None:
        pipeline = GuardPipeline(external_guards=[AlwaysAllowGuard()])
        tc = make_tool_call("fn", {"a": 1})

        result = pipeline.evaluate(tc, current_turn=1, max_turns=10)

        assert result.allowed is True

    def test_first_deny_wins(self) -> None:
        tracker = TrackingGuard(allowed=True)
        pipeline = GuardPipeline(external_guards=[tracker])
        tc = make_tool_call("fn", {"a": 1})

        pipeline.evaluate(tc, current_turn=1, max_turns=10)
        pipeline.evaluate(tc, current_turn=1, max_turns=10)
        tracker.called = False

        result = pipeline.evaluate(tc, current_turn=1, max_turns=10)

        assert result.allowed is False
        assert "Loop detected" in (result.reason or "")
        assert tracker.called is False

    def test_external_deny(self) -> None:
        tracker = TrackingGuard(allowed=True)
        deny_guard = AlwaysDenyGuard()
        pipeline = GuardPipeline(external_guards=[tracker, deny_guard])
        tc = make_tool_call("fn", {"a": 1})

        result = pipeline.evaluate(tc, current_turn=1, max_turns=10)

        assert result.allowed is False
        assert result.reason == "Denied by external guard"
        assert tracker.called is True

    def test_records_call_after_all_pass(self) -> None:
        pipeline = GuardPipeline()
        tc = make_tool_call("fn", {"a": 1})

        pipeline.evaluate(tc, current_turn=1, max_turns=10)

        assert len(pipeline._recent_calls) == 1
        assert pipeline._recent_calls[0].tool_name == "fn"

    def test_no_state_leakage(self) -> None:
        tc = make_tool_call("fn", {"a": 1})
        pipeline1 = GuardPipeline()
        pipeline1.evaluate(tc, current_turn=1, max_turns=10)

        pipeline2 = GuardPipeline()

        assert len(pipeline1._recent_calls) == 1
        assert len(pipeline2._recent_calls) == 0

    def test_deny_does_not_record_call(self) -> None:
        pipeline = GuardPipeline(external_guards=[AlwaysDenyGuard()])
        tc = make_tool_call("fn", {"a": 1})

        result = pipeline.evaluate(tc, current_turn=1, max_turns=10)

        assert result.allowed is False
        assert len(pipeline._recent_calls) == 0
