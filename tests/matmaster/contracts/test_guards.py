"""Tests for Guard Protocol, GuardContext, GuardResult, and RecentCall."""

from __future__ import annotations

from matmaster.contracts.guards import Guard, GuardContext, GuardResult, RecentCall


# ── RecentCall ──────────────────────────────────────────


class TestRecentCall:
    def test_instantiation(self) -> None:
        rc = RecentCall(
            tool_name="bash",
            tool_args={"cmd": "ls"},
            call_id="c1",
            timestamp=1234.0,
        )
        assert rc.tool_name == "bash"
        assert rc.tool_args == {"cmd": "ls"}
        assert rc.call_id == "c1"
        assert rc.timestamp == 1234.0


# ── GuardContext ────────────────────────────────────────


class TestGuardContext:
    def test_instantiation_with_defaults(self) -> None:
        ctx = GuardContext(
            tool_name="bash",
            tool_args={"cmd": "ls"},
            tool_call_id="tc1",
            current_turn=3,
            max_turns=100,
        )
        assert ctx.tool_name == "bash"
        assert ctx.tool_args == {"cmd": "ls"}
        assert ctx.tool_call_id == "tc1"
        assert ctx.current_turn == 3
        assert ctx.max_turns == 100
        assert ctx.recent_calls == []

    def test_instantiation_with_recent_calls(self) -> None:
        rc = RecentCall(tool_name="bash", tool_args={}, call_id="c1", timestamp=1.0)
        ctx = GuardContext(
            tool_name="bash",
            tool_args={},
            tool_call_id="tc2",
            current_turn=5,
            max_turns=100,
            recent_calls=[rc],
        )
        assert len(ctx.recent_calls) == 1
        assert ctx.recent_calls[0].tool_name == "bash"


# ── GuardResult ─────────────────────────────────────────


class TestGuardResult:
    def test_allowed(self) -> None:
        result = GuardResult(allowed=True)
        assert result.allowed is True
        assert result.reason is None
        assert result.guidance is None

    def test_denied_with_reason_and_guidance(self) -> None:
        result = GuardResult(
            allowed=False, reason="blocked", guidance="stop repeating"
        )
        assert result.allowed is False
        assert result.reason == "blocked"
        assert result.guidance == "stop repeating"


# ── Guard Protocol ──────────────────────────────────────


class _ValidGuard:
    """A class that satisfies the Guard protocol."""

    def evaluate(self, ctx: GuardContext) -> GuardResult:
        return GuardResult(allowed=True)


class _InvalidGuard:
    """A class that does NOT satisfy the Guard protocol (missing evaluate)."""

    pass


class TestGuardProtocol:
    def test_valid_guard_satisfies_protocol(self) -> None:
        guard = _ValidGuard()
        assert isinstance(guard, Guard)

    def test_invalid_guard_does_not_satisfy_protocol(self) -> None:
        obj = _InvalidGuard()
        assert not isinstance(obj, Guard)

    def test_guard_evaluate_returns_result(self) -> None:
        guard = _ValidGuard()
        ctx = GuardContext(
            tool_name="bash",
            tool_args={"cmd": "ls"},
            tool_call_id="tc1",
            current_turn=1,
            max_turns=10,
        )
        result = guard.evaluate(ctx)
        assert result.allowed is True
