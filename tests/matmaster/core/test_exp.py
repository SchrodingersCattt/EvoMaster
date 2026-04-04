"""Tests for Exp concrete config-driven class."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from matmaster.config.exp import ExpConfig, ExpToolsConfig
from matmaster.core.exp import Exp
from matmaster.core.hooks import HookExecutor
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.types.context import PlaygroundContext
from matmaster.types.runtime import (
    AgentRuntime,
    AgentRuntimeSpec,
)
from matmaster.types.session import Session
from matmaster.types.tool_runner_state import ToolRunnerState
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

    async def test_assemble_does_not_expose_guards_field(self) -> None:
        """Guard 配置已移除，assemble 产出的 spec 不再暴露 guards 字段。"""
        exp = Exp(ExpConfig(name='test'))
        ctx = _make_ctx()
        spec = await exp.assemble(ctx)
        assert "guards" not in type(spec).model_fields

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

    async def test_build_runtime_has_no_bus_parameter(self) -> None:
        """build_runtime() no longer accepts bus parameter (Phase 36 de-bus)."""
        exp = Exp(ExpConfig(name='test'))
        ctx = _make_ctx(with_llm=True)

        import inspect
        sig = inspect.signature(exp.build_runtime)
        assert 'bus' not in sig.parameters

    async def test_build_runtime_creates_hook_executor(self) -> None:
        """build_runtime() always injects a HookExecutor."""
        exp = Exp(ExpConfig(name='test'))
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        assert isinstance(runtime.spec.hook_executor, HookExecutor)

    async def test_runtime_has_cleanup_callable(self) -> None:
        """AgentRuntime.cleanup is a callable."""
        exp = Exp(ExpConfig(name='test'))
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        assert callable(runtime.cleanup)

    async def test_tool_runner_state_cleanup_registered(self, tmp_path: Path) -> None:
        exp = Exp(
            ExpConfig(
                name="test",
                tools=ExpToolsConfig(builtin=["Read"]),
            )
        )
        ctx = PlaygroundContext(
            workdir=tmp_path,
            execution_workdir=str(tmp_path / "exec"),
            session_type="local",
            cache_area=tmp_path / "cache",
            session=MagicMock(spec=Session),
            llm_provider=MockLLMProvider(),
        )

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        assert exp._cleanup_callbacks
        state = runtime.spec.tool_runner.state
        assert isinstance(state, ToolRunnerState)

        matching_callbacks = [
            cb for cb in exp._cleanup_callbacks if getattr(cb, "__self__", None) is state
        ]
        assert matching_callbacks

    async def test_collects_tool_prompts_into_system_prompt(self, tmp_path: Path) -> None:
        exp = Exp(
            ExpConfig(
                name="test",
                system_prompt="Base persona text.",
                tools=ExpToolsConfig(builtin=["Bash"]),
            )
        )
        ctx = PlaygroundContext(
            workdir=tmp_path,
            execution_workdir=str(tmp_path / "exec"),
            session_type="local",
            cache_area=tmp_path / "cache",
            session=MagicMock(spec=Session),
            llm_provider=MockLLMProvider(),
        )

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        assert "Base persona text." in runtime.spec.system_prompt
        assert "Avoid using this tool to run" in runtime.spec.system_prompt


# ── TestExpCleanup ───────────────────────────────────────


class TestExpCleanup:
    """Cleanup callbacks are guaranteed to execute via _run_cleanup_callbacks()."""

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
    """_init_builtin_tools CC-name registration: 9 builtin tools."""

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
        """9 native tools registered with source='builtin' (CC names)."""
        _, registry = self._build_registry(tmp_path)
        assert len(registry) == 9

    def test_native_tool_names(self, tmp_path: Path) -> None:
        """All 9 expected CC-name tools are present in registry."""
        _, registry = self._build_registry(tmp_path)
        expected_native = {
            'Bash',
            'Read',
            'Write',
            'Edit',
            'Glob',
            'Grep',
            'TodoWrite',
            'WebSearch',
            'WebFetch',
        }
        for name in expected_native:
            assert name in registry, f"Expected tool '{name}' not found in registry"

    def test_no_evo_adapted_tools(self, tmp_path: Path) -> None:
        """No evo adapter tools remain (EvoToolAdapter eliminated).
        All tools are native builtins; no 'builtin_evo' source exists."""
        _, registry = self._build_registry(tmp_path)
        for tool in registry.all_tools:
            assert 'evo' not in tool.name.lower(), f"Unexpected evo tool: {tool.name}"

    def test_editor_tool_removed(self, tmp_path: Path) -> None:
        """str_replace_editor (EditorTool) is NOT in the registry."""
        _, registry = self._build_registry(tmp_path)
        assert 'str_replace_editor' not in registry

    def test_total_count(self, tmp_path: Path) -> None:
        """Total tools = 9 native builtin (CC names, no legacy tools)."""
        _, registry = self._build_registry(tmp_path)
        assert len(registry) == 9

    def test_web_search_is_native_builtin(self, tmp_path: Path) -> None:
        """WebSearchTool is registered as native builtin with CC name."""
        _, registry = self._build_registry(tmp_path)
        assert 'WebSearch' in registry

    def test_init_builtin_tools_no_session(self, tmp_path: Path) -> None:
        """session=None registers only sessionless tools (TodoWrite, WebSearch, WebFetch)."""
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
        assert len(registry) == 3
        assert "TodoWrite" in registry
        assert "WebSearch" in registry
        assert "WebFetch" in registry

    async def test_explicit_builtin_config_filters_tools(self, tmp_path: Path) -> None:
        """Non-empty explicit tool list registers only the requested tools."""
        exp = Exp(
            ExpConfig(
                name='test',
                tools=ExpToolsConfig(builtin=['Bash', 'Read']),
            )
        )
        ctx = self._make_ctx_with_session(tmp_path)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        registered_names = {t.name for t in runtime.spec.tool_catalog.registry.all_tools}
        assert registered_names == {'Bash', 'Read'}

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

        assert len(runtime.spec.tool_catalog.registry) == 0


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
            'Bash',
            'Read',
            'Write',
            'Edit',
            'Glob',
            'Grep',
        ):
            assert by_name[name]._workdir == execution, name

    async def test_agent_tool_uses_execution_workdir(self, tmp_path: Path) -> None:
        from matmaster.tools.builtin.agent_tool import AgentTool

        control = tmp_path / 'control'
        execution = tmp_path / 'execution'
        control.mkdir()
        execution.mkdir()
        ctx = self._ctx(tmp_path, control=control, execution=execution)
        exp = Exp(ExpConfig(name="test", tools=ExpToolsConfig(builtin=["Agent"])))
        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)
        agents = [
            t for t in runtime.spec.tool_catalog.registry.all_tools if isinstance(t, AgentTool)
        ]
        assert len(agents) == 1
        assert agents[0]._workdir == execution


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


# ── TestSessionlessBuiltins ────────────────────────────


@pytest.mark.asyncio
async def test_build_runtime_registers_todowrite_without_session(tmp_path: Path) -> None:
    """TodoWrite (sessionless tool) registers even when ctx.session is None."""
    exp = Exp(
        ExpConfig(
            name="test",
            tools=ExpToolsConfig(builtin=["TodoWrite"]),
        )
    )
    ctx = PlaygroundContext(
        workdir=tmp_path,
        execution_workdir=str(tmp_path / "exec"),
        session_type="local",
        cache_area=tmp_path / "cache",
        session=None,
        llm_provider=MockLLMProvider(),
    )

    with patch("matmaster.core.agent.AgentKernel"):
        runtime = await exp.build_runtime(ctx)

    assert runtime.spec.tool_catalog.get_tool("TodoWrite") is not None


# ── TestAgentRegistration ──────────────────────────────


@pytest.mark.asyncio
async def test_build_runtime_registers_agent_by_cc_name(tmp_path: Path) -> None:
    """Agent registers with CC name when enabled in builtin config."""
    exp = Exp(
        ExpConfig(
            name="test",
            tools=ExpToolsConfig(builtin=["Agent"]),
        )
    )
    ctx = PlaygroundContext(
        workdir=tmp_path,
        execution_workdir=str(tmp_path / "exec"),
        session_type="local",
        cache_area=tmp_path / "cache",
        session=MagicMock(spec=Session),
        llm_provider=MockLLMProvider(),
    )

    with patch("matmaster.core.agent.AgentKernel"):
        runtime = await exp.build_runtime(ctx)

    assert runtime.spec.tool_catalog.get_tool("Agent") is not None


# ── TestSpawnGuard ─────────────────────────────────────


@pytest.mark.asyncio
async def test_build_runtime_hides_agent_when_allow_spawn_false(tmp_path: Path) -> None:
    """Agent tool is hidden (exposed_to_model=False) when allow_spawn=False."""
    exp = Exp(
        ExpConfig(
            name="test",
            tools=ExpToolsConfig(builtin=["Agent"]),
        ),
        allow_spawn=False,
    )
    ctx = PlaygroundContext(
        workdir=tmp_path,
        execution_workdir=str(tmp_path / "exec"),
        session_type="local",
        cache_area=tmp_path / "cache",
        session=MagicMock(spec=Session),
        llm_provider=MockLLMProvider(),
    )

    with patch("matmaster.core.agent.AgentKernel"):
        runtime = await exp.build_runtime(ctx)

    tool = runtime.spec.tool_catalog.get_tool("Agent")
    assert tool is not None
    assert tool.tool_spec.exposed_to_model is False
