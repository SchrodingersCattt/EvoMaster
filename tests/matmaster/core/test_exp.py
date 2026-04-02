"""Tests for Exp concrete config-driven class."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matmaster.config.exp import ExpConfig, ExpToolsConfig
from matmaster.core.exp import Exp
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.types.context import PlaygroundContext
from matmaster.types.messages import ToolCallData
from matmaster.types.runtime import (
    AgentRuntime,
    AgentRuntimeSpec,
    KernelResult,
    KernelRunResult,
)
from matmaster.types.session import Session
from tests.matmaster.core.conftest import MockLLMProvider


def _make_ctx(*, with_llm: bool = False) -> PlaygroundContext:
    """Create a minimal PlaygroundContext for testing."""
    kwargs: dict = dict(
        workdir=Path('/tmp/test'),
        session_type='local',
        cache_area=Path('/tmp/cache'),
    )
    if with_llm:
        kwargs['llm_provider'] = MockLLMProvider()
    return PlaygroundContext(**kwargs)


# ── TestExpConstruction ──────────────────────────────────


class TestExpConstruction:
    """Exp is a concrete class instantiated with an ExpConfig."""

    def test_exp_is_concrete(self) -> None:
        """Exp can be instantiated directly (not abstract)."""
        exp = Exp(ExpConfig(name='test'))
        assert isinstance(exp, Exp)

    def test_exp_name_from_config(self) -> None:
        """exp_name reads from config.name."""
        exp = Exp(ExpConfig(name='my-experiment'))
        assert exp.exp_name == 'my-experiment'

    def test_exp_name_defaults_to_direct(self) -> None:
        """Default name in ExpConfig is 'direct'."""
        exp = Exp(ExpConfig())
        assert exp.exp_name == 'direct'

    def test_exp_stores_config(self) -> None:
        """Exp accepts ExpConfig."""
        exp = Exp(ExpConfig())
        assert isinstance(exp._config, ExpConfig)


# ── TestExpAssemble ──────────────────────────────────────


class TestExpAssemble:
    """assemble() transforms config + ctx into AgentRuntimeSpec."""

    async def test_returns_agent_runtime_spec(self) -> None:
        """assemble() returns an AgentRuntimeSpec instance."""
        exp = Exp(ExpConfig(name='test'))
        ctx = _make_ctx()
        spec = await exp.assemble(ctx)
        assert isinstance(spec, AgentRuntimeSpec)

    async def test_max_turns_from_config(self) -> None:
        """max_turns in config propagates to spec."""
        exp = Exp(ExpConfig(name='test', max_turns=50))
        ctx = _make_ctx()
        spec = await exp.assemble(ctx)
        assert spec.max_turns == 50

    async def test_max_turns_default(self) -> None:
        """Default max_turns is 100 when not in config."""
        exp = Exp(ExpConfig(name='test'))
        ctx = _make_ctx()
        spec = await exp.assemble(ctx)
        assert spec.max_turns == 100

    async def test_guards_deferred(self) -> None:
        """guards are deferred to build_runtime; assemble returns empty list."""
        exp = Exp(ExpConfig(name='test', guards=['mock_guard']))
        ctx = _make_ctx()
        spec = await exp.assemble(ctx)
        assert spec.guards == []

    async def test_meta_is_empty(self) -> None:
        """Meta bag is empty with new ExpConfig design."""
        exp = Exp(ExpConfig(name='test'))
        ctx = _make_ctx()
        spec = await exp.assemble(ctx)
        assert spec.meta == {}

    async def test_llm_provider_from_ctx(self) -> None:
        """llm_provider comes from ctx, not config."""
        exp = Exp(ExpConfig(name='test'))
        ctx = _make_ctx(with_llm=True)
        spec = await exp.assemble(ctx)
        assert spec.llm_provider is ctx.llm_provider


# ── TestExpBuildRuntime ──────────────────────────────────


class TestExpBuildRuntime:
    """build_runtime() creates resources and returns AgentRuntime."""

    async def test_returns_agent_runtime(self) -> None:
        """build_runtime() returns an AgentRuntime dataclass."""
        exp = Exp(ExpConfig(name='test'))
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        assert isinstance(runtime, AgentRuntime)

    async def test_uses_ctx_llm_provider(self) -> None:
        """Runtime spec uses LLM provider from ctx."""
        exp = Exp(ExpConfig(name='test'))
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        assert runtime.spec.llm_provider is ctx.llm_provider

    async def test_bus_adds_event_emitter_hook(self) -> None:
        """When bus is provided, EventEmitterHook is added to spec.hooks."""
        from matmaster.core.bus import MessageBus
        from matmaster.core.hooks import EventEmitterHook

        exp = Exp(ExpConfig(name='test'))
        ctx = _make_ctx(with_llm=True)
        bus = MessageBus()

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx, bus=bus)

        emitter_hooks = [
            h for h in runtime.spec.hooks if isinstance(h, EventEmitterHook)
        ]
        assert len(emitter_hooks) == 1

    async def test_build_runtime_threads_spawn_id_into_emitter_hook(self) -> None:
        """Child runtimes pass spawn_id through EventEmitterHook into emitted events."""
        from matmaster.core.bus import MessageBus
        from matmaster.core.hooks import EventEmitterHook
        from matmaster.types.events import ToolCallEvent

        exp = Exp(ExpConfig(name='test'))
        ctx = _make_ctx(with_llm=True)
        bus = MessageBus()
        tool_call = ToolCallData(id='tc-1', name='spawn', arguments={'task': 'demo'})

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx, bus=bus, spawn_id="childdeadbeef123")

        emitter_hook = next(
            h for h in runtime.spec.hooks if isinstance(h, EventEmitterHook)
        )
        await emitter_hook.pre_tool_call(tool_call)

        event = bus.get_nowait()
        assert isinstance(event, ToolCallEvent)
        assert event.spawn_id == 'childdeadbeef123'

    async def test_parent_runtime_emits_none_spawn_id_by_default(self) -> None:
        """Parent runtimes keep spawn_id=None unless one is explicitly provided."""
        from matmaster.core.bus import MessageBus
        from matmaster.core.hooks import EventEmitterHook
        from matmaster.types.events import ToolCallEvent

        exp = Exp(ExpConfig(name='test'))
        ctx = _make_ctx(with_llm=True)
        bus = MessageBus()
        tool_call = ToolCallData(id='tc-1', name='spawn', arguments={'task': 'demo'})

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx, bus=bus)

        emitter_hook = next(
            h for h in runtime.spec.hooks if isinstance(h, EventEmitterHook)
        )
        await emitter_hook.pre_tool_call(tool_call)

        event = bus.get_nowait()
        assert isinstance(event, ToolCallEvent)
        assert event.spawn_id is None

    async def test_no_bus_no_emitter(self) -> None:
        """Without bus, no EventEmitterHook in spec.hooks."""
        from matmaster.core.hooks import EventEmitterHook

        exp = Exp(ExpConfig(name='test'))
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        emitter_hooks = [
            h for h in runtime.spec.hooks if isinstance(h, EventEmitterHook)
        ]
        assert len(emitter_hooks) == 0

    async def test_runtime_has_cleanup_callable(self) -> None:
        """AgentRuntime.cleanup is a callable."""
        exp = Exp(ExpConfig(name='test'))
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        assert callable(runtime.cleanup)


# ── TestExpRun ──────────────────────────────────────────


class TestExpRun:
    """run() calls build_runtime then kernel.run with proper args."""

    async def test_run_calls_build_runtime_then_kernel(self) -> None:
        """run() delegates to build_runtime then kernel.run."""
        exp = Exp(ExpConfig(name='test'))
        ctx = _make_ctx(with_llm=True)
        mock_kr = KernelResult(status='completed', reason='natural')
        mock_kernel_result = KernelRunResult(result=mock_kr, messages=[])

        mock_kernel = MagicMock()
        mock_kernel.run = AsyncMock(return_value=mock_kernel_result)
        mock_spec = MagicMock(spec=AgentRuntimeSpec)
        mock_cleanup = MagicMock()
        mock_runtime = AgentRuntime(
            kernel=mock_kernel, spec=mock_spec, cleanup=mock_cleanup
        )

        with patch.object(
            exp, "build_runtime", new_callable=AsyncMock, return_value=mock_runtime
        ) as mock_br:
            result = await exp.run(ctx, "do something")

        mock_br.assert_called_once_with(
            ctx,
            bus=None,
            skills=None,
            source_override=None,
            spawn_id=None,
        )
        mock_kernel.run.assert_called_once_with(
            mock_spec, 'do something', history=None, stop_event=None
        )
        assert result is mock_kr

    async def test_run_forwards_bus(self) -> None:
        """run() passes bus to build_runtime."""
        from matmaster.core.bus import MessageBus

        exp = Exp(ExpConfig(name='test'))
        ctx = _make_ctx(with_llm=True)
        bus = MessageBus()
        mock_kr = KernelResult(status='completed', reason='natural')

        mock_kernel = MagicMock()
        mock_kernel.run = AsyncMock(
            return_value=KernelRunResult(result=mock_kr, messages=[])
        )
        mock_spec = MagicMock(spec=AgentRuntimeSpec)
        mock_cleanup = MagicMock()
        mock_runtime = AgentRuntime(
            kernel=mock_kernel, spec=mock_spec, cleanup=mock_cleanup
        )

        with patch.object(
            exp, "build_runtime", new_callable=AsyncMock, return_value=mock_runtime
        ) as mock_br:
            await exp.run(ctx, "task", bus=bus)

        mock_br.assert_called_once_with(
            ctx,
            bus=bus,
            skills=None,
            source_override=None,
            spawn_id=None,
        )

    async def test_run_forwards_history_and_stop_event(self) -> None:
        """run() passes history and stop_event to kernel.run."""
        import threading

        exp = Exp(ExpConfig(name='test'))
        ctx = _make_ctx(with_llm=True)
        mock_kr = KernelResult(status='completed', reason='natural')
        stop = threading.Event()
        history = [MagicMock()]

        mock_kernel = MagicMock()
        mock_kernel.run = AsyncMock(
            return_value=KernelRunResult(result=mock_kr, messages=[])
        )
        mock_spec = MagicMock(spec=AgentRuntimeSpec)
        mock_cleanup = MagicMock()
        mock_runtime = AgentRuntime(
            kernel=mock_kernel, spec=mock_spec, cleanup=mock_cleanup
        )

        with patch.object(
            exp, "build_runtime", new_callable=AsyncMock, return_value=mock_runtime
        ):
            await exp.run(ctx, "task", history=history, stop_event=stop)

        mock_kernel.run.assert_called_once_with(
            mock_spec, 'task', history=history, stop_event=stop
        )


# ── TestExpCleanup ───────────────────────────────────────


class TestExpCleanup:
    """Cleanup callbacks are guaranteed to execute via _run_cleanup_callbacks()."""

    async def test_cleanup_runs_on_success(self) -> None:
        """Cleanup is called after successful kernel.run via run()."""
        exp = Exp(ExpConfig(name='test'))
        ctx = _make_ctx(with_llm=True)
        mock_kr = KernelResult(status='completed', reason='natural')

        mock_kernel = MagicMock()
        mock_kernel.run = AsyncMock(
            return_value=KernelRunResult(result=mock_kr, messages=[])
        )
        mock_spec = MagicMock(spec=AgentRuntimeSpec)
        mock_cleanup = MagicMock()
        mock_runtime = AgentRuntime(
            kernel=mock_kernel, spec=mock_spec, cleanup=mock_cleanup
        )

        # Register a cleanup callback before run() so we can verify it gets called
        cleanup_cb = MagicMock()
        exp._register_cleanup(cleanup_cb)

        with patch.object(
            exp, "build_runtime", new_callable=AsyncMock, return_value=mock_runtime
        ):
            await exp.run(ctx, "task")

        cleanup_cb.assert_called_once()
        assert exp._cleanup_callbacks == []  # cleared after execution

    async def test_cleanup_runs_on_error(self) -> None:
        """Cleanup is called even when kernel.run() raises."""
        exp = Exp(ExpConfig(name='test'))
        ctx = _make_ctx(with_llm=True)

        mock_kernel = MagicMock()
        mock_kernel.run = AsyncMock(side_effect=RuntimeError("kernel exploded"))
        mock_spec = MagicMock(spec=AgentRuntimeSpec)
        mock_cleanup = MagicMock()
        mock_runtime = AgentRuntime(
            kernel=mock_kernel, spec=mock_spec, cleanup=mock_cleanup
        )

        cleanup_cb = MagicMock()
        exp._register_cleanup(cleanup_cb)

        with patch.object(
            exp, "build_runtime", new_callable=AsyncMock, return_value=mock_runtime
        ):
            with pytest.raises(RuntimeError, match="kernel exploded"):
                await exp.run(ctx, "task")

        cleanup_cb.assert_called_once()
        assert exp._cleanup_callbacks == []

    async def test_multiple_cleanups_all_execute(self) -> None:
        """All registered cleanup callbacks run even if one raises."""
        exp = Exp(ExpConfig(name='test'))
        cb1 = MagicMock(side_effect=ValueError('cb1 broken'))
        cb2 = MagicMock()
        exp._register_cleanup(cb1)
        exp._register_cleanup(cb2)

        await exp._run_cleanup_callbacks()

        cb1.assert_called_once()
        cb2.assert_called_once()

    async def test_cleanup_clears_list(self) -> None:
        """_run_cleanup_callbacks clears the list after execution."""
        exp = Exp(ExpConfig(name='test'))
        cb = MagicMock()
        exp._register_cleanup(cb)

        await exp._run_cleanup_callbacks()

        assert exp._cleanup_callbacks == []


# ── TestIdentityOverride ────────────────────────────────


class TestIdentityOverride:
    """Identity from config is forwarded to ContextBuilder.build()."""

    async def test_identity_from_config(self) -> None:
        exp = Exp(
            ExpConfig(
                name="test",
                developer_instructions="I am a materials scientist.",
                tools=ExpToolsConfig(builtin=[]),
            )
        )
        ctx = _make_ctx(with_llm=True)
        runtime = await exp.build_runtime(ctx)

        assert 'I am a materials scientist.' in runtime.spec.system_prompt

    async def test_default_identity_when_not_set(self) -> None:
        """Empty developer_instructions means no identity section in prompt."""
        exp = Exp(
            ExpConfig(
                name='test',
                tools=ExpToolsConfig(builtin=[]),
            )
        )
        ctx = _make_ctx(with_llm=True)
        runtime = await exp.build_runtime(ctx)

        assert '# Identity' not in runtime.spec.system_prompt


class TestSystemPromptOverride:
    """system_prompt from config is forwarded to ContextBuilder.build()."""

    async def test_system_prompt_from_config(self) -> None:
        exp = Exp(
            ExpConfig(
                name="test",
                system_prompt="Base persona text.",
                tools=ExpToolsConfig(builtin=[]),
            )
        )
        ctx = _make_ctx(with_llm=True)
        runtime = await exp.build_runtime(ctx)

        assert 'Base persona text.' in runtime.spec.system_prompt

    async def test_empty_system_prompt_skips_section(self) -> None:
        exp = Exp(
            ExpConfig(
                name="test",
                system_prompt="",
                tools=ExpToolsConfig(builtin=[]),
            )
        )
        ctx = _make_ctx(with_llm=True)
        runtime = await exp.build_runtime(ctx)

        assert '# System' not in runtime.spec.system_prompt


# ── TestExpBuiltinTools ─────────────────────────────────


class TestExpBuiltinTools:
    """_init_builtin_tools native registration: 15 builtin tools (no evo adapter)."""

    def _make_ctx_with_session(self, tmp_path: Path) -> PlaygroundContext:
        """Create PlaygroundContext with a mock session for builtin tool tests."""
        return PlaygroundContext(
            workdir=tmp_path,
            session_type='local',
            cache_area=tmp_path / 'cache',
            session=MagicMock(spec=Session),
            llm_provider=MockLLMProvider(),
        )

    def _build_registry(self, tmp_path: Path) -> tuple[Exp, ToolRegistry]:
        """Build an Exp and run _init_builtin_tools, returning (exp, registry)."""
        exp = Exp(ExpConfig(name='test'))
        ctx = self._make_ctx_with_session(tmp_path)
        registry = ToolRegistry()
        exp._init_builtin_tools(ctx, registry, ['*'])
        return exp, registry

    def test_native_tools_count(self, tmp_path: Path) -> None:
        """15 native tools registered with source='builtin' (includes MonitorJobTool)."""
        _, registry = self._build_registry(tmp_path)
        native = registry.get_tools_by_source("builtin")
        assert len(native) == 15

    def test_native_tool_names(self, tmp_path: Path) -> None:
        """All 12 expected native tool names are present in registry."""
        _, registry = self._build_registry(tmp_path)
        expected_native = {
            'execute_bash',
            'list_dir',
            'read_file',
            'write_file',
            'edit_file',
            'glob',
            'grep',
            'task_create',
            'task_get',
            'task_list',
            'task_update',
            'task_complete',
        }
        for name in expected_native:
            assert name in registry, f"Expected tool '{name}' not found in registry"

    def test_no_evo_adapted_tools(self, tmp_path: Path) -> None:
        """No evo adapter tools remain (EvoToolAdapter eliminated)."""
        _, registry = self._build_registry(tmp_path)
        evo = registry.get_tools_by_source('builtin_evo')
        assert len(evo) == 0

    def test_editor_tool_removed(self, tmp_path: Path) -> None:
        """str_replace_editor (EditorTool) is NOT in the registry."""
        _, registry = self._build_registry(tmp_path)
        assert 'str_replace_editor' not in registry

    def test_monitor_job_is_native_builtin(self, tmp_path: Path) -> None:
        """MonitorJobTool is registered as native builtin (source='builtin')."""
        _, registry = self._build_registry(tmp_path)
        native = registry.get_tools_by_source('builtin')
        native_names = {t.name for t in native}
        assert 'monitor_job' in native_names

    def test_total_count(self, tmp_path: Path) -> None:
        """Total tools = 15 native builtin (no evo adapters)."""
        _, registry = self._build_registry(tmp_path)
        assert len(registry) == 15

    def test_web_search_is_native_builtin(self, tmp_path: Path) -> None:
        """WebSearchTool is registered as native builtin (not evo adapter)."""
        _, registry = self._build_registry(tmp_path)
        native = registry.get_tools_by_source('builtin')
        native_names = {t.name for t in native}
        assert 'web_search' in native_names

    def test_read_tracker_cleanup_registered(self, tmp_path: Path) -> None:
        """ReadTracker.clear is registered as a cleanup callback after _init_builtin_tools."""
        exp, _ = self._build_registry(tmp_path)
        # At least one cleanup callback should be registered (ReadTracker.clear)
        assert len(exp._cleanup_callbacks) >= 1
        # The callback should be the bound clear method of a ReadTracker instance
        cb = exp._cleanup_callbacks[-1]
        assert hasattr(cb, '__self__')
        from matmaster.tools.builtin import ReadTracker

        assert isinstance(cb.__self__, ReadTracker)

    def test_init_builtin_tools_no_session(self, tmp_path: Path) -> None:
        """session=None skips all tool registration."""
        from matmaster.tools.tool_registry import ToolRegistry

        exp = Exp(ExpConfig(name='test'))
        ctx = PlaygroundContext(
            workdir=tmp_path,
            session_type='local',
            cache_area=tmp_path / 'cache',
            session=None,
            llm_provider=MockLLMProvider(),
        )
        registry = ToolRegistry()
        exp._init_builtin_tools(ctx, registry, ['*'])
        assert len(registry) == 0

    async def test_explicit_builtin_config_filters_tools(self, tmp_path: Path) -> None:
        """Non-empty explicit tool list registers only the requested tools."""
        exp = Exp(
            ExpConfig(
                name='test',
                tools=ExpToolsConfig(builtin=['execute_bash', 'read_file']),
            )
        )
        ctx = self._make_ctx_with_session(tmp_path)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        native = runtime.spec.tool_registry.get_tools_by_source('builtin')
        registered_names = {t.name for t in native}
        assert registered_names == {'execute_bash', 'read_file'}

    async def test_empty_builtin_config_skips_init(self, tmp_path: Path) -> None:
        """Empty builtin list skips _init_builtin_tools entirely."""
        exp = Exp(
            ExpConfig(
                name='test',
                tools=ExpToolsConfig(builtin=[]),
            )
        )
        ctx = self._make_ctx_with_session(tmp_path)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        native = runtime.spec.tool_registry.get_tools_by_source('builtin')
        assert len(native) == 0


# ── TestExecutionWorkdirBinding ─────────────────────────


class TestExecutionWorkdirBinding:
    """Builtin tools: execution plane vs control-plane (task) workdirs."""

    @staticmethod
    def _ctx(
        tmp_path: Path,
        *,
        control: Path,
        execution: Path,
    ) -> PlaygroundContext:
        return PlaygroundContext(
            workdir=control,
            execution_workdir=str(execution),
            session_type='local',
            cache_area=tmp_path / 'cache',
            session=MagicMock(spec=Session),
            llm_provider=MockLLMProvider(),
        )

    def test_execution_side_tools_use_execution_workdir(self, tmp_path: Path) -> None:
        from matmaster.tools.tool_registry import ToolRegistry

        control = tmp_path / 'control'
        execution = tmp_path / 'execution'
        control.mkdir()
        execution.mkdir()
        ctx = self._ctx(tmp_path, control=control, execution=execution)
        exp = Exp(ExpConfig(name='test'))
        registry = ToolRegistry()
        exp._init_builtin_tools(ctx, registry, ['*'])
        by_name = {t.name: t for t in registry.all_tools}
        for name in (
            'execute_bash',
            'list_dir',
            'read_file',
            'write_file',
            'edit_file',
            'glob',
            'grep',
        ):
            assert by_name[name]._workdir == execution, name

    def test_task_tools_use_local_workdir(self, tmp_path: Path) -> None:
        from matmaster.tools.tool_registry import ToolRegistry

        control = tmp_path / 'control'
        execution = tmp_path / 'execution'
        control.mkdir()
        execution.mkdir()
        ctx = self._ctx(tmp_path, control=control, execution=execution)
        exp = Exp(ExpConfig(name='test'))
        registry = ToolRegistry()
        exp._init_builtin_tools(ctx, registry, ['*'])
        by_name = {t.name: t for t in registry.all_tools}
        for name in (
            'task_create',
            'task_get',
            'task_list',
            'task_update',
            'task_complete',
        ):
            assert by_name[name]._workdir == control, name

    async def test_spawn_tool_uses_execution_workdir(self, tmp_path: Path) -> None:
        from matmaster.tools.builtin.spawn_tool import SpawnTool

        control = tmp_path / 'control'
        execution = tmp_path / 'execution'
        control.mkdir()
        execution.mkdir()
        ctx = self._ctx(tmp_path, control=control, execution=execution)
        exp = Exp(ExpConfig(name="test", tools=ExpToolsConfig(builtin=["*"])))
        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)
        subs = [
            t for t in runtime.spec.tool_registry.all_tools if isinstance(t, SpawnTool)
        ]
        assert len(subs) == 1
        assert subs[0]._workdir == execution


class TestExpCompaction:
    async def test_assemble_compaction_defaults_disabled(self) -> None:
        from matmaster.types.runtime import CompactionConfig

        exp = Exp(ExpConfig(name='test'))
        ctx = MagicMock()
        ctx.llm_provider = None

        spec = await exp.assemble(ctx)
        assert isinstance(spec.compaction, CompactionConfig)
        assert spec.compaction.enabled is False

    async def test_assemble_default_compaction(self) -> None:
        exp = Exp(ExpConfig(name="test"))
        ctx = MagicMock()
        ctx.llm_provider = None

        spec = await exp.assemble(ctx)
        assert spec.compaction.enabled is False

    async def test_build_runtime_compactor_none_when_disabled(self) -> None:
        exp = Exp(ExpConfig(name="test", tools=ExpToolsConfig(builtin=[])))
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        assert runtime.spec.compactor is None
