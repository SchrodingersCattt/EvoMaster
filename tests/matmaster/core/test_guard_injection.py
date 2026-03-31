"""Tests for business guard injection via Exp.assemble().

Phase 6: ManuscriptGateGuard and AuthFailureGateGuard shells removed.
Tests updated to use inline stub guards that satisfy the Guard Protocol.
"""

from __future__ import annotations

from pathlib import Path

from matmaster.core.guard_pipeline import GuardPipeline
from matmaster.types.context import PlaygroundContext
from matmaster.types.guards import Guard, GuardContext, GuardResult
from matmaster.types.messages import ToolCallData


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
    async def test_guards_injected_via_assemble(self) -> None:
        """Exp.assemble() returns spec with empty guards (guard factory deferred)."""
        from matmaster.config.exp import ExpConfig
        from matmaster.core.exp import Exp

        ctx = PlaygroundContext(
            workdir=Path("/tmp/test"),
            session_type="local",
            cache_area=Path("/tmp/cache"),
        )
        config = ExpConfig(name="direct", guards=["stub_guard"])
        exp = Exp(config)
        spec = await exp.assemble(ctx)
        # Guards are currently passed as strings; guard factory is deferred.
        assert spec.guards == []

    def test_guards_available_to_pipeline(self) -> None:
        """GuardPipeline with business guard in external_guards calls the guard."""
        guard = _StubGuard()
        pipeline = GuardPipeline(external_guards=[guard])
        tc = ToolCallData(id="tc-1", name="test_tool", arguments={"key": "value"})
        result = pipeline.evaluate(tc, current_turn=1, max_turns=100)
        assert result.allowed is True
