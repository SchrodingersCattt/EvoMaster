"""Tests for Exp.build_runtime() FullToolRunner injection and Exp.run_stream().

Phase 34 Plan 1 Task 2: ESIN-01, ESIN-04, ESIN-05.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matmaster.types.cancellation import CancellationController
from matmaster.types.messages import (
    LLMResponse,
    StreamChunk,
)
from matmaster.types.runtime import AgentRuntimeSpec


# ── Minimal mocks for Exp.build_runtime() tests ──────────


class _MockSession:
    """Minimal Session mock satisfying the Session Protocol."""

    _cancel_token = None

    @property
    def is_open(self) -> bool:
        return True

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def exec_bash(self, command, timeout=None, cancel_token=None):
        return {"stdout": "", "stderr": "", "exit_code": 0}

    def read_file(self, path, encoding="utf-8"):
        return ""

    def write_file(self, path, content, encoding="utf-8"):
        pass

    def path_exists(self, path):
        return False

    def is_file(self, path):
        return False

    @property
    def capabilities(self):
        from matmaster.types.topology import SessionCapabilities
        return SessionCapabilities()


class _MockProvider:
    """Minimal LLM provider for tests."""

    stream_timeout = 10.0
    max_retries = 1
    retry_delay = 0.0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None):
        return LLMResponse(content="mock", finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="hello")
        yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 5})


def _make_playground_context(
    workdir: str = "/tmp/test-workdir",
    execution_workdir: str = "/tmp/test-exec",
    session: Any = None,
    llm_provider: Any = None,
) -> Any:
    """Build a minimal PlaygroundContext-like object."""
    from matmaster.types.context import PlaygroundContext

    ctx = PlaygroundContext(
        workdir=Path(workdir),
        session_type="local",
        cache_area=Path("/tmp/test-cache"),
        execution_workdir=execution_workdir,
        session=session or _MockSession(),
        llm_provider=llm_provider or _MockProvider(),
    )
    return ctx


def _make_exp_config(**overrides: Any) -> Any:
    """Build a minimal ExpConfig for testing."""
    from matmaster.config.exp import ExpConfig

    defaults = {
        "name": "test",
        "max_turns": 5,
        "tools": {"builtin": []},  # No builtins to avoid real file system tools
        "skills": {"enabled": False},
    }
    defaults.update(overrides)
    return ExpConfig(**defaults)


# ── ESIN-04: build_runtime() injects FullToolRunner ──────


class TestBuildRuntimeFullToolRunner:
    """ESIN-04: build_runtime() constructs and injects FullToolRunner."""

    @pytest.mark.asyncio
    async def test_spec_has_full_tool_runner(self) -> None:
        """build_runtime() injects a FullToolRunner instance into spec."""
        from matmaster.core.exp import Exp
        from matmaster.core.tool_runner import FullToolRunner

        config = _make_exp_config()
        exp = Exp(config)
        ctx = _make_playground_context()

        runtime = await exp.build_runtime(ctx)

        assert runtime.spec.tool_runner is not None
        assert isinstance(runtime.spec.tool_runner, FullToolRunner)

    @pytest.mark.asyncio
    async def test_spec_has_tool_catalog(self) -> None:
        """build_runtime() injects a ToolCatalog instance into spec."""
        from matmaster.core.exp import Exp
        from matmaster.tools.tool_catalog import ToolCatalog

        config = _make_exp_config()
        exp = Exp(config)
        ctx = _make_playground_context()

        runtime = await exp.build_runtime(ctx)

        assert runtime.spec.tool_catalog is not None
        assert isinstance(runtime.spec.tool_catalog, ToolCatalog)

    @pytest.mark.asyncio
    async def test_spec_has_runtime_topology(self) -> None:
        """build_runtime() injects a RuntimeTopology instance into spec."""
        from matmaster.core.exp import Exp
        from matmaster.types.topology import RuntimeTopology

        config = _make_exp_config()
        exp = Exp(config)
        ctx = _make_playground_context()

        runtime = await exp.build_runtime(ctx)

        assert runtime.spec.runtime_topology is not None
        assert isinstance(runtime.spec.runtime_topology, RuntimeTopology)

    @pytest.mark.asyncio
    async def test_build_runtime_derives_active_planes_for_local_session(self) -> None:
        """build_runtime() derives active_planes from session and builtin config."""
        from matmaster.core.exp import Exp
        from matmaster.types.topology import ToolPlane

        config = _make_exp_config(tools={"builtin": ["Read"]})
        exp = Exp(config)
        ctx = _make_playground_context()

        runtime = await exp.build_runtime(ctx)
        topology = runtime.spec.runtime_topology

        assert topology.active_planes == frozenset(
            {
                ToolPlane.CONTROL_PLANE,
                ToolPlane.SESSION_SHELL,
                ToolPlane.SESSION_FS,
            }
        )

    @pytest.mark.asyncio
    async def test_build_runtime_runner_can_execute_read(self) -> None:
        """Default build_runtime path can execute Read without plane errors."""
        from matmaster.core.exp import Exp
        from matmaster.core.tool_runner import ToolExecutionContext
        from matmaster.types.messages import ToolCallData

        class _ReadableSession(_MockSession):
            def path_exists(self, path):
                return True

            def is_file(self, path):
                return True

            def read_file(self, path, encoding="utf-8"):
                return "hello from test"

        config = _make_exp_config(tools={"builtin": ["Read"]})
        exp = Exp(config)
        ctx = _make_playground_context(session=_ReadableSession())

        runtime = await exp.build_runtime(ctx)
        results = await runtime.spec.tool_runner.execute_batch(
            [ToolCallData(id="c1", name="Read", arguments={"file_path": "/tmp/test-exec/test.txt"})],
            ToolExecutionContext(turn=1, max_turns=10),
        )
        assert results[0][1].status == "success"

    @pytest.mark.asyncio
    async def test_topology_has_correct_paths(self) -> None:
        """RuntimeTopology paths match PlaygroundContext."""
        from matmaster.core.exp import Exp

        config = _make_exp_config()
        exp = Exp(config)
        ctx = _make_playground_context(
            workdir="/tmp/ctrl",
            execution_workdir="/tmp/exec",
        )

        runtime = await exp.build_runtime(ctx)
        topology = runtime.spec.runtime_topology

        assert topology.control_root == "/tmp/ctrl"
        assert topology.workspace_root == "/tmp/exec"


# ── ESIN-01: run_stream() yields events and runs cleanup ─


class TestRunStream:
    """ESIN-01: Exp.run_stream() yields kernel generator events."""

    @pytest.mark.asyncio
    async def test_run_stream_yields_events(self) -> None:
        """run_stream() yields BusEvent objects from kernel.run_stream().

        After Gap 2 closure, kernel.run_stream() yields BusEvent directly
        (not _KernelItem). Terminal events are RunResultEvent.
        """
        from matmaster.core.exp import Exp
        from matmaster.types.events import RunResultEvent

        config = _make_exp_config()
        exp = Exp(config)
        ctx = _make_playground_context()

        events: list = []
        async for event in exp.run_stream(ctx, "test task"):
            events.append(event)

        # Should have at least some events (start, streaming, complete, end)
        # and a terminal RunResultEvent
        assert len(events) > 0
        # All events should have 'type' attribute (BusEvent contract)
        for event in events:
            assert hasattr(event, 'type'), \
                f"Yielded object missing 'type' attribute: {type(event).__name__}"
        terminal_events = [e for e in events if isinstance(e, RunResultEvent)]
        assert len(terminal_events) == 1
        assert terminal_events[0].reason == "natural"

    @pytest.mark.asyncio
    async def test_run_stream_runs_cleanup(self) -> None:
        """run_stream() calls cleanup callbacks in finally block."""
        from matmaster.core.exp import Exp

        config = _make_exp_config()
        exp = Exp(config)
        ctx = _make_playground_context()

        cleanup_called = False

        def on_cleanup():
            nonlocal cleanup_called
            cleanup_called = True

        exp._register_cleanup(on_cleanup)

        async for _ in exp.run_stream(ctx, "test task"):
            pass

        assert cleanup_called, "Cleanup callback should have been called"

    @pytest.mark.asyncio
    async def test_run_stream_cleanup_on_aclose(self) -> None:
        """run_stream() runs cleanup when generator is explicitly closed."""
        from matmaster.core.exp import Exp

        config = _make_exp_config()
        exp = Exp(config)
        ctx = _make_playground_context()

        cleanup_called = False

        def on_cleanup():
            nonlocal cleanup_called
            cleanup_called = True

        exp._register_cleanup(on_cleanup)

        gen = exp.run_stream(ctx, "test task")
        try:
            await gen.__anext__()  # Get first item
        finally:
            await gen.aclose()  # Explicitly close, triggering finally

        assert cleanup_called, "Cleanup should run on explicit aclose"

    @pytest.mark.asyncio
    async def test_run_stream_injects_cancel_token_into_session_and_catalog(self) -> None:
        from matmaster.core.exp import Exp

        config = _make_exp_config()
        exp = Exp(config)
        ctx = _make_playground_context()
        controller = CancellationController()
        catalog = MagicMock()

        observed: dict[str, Any] = {}

        async def fake_kernel_run_stream(
            spec: Any,
            task: str,
            history: list[Any] | None = None,
            cancel_token: Any = None,
        ) -> AsyncIterator[Any]:
            observed["spec"] = spec
            observed["task"] = task
            observed["history"] = history
            observed["cancel_token"] = cancel_token
            yield MagicMock(type="test.event")

        runtime = MagicMock()
        runtime.spec = MagicMock(tool_catalog=catalog)
        runtime.kernel = MagicMock()
        runtime.kernel.run_stream = fake_kernel_run_stream

        exp.build_runtime = AsyncMock(return_value=runtime)

        events = []
        async for event in exp.run_stream(
            ctx,
            "test task",
            cancel_token=controller.token,
        ):
            events.append(event)

        assert len(events) == 1
        assert ctx.session._cancel_token is controller.token
        catalog.inject_cancel_token.assert_called_once_with(controller.token)
        assert observed["task"] == "test task"
        assert observed["cancel_token"] is controller.token


# ── ESIN-05: on_skill_hit uses catalog.register_overlay() ─


class TestSkillOverlayCatalog:
    """ESIN-05: on_skill_hit uses ToolCatalog.register_overlay()."""

    @pytest.mark.asyncio
    async def test_catalog_register_overlay_called(self) -> None:
        """When catalog is provided, on_skill_hit uses catalog.register_overlay()."""
        from matmaster.tools.tool_catalog import ToolCatalog
        from matmaster.tools.tool_compiler import ToolCompiler
        from matmaster.tools.tool_registry import ToolRegistry
        from matmaster.types.topology import RuntimeTopology

        registry = ToolRegistry()
        topology = RuntimeTopology(
            session_kind="local",
            control_root="/tmp",
            workspace_root="/tmp",
        )
        compiler = ToolCompiler()
        catalog = ToolCatalog(registry, compiler=compiler, topology=topology)

        initial_version = catalog.version
        assert initial_version == 0

        # Simulate what on_skill_hit does: register a lazy MCP tool via overlay
        class FakeLazyTool:
            name = "server_tool1"
            description = "test tool"
            json_schema = {"type": "object", "properties": {}}
            resource_claims = ()
            capabilities = frozenset()
            effect_level = "none"
            fast_path_eligible = False
            max_result_chars = 0
            plane = "control_plane"
            state_mode = "stateless"
            stop_mode = "cancellable"
            exposed_to_model = True

            def describe(self, ctx=None):
                return self.description

            def prompt(self, ctx=None):
                return None

            async def execute(self, arguments):
                return "result"

        catalog.register_overlay(FakeLazyTool(), source="mcp")

        assert catalog.version == 1
        assert "server_tool1" in registry

    @pytest.mark.asyncio
    async def test_exp_build_runtime_passes_catalog(self) -> None:
        """Verify build_runtime passes catalog to _init_skill_tools."""
        from matmaster.core.exp import Exp

        config = _make_exp_config()
        exp = Exp(config)

        # _init_skill_tools should accept catalog kwarg
        # We test this indirectly: skills.enabled=False means it's not called,
        # but the signature should accept it
        import inspect

        sig = inspect.signature(exp._init_skill_tools)
        params = list(sig.parameters.keys())
        assert "catalog" in params, "_init_skill_tools should accept catalog parameter"


# ── Compactor event_sink in build_runtime ─────────────────


class TestBuildRuntimeCompactorEventSink:
    """Compactor creation uses event_sink=None (bus parameter removed in Phase 36)."""

    @pytest.mark.asyncio
    async def test_compactor_uses_event_sink(self) -> None:
        """Compactor created with event_sink=None for _run_items() injection."""
        from matmaster.config.exp import CompactionConfig
        from matmaster.core.exp import Exp

        compaction_cfg = CompactionConfig(
            enabled=True,
            context_window_tokens=128000,
            trigger_ratio=0.9,
        )
        config = _make_exp_config()
        # Manually override compaction in the assembled spec
        exp = Exp(config)
        ctx = _make_playground_context()

        # Patch assemble to return spec with compaction enabled
        original_assemble = exp.assemble

        async def patched_assemble(ctx):
            spec = await original_assemble(ctx)
            return spec.model_copy(update={"compaction": compaction_cfg})

        exp.assemble = patched_assemble

        runtime = await exp.build_runtime(ctx)

        # Compactor should exist and have _event_sink attribute
        assert runtime.spec.compactor is not None
        assert hasattr(runtime.spec.compactor, "_event_sink")
        # event_sink should be None (set later by _run_items)
        assert runtime.spec.compactor._event_sink is None


# ── Active planes with new CC tool names ─────────────────


class TestActivePlanesNewNames:
    """_derive_active_planes recognises new CC-style tool names."""

    @pytest.mark.asyncio
    async def test_build_runtime_adds_external_service_plane_for_websearch(
        self,
        tmp_path: Path,
    ) -> None:
        """WebSearch in builtin_cfg activates EXTERNAL_SERVICE plane."""
        from matmaster.config.exp import ExpConfig
        from matmaster.core.exp import Exp
        from matmaster.types.context import PlaygroundContext
        from matmaster.types.topology import ToolPlane

        config = ExpConfig(name="test", tools={"builtin": ["WebSearch"]})
        exp = Exp(config)
        ctx = PlaygroundContext(
            workdir=tmp_path,
            execution_workdir=str(tmp_path / "exec"),
            session_type="local",
            cache_area=tmp_path / "cache",
            session=None,
            llm_provider=_MockProvider(),
        )

        runtime = await exp.build_runtime(ctx)

        assert ToolPlane.EXTERNAL_SERVICE in runtime.spec.runtime_topology.active_planes
