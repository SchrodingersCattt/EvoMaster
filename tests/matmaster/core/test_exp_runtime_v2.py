"""Tests for Exp.build_runtime() FullToolRunner injection and Exp.run_stream()."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from matmaster.core.playground import ExecutionEnvironment
from matmaster.core.run_context import AgentRunContext, AgentRunRequest
from matmaster.types.cancellation import CancellationController
from matmaster.types.messages import (
    LLMResponse,
    StreamChunk,
)
from matmaster.types.run_metadata import RunMetadata
from matmaster.types.runtime_ports import BohriumRuntimeSnapshot

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

    def stat_file(self, path):
        from matmaster.types.session import SessionFileStat

        return SessionFileStat(size=0, mtime=0.0)

    def download(self, path, timeout=None):
        return b""

    def upload_directory(self, local_dir, remote_dir, exclude=None):
        pass

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


@pytest.mark.asyncio
async def test_build_runtime_uses_runtime_ports_history(
    tmp_path: Path,
) -> None:
    from matmaster.config.exp import ExpConfig
    from matmaster.core.exp import Exp
    from matmaster.types.runtime_ports import (
        AgentRunPorts,
        PlaygroundCompactionPort,
    )

    class RuntimeHistory:
        def query_events(self):
            return [{"event_id": 2, "source": "runtime"}]

        def all_events(self):
            return [{"event_id": 20, "source": "runtime"}]

        async def load_events(self, query):
            self.context_query = query
            return ()

        def latest_checkpoint_covered_until_event_id(self):
            return 20

        def latest_scope_event_id(self):
            return 25

    ctx = AgentRunContext(
        environment=ExecutionEnvironment(
            workdir=tmp_path,
            execution_workdir=str(tmp_path),
            session_type="local",
            cache_area=tmp_path / "cache",
        ),
        request=AgentRunRequest(
            llm_provider=_MockProvider(),
            ports=AgentRunPorts(
                compaction=PlaygroundCompactionPort(history=RuntimeHistory()),
            ),
        ),
    )

    runtime = await Exp(ExpConfig(name="test")).build_runtime(ctx)

    assert runtime.kernel_runtime.resources.compactor is not None
    assert (
        runtime.kernel_runtime.resources.compactor._runtime_covered_until_provider()
        == 25
    )


@pytest.mark.asyncio
async def test_build_runtime_missing_runtime_history_has_no_scope_boundary(
    tmp_path: Path,
) -> None:
    from matmaster.config.exp import ExpConfig
    from matmaster.core.exp import Exp

    ctx = AgentRunContext(
        environment=ExecutionEnvironment(
            workdir=tmp_path,
            execution_workdir=str(tmp_path),
            session_type="local",
            cache_area=tmp_path / "cache",
        ),
        request=AgentRunRequest(llm_provider=_MockProvider()),
    )

    runtime = await Exp(ExpConfig(name="test")).build_runtime(ctx)

    assert (
        runtime.kernel_runtime.resources.compactor._runtime_covered_until_provider()
        is None
    )


@pytest.mark.asyncio
async def test_build_runtime_passes_turn_input_to_kernel_spec(
    tmp_path: Path,
) -> None:
    from matmaster.config.exp import ExpConfig
    from matmaster.context.sources.turn_input import TurnInput
    from matmaster.core.exp import Exp

    turn_input = TurnInput.from_values(
        user_text="current task",
        files=["https://oss.example.com/chat/current.cif"],
        pre_turn_history_event_id=12,
    )
    ctx = AgentRunContext(
        environment=ExecutionEnvironment(
            workdir=tmp_path,
            execution_workdir=str(tmp_path),
            session_type="local",
            cache_area=tmp_path / "cache",
        ),
        request=AgentRunRequest(
            llm_provider=_MockProvider(),
            turn_input=turn_input,
        ),
    )

    runtime = await Exp(ExpConfig(name="test")).build_runtime(ctx)

    assert runtime.kernel_runtime.spec.turn_input == turn_input


def _make_playground_context(
    workdir: str = "/tmp/test-workdir",
    execution_workdir: str = "/tmp/test-exec",
    session: Any = None,
    llm_provider: Any = None,
) -> Any:
    """Build a minimal AgentRunContext for build_runtime tests."""
    return AgentRunContext(
        environment=ExecutionEnvironment(
            workdir=Path(workdir),
            session_type="local",
            cache_area=Path("/tmp/test-cache"),
            execution_workdir=execution_workdir,
            session=session or _MockSession(),
        ),
        request=AgentRunRequest(llm_provider=llm_provider or _MockProvider()),
    )


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

        assert runtime.kernel_runtime.resources.tool_runner is not None
        assert isinstance(runtime.kernel_runtime.resources.tool_runner, FullToolRunner)

    @pytest.mark.asyncio
    async def test_spec_has_tool_catalog(self) -> None:
        """build_runtime() injects a ToolCatalog instance into spec."""
        from matmaster.core.exp import Exp
        from matmaster.tools.tool_catalog import ToolCatalog

        config = _make_exp_config()
        exp = Exp(config)
        ctx = _make_playground_context()

        runtime = await exp.build_runtime(ctx)

        assert runtime.kernel_runtime.resources.tool_catalog is not None
        assert isinstance(runtime.kernel_runtime.resources.tool_catalog, ToolCatalog)

    @pytest.mark.asyncio
    async def test_build_runtime_identity_uses_explicit_session_id(self) -> None:
        from matmaster.core.exp import Exp

        config = _make_exp_config()
        exp = Exp(config)
        base = _make_playground_context()
        ctx = base.model_copy(
            update={
                "environment": base.environment.model_copy(
                    update={
                        "session_id": "sess-explicit",
                        "metadata": RunMetadata(task_id="task-1"),
                    }
                ),
            }
        )

        runtime = await exp.build_runtime(ctx)

        assert runtime.kernel_runtime.spec.run_identity.task_id == "task-1"
        assert runtime.kernel_runtime.spec.run_identity.session_id == "sess-explicit"

    @pytest.mark.asyncio
    async def test_spec_has_runtime_topology(self) -> None:
        """build_runtime() injects a RuntimeTopology instance into spec."""
        from matmaster.core.exp import Exp
        from matmaster.types.topology import RuntimeTopology

        config = _make_exp_config()
        exp = Exp(config)
        ctx = _make_playground_context()

        runtime = await exp.build_runtime(ctx)

        assert runtime.kernel_runtime.resources.runtime_topology is not None
        assert isinstance(
            runtime.kernel_runtime.resources.runtime_topology, RuntimeTopology
        )

    @pytest.mark.asyncio
    async def test_build_runtime_derives_active_planes_for_local_session(self) -> None:
        """build_runtime() derives active_planes from session and builtin config."""
        from matmaster.core.exp import Exp
        from matmaster.types.topology import ToolPlane

        config = _make_exp_config(tools={"builtin": ["Read"]})
        exp = Exp(config)
        ctx = _make_playground_context()

        runtime = await exp.build_runtime(ctx)
        topology = runtime.kernel_runtime.resources.runtime_topology

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
        results = await runtime.kernel_runtime.resources.tool_runner.execute_batch(
            [
                ToolCallData(
                    id="c1",
                    name="Read",
                    arguments={"file_path": "/tmp/test-exec/test.txt"},
                )
            ],
            ToolExecutionContext(turn=1, max_turns=10),
        )
        assert results[0][1].status == "success"

    @pytest.mark.asyncio
    async def test_topology_has_correct_paths(self) -> None:
        """RuntimeTopology paths match the ExecutionEnvironment."""
        from matmaster.core.exp import Exp

        config = _make_exp_config()
        exp = Exp(config)
        ctx = _make_playground_context(
            workdir="/tmp/ctrl",
            execution_workdir="/tmp/exec",
        )

        runtime = await exp.build_runtime(ctx)
        topology = runtime.kernel_runtime.resources.runtime_topology

        assert topology.control_root == "/tmp/ctrl"
        assert topology.workspace_root == "/tmp/exec"

    @pytest.mark.asyncio
    async def test_topology_includes_session_remote_project_root(self) -> None:
        """RuntimeTopology allows read/search access to the remote skill mirror."""
        from matmaster.core.exp import Exp

        session = _MockSession()
        session.remote_project_root = "/personal/.matmaster/skills"
        config = _make_exp_config()
        exp = Exp(config)
        ctx = _make_playground_context(session=session)

        runtime = await exp.build_runtime(ctx)
        roots = runtime.kernel_runtime.resources.runtime_topology.path_access_roots

        assert any(
            root.root == "/personal/.matmaster/skills"
            and root.permissions == frozenset({"read", "search"})
            for root in roots
        )

    @pytest.mark.asyncio
    async def test_topology_includes_session_remote_skill_roots(self) -> None:
        """RuntimeTopology allows read/search access to scanned remote skills."""
        from matmaster.core.exp import Exp

        session = _MockSession()
        session.remote_project_root = None
        session.remote_skill_roots = ["/personal/.matmaster/skills"]
        config = _make_exp_config()
        exp = Exp(config)
        ctx = _make_playground_context(session=session)

        runtime = await exp.build_runtime(ctx)
        roots = runtime.kernel_runtime.resources.runtime_topology.path_access_roots

        assert any(
            root.root == "/personal/.matmaster/skills"
            and root.permissions == frozenset({"read", "search"})
            for root in roots
        )

    @pytest.mark.asyncio
    async def test_topology_includes_remote_workspace_matmaster_root(self) -> None:
        """Project-level .matmaster under remote workspace is allowed for read/search."""
        from matmaster.core.exp import Exp

        config = _make_exp_config()
        exp = Exp(config)
        base = _make_playground_context()
        ctx = base.model_copy(
            update={
                "environment": base.environment.with_bohrium(
                    BohriumRuntimeSnapshot(remote_workspace_root="/share")
                ),
            }
        )

        runtime = await exp.build_runtime(ctx)
        roots = runtime.kernel_runtime.resources.runtime_topology.path_access_roots

        assert any(
            root.root == "/share/.matmaster"
            and root.permissions == frozenset({"read", "search"})
            for root in roots
        )

    @pytest.mark.asyncio
    async def test_glob_and_grep_receive_path_access_roots(self) -> None:
        """Shell-backed search tools receive the same extra roots as topology."""
        from matmaster.core.exp import Exp

        session = _MockSession()
        session.remote_project_root = None
        session.remote_skill_roots = ["/personal/.matmaster/skills"]
        config = _make_exp_config(tools={"builtin": ["Glob", "Grep"]})
        exp = Exp(config)
        ctx = _make_playground_context(session=session)

        runtime = await exp.build_runtime(ctx)
        registry = runtime.kernel_runtime.resources.tool_catalog.registry
        glob_tool = registry.get_raw("Glob")
        grep_tool = registry.get_raw("Grep")

        assert glob_tool is not None
        assert grep_tool is not None
        assert "/personal/.matmaster/skills" in glob_tool._path_access_roots
        assert "/personal/.matmaster/skills" in grep_tool._path_access_roots

    def test_build_runtime_seeds_bohrium_registry_from_metadata(self) -> None:
        from matmaster.core.exp import Exp

        config = _make_exp_config()
        exp = Exp(config)
        base = _make_playground_context()
        ctx = base.model_copy(
            update={
                "request": base.request.model_copy(
                    update={
                        "bohrium_rebuild_events": (
                            {
                                "action": "submit",
                                "job_id": "job-1",
                                "job_name": "alpha",
                                "status": "Submitted",
                                "cached": False,
                            },
                            {
                                "action": "poll",
                                "job_id": "job-1",
                                "status": "Running",
                                "cached": False,
                            },
                            {
                                "action": "poll",
                                "job_id": "job-1",
                                "status": "Running",
                                "cached": True,
                            },
                        )
                    }
                ),
            }
        )

        runtime = asyncio.run(exp.build_runtime(ctx))

        registry = runtime.kernel_runtime.resources.tool_runner.state.get(
            "bohrium_job_registry"
        )
        assert registry is not None
        rec = registry.get("job-1")
        assert rec is not None
        assert rec.job_name == "alpha"
        assert rec.status == "running"
        assert rec.poll_count == 1
        assert rec.last_polled_at == 0.0

    @pytest.mark.asyncio
    async def test_build_runtime_preserves_figure_upload_config_in_runner_state(
        self,
    ) -> None:
        from matmaster.core.exp import Exp
        from matmaster.types.figures import FigureUploadConfig
        from matmaster.types.runtime_ports import AgentRunPorts, FigureUploadPort

        figure_upload_config = FigureUploadConfig(
            session_id="sess-1",
            task_id="task-1",
            asset_key_prefix="figures/sess-1/task-1",
            upload_bytes=lambda data, name: f"https://oss.example/{name}",
        )
        config = _make_exp_config()
        exp = Exp(config)
        base = _make_playground_context()
        ctx = base.model_copy(
            update={
                "request": base.request.model_copy(
                    update={
                        "ports": AgentRunPorts(
                            figure_upload=FigureUploadPort(config=figure_upload_config)
                        )
                    }
                ),
            }
        )

        runtime = await exp.build_runtime(ctx)

        stored = runtime.kernel_runtime.resources.tool_runner.state.get(
            "figure_upload_config"
        )
        assert stored is figure_upload_config


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
            assert hasattr(
                event, 'type'
            ), f"Yielded object missing 'type' attribute: {type(event).__name__}"
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
    async def test_run_stream_injects_cancel_token_into_session_and_catalog(
        self,
    ) -> None:
        from matmaster.core.exp import Exp

        config = _make_exp_config()
        exp = Exp(config)
        ctx = _make_playground_context()
        controller = CancellationController()
        catalog = MagicMock()

        observed: dict[str, Any] = {}

        async def fake_kernel_run_stream(
            kernel_runtime: Any,
            task: str,
            history: list[Any] | None = None,
            cancel_token: Any = None,
        ) -> AsyncIterator[Any]:
            observed["kernel_runtime"] = kernel_runtime
            observed["task"] = task
            observed["history"] = history
            observed["cancel_token"] = cancel_token
            yield MagicMock(type="test.event")

        runtime = MagicMock()
        runtime.kernel_runtime = MagicMock(resources=MagicMock(tool_catalog=catalog))
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
        assert ctx.environment.session._cancel_token is controller.token
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
    """Compactor creation uses event_sink=None."""

    @pytest.mark.asyncio
    async def test_compactor_uses_event_sink(self) -> None:
        """Compactor created with event_sink=None for _run_items() injection."""
        from matmaster.core.exp import Exp

        config = _make_exp_config()
        exp = Exp(config)
        ctx = _make_playground_context()

        runtime = await exp.build_runtime(ctx)

        compactor = runtime.kernel_runtime.resources.compactor
        # Compactor should exist and have _event_sink attribute
        assert compactor is not None
        assert hasattr(compactor, "_event_sink")
        # event_sink should be None (set later by _run_items)
        assert compactor._event_sink is None


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
        from matmaster.types.topology import ToolPlane

        config = ExpConfig(name="test", tools={"builtin": ["WebSearch"]})
        exp = Exp(config)
        ctx = AgentRunContext(
            environment=ExecutionEnvironment(
                workdir=tmp_path,
                execution_workdir=str(tmp_path / "exec"),
                session_type="local",
                cache_area=tmp_path / "cache",
                session=None,
            ),
            request=AgentRunRequest(llm_provider=_MockProvider()),
        )

        runtime = await exp.build_runtime(ctx)

        assert (
            ToolPlane.EXTERNAL_SERVICE
            in runtime.kernel_runtime.resources.runtime_topology.active_planes
        )
