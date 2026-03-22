"""Tests for matmaster.core.guard_pipeline -- LoopDetectionGuard and GuardPipeline."""

from __future__ import annotations

from typing import Any

import pytest

from matmaster.types.guards import Guard, GuardContext, GuardResult, RecentCall
from matmaster.core.guard_pipeline import (
    LOOP_THRESHOLD,
    LOOP_WINDOW,
    GuardPipeline,
    LoopDetectionGuard,
)
from matmaster.types.messages import ToolCallData
from tests.matmaster.core.conftest import make_tool_call


# ── Helper: build a GuardContext with recent_calls ────


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


# ── LoopDetectionGuard ───────────────────────────────


class TestLoopDetectionGuard:
    def test_first_call_allowed(self) -> None:
        """First call to a tool is always allowed."""
        guard = LoopDetectionGuard()
        ctx = _make_context("fn", {"a": 1}, recent_calls=[])
        result = guard.evaluate(ctx)
        assert result.allowed is True

    def test_repeated_call_below_threshold(self) -> None:
        """One previous identical call (below threshold of 2) is allowed."""
        guard = LoopDetectionGuard()
        recent = [_make_recent("fn", {"a": 1})]
        ctx = _make_context("fn", {"a": 1}, recent_calls=recent)
        result = guard.evaluate(ctx)
        assert result.allowed is True

    def test_repeated_call_at_threshold(self) -> None:
        """Two identical calls in recent history (== threshold) triggers deny."""
        guard = LoopDetectionGuard()
        recent = [_make_recent("fn", {"a": 1}), _make_recent("fn", {"a": 1})]
        ctx = _make_context("fn", {"a": 1}, recent_calls=recent)
        result = guard.evaluate(ctx)
        assert result.allowed is False
        assert "Loop detected" in (result.reason or "")

    def test_different_tool_name_allowed(self) -> None:
        """Same args but different tool name -> allowed."""
        guard = LoopDetectionGuard()
        recent = [
            _make_recent("fn", {"a": 1}),
            _make_recent("fn", {"a": 1}),
        ]
        ctx = _make_context("other_fn", {"a": 1}, recent_calls=recent)
        result = guard.evaluate(ctx)
        assert result.allowed is True

    def test_different_args_allowed(self) -> None:
        """Same tool name but different args -> allowed."""
        guard = LoopDetectionGuard()
        recent = [
            _make_recent("fn", {"a": 1}),
            _make_recent("fn", {"a": 1}),
        ]
        ctx = _make_context("fn", {"a": 2}, recent_calls=recent)
        result = guard.evaluate(ctx)
        assert result.allowed is True

    def test_window_limits_lookup(self) -> None:
        """Calls beyond the window do not count toward threshold."""
        guard = LoopDetectionGuard(window=3, threshold=2)
        # 5 calls total, but window=3 means only last 3 are considered
        recent = [
            _make_recent("fn", {"a": 1}),  # outside window (idx 0)
            _make_recent("fn", {"a": 1}),  # outside window (idx 1)
            _make_recent("other", {"b": 2}),  # inside window
            _make_recent("other", {"b": 2}),  # inside window
            _make_recent("other", {"b": 2}),  # inside window
        ]
        ctx = _make_context("fn", {"a": 1}, recent_calls=recent)
        result = guard.evaluate(ctx)
        assert result.allowed is True  # no matches in last 3

    def test_guard_result_has_guidance(self) -> None:
        """Denied result includes guidance for the LLM."""
        guard = LoopDetectionGuard()
        recent = [_make_recent("fn", {"a": 1}), _make_recent("fn", {"a": 1})]
        ctx = _make_context("fn", {"a": 1}, recent_calls=recent)
        result = guard.evaluate(ctx)
        assert result.guidance is not None
        assert len(result.guidance) > 0


# ── GuardPipeline ─────────────────────────────────────


class AlwaysAllowGuard:
    """External guard that always allows."""

    def evaluate(self, ctx: GuardContext) -> GuardResult:
        return GuardResult(allowed=True)


class AlwaysDenyGuard:
    """External guard that always denies."""

    def evaluate(self, ctx: GuardContext) -> GuardResult:
        return GuardResult(allowed=False, reason="Denied by external guard")


class TrackingGuard:
    """Guard that tracks whether evaluate was called."""

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
        """Pipeline with no external guards has exactly 1 guard (LoopDetectionGuard)."""
        pipeline = GuardPipeline()
        assert len(pipeline._guards) == 1
        assert isinstance(pipeline._guards[0], LoopDetectionGuard)

    def test_pipeline_order(self) -> None:
        """LoopDetectionGuard is first, external guards follow in order."""
        ext1 = AlwaysAllowGuard()
        ext2 = AlwaysAllowGuard()
        pipeline = GuardPipeline(external_guards=[ext1, ext2])
        assert len(pipeline._guards) == 3
        assert isinstance(pipeline._guards[0], LoopDetectionGuard)
        assert pipeline._guards[1] is ext1
        assert pipeline._guards[2] is ext2

    def test_all_allow(self) -> None:
        """All guards allow -> returns allowed=True."""
        pipeline = GuardPipeline(external_guards=[AlwaysAllowGuard()])
        tc = make_tool_call("fn", {"a": 1})
        result = pipeline.evaluate(tc, current_turn=1, max_turns=10)
        assert result.allowed is True

    def test_first_deny_wins(self) -> None:
        """LoopDetectionGuard denies -> external guards not called."""
        tracker = TrackingGuard(allowed=True)
        pipeline = GuardPipeline(external_guards=[tracker])
        tc = make_tool_call("fn", {"a": 1})
        # Fill recent_calls to trigger loop detection
        for _ in range(3):
            pipeline.evaluate(tc, current_turn=1, max_turns=10)
        # Now loop guard should deny
        result = pipeline.evaluate(tc, current_turn=1, max_turns=10)
        assert result.allowed is False
        assert "Loop detected" in (result.reason or "")

    def test_external_deny(self) -> None:
        """External guard denies -> returns external guard's result."""
        tracker1 = TrackingGuard(allowed=True)
        deny_guard = AlwaysDenyGuard()
        pipeline = GuardPipeline(external_guards=[tracker1, deny_guard])
        tc = make_tool_call("fn", {"a": 1})
        result = pipeline.evaluate(tc, current_turn=1, max_turns=10)
        assert result.allowed is False
        assert result.reason == "Denied by external guard"
        # First external guard was called
        assert tracker1.called is True

    def test_records_call_after_all_pass(self) -> None:
        """Evaluate records call to recent_calls only after all guards pass."""
        pipeline = GuardPipeline()
        tc = make_tool_call("fn", {"a": 1})
        assert len(pipeline._recent_calls) == 0
        pipeline.evaluate(tc, current_turn=1, max_turns=10)
        assert len(pipeline._recent_calls) == 1
        assert pipeline._recent_calls[0].tool_name == "fn"

    def test_no_state_leakage(self) -> None:
        """New GuardPipeline instance has empty recent_calls."""
        pipeline1 = GuardPipeline()
        tc = make_tool_call("fn", {"a": 1})
        pipeline1.evaluate(tc, current_turn=1, max_turns=10)
        assert len(pipeline1._recent_calls) == 1

        pipeline2 = GuardPipeline()
        assert len(pipeline2._recent_calls) == 0

    def test_deny_does_not_record_call(self) -> None:
        """When a guard denies, the call is NOT recorded in recent_calls."""
        pipeline = GuardPipeline(external_guards=[AlwaysDenyGuard()])
        tc = make_tool_call("fn", {"a": 1})
        result = pipeline.evaluate(tc, current_turn=1, max_turns=10)
        assert result.allowed is False
        assert len(pipeline._recent_calls) == 0
