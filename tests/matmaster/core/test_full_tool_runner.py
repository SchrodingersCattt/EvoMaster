"""Tests for FullToolRunner -- complete tool execution chain.

Verifies:
- Catalog miss -> ToolResult(status='error', meta={'layer': 'catalog'})
- StructuralValidation deny -> ToolResult(status='error', meta={'layer': 'structural'})
- CapabilityPolicy deny -> ToolResult(status='error', meta={'layer': 'policy'})
- Scheduler timeout -> ToolResult(status='error', meta={'layer': 'scheduler'})
- Fast path skips Scheduler but not CapabilityPolicy
- Executor exception -> ToolResult.from_error() + ticket released
- Cancel semantics -> skip remaining tool_calls
- stop_mode-aware cancellation behavior
- Happy path -> executor result + on_result callback
- isinstance(FullToolRunner(...), ToolRunner) check
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matmaster.core.capability_policy import DefaultCapabilityPolicy
from matmaster.core.structural_validation import StructuralValidation
from matmaster.core.tool_runner import (
    FullToolRunner,
    ToolExecutionContext,
    ToolRunner,
)
from matmaster.core.tool_scheduler import SchedulerTicket, ToolScheduler
from matmaster.sessions.local import LocalSession
from matmaster.tools.builtin.bash_tool import BashTool
from matmaster.tools.tool_catalog import ToolCatalog
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.tools.tool_result import ToolResult
from matmaster.types.cancellation import CancellationController
from matmaster.types.messages import ToolCallData
from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ResourceClaim, ToolBinding, ToolInstance, ToolSpec
from matmaster.types.topology import RuntimeTopology, SessionCapabilities, ToolPlane


# ── Helpers ──────────────────────────────────────────────


class _SimpleTool:
    """Minimal tool for testing."""

    def __init__(self, name: str, result: str = "ok") -> None:
        self._name = name
        self._result = result
        self.resource_claims: tuple[ResourceClaim, ...] = ()
        self.capabilities = frozenset()
        self.effect_level = "none"
        self.fast_path_eligible = False
        self.max_result_chars = 0
        self.plane = ToolPlane.CONTROL_PLANE
        self.state_mode = "stateless"
        self.stop_mode = "cancellable"
        self.exposed_to_model = True

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"test tool {self._name}"

    @property
    def json_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def describe(self, ctx: Any | None = None) -> str:
        return self.description

    def prompt(self, ctx: Any | None = None) -> str | None:
        return None

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(content=self._result)


class _ErrorTool:
    """Tool that raises RuntimeError on execute."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.resource_claims: tuple[ResourceClaim, ...] = ()
        self.capabilities = frozenset()
        self.effect_level = "none"
        self.fast_path_eligible = False
        self.max_result_chars = 0
        self.plane = ToolPlane.CONTROL_PLANE
        self.state_mode = "stateless"
        self.stop_mode = "cancellable"
        self.exposed_to_model = True

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"error tool {self._name}"

    @property
    def json_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def describe(self, ctx: Any | None = None) -> str:
        return self.description

    def prompt(self, ctx: Any | None = None) -> str | None:
        return None

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
    state: ToolRunnerState | None = None,
) -> FullToolRunner:
    return FullToolRunner(
        catalog=catalog,
        structural_validation=StructuralValidation(),
        capability_policy=policy or DefaultCapabilityPolicy(),
        scheduler=scheduler or ToolScheduler(default_timeout=1.0),
        topology=topology or _make_topology(),
        state=state,
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

    def test_uses_explicit_state(self) -> None:
        catalog = _make_catalog("test")
        state = ToolRunnerState()

        runner = _make_runner(catalog, state=state)

        assert runner.state is state

    def test_creates_default_state(self) -> None:
        catalog = _make_catalog("test")

        runner = _make_runner(catalog)

        assert runner.state is not None


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

    @pytest.mark.asyncio
    async def test_execute_bash_allowed_for_stateless_local_session(
        self, tmp_path
    ) -> None:
        """Real LocalSession capabilities should not structurally block execute_bash."""
        session = LocalSession(workspace_path=tmp_path)
        assert session.capabilities.shell_persistence == "stateless"
        assert session.capabilities.shell_input is False
        topology = _make_topology(session_caps=session.capabilities)
        registry = ToolRegistry()
        registry.register(
            BashTool(session=session, workdir=Path(tmp_path)),
            source="builtin",
        )
        catalog = ToolCatalog(registry, topology=topology)
        runner = _make_runner(catalog, topology=topology)
        ctx = _make_ctx()

        results = await runner.execute_batch(
            [_make_tc("execute_bash", command="echo runner_ok")],
            ctx,
        )

        assert len(results) == 1
        tc, tr = results[0]
        assert tc.name == "execute_bash"
        assert tr.status == "success"
        assert "runner_ok" in tr.content


# ── Repeated Calls ───────────────────────────────────────


class TestRepeatedCalls:
    @pytest.mark.asyncio
    async def test_repeated_identical_calls_still_execute(self) -> None:
        """重复工具调用不再被 guard 层拦截。"""
        catalog = _make_catalog("looped_tool")
        topology = _make_topology()
        runner = _make_runner(catalog, topology=topology)
        ctx = _make_ctx()
        tcs = [
            _make_tc("looped_tool", "c1"),
            _make_tc("looped_tool", "c2"),
            _make_tc("looped_tool", "c3"),
        ]
        results = await runner.execute_batch(tcs, ctx)
        assert len(results) == 3
        assert all(tr.status != "error" for _, tr in results)
        assert [tr.content for _, tr in results] == ["result_looped_tool"] * 3


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
        """Fast path: effect_level='none', shared_read, fast_path_eligible -> skip Scheduler."""
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
                effect_level="none",
                fast_path_eligible=True,
            )
            binding = ToolBinding(
                binding_key=f"session_fs:{name}",
                plane=ToolPlane.SESSION_FS,
                resource_claims=(
                    ResourceClaim(resource="workspace", mode="shared_read"),
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
                effect_level="none",
                fast_path_eligible=True,
            )
            binding = ToolBinding(
                binding_key=f"session_fs:{name}",
                plane=ToolPlane.SESSION_FS,
                resource_claims=(
                    ResourceClaim(resource="workspace", mode="shared_read"),
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
    async def test_cancel_token_skips_remaining(self) -> None:
        """cancel_token.is_cancelled -> skip remaining tool_calls with cancelled."""
        ctrl = CancellationController()
        ctrl.cancel()

        catalog = _make_catalog("t1", "t2", "t3")
        runner = _make_runner(catalog)
        ctx = ToolExecutionContext(turn=1, max_turns=10, cancel_token=ctrl.token)

        results = await runner.execute_batch(
            [_make_tc("t1"), _make_tc("t2"), _make_tc("t3")], ctx
        )

        assert len(results) == 3
        for _, tr in results:
            assert tr.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_on_result_callback(self) -> None:
        """Cancelled results do NOT fire on_result callback."""
        ctrl = CancellationController()
        ctrl.cancel()

        catalog = _make_catalog("t1")
        runner = _make_runner(catalog)
        ctx = ToolExecutionContext(turn=1, max_turns=10, cancel_token=ctrl.token)

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


# ── Normalize + Truncation ──────────────────────────────


class _StringReturnTool:
    """Tool that returns a plain string (not ToolResult)."""

    def __init__(self, name: str, result: str = "ok") -> None:
        self._name = name
        self._result = result
        if name == "read_file":
            self.resource_claims = (
                ResourceClaim(resource="workspace", mode="shared_read"),
            )
            self.capabilities = frozenset({"workspace.read"})
            self.effect_level = "none"
            self.fast_path_eligible = True
            self.max_result_chars = 12000
            self.plane = ToolPlane.SESSION_FS
        elif name == "write_file":
            self.resource_claims = (
                ResourceClaim(resource="workspace", mode="exclusive"),
            )
            self.capabilities = frozenset({"workspace.write"})
            self.effect_level = "local_mutation"
            self.fast_path_eligible = False
            self.max_result_chars = 0
            self.plane = ToolPlane.SESSION_FS
        self.state_mode = "stateless"
        self.stop_mode = "cancellable"
        self.exposed_to_model = True

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"string tool {self._name}"

    @property
    def json_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def describe(self, ctx: Any | None = None) -> str:
        return self.description

    def prompt(self, ctx: Any | None = None) -> str | None:
        return None

    async def execute(self, arguments: dict[str, Any]) -> str:
        return self._result


class TestNormalizeAndTruncation:
    @pytest.mark.asyncio
    async def test_string_return_is_normalized(self) -> None:
        """Executor returning str is normalized to ToolResult."""
        registry = ToolRegistry()
        registry.register(_StringReturnTool("read_file", result="some content"), source="builtin")
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog)
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("read_file")], ctx)
        _, tr = results[0]
        assert isinstance(tr, ToolResult)
        assert tr.content == "some content"
        assert tr.status == "success"

    @pytest.mark.asyncio
    async def test_none_return_is_normalized(self) -> None:
        """Executor returning None is normalized to empty ToolResult."""

        class _NoneReturnTool:
            name = "read_file"
            description = "none tool"
            json_schema: dict[str, Any] = {"type": "object", "properties": {}}
            resource_claims = (ResourceClaim(resource="workspace", mode="shared_read"),)
            capabilities = frozenset({"workspace.read"})
            effect_level = "none"
            fast_path_eligible = True
            max_result_chars = 12000
            plane = ToolPlane.SESSION_FS
            state_mode = "stateless"
            stop_mode = "cancellable"
            exposed_to_model = True

            def describe(self, ctx: Any | None = None) -> str:
                return self.description

            def prompt(self, ctx: Any | None = None) -> str | None:
                return None

            async def execute(self, arguments: dict[str, Any]) -> None:
                return None

        registry = ToolRegistry()
        registry.register(_NoneReturnTool(), source="builtin")
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog)
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("read_file")], ctx)
        _, tr = results[0]
        assert isinstance(tr, ToolResult)
        assert tr.content == ""

    @pytest.mark.asyncio
    async def test_truncation_triggers_on_oversized_content(self, tmp_path: "Path") -> None:
        """Content exceeding max_result_chars is truncated."""
        from pathlib import Path

        long_content = "A" * 20000
        registry = ToolRegistry()
        registry.register(_StringReturnTool("read_file", result=long_content), source="builtin")
        topology_with_tmp = RuntimeTopology(
            session_kind="local",
            control_root=str(tmp_path),
            workspace_root="/tmp/ws",
            active_planes=frozenset(ToolPlane),
        )
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog, topology=topology_with_tmp)
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("read_file", call_id="call_123")], ctx)
        _, tr = results[0]

        assert len(tr.content) < 20000
        assert "truncated" in tr.content
        assert tr.meta.get("truncated") is True
        assert "full_result_path" in tr.meta

        # Verify disk file
        disk_path = Path(tr.meta["full_result_path"])
        assert disk_path.exists()
        assert disk_path.read_text() == long_content

    @pytest.mark.asyncio
    async def test_no_truncation_when_under_limit(self) -> None:
        """Content under max_result_chars is not truncated."""
        short_content = "short"
        registry = ToolRegistry()
        registry.register(_StringReturnTool("read_file", result=short_content), source="builtin")
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog)
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("read_file")], ctx)
        _, tr = results[0]

        assert tr.content == short_content
        assert "truncated" not in tr.meta

    @pytest.mark.asyncio
    async def test_execute_batch_runs_shared_read_calls_concurrently(self) -> None:
        """Two shared_read tools execute concurrently in the two-phase model."""
        import asyncio as _aio

        started_count = 0
        release = _aio.Event()

        class _SlowReadTool:
            def __init__(self, name: str) -> None:
                self._name = name
                self.resource_claims = (
                    ResourceClaim(resource="workspace", mode="shared_read"),
                )
                self.capabilities = frozenset({"workspace.read"})
                self.effect_level = "none"
                self.fast_path_eligible = True
                self.max_result_chars = 12000
                self.plane = ToolPlane.SESSION_FS
                self.state_mode = "stateless"
                self.stop_mode = "cancellable"
                self.exposed_to_model = True

            @property
            def name(self) -> str:
                return self._name

            @property
            def description(self) -> str:
                return "slow"

            @property
            def json_schema(self) -> dict[str, Any]:
                return {"type": "object", "properties": {}}

            def describe(self, ctx: Any | None = None) -> str:
                return self.description

            def prompt(self, ctx: Any | None = None) -> str | None:
                return None

            async def execute(self, arguments: dict[str, Any]) -> ToolResult:
                nonlocal started_count
                started_count += 1
                await release.wait()
                return ToolResult(content="ok")

        registry = ToolRegistry()
        registry.register(_SlowReadTool("read_a"), source="builtin")
        registry.register(_SlowReadTool("read_b"), source="builtin")
        catalog = ToolCatalog(registry)

        # Patch get_tool to return shared_read fast-path instances
        original_get = catalog.get_tool

        def fast_read_get(name: str):
            inst = original_get(name)
            if inst is None:
                return None
            spec = ToolSpec(
                tool_name=name,
                description="fast",
                args_schema={},
                source="builtin",
                effect_level="none",
                fast_path_eligible=True,
            )
            binding = ToolBinding(
                binding_key=f"session_fs:{name}",
                plane=ToolPlane.SESSION_FS,
                resource_claims=(ResourceClaim(resource="workspace", mode="shared_read"),),
            )
            return ToolInstance(
                tool_spec=spec,
                tool_binding=binding,
                tool_executor=inst.tool_executor,
            )

        catalog.get_tool = fast_read_get  # type: ignore

        runner = _make_runner(catalog)
        ctx = _make_ctx()

        async def run_batch():
            return await runner.execute_batch(
                [_make_tc("read_a"), _make_tc("read_b")], ctx
            )

        task = _aio.create_task(run_batch())
        # Wait briefly for both to start
        for _ in range(50):
            if started_count >= 2:
                break
            await _aio.sleep(0.01)

        assert started_count == 2, f"Expected 2 concurrent starts, got {started_count}"
        release.set()
        results = await task
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_executor_receives_cancel_token_from_execution_context(self) -> None:
        """Executor receives ToolExecutionContext with cancel_token from batch context."""
        from matmaster.types.tool_spec import ToolExecutionContext as ExecCtx

        captured: dict[str, Any] = {}

        class _CtxCaptureTool:
            name = "ctx_tool"
            description = "capture"
            json_schema: dict[str, Any] = {"type": "object", "properties": {}}
            resource_claims = ()
            capabilities = frozenset()
            effect_level = "none"
            fast_path_eligible = False
            max_result_chars = 0
            plane = ToolPlane.CONTROL_PLANE
            state_mode = "stateless"
            stop_mode = "cancellable"
            exposed_to_model = True

            def describe(self, ctx: Any | None = None) -> str:
                return self.description

            def prompt(self, ctx: Any | None = None) -> str | None:
                return None

            async def execute_with_context(
                self, arguments: dict[str, Any], exec_ctx: ExecCtx
            ) -> ToolResult:
                captured["cancel_token"] = exec_ctx.cancel_token
                return ToolResult(content="ok")

        registry = ToolRegistry()
        registry.register(_CtxCaptureTool(), source="builtin")
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog)

        ctrl = CancellationController()
        ctx = ToolExecutionContext(turn=1, max_turns=10, cancel_token=ctrl.token)

        await runner.execute_batch([_make_tc("ctx_tool")], ctx)
        assert captured["cancel_token"] is ctrl.token

    @pytest.mark.asyncio
    async def test_no_truncation_when_max_result_chars_zero(self) -> None:
        """Tools with max_result_chars=0 are never truncated."""
        long_content = "B" * 100000
        registry = ToolRegistry()
        registry.register(_StringReturnTool("write_file", result=long_content), source="builtin")
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog)
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("write_file")], ctx)
        _, tr = results[0]

        assert tr.content == long_content
        assert "truncated" not in tr.meta


# ── Input Validator in Runner ───────────────────────────


class TestInputValidatorInRunner:
    @pytest.mark.asyncio
    async def test_deny_validator_returns_error(self) -> None:
        """input_validator deny -> error ToolResult, executor not called."""
        registry = ToolRegistry()
        tool = _SimpleTool("write_file", result="should not reach")
        registry.register(tool, source="builtin")
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog)
        ctx = _make_ctx()

        # Inject a validator that denies
        instance = catalog.get_tool("write_file")
        assert instance is not None

        # Patch the catalog to return a ToolInstance with a deny validator.
        async def _deny_validator(
            args: dict[str, Any],
            runner_state: ToolRunnerState | None,
        ) -> ToolDecision:
            return ToolDecision(decision="deny", reason="path outside boundary")

        executor_called = False
        original_executor = instance.tool_executor

        async def _tracking_executor(args: dict[str, Any]) -> ToolResult:
            nonlocal executor_called
            executor_called = True
            return await original_executor(args)

        patched = ToolInstance(
            tool_spec=instance.tool_spec,
            tool_binding=instance.tool_binding,
            tool_executor=_tracking_executor,
            input_validator=_deny_validator,
        )

        # Patch catalog to return our custom instance
        original_get = catalog.get_tool
        catalog.get_tool = lambda name: patched if name == "write_file" else original_get(name)

        results = await runner.execute_batch([_make_tc("write_file")], ctx)
        _, tr = results[0]

        assert tr.status == "error"
        assert "path outside boundary" in tr.content
        assert tr.meta.get("layer") == "input_validation"
        assert not executor_called

    @pytest.mark.asyncio
    async def test_validator_exception_returns_error(self) -> None:
        """input_validator raising exception -> error ToolResult."""
        registry = ToolRegistry()
        tool = _SimpleTool("write_file", result="should not reach")
        registry.register(tool, source="builtin")
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog)
        ctx = _make_ctx()

        async def _exploding_validator(
            args: dict[str, Any],
            runner_state: ToolRunnerState | None,
        ) -> None:
            raise ValueError("validator kaboom")

        instance = catalog.get_tool("write_file")
        patched = ToolInstance(
            tool_spec=instance.tool_spec,
            tool_binding=instance.tool_binding,
            tool_executor=instance.tool_executor,
            input_validator=_exploding_validator,
        )
        catalog.get_tool = lambda name: patched if name == "write_file" else None

        results = await runner.execute_batch([_make_tc("write_file")], ctx)
        _, tr = results[0]

        assert tr.status == "error"
        assert "validator kaboom" in tr.content
        assert tr.meta.get("layer") == "input_validation"

    @pytest.mark.asyncio
    async def test_runner_uses_modified_args_from_structural_validation(self) -> None:
        """Runner passes modified_args (from StructuralValidation) to executor."""
        captured_args: dict[str, Any] = {}

        class _CaptureTool:
            name = "read_file"
            description = "capture"
            json_schema: dict[str, Any] = {"type": "object", "properties": {}}
            resource_claims = (ResourceClaim(resource="workspace", mode="shared_read"),)
            capabilities = frozenset({"workspace.read"})
            effect_level = "none"
            fast_path_eligible = True
            max_result_chars = 12000
            plane = ToolPlane.SESSION_FS
            state_mode = "stateless"
            stop_mode = "cancellable"
            exposed_to_model = True

            def describe(self, ctx: Any | None = None) -> str:
                return self.description

            def prompt(self, ctx: Any | None = None) -> str | None:
                return None

            async def execute(self, arguments: dict[str, Any]) -> ToolResult:
                captured_args.update(arguments)
                return ToolResult(content="ok")

        registry = ToolRegistry()
        registry.register(_CaptureTool(), source="builtin")
        topology = RuntimeTopology(
            session_kind="local",
            control_root="/tmp/ctrl",
            workspace_root="/tmp/ws",
            active_planes=frozenset(ToolPlane),
        )
        catalog = ToolCatalog(registry, topology=topology)
        runner = _make_runner(catalog, topology=topology)
        ctx = _make_ctx()

        await runner.execute_batch(
            [_make_tc("read_file", file_path="src/app.py")], ctx
        )

        assert captured_args.get("file_path") == "/tmp/ws/src/app.py"

    @pytest.mark.asyncio
    async def test_allow_validator_lets_execution_proceed(self) -> None:
        """input_validator returning None -> execution proceeds normally."""
        registry = ToolRegistry()
        tool = _SimpleTool("write_file", result="written ok")
        registry.register(tool, source="builtin")
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog)
        ctx = _make_ctx()

        async def _allow_validator(
            args: dict[str, Any],
            runner_state: ToolRunnerState | None,
        ) -> None:
            return None

        instance = catalog.get_tool("write_file")
        patched = ToolInstance(
            tool_spec=instance.tool_spec,
            tool_binding=instance.tool_binding,
            tool_executor=instance.tool_executor,
            input_validator=_allow_validator,
        )
        catalog.get_tool = lambda name: patched if name == "write_file" else None

        results = await runner.execute_batch([_make_tc("write_file")], ctx)
        _, tr = results[0]

        assert tr.status == "success"
        assert tr.content == "written ok"


def _make_runner_with_stop_mode(
    tool_name: str,
    stop_mode: str = "cancellable",
    state_mode: str = "stateless",
    result_content: str = "executed",
) -> FullToolRunner:
    """Build a FullToolRunner with a single tool using the requested stop mode."""

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

    registry = ToolRegistry()
    topology = RuntimeTopology(
        session_kind="local",
        control_root="/tmp/control",
        workspace_root="/tmp/workspace",
        active_planes=frozenset(ToolPlane),
    )
    catalog = ToolCatalog(registry, topology=topology)
    catalog._compiled_tools[tool_name] = instance
    catalog._version += 1

    return FullToolRunner(
        catalog=catalog,
        structural_validation=StructuralValidation(),
        capability_policy=DefaultCapabilityPolicy(),
        scheduler=ToolScheduler(),
        topology=topology,
    )


class TestStopModeCancel:
    """FullToolRunner uses stop_mode to decide cancel behavior."""

    @pytest.mark.asyncio
    async def test_cancellable_tool_cancelled_when_token_cancelled(self) -> None:
        runner = _make_runner_with_stop_mode("test_tool", stop_mode="cancellable")
        ctrl = CancellationController()
        ctrl.cancel()
        ctx = ToolExecutionContext(turn=1, max_turns=10, cancel_token=ctrl.token)

        results = await runner.execute_batch([_make_tc("test_tool")], ctx)

        assert len(results) == 1
        assert results[0][1].status == "cancelled"
        assert "cancelled" in results[0][1].content.lower()

    @pytest.mark.asyncio
    async def test_best_effort_tool_cancelled_with_message(self) -> None:
        runner = _make_runner_with_stop_mode("web_tool", stop_mode="best_effort")
        ctrl = CancellationController()
        ctrl.cancel()
        ctx = ToolExecutionContext(turn=1, max_turns=10, cancel_token=ctrl.token)

        results = await runner.execute_batch([_make_tc("web_tool")], ctx)

        assert len(results) == 1
        assert results[0][1].status == "cancelled"
        assert (
            "best-effort" in results[0][1].content.lower()
            or "best_effort" in results[0][1].content.lower()
        )

    @pytest.mark.asyncio
    async def test_non_cancellable_tool_executes_when_token_cancelled(self) -> None:
        runner = _make_runner_with_stop_mode(
            "spawn_tool",
            stop_mode="non_cancellable",
            result_content="spawn_done",
        )
        ctrl = CancellationController()
        ctrl.cancel()
        ctx = ToolExecutionContext(turn=1, max_turns=10, cancel_token=ctrl.token)

        results = await runner.execute_batch([_make_tc("spawn_tool")], ctx)

        assert len(results) == 1
        assert results[0][1].status == "success"
        assert results[0][1].content == "spawn_done"

    @pytest.mark.asyncio
    async def test_no_stop_event_all_modes_execute(self) -> None:
        for mode in ("cancellable", "best_effort", "non_cancellable"):
            runner = _make_runner_with_stop_mode(
                f"tool_{mode}",
                stop_mode=mode,
                result_content=f"result_{mode}",
            )
            ctx = ToolExecutionContext(turn=1, max_turns=10)

            results = await runner.execute_batch([_make_tc(f"tool_{mode}")], ctx)

            assert len(results) == 1
            assert results[0][1].status == "success", f"Failed for mode={mode}"
            assert results[0][1].content == f"result_{mode}"
