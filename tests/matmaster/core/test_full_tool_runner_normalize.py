"""Tests for FullToolRunner -- normalize and truncation logic.

Split from test_full_tool_runner.py to keep file under 1000 lines.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from matmaster.core.capability_policy import DefaultCapabilityPolicy
from matmaster.core.structural_validation import StructuralValidation
from matmaster.core.tool_runner import (
    FullToolRunner,
    ToolExecutionContext,
)
from matmaster.core.tool_scheduler import ToolScheduler
from matmaster.tools.tool_catalog import ToolCatalog
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.tools.tool_result import ToolResult
from matmaster.types.cancellation import CancellationController
from matmaster.types.messages import ToolCallData
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ResourceClaim, ToolBinding, ToolInstance, ToolSpec
from matmaster.types.topology import RuntimeTopology, ToolPlane


def _make_tc(name: str, call_id: str = "", **args: Any) -> ToolCallData:
    return ToolCallData(id=call_id or f"call_{name}", name=name, arguments=args)


def _make_topology(
    all_planes: bool = True,
) -> RuntimeTopology:
    planes = frozenset(ToolPlane) if all_planes else frozenset()
    return RuntimeTopology(
        session_kind="local",
        control_root="/tmp/ctrl",
        workspace_root="/tmp/ws",
        active_planes=planes,
    )


def _make_runner(
    catalog: ToolCatalog,
    topology: RuntimeTopology | None = None,
    state: ToolRunnerState | None = None,
) -> FullToolRunner:
    return FullToolRunner(
        catalog=catalog,
        structural_validation=StructuralValidation(),
        capability_policy=DefaultCapabilityPolicy(),
        scheduler=ToolScheduler(default_timeout=1.0),
        topology=topology or _make_topology(),
        state=state,
    )


def _make_ctx(turn: int = 1, max_turns: int = 10) -> ToolExecutionContext:
    return ToolExecutionContext(turn=turn, max_turns=max_turns)


# ── Helpers ──────────────────────────────────────────────


class _StringReturnTool:
    """Tool that returns a plain string (not ToolResult)."""

    def __init__(self, name: str, result: str = "ok") -> None:
        self._name = name
        self._result = result
        if name == "Read":
            self.resource_claims = (
                ResourceClaim(resource="workspace", mode="shared_read"),
            )
            self.capabilities = frozenset({"workspace.read"})
            self.effect_level = "none"
            self.fast_path_eligible = True
            self.max_result_chars = 12000
            self.plane = ToolPlane.SESSION_FS
        elif name == "Write":
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


# ── Tests ────────────────────────────────────────────────


class TestNormalizeAndTruncation:
    @pytest.mark.asyncio
    async def test_string_return_is_normalized(self) -> None:
        """Executor returning str is normalized to ToolResult."""
        registry = ToolRegistry()
        registry.register(
            _StringReturnTool("Read", result="some content"), source="builtin"
        )
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog)
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("Read")], ctx)
        _, tr = results[0]
        assert isinstance(tr, ToolResult)
        assert tr.content == "some content"
        assert tr.status == "success"

    @pytest.mark.asyncio
    async def test_none_return_is_normalized(self) -> None:
        """Executor returning None is normalized to empty ToolResult."""

        class _NoneReturnTool:
            name = "Read"
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

        results = await runner.execute_batch([_make_tc("Read")], ctx)
        _, tr = results[0]
        assert isinstance(tr, ToolResult)
        assert tr.content == ""

    @pytest.mark.asyncio
    async def test_truncation_triggers_on_oversized_content(
        self, tmp_path: Path
    ) -> None:
        """Content exceeding max_result_chars is truncated."""
        long_content = "A" * 20000
        registry = ToolRegistry()
        registry.register(
            _StringReturnTool("Read", result=long_content), source="builtin"
        )
        topology_with_tmp = RuntimeTopology(
            session_kind="local",
            control_root=str(tmp_path),
            workspace_root="/tmp/ws",
            active_planes=frozenset(ToolPlane),
        )
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog, topology=topology_with_tmp)
        ctx = _make_ctx()

        results = await runner.execute_batch(
            [_make_tc("Read", call_id="call_123")], ctx
        )
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
        registry.register(
            _StringReturnTool("Read", result=short_content), source="builtin"
        )
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog)
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("Read")], ctx)
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
                resource_claims=(
                    ResourceClaim(resource="workspace", mode="shared_read"),
                ),
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
        registry.register(
            _StringReturnTool("Write", result=long_content), source="builtin"
        )
        catalog = ToolCatalog(registry)
        runner = _make_runner(catalog)
        ctx = _make_ctx()

        results = await runner.execute_batch([_make_tc("Write")], ctx)
        _, tr = results[0]

        assert tr.content == long_content
        assert "truncated" not in tr.meta
