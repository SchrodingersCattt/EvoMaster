"""Tests for business guard injection via Exp.assemble().

Phase 6: ManuscriptGateGuard and AuthFailureGateGuard shells removed.
Tests updated to use inline stub guards that satisfy the Guard Protocol.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from matmaster.core.guard_pipeline import GuardPipeline
from matmaster.types.messages import ToolCallData
from matmaster.types.context import PlaygroundContext
from matmaster.types.guards import Guard, GuardContext, GuardResult


class _StubGuard:
    """Inline guard satisfying Guard Protocol for testing."""

    def evaluate(self, ctx: GuardContext) -> GuardResult:
        return GuardResult(allowed=True)


def _make_guard_ctx() -> GuardContext:
    return GuardContext(
        tool_name="test_tool",
        tool_args={"key": "value"},
        tool_call_id="tc-1",
        current_turn=1,
        max_turns=100,
    )


class TestGuardProtocol:
    def test_stub_guard_implements_protocol(self) -> None:
        """Stub guard instance passes isinstance(guard, Guard) check."""
        guard = _StubGuard()
        assert isinstance(guard, Guard)

    def test_stub_guard_evaluate_returns_result(self) -> None:
        """Stub guard evaluate(ctx) returns GuardResult instance."""
        guard = _StubGuard()
        result = guard.evaluate(_make_guard_ctx())
        assert isinstance(result, GuardResult)
        assert result.allowed is True


class TestGuardInjection:
    def test_guards_injected_via_assemble(self) -> None:
        """Create Exp with guards in config, assemble(ctx) returns spec with guards."""
        from matmaster.core.exp import Exp

        ctx = PlaygroundContext(
            workdir=Path("/tmp/test"),
            session_type="local",
            cache_area=Path("/tmp/cache"),
        )
        guard = _StubGuard()
        config = {
            "name": "direct",
            "guards": [guard],
            "termination": {"max_turns": 100},
            "tools": {"builtin": []},
            "prompt": {},
            "context": {},
            "skills": {},
            "mcp": {},
        }
        exp = Exp(config)
        spec = exp.assemble(ctx)
        assert guard in spec.guards

    def test_guards_available_to_pipeline(self) -> None:
        """GuardPipeline with business guard in external_guards calls the guard."""
        guard = _StubGuard()
        pipeline = GuardPipeline(external_guards=[guard])
        tc = ToolCallData(id="tc-1", name="test_tool", arguments={"key": "value"})
        result = pipeline.evaluate(tc, current_turn=1, max_turns=100)
        assert result.allowed is True
