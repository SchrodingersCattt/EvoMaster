"""Tests for FullToolRunner -- complete seven-step execution chain.

Verifies:
- Catalog miss -> ToolResult(status='error', meta={'layer': 'catalog'})
- StructuralValidation deny -> ToolResult(status='error', meta={'layer': 'structural'})
- GuardPipeline deny -> ToolResult(status='error', meta={'layer': 'guard'})
- CapabilityPolicy deny -> ToolResult(status='error', meta={'layer': 'policy'})
- Scheduler timeout -> ToolResult(status='error', meta={'layer': 'scheduler'})
- Fast path skips Scheduler but not CapabilityPolicy
- Executor exception -> ToolResult.from_error() + ticket released
- Cancel semantics -> skip remaining tool_calls
- Happy path -> executor result + on_result callback
- isinstance(FullToolRunner(...), ToolRunner) check
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matmaster.core.capability_policy import DefaultCapabilityPolicy
from matmaster.core.guard_pipeline import GuardPipeline
from matmaster.core.structural_validation import StructuralValidation
from matmaster.core.tool_runner import (
    FullToolRunner,
    ToolExecutionContext,
    ToolRunner,
)
from matmaster.core.tool_scheduler import SchedulerTicket, ToolScheduler
from matmaster.tools.tool_catalog import ToolCatalog
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.tools.tool_result import ToolResult
from matmaster.types.messages import ToolCallData
from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_spec import ResourceClaim, ToolBinding, ToolInstance, ToolSpec
from matmaster.types.topology import RuntimeTopology, SessionCapabilities, ToolPlane


# ── Helpers ──────────────────────────────────────────────


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


class _ErrorTool:
    """Tool that raises RuntimeError on execute."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"error tool {self._name}"

    @property
    def json_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        raise RuntimeError("boom")


def _make_tc(name: str, call_id: str = "", **args: Any) -> ToolCallData:
    return ToolCallData(id=call_id or f"call_{name}", name=name, arguments=args)


def _make_topology(
    all_planes: bool = True,
    session_caps: SessionCapabilities | None = None,
) -> RuntimeTopology:
    planes = frozenset(ToolPlane) if all_planes else frozenset()
    return RuntimeTopology(
        session_kind="local",
        control_root="/tmp/ctrl",
        workspace_root="/tmp/ws",
        active_planes=planes,
        session_capabilities=session_caps,
    )


def _make_catalog(*tool_names: str) -> ToolCatalog:
    registry = ToolRegistry()
    for n in tool_names:
        registry.register(_SimpleTool(n, result=f"result_{n}"), source="builtin")
    return ToolCatalog(registry)


def _make_catalog_with_error(name: str) -> ToolCatalog:
    registry = ToolRegistry()
    registry.register(_ErrorTool(name), source="builtin")
    return ToolCatalog(registry)


def _make_runner(
    catalog: ToolCatalog,
    topology: RuntimeTopology | None = None,
    policy: Any | None = None,
    scheduler: ToolScheduler | None = None,
) -> FullToolRunner:
    return FullToolRunner(
        catalog=catalog,
        structural_validation=StructuralValidation(),
        guard_pipeline=GuardPipeline(),
        capability_policy=policy or DefaultCapabilityPolicy(),
        scheduler=scheduler or ToolScheduler(default_timeout=1.0),
        topology=topology or _make_topology(),
    )


def _make_ctx(turn: int = 1, max_turns: int = 10) -> ToolExecutionContext:
    return ToolExecutionContext(turn=turn, max_turns=max_turns)


# ── Protocol Check ───────────────────────────────────────


class TestFullToolRunnerProtocol:
    def test_isinstance_check(self) -> None:
        """FullToolRunner satisfies ToolRunner Protocol."""
        catalog = _make_catalog("test")
        runner = _make_runner(catalog)
        assert isinstance(runner, ToolRunner)


# ── Catalog Miss ─────────────────────────────────────────


class TestCatalogMiss:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_catalog_error(self) -> None:
        """Unknown tool_name -> ToolResult(status='error', meta={'layer': 'catalog'})."""
        catalog = _make_catalog()  # empty catalog
        runner = _make_runner(catalog)
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("nonexistent")], ctx)

        assert len(results) == 1
        tc, tr = results[0]
        assert tr.status == "error"
        assert "Unknown tool" in tr.content
        assert tr.meta["layer"] == "catalog"


# ── Structural Deny ──────────────────────────────────────


class TestStructuralDeny:
    @pytest.mark.asyncio
    async def test_structural_validation_deny(self) -> None:
        """StructuralValidation deny -> error with layer='structural'."""
        catalog = _make_catalog("test_tool")
        # Create topology with no active planes -> plane check fails
        topology = RuntimeTopology(
            session_kind="local",
            control_root="/tmp/ctrl",
            workspace_root="/tmp/ws",
            active_planes=frozenset(),  # no planes active
        )
        runner = _make_runner(catalog, topology=topology)
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("test_tool")], ctx)

        assert len(results) == 1
        tc, tr = results[0]
        assert tr.status == "error"
        assert tr.meta["layer"] == "structural"


# ── Guard Deny ───────────────────────────────────────────


class TestGuardDeny:
    @pytest.mark.asyncio
    async def test_guard_pipeline_deny(self) -> None:
        """GuardPipeline deny -> error with layer='guard'."""
        catalog = _make_catalog("looped_tool")
        topology = _make_topology()

        # Trigger LoopDetectionGuard by repeating the same call
        runner = _make_runner(catalog, topology=topology)
        ctx = _make_ctx()

        # First two identical calls should pass; the third should trigger loop
        tcs = [
            _make_tc("looped_tool", "c1"),
            _make_tc("looped_tool", "c2"),
            _make_tc("looped_tool", "c3"),
        ]
        results = await runner.execute_batch(tcs, ctx)

        # The third call (or later) should be denied by loop detection
        guard_denied = [
            (tc, tr) for tc, tr in results if tr.meta.get("layer") == "guard"
        ]
        assert len(guard_denied) >= 1
        assert guard_denied[0][1].status == "error"


# ── Policy Deny ──────────────────────────────────────────


class TestPolicyDeny:
    @pytest.mark.asyncio
    async def test_policy_deny_returns_policy_error(self) -> None:
        """CapabilityPolicy deny -> error with layer='policy' and guidance."""

        class DenyAllPolicy:
            def evaluate(
                self,
                runtime_topology: RuntimeTopology,
                tool_instance: ToolInstance,
                tool_args: dict[str, Any],
            ) -> ToolDecision:
                return ToolDecision(
                    decision="deny",
                    reason="Policy forbids this",
                    guidance="Try something else",
                )

        catalog = _make_catalog("policy_tool")
        runner = _make_runner(catalog, policy=DenyAllPolicy())
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("policy_tool")], ctx)

        assert len(results) == 1
        tc, tr = results[0]
        assert tr.status == "error"
        assert tr.meta["layer"] == "policy"
        assert "guidance" in tr.meta
        assert tr.meta["guidance"] == "Try something else"


# ── Scheduler Timeout ────────────────────────────────────


class TestSchedulerTimeout:
    @pytest.mark.asyncio
    async def test_scheduler_timeout_returns_scheduler_error(self) -> None:
        """Scheduler acquire timeout -> error with layer='scheduler'."""
        catalog = _make_catalog("sched_tool")
        scheduler = ToolScheduler(default_timeout=0.01)
        runner = _make_runner(catalog, scheduler=scheduler)
        ctx = _make_ctx()

        # Mock acquire to return None (timeout)
        scheduler.acquire = AsyncMock(return_value=None)

        results = await runner.execute_batch([_make_tc("sched_tool")], ctx)

        assert len(results) == 1
        tc, tr = results[0]
        assert tr.status == "error"
        assert tr.meta["layer"] == "scheduler"


# ── Fast Path ────────────────────────────────────────────


class TestFastPath:
    @pytest.mark.asyncio
    async def test_fast_path_skips_scheduler(self) -> None:
        """Fast path: effect_level='pure_read', shared_read, fast_path_eligible -> skip Scheduler."""
        registry = ToolRegistry()
        registry.register(_SimpleTool("fast_tool", "fast_result"), source="builtin")
        catalog = ToolCatalog(registry)

        # Patch get_tool to return a fast-path-eligible ToolInstance
        original_get_tool = catalog.get_tool

        def patched_get_tool(name: str) -> ToolInstance | None:
            instance = original_get_tool(name)
            if instance is None:
                return None
            # Override spec and binding for fast path eligibility
            spec = ToolSpec(
                tool_name=name,
                description="fast tool",
                args_schema={},
                source="builtin",
                effect_level="pure_read",
                fast_path_eligible=True,
            )
            binding = ToolBinding(
                binding_key=f"session_fs:{name}",
                plane=ToolPlane.SESSION_FS,
                resource_claims=(
                    ResourceClaim(resource_id="workspace", mode="shared_read"),
                ),
            )
            return ToolInstance(
                tool_spec=spec,
                tool_binding=binding,
                tool_executor=instance.tool_executor,
            )

        catalog.get_tool = patched_get_tool  # type: ignore

        scheduler = ToolScheduler(default_timeout=1.0)
        scheduler.acquire = AsyncMock(return_value=SchedulerTicket())
        scheduler.release = AsyncMock()

        runner = _make_runner(catalog, scheduler=scheduler)
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("fast_tool")], ctx)

        assert len(results) == 1
        tc, tr = results[0]
        assert tr.content == "fast_result"
        # Scheduler should NOT have been called
        scheduler.acquire.assert_not_awaited()
        scheduler.release.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fast_path_still_checks_policy(self) -> None:
        """Fast path still goes through CapabilityPolicy."""

        class DenyAllPolicy:
            def evaluate(
                self,
                runtime_topology: RuntimeTopology,
                tool_instance: ToolInstance,
                tool_args: dict[str, Any],
            ) -> ToolDecision:
                return ToolDecision(
                    decision="deny",
                    reason="Policy forbids this",
                    guidance="Nope",
                )

        registry = ToolRegistry()
        registry.register(_SimpleTool("fast_tool", "fast_result"), source="builtin")
        catalog = ToolCatalog(registry)

        original_get_tool = catalog.get_tool

        def patched_get_tool(name: str) -> ToolInstance | None:
            instance = original_get_tool(name)
            if instance is None:
                return None
            spec = ToolSpec(
                tool_name=name,
                description="fast tool",
                args_schema={},
                source="builtin",
                effect_level="pure_read",
                fast_path_eligible=True,
            )
            binding = ToolBinding(
                binding_key=f"session_fs:{name}",
                plane=ToolPlane.SESSION_FS,
                resource_claims=(
                    ResourceClaim(resource_id="workspace", mode="shared_read"),
                ),
            )
            return ToolInstance(
                tool_spec=spec,
                tool_binding=binding,
                tool_executor=instance.tool_executor,
            )

        catalog.get_tool = patched_get_tool  # type: ignore

        runner = _make_runner(catalog, policy=DenyAllPolicy())
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("fast_tool")], ctx)

        assert len(results) == 1
        tc, tr = results[0]
        assert tr.status == "error"
        assert tr.meta["layer"] == "policy"


# ── Executor Exception ───────────────────────────────────


class TestExecutorException:
    @pytest.mark.asyncio
    async def test_executor_exception_releases_ticket(self) -> None:
        """Executor raises -> from_error() + scheduler ticket released (await)."""
        catalog = _make_catalog_with_error("error_tool")
        scheduler = ToolScheduler(default_timeout=1.0)

        mock_ticket = SchedulerTicket(resource_locks=[("session", "exclusive")])
        scheduler.acquire = AsyncMock(return_value=mock_ticket)
        scheduler.release = AsyncMock()

        runner = _make_runner(catalog, scheduler=scheduler)
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("error_tool")], ctx)

        assert len(results) == 1
        tc, tr = results[0]
        assert tr.status == "error"
        assert "RuntimeError" in tr.content
        # CRITICAL: release must have been awaited
        scheduler.release.assert_awaited_once_with(mock_ticket)


# ── Cancel Semantics ─────────────────────────────────────


class TestCancelSemantics:
    @pytest.mark.asyncio
    async def test_stop_event_skips_remaining(self) -> None:
        """stop_event.is_set() -> skip remaining tool_calls with cancelled."""
        stop = threading.Event()
        stop.set()

        catalog = _make_catalog("t1", "t2", "t3")
        runner = _make_runner(catalog)
        ctx = ToolExecutionContext(turn=1, max_turns=10, stop_event=stop)

        results = await runner.execute_batch(
            [_make_tc("t1"), _make_tc("t2"), _make_tc("t3")], ctx
        )

        assert len(results) == 3
        for _, tr in results:
            assert tr.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_on_result_callback(self) -> None:
        """Cancelled results do NOT fire on_result callback."""
        stop = threading.Event()
        stop.set()

        catalog = _make_catalog("t1")
        runner = _make_runner(catalog)
        ctx = ToolExecutionContext(turn=1, max_turns=10, stop_event=stop)

        callback_count = 0

        async def on_result(tc: ToolCallData, tr: ToolResult) -> None:
            nonlocal callback_count
            callback_count += 1

        await runner.execute_batch([_make_tc("t1")], ctx, on_result=on_result)

        # Cancel path skips on_result
        assert callback_count == 0


# ── Happy Path ───────────────────────────────────────────


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_full_chain_success(self) -> None:
        """Full chain success: executor result returned, on_result called."""
        catalog = _make_catalog("good_tool")
        runner = _make_runner(catalog)
        ctx = _make_ctx()

        callback_args: list[tuple[ToolCallData, ToolResult]] = []

        async def on_result(tc: ToolCallData, tr: ToolResult) -> None:
            callback_args.append((tc, tr))

        results = await runner.execute_batch(
            [_make_tc("good_tool")], ctx, on_result=on_result
        )

        assert len(results) == 1
        tc, tr = results[0]
        assert tr.status == "success"
        assert tr.content == "result_good_tool"

        # on_result callback fired
        assert len(callback_args) == 1
        assert callback_args[0][0].name == "good_tool"

    @pytest.mark.asyncio
    async def test_multiple_tools_happy_path(self) -> None:
        """Multiple tools all pass full chain."""
        catalog = _make_catalog("a", "b", "c")
        runner = _make_runner(catalog)
        ctx = _make_ctx()

        results = await runner.execute_batch(
            [_make_tc("a"), _make_tc("b"), _make_tc("c")], ctx
        )

        assert len(results) == 3
        assert results[0][1].content == "result_a"
        assert results[1][1].content == "result_b"
        assert results[2][1].content == "result_c"
