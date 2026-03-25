"""Tests for Exp concrete config-driven class."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from matmaster.config.exp import ExpConfig, ExpToolsConfig
from matmaster.core.exp import Exp
from matmaster.types.context import PlaygroundContext
from matmaster.types.events import FinishEvent
from matmaster.types.runtime import AgentRuntime, AgentRuntimeSpec, KernelRunResult
from tests.matmaster.core.conftest import MockLLMProvider


def _make_ctx(*, with_llm: bool = False) -> PlaygroundContext:
    """Create a minimal PlaygroundContext for testing."""
    kwargs: dict = dict(
        workdir=Path("/tmp/test"),
        session_type="local",
        cache_area=Path("/tmp/cache"),
    )
    if with_llm:
        kwargs["llm_provider"] = MockLLMProvider()
    return PlaygroundContext(**kwargs)


# ── TestExpConstruction ──────────────────────────────────


class TestExpConstruction:
    """Exp is a concrete class instantiated with an ExpConfig."""

    def test_exp_is_concrete(self) -> None:
        """Exp can be instantiated directly (not abstract)."""
        exp = Exp(ExpConfig(name="test"))
        assert isinstance(exp, Exp)

    def test_exp_name_from_config(self) -> None:
        """exp_name reads from config.name."""
        exp = Exp(ExpConfig(name="my-experiment"))
        assert exp.exp_name == "my-experiment"

    def test_exp_name_defaults_to_direct(self) -> None:
        """Default name in ExpConfig is 'direct'."""
        exp = Exp(ExpConfig())
        assert exp.exp_name == "direct"

    def test_exp_stores_config(self) -> None:
        """Exp accepts ExpConfig."""
        exp = Exp(ExpConfig())
        assert isinstance(exp._config, ExpConfig)


# ── TestExpAssemble ──────────────────────────────────────


class TestExpAssemble:
    """assemble() transforms config + ctx into AgentRuntimeSpec."""

    def test_returns_agent_runtime_spec(self) -> None:
        """assemble() returns an AgentRuntimeSpec instance."""
        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx()
        spec = exp.assemble(ctx)
        assert isinstance(spec, AgentRuntimeSpec)

    def test_max_turns_from_config(self) -> None:
        """max_turns in config propagates to spec."""
        exp = Exp(ExpConfig(name="test", max_turns=50))
        ctx = _make_ctx()
        spec = exp.assemble(ctx)
        assert spec.max_turns == 50

    def test_max_turns_default(self) -> None:
        """Default max_turns is 100 when not in config."""
        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx()
        spec = exp.assemble(ctx)
        assert spec.max_turns == 100

    def test_guards_deferred(self) -> None:
        """guards are deferred to build_runtime; assemble returns empty list."""
        exp = Exp(ExpConfig(name="test", guards=["mock_guard"]))
        ctx = _make_ctx()
        spec = exp.assemble(ctx)
        assert spec.guards == []

    def test_meta_is_empty(self) -> None:
        """Meta bag is empty with new ExpConfig design."""
        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx()
        spec = exp.assemble(ctx)
        assert spec.meta == {}

    def test_mode_from_config(self) -> None:
        """mode in config propagates to spec."""
        exp = Exp(ExpConfig(name="test", mode="planner"))
        ctx = _make_ctx()
        spec = exp.assemble(ctx)
        assert spec.mode == "planner"

    def test_mode_default(self) -> None:
        """Default mode is 'direct'."""
        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx()
        spec = exp.assemble(ctx)
        assert spec.mode == "direct"

    def test_llm_provider_from_ctx(self) -> None:
        """llm_provider comes from ctx, not config."""
        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx(with_llm=True)
        spec = exp.assemble(ctx)
        assert spec.llm_provider is ctx.llm_provider


# ── TestExpBuildRuntime ──────────────────────────────────


class TestExpBuildRuntime:
    """build_runtime() creates resources and returns AgentRuntime."""

    def test_returns_agent_runtime(self) -> None:
        """build_runtime() returns an AgentRuntime dataclass."""
        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = exp.build_runtime(ctx)

        assert isinstance(runtime, AgentRuntime)

    def test_uses_ctx_llm_provider(self) -> None:
        """Runtime spec uses LLM provider from ctx."""
        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = exp.build_runtime(ctx)

        assert runtime.spec.llm_provider is ctx.llm_provider

    def test_bus_adds_event_emitter_hook(self) -> None:
        """When bus is provided, EventEmitterHook is added to spec.hooks."""
        from matmaster.core.bus import MessageBus
        from matmaster.core.hooks import EventEmitterHook

        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx(with_llm=True)
        bus = MessageBus()

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = exp.build_runtime(ctx, bus=bus)

        emitter_hooks = [h for h in runtime.spec.hooks if isinstance(h, EventEmitterHook)]
        assert len(emitter_hooks) == 1

    def test_no_bus_no_emitter(self) -> None:
        """Without bus, no EventEmitterHook in spec.hooks."""
        from matmaster.core.hooks import EventEmitterHook

        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = exp.build_runtime(ctx)

        emitter_hooks = [h for h in runtime.spec.hooks if isinstance(h, EventEmitterHook)]
        assert len(emitter_hooks) == 0

    def test_runtime_has_cleanup_callable(self) -> None:
        """AgentRuntime.cleanup is a callable."""
        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = exp.build_runtime(ctx)

        assert callable(runtime.cleanup)


# ── TestExpRun ──────────────────────────────────────────


class TestExpRun:
    """run() calls build_runtime then kernel.run with proper args."""

    def test_run_calls_build_runtime_then_kernel(self) -> None:
        """run() delegates to build_runtime then kernel.run."""
        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx(with_llm=True)
        mock_finish = FinishEvent(source="agent", status="completed", reason="natural")
        mock_kernel_result = KernelRunResult(event=mock_finish, messages=[])

        mock_kernel = MagicMock()
        mock_kernel.run.return_value = mock_kernel_result
        mock_spec = MagicMock(spec=AgentRuntimeSpec)
        mock_cleanup = MagicMock()
        mock_runtime = AgentRuntime(kernel=mock_kernel, spec=mock_spec, cleanup=mock_cleanup)

        with patch.object(exp, "build_runtime", return_value=mock_runtime) as mock_br:
            result = exp.run(ctx, "do something")

        mock_br.assert_called_once_with(ctx, bus=None, skills=None, mcp=None)
        mock_kernel.run.assert_called_once_with(
            mock_spec, "do something", history=None, stop_event=None
        )
        assert result is mock_finish

    def test_run_forwards_bus(self) -> None:
        """run() passes bus to build_runtime."""
        from matmaster.core.bus import MessageBus

        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx(with_llm=True)
        bus = MessageBus()
        mock_finish = FinishEvent(source="agent", status="completed", reason="natural")

        mock_kernel = MagicMock()
        mock_kernel.run.return_value = KernelRunResult(event=mock_finish, messages=[])
        mock_spec = MagicMock(spec=AgentRuntimeSpec)
        mock_cleanup = MagicMock()
        mock_runtime = AgentRuntime(kernel=mock_kernel, spec=mock_spec, cleanup=mock_cleanup)

        with patch.object(exp, "build_runtime", return_value=mock_runtime) as mock_br:
            exp.run(ctx, "task", bus=bus)

        mock_br.assert_called_once_with(ctx, bus=bus, skills=None, mcp=None)

    def test_run_forwards_history_and_stop_event(self) -> None:
        """run() passes history and stop_event to kernel.run."""
        import threading

        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx(with_llm=True)
        mock_finish = FinishEvent(source="agent", status="completed", reason="natural")
        stop = threading.Event()
        history = [MagicMock()]

        mock_kernel = MagicMock()
        mock_kernel.run.return_value = KernelRunResult(event=mock_finish, messages=[])
        mock_spec = MagicMock(spec=AgentRuntimeSpec)
        mock_cleanup = MagicMock()
        mock_runtime = AgentRuntime(kernel=mock_kernel, spec=mock_spec, cleanup=mock_cleanup)

        with patch.object(exp, "build_runtime", return_value=mock_runtime):
            exp.run(ctx, "task", history=history, stop_event=stop)

        mock_kernel.run.assert_called_once_with(
            mock_spec, "task", history=history, stop_event=stop
        )


# ── TestExpCleanup ───────────────────────────────────────


class TestExpCleanup:
    """Cleanup callbacks are guaranteed to execute via runtime.cleanup()."""

    def test_cleanup_runs_on_success(self) -> None:
        """Cleanup is called after successful kernel.run via run()."""
        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx(with_llm=True)
        mock_finish = FinishEvent(source="agent", status="completed", reason="natural")

        mock_kernel = MagicMock()
        mock_kernel.run.return_value = KernelRunResult(event=mock_finish, messages=[])
        mock_spec = MagicMock(spec=AgentRuntimeSpec)
        mock_cleanup = MagicMock()
        mock_runtime = AgentRuntime(kernel=mock_kernel, spec=mock_spec, cleanup=mock_cleanup)

        with patch.object(exp, "build_runtime", return_value=mock_runtime):
            exp.run(ctx, "task")

        mock_cleanup.assert_called_once()

    def test_cleanup_runs_on_error(self) -> None:
        """Cleanup is called even when kernel.run() raises."""
        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx(with_llm=True)

        mock_kernel = MagicMock()
        mock_kernel.run.side_effect = RuntimeError("kernel exploded")
        mock_spec = MagicMock(spec=AgentRuntimeSpec)
        mock_cleanup = MagicMock()
        mock_runtime = AgentRuntime(kernel=mock_kernel, spec=mock_spec, cleanup=mock_cleanup)

        with patch.object(exp, "build_runtime", return_value=mock_runtime):
            with pytest.raises(RuntimeError, match="kernel exploded"):
                exp.run(ctx, "task")

        mock_cleanup.assert_called_once()

    def test_multiple_cleanups_all_execute(self) -> None:
        """All registered cleanup callbacks run even if one raises."""
        exp = Exp(ExpConfig(name="test"))
        cb1 = MagicMock(side_effect=ValueError("cb1 broken"))
        cb2 = MagicMock()
        exp._register_cleanup(cb1)
        exp._register_cleanup(cb2)

        exp._run_cleanup_callbacks()

        cb1.assert_called_once()
        cb2.assert_called_once()

    def test_cleanup_clears_list(self) -> None:
        """_run_cleanup_callbacks clears the list after execution."""
        exp = Exp(ExpConfig(name="test"))
        cb = MagicMock()
        exp._register_cleanup(cb)

        exp._run_cleanup_callbacks()

        assert exp._cleanup_callbacks == []


# ── TestIdentityOverride ────────────────────────────────


class TestIdentityOverride:
    """Identity from config is forwarded to ContextBuilder.build()."""

    def test_identity_from_config(self) -> None:
        exp = Exp(ExpConfig(
            name="test",
            developer_instructions="I am a materials scientist.",
            tools=ExpToolsConfig(builtin=[]),
        ))
        ctx = _make_ctx(with_llm=True)
        runtime = exp.build_runtime(ctx)

        assert "I am a materials scientist." in runtime.spec.system_prompt

    def test_default_identity_when_not_set(self) -> None:
        exp = Exp(ExpConfig(
            name="test",
            tools=ExpToolsConfig(builtin=[]),
        ))
        ctx = _make_ctx(with_llm=True)
        runtime = exp.build_runtime(ctx)

        assert "helpful AI assistant" in runtime.spec.system_prompt


# ── TestExpBuiltinTools ─────────────────────────────────


class TestExpBuiltinTools:
    """_init_builtin_tools dual-source registration: 12 native + 1 evo adapter."""

    def _make_ctx_with_session(self, tmp_path: Path) -> PlaygroundContext:
        """Create PlaygroundContext with a mock session for builtin tool tests."""
        return PlaygroundContext(
            workdir=tmp_path,
            session_type="local",
            cache_area=tmp_path / "cache",
            session=MagicMock(),
            llm_provider=MockLLMProvider(),
        )

    def _build_registry(self, tmp_path: Path) -> tuple[Exp, "ToolRegistry"]:
        """Build an Exp and run _init_builtin_tools, returning (exp, registry)."""
        from matmaster.tools.tool_registry import ToolRegistry

        exp = Exp(ExpConfig(name="test"))
        ctx = self._make_ctx_with_session(tmp_path)
        registry = ToolRegistry()
        exp._init_builtin_tools(ctx, registry)
        return exp, registry

    def test_native_tools_count(self, tmp_path: Path) -> None:
        """12 native tools registered with source='builtin'."""
        _, registry = self._build_registry(tmp_path)
        native = registry.get_tools_by_source("builtin")
        assert len(native) == 12

    def test_native_tool_names(self, tmp_path: Path) -> None:
        """All 12 expected native tool names are present in registry."""
        _, registry = self._build_registry(tmp_path)
        expected_native = {
            "execute_bash",
            "list_dir",
            "read_file",
            "write_file",
            "edit_file",
            "glob",
            "grep",
            "task_create",
            "task_get",
            "task_list",
            "task_update",
            "task_complete",
        }
        for name in expected_native:
            assert name in registry, f"Expected tool '{name}' not found in registry"

    def test_evo_adapted_tools_count(self, tmp_path: Path) -> None:
        """1 evo adapter tool registered with source='builtin_evo' (MonitorJobTool only)."""
        _, registry = self._build_registry(tmp_path)
        evo = registry.get_tools_by_source("builtin_evo")
        assert len(evo) == 1

    def test_editor_tool_removed(self, tmp_path: Path) -> None:
        """str_replace_editor (EditorTool) is NOT in the registry."""
        _, registry = self._build_registry(tmp_path)
        assert "str_replace_editor" not in registry

    def test_monitor_job_retained(self, tmp_path: Path) -> None:
        """MonitorJobTool is still registered with source='builtin_evo'."""
        _, registry = self._build_registry(tmp_path)
        evo = registry.get_tools_by_source("builtin_evo")
        evo_names = {t.name for t in evo}
        assert "monitor_job" in evo_names

    def test_total_count(self, tmp_path: Path) -> None:
        """Total tools = 12 native + 1 evo adapter = 13."""
        _, registry = self._build_registry(tmp_path)
        assert len(registry) == 13

    def test_read_tracker_cleanup_registered(self, tmp_path: Path) -> None:
        """ReadTracker.clear is registered as a cleanup callback after _init_builtin_tools."""
        exp, _ = self._build_registry(tmp_path)
        # At least one cleanup callback should be registered (ReadTracker.clear)
        assert len(exp._cleanup_callbacks) >= 1
        # The callback should be the bound clear method of a ReadTracker instance
        cb = exp._cleanup_callbacks[-1]
        assert hasattr(cb, "__self__")
        from matmaster.tools.builtin import ReadTracker

        assert isinstance(cb.__self__, ReadTracker)

    def test_init_builtin_tools_no_session(self, tmp_path: Path) -> None:
        """session=None skips all tool registration."""
        from matmaster.tools.tool_registry import ToolRegistry

        exp = Exp(ExpConfig(name="test"))
        ctx = PlaygroundContext(
            workdir=tmp_path,
            session_type="local",
            cache_area=tmp_path / "cache",
            session=None,
            llm_provider=MockLLMProvider(),
        )
        registry = ToolRegistry()
        exp._init_builtin_tools(ctx, registry)
        assert len(registry) == 0

    def test_explicit_builtin_config_triggers_init(self, tmp_path: Path) -> None:
        """Non-empty explicit tool list (not wildcard) still triggers _init_builtin_tools."""
        exp = Exp(ExpConfig(
            name="test",
            tools=ExpToolsConfig(builtin=["execute_bash", "read_file"]),
        ))
        ctx = self._make_ctx_with_session(tmp_path)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = exp.build_runtime(ctx)

        # If _init_builtin_tools ran, native tools should be in the registry
        native = runtime.spec.tool_registry.get_tools_by_source("builtin")
        assert len(native) == 12  # All native tools registered regardless of config list

    def test_empty_builtin_config_skips_init(self, tmp_path: Path) -> None:
        """Empty builtin list skips _init_builtin_tools entirely."""
        exp = Exp(ExpConfig(
            name="test",
            tools=ExpToolsConfig(builtin=[]),
        ))
        ctx = self._make_ctx_with_session(tmp_path)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = exp.build_runtime(ctx)

        native = runtime.spec.tool_registry.get_tools_by_source("builtin")
        assert len(native) == 0


class TestExpCompaction:
    def test_assemble_compaction_defaults_disabled(self) -> None:
        from matmaster.types.runtime import CompactionConfig

        exp = Exp(ExpConfig(name="test"))
        ctx = MagicMock()
        ctx.llm_provider = None

        spec = exp.assemble(ctx)
        assert isinstance(spec.compaction, CompactionConfig)
        assert spec.compaction.enabled is False

    def test_assemble_default_compaction(self) -> None:
        exp = Exp(ExpConfig(name="test"))
        ctx = MagicMock()
        ctx.llm_provider = None

        spec = exp.assemble(ctx)
        assert spec.compaction.enabled is False

    def test_build_runtime_compactor_none_when_disabled(self) -> None:
        exp = Exp(ExpConfig(name="test", tools=ExpToolsConfig(builtin=[])))
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = exp.build_runtime(ctx)

        assert runtime.spec.compactor is None
