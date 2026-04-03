"""Tests for ToolRunner Protocol, InlineToolRunner, and FullToolRunner implementations.

Verifies:
- ToolRunner is @runtime_checkable Protocol
- InlineToolRunner satisfies ToolRunner Protocol
- execute_batch guard deny -> BLOCKED ToolResult
- execute_batch hook SKIP -> "skipped" ToolResult
- execute_batch approved tools run concurrently
- execute_batch on_result callback fires
- execute_batch post_hook called for executed tools
- execute_batch preserves input order
- FullToolRunner stop_mode-aware cancellation
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from unittest.mock import AsyncMock

import pytest

from matmaster.core.hooks import BaseHook, HookAction
from matmaster.core.tool_runner import (
    FullToolRunner,
    InlineToolRunner,
    ToolExecutionContext,
    ToolRunner,
)
from matmaster.tools.tool_catalog import ToolCatalog
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.tools.tool_result import ToolResult
from matmaster.types.guards import GuardContext, GuardResult
from matmaster.types.messages import ToolCallData
from matmaster.types.runtime import AgentRuntimeSpec


# ── Helpers ──────────────────────────────────────────────


class _AllowGuard:
    """Guard that allows everything."""

    def evaluate(self, ctx: GuardContext) -> GuardResult:
        return GuardResult(allowed=True)


class _DenyGuard:
    """Guard that denies a specific tool."""

    def __init__(self, deny_name: str, reason: str = "forbidden") -> None:
        self._deny_name = deny_name
        self._reason = reason

    def evaluate(self, ctx: GuardContext) -> GuardResult:
        if ctx.tool_name == self._deny_name:
            return GuardResult(
                allowed=False,
                reason=self._reason,
                guidance="try something else",
            )
        return GuardResult(allowed=True)


class _SkipHook(BaseHook):
    """Hook that SKIPs a specific tool."""

    def __init__(self, skip_name: str) -> None:
        self._skip_name = skip_name

    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        if tool_call.name == self._skip_name:
            return HookAction.SKIP
        return HookAction.CONTINUE


class _SimpleTool:
    """Minimal tool for testing."""

    def __init__(self, name: str, result: str = "ok") -> None:
        self._name = name
        self._result = result

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"test tool {self._name}"

    @property
    def json_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(content=self._result)


def _make_tc(name: str, call_id: str = "") -> ToolCallData:
    return ToolCallData(id=call_id or f"call_{name}", name=name, arguments={})


def _make_registry(*tool_names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for n in tool_names:
        registry.register(_SimpleTool(n, result=f"result_{n}"), source="test")
    return registry


def _make_spec(
    registry: ToolRegistry,
    guards: list[Any] | None = None,
    hooks: list[Any] | None = None,
) -> AgentRuntimeSpec:
    catalog = ToolCatalog(registry)
    return AgentRuntimeSpec(
        tool_catalog=catalog,
        guards=guards or [],
        hooks=hooks or [],
    )


def _make_ctx(turn: int = 1, max_turns: int = 10) -> ToolExecutionContext:
    return ToolExecutionContext(turn=turn, max_turns=max_turns)


# ── Protocol ─────────────────────────────────────────────


class TestToolRunnerProtocol:
    def test_tool_runner_is_runtime_checkable(self) -> None:
        """ToolRunner Protocol has @runtime_checkable decorator."""
        assert hasattr(ToolRunner, "__protocol_attrs__") or hasattr(
            ToolRunner, "__abstractmethods__"
        )
        # The key check: isinstance works
        registry = _make_registry("test")
        spec = _make_spec(registry)
        runner = InlineToolRunner(spec, [])
        assert isinstance(runner, ToolRunner)

    def test_inline_runner_isinstance_check(self) -> None:
        """InlineToolRunner passes isinstance(runner, ToolRunner)."""
        registry = _make_registry("a")
        spec = _make_spec(registry)
        runner = InlineToolRunner(spec, [])
        assert isinstance(runner, ToolRunner)


# ── Guard Deny ───────────────────────────────────────────


class TestExecuteBatchGuardDeny:
    @pytest.mark.asyncio
    async def test_guard_denied_returns_blocked(self) -> None:
        """Guard deny -> ToolResult with status='blocked'."""
        registry = _make_registry("forbidden_tool")
        guards = [_DenyGuard("forbidden_tool")]
        spec = _make_spec(registry, guards=guards)
        runner = InlineToolRunner(spec, guards)
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("forbidden_tool")], ctx)

        assert len(results) == 1
        tc, tr = results[0]
        assert tc.name == "forbidden_tool"
        assert tr.status == "blocked"
        assert "BLOCKED" in tr.content
        assert "forbidden" in tr.content

    @pytest.mark.asyncio
    async def test_guard_denied_includes_guidance(self) -> None:
        """Guard guidance is included in blocked content."""
        registry = _make_registry("bad_tool")
        guards = [_DenyGuard("bad_tool")]
        spec = _make_spec(registry, guards=guards)
        runner = InlineToolRunner(spec, guards)
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("bad_tool")], ctx)

        _, tr = results[0]
        assert "try something else" in tr.content


# ── Hook Skip ────────────────────────────────────────────


class TestExecuteBatchHookSkip:
    @pytest.mark.asyncio
    async def test_hook_skip_returns_skipped(self) -> None:
        """Hook SKIP -> ToolResult with status='skipped'."""
        registry = _make_registry("skip_me")
        hooks = [_SkipHook("skip_me")]
        spec = _make_spec(registry, hooks=hooks)
        runner = InlineToolRunner(spec, [])
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("skip_me")], ctx)

        assert len(results) == 1
        tc, tr = results[0]
        assert tr.status == "skipped"
        assert "skipped" in tr.content.lower()


# ── Parallel Execution ───────────────────────────────────


class TestExecuteBatchApproved:
    @pytest.mark.asyncio
    async def test_approved_tools_execute_concurrently(self) -> None:
        """Multiple approved tools are gathered (concurrent execution)."""
        registry = _make_registry("a", "b", "c")
        spec = _make_spec(registry)
        runner = InlineToolRunner(spec, [])
        ctx = _make_ctx()

        results = await runner.execute_batch(
            [_make_tc("a"), _make_tc("b"), _make_tc("c")], ctx
        )

        assert len(results) == 3
        assert results[0][1].content == "result_a"
        assert results[1][1].content == "result_b"
        assert results[2][1].content == "result_c"


# ── on_result Callback ───────────────────────────────────


class TestExecuteBatchOnResult:
    @pytest.mark.asyncio
    async def test_on_result_callback_called(self) -> None:
        """on_result callback fires for each tool call result."""
        registry = _make_registry("x")
        spec = _make_spec(registry)
        runner = InlineToolRunner(spec, [])
        ctx = _make_ctx()

        callback_args: list[tuple[ToolCallData, ToolResult]] = []

        async def on_result(tc: ToolCallData, tr: ToolResult) -> None:
            callback_args.append((tc, tr))

        results = await runner.execute_batch(
            [_make_tc("x")], ctx, on_result=on_result
        )

        assert len(callback_args) >= 1
        assert callback_args[0][0].name == "x"

    @pytest.mark.asyncio
    async def test_on_result_called_for_blocked(self) -> None:
        """on_result fires even for blocked tool calls."""
        registry = _make_registry("deny_me")
        guards = [_DenyGuard("deny_me")]
        spec = _make_spec(registry, guards=guards)
        runner = InlineToolRunner(spec, guards)
        ctx = _make_ctx()

        callback_args: list[tuple[ToolCallData, ToolResult]] = []

        async def on_result(tc: ToolCallData, tr: ToolResult) -> None:
            callback_args.append((tc, tr))

        await runner.execute_batch([_make_tc("deny_me")], ctx, on_result=on_result)

        assert len(callback_args) == 1
        assert callback_args[0][1].status == "blocked"


# ── Order Preservation ───────────────────────────────────


class TestExecuteBatchOrderPreserved:
    @pytest.mark.asyncio
    async def test_output_order_matches_input(self) -> None:
        """Results list preserves input tool_calls order."""
        registry = _make_registry("first", "second", "third")
        spec = _make_spec(registry)
        runner = InlineToolRunner(spec, [])
        ctx = _make_ctx()

        tcs = [_make_tc("first"), _make_tc("second"), _make_tc("third")]
        results = await runner.execute_batch(tcs, ctx)

        assert [tc.name for tc, _ in results] == ["first", "second", "third"]

    @pytest.mark.asyncio
    async def test_mixed_deny_allow_order(self) -> None:
        """Mixed blocked+approved results maintain original order."""
        registry = _make_registry("allow1", "deny1", "allow2")
        guards = [_DenyGuard("deny1")]
        spec = _make_spec(registry, guards=guards)
        runner = InlineToolRunner(spec, guards)
        ctx = _make_ctx()

        tcs = [_make_tc("allow1"), _make_tc("deny1"), _make_tc("allow2")]
        results = await runner.execute_batch(tcs, ctx)

        assert len(results) == 3
        assert results[0][0].name == "allow1"
        assert results[0][1].status == "success"
        assert results[1][0].name == "deny1"
        assert results[1][1].status == "blocked"
        assert results[2][0].name == "allow2"
        assert results[2][1].status == "success"


# ── Post-hook ────────────────────────────────────────────


class TestExecuteBatchPostHook:
    @pytest.mark.asyncio
    async def test_post_hook_called_for_executed_tools(self) -> None:
        """post_tool_call hook fires only for executed (not blocked/skipped) tools."""
        post_call_log: list[str] = []

        class _RecordPostHook(BaseHook):
            async def post_tool_call(
                self, tool_call: ToolCallData, result: ToolResult
            ) -> None:
                post_call_log.append(tool_call.name)

        registry = _make_registry("exec_me", "deny_me")
        guards = [_DenyGuard("deny_me")]
        hooks = [_RecordPostHook()]
        spec = _make_spec(registry, guards=guards, hooks=hooks)
        runner = InlineToolRunner(spec, guards)
        ctx = _make_ctx()

        await runner.execute_batch(
            [_make_tc("exec_me"), _make_tc("deny_me")], ctx
        )

        assert "exec_me" in post_call_log
        assert "deny_me" not in post_call_log


# ── Stop event (cancel semantics) ──────────────────────


class TestExecuteBatchStopEvent:
    @pytest.mark.asyncio
    async def test_stop_event_skips_remaining_tools(self) -> None:
        """When stop_event is set during serial phase, remaining tools get cancelled."""
        stop = threading.Event()
        executed: list[str] = []

        class _SetStopOnPreHook(BaseHook):
            """Sets stop_event during pre_tool_call of the first tool."""

            def __init__(self) -> None:
                self._fired = False

            async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
                if not self._fired:
                    self._fired = True
                    stop.set()
                executed.append(tool_call.name)
                return HookAction.CONTINUE

        registry = _make_registry("tool_a", "tool_b", "tool_c")
        hooks = [_SetStopOnPreHook()]
        spec = _make_spec(registry, hooks=hooks)
        runner = InlineToolRunner(spec, [])
        ctx = ToolExecutionContext(turn=1, max_turns=10, stop_event=stop)

        results = await runner.execute_batch(
            [_make_tc("tool_a"), _make_tc("tool_b"), _make_tc("tool_c")], ctx
        )

        assert len(results) == 3
        # tool_a went through pre_hook (which set stop_event) and executed
        assert results[0][1].status == "success"
        # tool_b and tool_c should be cancelled (stop_event was set before their iteration)
        assert results[1][1].status == "cancelled"
        assert results[2][1].status == "cancelled"
        # Only tool_a should have reached pre_tool_call
        assert executed == ["tool_a"]

    @pytest.mark.asyncio
    async def test_stop_event_already_set(self) -> None:
        """If stop_event is already set, all tools are cancelled immediately."""
        stop = threading.Event()
        stop.set()

        registry = _make_registry("t1", "t2")
        spec = _make_spec(registry)
        runner = InlineToolRunner(spec, [])
        ctx = ToolExecutionContext(turn=1, max_turns=10, stop_event=stop)

        results = await runner.execute_batch(
            [_make_tc("t1"), _make_tc("t2")], ctx
        )

        assert all(r[1].status == "cancelled" for r in results)


# ── FullToolRunner stop_mode-aware cancellation ─────────


def _make_full_runner_with_tool(
    tool_name: str,
    stop_mode: str = "cancellable",
    state_mode: str = "stateless",
    result_content: str = "executed",
) -> FullToolRunner:
    """Build a minimal FullToolRunner with a single tool in its catalog."""
    from matmaster.core.guard_pipeline import GuardPipeline
    from matmaster.core.structural_validation import StructuralValidation
    from matmaster.core.tool_scheduler import ToolScheduler
    from matmaster.tools.tool_catalog import ToolCatalog
    from matmaster.tools.tool_compiler import ToolCompiler
    from matmaster.tools.tool_registry import ToolRegistry
    from matmaster.types.topology import RuntimeTopology, ToolPlane

    class _AllowPolicy:
        def evaluate(self, runtime_topology, instance, arguments):
            from matmaster.types.tool_decision import ToolDecision

            return ToolDecision(decision="allow")

    # Build a real catalog through the compiler to get proper stop_mode.
    # But we need to control the stop_mode, so we'll directly construct.
    from matmaster.types.tool_spec import ResourceClaim, ToolBinding, ToolInstance, ToolSpec

    async def _executor(args: dict[str, Any], exec_ctx: Any) -> ToolResult:
        return ToolResult(content=result_content)

    spec = ToolSpec(
        tool_name=tool_name,
        description=f"test {tool_name}",
        args_schema={"type": "object", "properties": {}},
        source="test",
        effect_level="none",
        fast_path_eligible=True,
    )
    binding = ToolBinding(
        binding_key=f"control_plane:{tool_name}",
        plane=ToolPlane.CONTROL_PLANE,
        resource_claims=(),
        state_mode=state_mode,
        stop_mode=stop_mode,
    )
    instance = ToolInstance(
        tool_spec=spec,
        tool_binding=binding,
        tool_executor=_executor,
    )

    # Use a ToolCatalog with pre-built instance (via register_overlay mock)
    registry = ToolRegistry()
    topology = RuntimeTopology(
        session_kind="local",
        control_root="/tmp/control",
        workspace_root="/tmp/workspace",
        active_planes=frozenset(ToolPlane),
    )
    compiler = ToolCompiler()
    catalog = ToolCatalog(registry, compiler=compiler, topology=topology)
    # Directly inject the instance into catalog compiled cache
    catalog._compiled_tools[tool_name] = instance
    catalog._version += 1

    return FullToolRunner(
        catalog=catalog,
        structural_validation=StructuralValidation(),
        guard_pipeline=GuardPipeline([]),
        capability_policy=_AllowPolicy(),
        scheduler=ToolScheduler(),
        topology=topology,
    )


class TestFullToolRunnerStopModeCancel:
    """FullToolRunner uses stop_mode to decide cancel behavior."""

    @pytest.mark.asyncio
    async def test_cancellable_tool_cancelled_when_stop_set(self) -> None:
        """stop_event set + cancellable tool -> ToolResult(status='cancelled')."""
        runner = _make_full_runner_with_tool("test_tool", stop_mode="cancellable")
        stop = threading.Event()
        stop.set()
        ctx = ToolExecutionContext(turn=1, max_turns=10, stop_event=stop)

        results = await runner.execute_batch([_make_tc("test_tool")], ctx)

        assert len(results) == 1
        assert results[0][1].status == "cancelled"
        assert "cancelled" in results[0][1].content.lower()

    @pytest.mark.asyncio
    async def test_best_effort_tool_cancelled_with_message(self) -> None:
        """stop_event set + best_effort tool -> cancelled with best-effort message."""
        runner = _make_full_runner_with_tool("web_tool", stop_mode="best_effort")
        stop = threading.Event()
        stop.set()
        ctx = ToolExecutionContext(turn=1, max_turns=10, stop_event=stop)

        results = await runner.execute_batch([_make_tc("web_tool")], ctx)

        assert len(results) == 1
        assert results[0][1].status == "cancelled"
        assert "best-effort" in results[0][1].content.lower() or "best_effort" in results[0][1].content.lower()

    @pytest.mark.asyncio
    async def test_non_cancellable_tool_executes_when_stop_set(self) -> None:
        """stop_event set + non_cancellable tool -> tool executes normally."""
        runner = _make_full_runner_with_tool(
            "spawn_tool", stop_mode="non_cancellable", result_content="spawn_done"
        )
        stop = threading.Event()
        stop.set()
        ctx = ToolExecutionContext(turn=1, max_turns=10, stop_event=stop)

        results = await runner.execute_batch([_make_tc("spawn_tool")], ctx)

        assert len(results) == 1
        assert results[0][1].status == "success"
        assert results[0][1].content == "spawn_done"

    @pytest.mark.asyncio
    async def test_no_stop_event_all_modes_execute(self) -> None:
        """No stop_event -> all tools execute regardless of stop_mode."""
        for mode in ("cancellable", "best_effort", "non_cancellable"):
            runner = _make_full_runner_with_tool(
                f"tool_{mode}", stop_mode=mode, result_content=f"result_{mode}"
            )
            ctx = ToolExecutionContext(turn=1, max_turns=10)

            results = await runner.execute_batch([_make_tc(f"tool_{mode}")], ctx)

            assert len(results) == 1
            assert results[0][1].status == "success", f"Failed for mode={mode}"
            assert results[0][1].content == f"result_{mode}"
