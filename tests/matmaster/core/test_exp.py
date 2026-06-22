"""Tests for Exp concrete config-driven class."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matmaster.config.exp import ExpConfig, ExpSubagentMeta, ExpToolsConfig
from matmaster.config.loader import load_exp_config
from matmaster.core.exp import Exp
from matmaster.core.hooks import HookExecutor
from matmaster.core.playground import ExecutionEnvironment
from matmaster.core.run_context import AgentRunContext, AgentRunRequest
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.types.runtime import AgentRuntime
from matmaster.types.session import Session
from matmaster.types.tool_desc_ctx import ToolDescriptionContext
from matmaster.types.tool_runner_state import ToolRunnerState
from tests.matmaster.core.conftest import MockLLMProvider

_UNSET = object()


def _make_ctx(
    *,
    with_llm: bool = False,
    workdir: Path | None = None,
    execution_workdir: str | None = None,
    session: object = _UNSET,
    interaction_bridge: object = None,
) -> AgentRunContext:
    """Create an AgentRunContext for testing (physical env + runtime request).

    Routes the common test fixtures through one builder so the
    environment/request split stays consistent. ``workdir`` defaults to
    ``/tmp/test`` (cache under ``/tmp/cache``); when given, cache lives under it.
    ``session`` is omitted unless passed (so ``session=None`` is distinct from
    not passing it).
    """
    wd = Path("/tmp/test") if workdir is None else workdir
    cache_area = Path("/tmp/cache") if workdir is None else wd / "cache"
    env_kwargs: dict = {
        "workdir": wd,
        "session_type": "local",
        "cache_area": cache_area,
    }
    if execution_workdir is not None:
        env_kwargs["execution_workdir"] = execution_workdir
    if session is not _UNSET:
        env_kwargs["session"] = session
    request_kwargs: dict = {}
    if with_llm:
        request_kwargs["llm_provider"] = MockLLMProvider()
    if interaction_bridge is not None:
        request_kwargs["interaction_bridge"] = interaction_bridge
    return AgentRunContext(
        environment=ExecutionEnvironment(**env_kwargs),
        request=AgentRunRequest(**request_kwargs),
    )


class _Bridge:
    async def ask(self, **kwargs):
        return {"request_id": kwargs["request_id"], "answers": {}, "annotations": {}}


def _tool_names(runtime) -> set[str]:
    return {
        item["function"]["name"]
        for item in runtime.kernel_runtime.resources.tool_catalog.build_definitions()
    }


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


# ── TestExpBuildRuntimeConfig ────────────────────────────


class TestExpBuildRuntimeConfig:
    """build_runtime() maps config + ctx into the kernel runtime spec.

    Migrated from the old assemble() data-transform tests: assemble() is
    deleted, so these assert the same config values on the kernel runtime
    produced by build_runtime() instead.
    """

    async def test_max_turns_from_config(self) -> None:
        """max_turns in config propagates to the kernel spec."""
        exp = Exp(ExpConfig(name="test", max_turns=50))
        ctx = _make_ctx(with_llm=True)
        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)
        assert runtime.kernel_runtime.spec.max_turns == 50

    async def test_max_turns_default(self) -> None:
        """Default max_turns is 100 when not in config."""
        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx(with_llm=True)
        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)
        assert runtime.kernel_runtime.spec.max_turns == 100

    async def test_runtime_identity_defaults_empty(self) -> None:
        """Runtime identity is explicit and empty for a top-level run."""
        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx(with_llm=True)
        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)
        spec = runtime.kernel_runtime.spec
        assert spec.run_identity.task_id == ""
        assert spec.run_identity.session_id == ""
        assert spec.run_identity.spawn_id is None

    async def test_llm_provider_from_ctx(self) -> None:
        """llm_provider comes from ctx, not config."""
        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx(with_llm=True)
        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)
        assert runtime.kernel_runtime.resources.llm_provider is ctx.request.llm_provider


# ── TestExpBuildRuntime ──────────────────────────────────


class TestExpBuildRuntime:
    """build_runtime() creates resources and returns AgentRuntime."""

    async def test_returns_agent_runtime(self) -> None:
        """build_runtime() returns an AgentRuntime dataclass."""
        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        assert isinstance(runtime, AgentRuntime)

    async def test_uses_ctx_llm_provider(self) -> None:
        """Runtime spec uses LLM provider from ctx."""
        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        assert runtime.kernel_runtime.resources.llm_provider is ctx.request.llm_provider

    async def test_build_runtime_has_no_bus_parameter(self) -> None:
        """build_runtime() does not accept a bus parameter."""
        exp = Exp(ExpConfig(name="test"))
        _make_ctx(with_llm=True)

        import inspect

        sig = inspect.signature(exp.build_runtime)
        assert "bus" not in sig.parameters

    async def test_build_runtime_creates_hook_executor(self) -> None:
        """build_runtime() always injects a HookExecutor."""
        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        assert isinstance(runtime.kernel_runtime.resources.hook_executor, HookExecutor)

    async def test_runtime_has_cleanup_callable(self) -> None:
        """AgentRuntime.cleanup is a callable."""
        exp = Exp(ExpConfig(name="test"))
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
        ctx = _make_ctx(
            workdir=tmp_path,
            execution_workdir=str(tmp_path / "exec"),
            session=MagicMock(spec=Session),
            with_llm=True,
        )

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        assert exp._cleanup_callbacks
        state = runtime.kernel_runtime.resources.tool_runner.state
        assert isinstance(state, ToolRunnerState)

        matching_callbacks = [
            cb
            for cb in exp._cleanup_callbacks
            if getattr(cb, "__self__", None) is state
        ]
        assert matching_callbacks

    async def test_bash_prompt_moves_to_function_description(
        self, tmp_path: Path
    ) -> None:
        exp = Exp(
            ExpConfig(
                name="test",
                system_prompt="Base persona text.",
                tools=ExpToolsConfig(builtin=["Bash"]),
            )
        )
        ctx = _make_ctx(
            workdir=tmp_path,
            execution_workdir=str(tmp_path / "exec"),
            session=MagicMock(spec=Session),
            with_llm=True,
        )

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        spec = runtime.kernel_runtime.spec
        resources = runtime.kernel_runtime.resources
        assert "Base persona text." in spec.system_prompt
        assert "# Tools" in spec.system_prompt
        assert "Use the tools declared in function calling." in (spec.system_prompt)
        assert "Use dedicated tools instead of shell equivalents" not in (
            spec.system_prompt
        )
        desc_ctx = ToolDescriptionContext(
            session_kind=resources.runtime_topology.session_kind,
            workspace_root=resources.runtime_topology.workspace_root,
            topology=resources.runtime_topology,
        )
        defs = resources.tool_catalog.build_definitions(desc_ctx)
        bash_def = next(d for d in defs if d["function"]["name"] == "Bash")
        assert "/exec" in bash_def["function"]["description"]

    async def test_builtin_tool_prompts_layered_correctly(self, tmp_path: Path) -> None:
        """End-to-end: builtin prompts leave system_prompt and move into definitions."""
        exp = Exp(
            ExpConfig(
                name="test",
                system_prompt="Base.",
                tools=ExpToolsConfig(
                    builtin=["Bash", "Glob", "Grep", "Read", "Write", "Edit"]
                ),
            )
        )
        ctx = _make_ctx(
            workdir=tmp_path,
            execution_workdir=str(tmp_path / "exec"),
            session=MagicMock(spec=Session),
            with_llm=True,
        )

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        resources = runtime.kernel_runtime.resources
        sys_prompt = runtime.kernel_runtime.spec.system_prompt

        assert "Base." in sys_prompt
        assert "# Tools" in sys_prompt
        assert "Use the tools declared in function calling." in sys_prompt
        assert "Use dedicated tools instead of shell equivalents" not in sys_prompt
        assert "pattern matching" not in sys_prompt.lower()
        assert "ripgrep" not in sys_prompt.lower()

        desc_ctx = ToolDescriptionContext(
            session_kind=resources.runtime_topology.session_kind,
            workspace_root=resources.runtime_topology.workspace_root,
            topology=resources.runtime_topology,
        )
        defs = resources.tool_catalog.build_definitions(desc_ctx)
        by_name = {d["function"]["name"]: d["function"]["description"] for d in defs}

        assert "Use dedicated tools instead of shell equivalents" in by_name["Bash"]
        assert "Fast file pattern matching tool" in by_name["Glob"]
        assert "ALWAYS use Grep for search tasks" in by_name["Grep"]
        assert by_name["Read"].startswith("Use absolute paths.")
        assert by_name["Edit"].startswith("Read the file first.")

    async def test_agent_tool_uses_model_visible_exp_discovery(
        self, tmp_path: Path
    ) -> None:
        exp = Exp(
            ExpConfig(
                name="test",
                tools=ExpToolsConfig(builtin=["Agent"]),
            )
        )
        ctx = _make_ctx(
            workdir=tmp_path,
            execution_workdir=str(tmp_path / "exec"),
            session=MagicMock(spec=Session),
            with_llm=True,
        )
        meta = ExpSubagentMeta.model_validate(
            {
                "name": "explore",
                "description": "Read-only exploration subagent",
                "when_to_use": "Use for evidence gathering",
                "read_only": True,
                "tools_summary": "Builtin: Read; MCP: none; Skills: disabled",
            }
        )

        with (
            patch("matmaster.core.agent.AgentKernel"),
            patch(
                "matmaster.config.loader.list_model_visible_exps",
                return_value=[meta],
            ),
        ):
            runtime = await exp.build_runtime(ctx)

        raw_tool = runtime.kernel_runtime.resources.tool_catalog.registry.get_raw(
            "Agent"
        )
        assert raw_tool is not None
        assert raw_tool._available_exps == (meta,)


# ── TestExpCleanup ───────────────────────────────────────


class TestExpCleanup:
    """Cleanup callbacks are guaranteed to execute via _run_cleanup_callbacks()."""

    async def test_multiple_cleanups_all_execute(self) -> None:
        """All registered cleanup callbacks run even if one raises."""
        exp = Exp(ExpConfig(name="test"))
        cb1 = MagicMock(side_effect=ValueError("cb1 broken"))
        cb2 = MagicMock()
        exp._register_cleanup(cb1)
        exp._register_cleanup(cb2)

        await exp._run_cleanup_callbacks()

        cb1.assert_called_once()
        cb2.assert_called_once()

    async def test_cleanup_clears_list(self) -> None:
        """_run_cleanup_callbacks clears the list after execution."""
        exp = Exp(ExpConfig(name="test"))
        cb = MagicMock()
        exp._register_cleanup(cb)

        await exp._run_cleanup_callbacks()

        assert exp._cleanup_callbacks == []


# ── TestRuntimeScope ─────────────────────────────────────


class TestRuntimeScope:
    """runtime_scope() owns the reusable build -> inject -> cleanup lifecycle."""

    async def test_yields_built_runtime(self, tmp_path: Path) -> None:
        exp = Exp(ExpConfig(name="test"))
        runtime = MagicMock()
        runtime.kernel_runtime = MagicMock(resources=MagicMock(tool_catalog=None))
        ctx = _make_ctx(with_llm=True)
        with patch.object(exp, "build_runtime", AsyncMock(return_value=runtime)):
            async with exp.runtime_scope(ctx) as scoped:
                assert scoped is runtime

    async def test_cleanup_runs_on_exception(self, tmp_path: Path) -> None:
        exp = Exp(ExpConfig(name="test"))
        cb = MagicMock()
        exp._register_cleanup(cb)
        runtime = MagicMock()
        runtime.kernel_runtime = MagicMock(resources=MagicMock(tool_catalog=None))
        ctx = _make_ctx(with_llm=True)
        with patch.object(exp, "build_runtime", AsyncMock(return_value=runtime)):
            with pytest.raises(ValueError, match="boom"):
                async with exp.runtime_scope(ctx):
                    raise ValueError("boom")
        cb.assert_called_once()

    async def test_injects_cancel_token_into_session_and_catalog(
        self, tmp_path: Path
    ) -> None:
        exp = Exp(ExpConfig(name="test"))
        session = MagicMock(spec=Session)
        catalog = MagicMock()
        runtime = MagicMock()
        runtime.kernel_runtime = MagicMock(resources=MagicMock(tool_catalog=catalog))
        ctx = _make_ctx(workdir=tmp_path, session=session, with_llm=True)
        token = MagicMock()
        with patch.object(exp, "build_runtime", AsyncMock(return_value=runtime)):
            async with exp.runtime_scope(ctx, token):
                pass
        assert session._cancel_token is token
        catalog.inject_cancel_token.assert_called_once_with(token)


# ── TestIdentityOverride ────────────────────────────────


class TestIdentityOverride:
    """Identity from config is forwarded to SystemPromptBuilder.build_system_prompt()."""

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

        assert (
            "I am a materials scientist." in runtime.kernel_runtime.spec.system_prompt
        )

    async def test_default_identity_when_not_set(self) -> None:
        """Empty developer_instructions means no identity section in prompt."""
        exp = Exp(
            ExpConfig(
                name="test",
                tools=ExpToolsConfig(builtin=[]),
            )
        )
        ctx = _make_ctx(with_llm=True)
        runtime = await exp.build_runtime(ctx)

        assert "# Identity" not in runtime.kernel_runtime.spec.system_prompt


class TestSystemPromptOverride:
    """system_prompt from config is forwarded to SystemPromptBuilder.build_system_prompt()."""

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

        assert "Base persona text." in runtime.kernel_runtime.spec.system_prompt

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

        assert "# System" not in runtime.kernel_runtime.spec.system_prompt


# ── TestExpBuiltinTools ─────────────────────────────────


class TestExpBuiltinTools:
    """_init_builtin_tools CC-name registration: native builtin tools."""

    def _make_ctx_with_session(self, tmp_path: Path) -> AgentRunContext:
        """Create AgentRunContext with a mock session for builtin tool tests."""
        return AgentRunContext(
            environment=ExecutionEnvironment(
                workdir=tmp_path,
                session_type="local",
                cache_area=tmp_path / "cache",
                session=MagicMock(spec=Session),
            ),
            request=AgentRunRequest(llm_provider=MockLLMProvider()),
        )

    def _build_registry(self, tmp_path: Path) -> tuple[Exp, ToolRegistry]:
        """Build an Exp and run _init_builtin_tools, returning (exp, registry)."""
        exp = Exp(ExpConfig(name="test"))
        ctx = self._make_ctx_with_session(tmp_path)
        registry = ToolRegistry()
        exp._init_builtin_tools(ctx, registry, ["*"])
        return exp, registry

    def test_native_tools_count(self, tmp_path: Path) -> None:
        """12 native tools registered with source='builtin' (CC names)."""
        _, registry = self._build_registry(tmp_path)
        assert len(registry) == 12

    def test_native_tool_names(self, tmp_path: Path) -> None:
        """All 12 expected CC-name tools are present in registry."""
        _, registry = self._build_registry(tmp_path)
        expected_native = {
            "AskQuestion",
            "Bash",
            "AttachFigure",
            "Read",
            "Write",
            "Edit",
            "Glob",
            "Grep",
            "TodoWrite",
            "WebSearch",
            "WebFetch",
            "Bohrium",
        }
        for name in expected_native:
            assert name in registry, f"Expected tool '{name}' not found in registry"

    def test_no_evo_adapted_tools(self, tmp_path: Path) -> None:
        """No evo adapter tools remain (EvoToolAdapter eliminated).
        All tools are native builtins; no 'builtin_evo' source exists."""
        _, registry = self._build_registry(tmp_path)
        for tool in registry.all_tools:
            assert "evo" not in tool.name.lower(), f"Unexpected evo tool: {tool.name}"

    def test_editor_tool_removed(self, tmp_path: Path) -> None:
        """str_replace_editor (EditorTool) is NOT in the registry."""
        _, registry = self._build_registry(tmp_path)
        assert "str_replace_editor" not in registry

    def test_total_count(self, tmp_path: Path) -> None:
        """Total tools = 12 native builtin (CC names, no legacy tools)."""
        _, registry = self._build_registry(tmp_path)
        assert len(registry) == 12

    def test_direct_and_planner_configs_include_attach_figure(self) -> None:
        assert "AttachFigure" in load_exp_config("direct").tools.builtin
        assert "AttachFigure" in load_exp_config("planner").tools.builtin

    def test_web_search_is_native_builtin(self, tmp_path: Path) -> None:
        """WebSearchTool is registered as native builtin with CC name."""
        _, registry = self._build_registry(tmp_path)
        assert "WebSearch" in registry

    def test_init_builtin_tools_no_session(self, tmp_path: Path) -> None:
        """session=None registers only sessionless tools."""
        from matmaster.tools.tool_registry import ToolRegistry

        exp = Exp(ExpConfig(name="test"))
        ctx = _make_ctx(workdir=tmp_path, session=None, with_llm=True)
        registry = ToolRegistry()
        exp._init_builtin_tools(ctx, registry, ["*"])
        assert len(registry) == 5
        assert "AskQuestion" in registry
        assert "TodoWrite" in registry
        assert "WebSearch" in registry
        assert "WebFetch" in registry
        assert "Bohrium" in registry

    async def test_explicit_builtin_config_filters_tools(self, tmp_path: Path) -> None:
        """Non-empty explicit tool list registers only the requested tools."""
        exp = Exp(
            ExpConfig(
                name="test",
                tools=ExpToolsConfig(builtin=["Bash", "Read"]),
            )
        )
        ctx = self._make_ctx_with_session(tmp_path)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        registered_names = {
            t.name
            for t in runtime.kernel_runtime.resources.tool_catalog.registry.all_tools
        }
        assert registered_names == {"Bash", "Read"}

    async def test_empty_builtin_config_skips_init(self, tmp_path: Path) -> None:
        """Empty builtin list skips _init_builtin_tools entirely."""
        exp = Exp(
            ExpConfig(
                name="test",
                tools=ExpToolsConfig(builtin=[]),
            )
        )
        ctx = self._make_ctx_with_session(tmp_path)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        assert len(runtime.kernel_runtime.resources.tool_catalog.registry) == 0


# ── TestExecutionWorkdirBinding ─────────────────────────


class TestExecutionWorkdirBinding:
    """Builtin tools: execution plane vs control-plane (task) workdirs."""

    @staticmethod
    def _ctx(
        tmp_path: Path,
        *,
        control: Path,
        execution: Path,
    ) -> AgentRunContext:
        return AgentRunContext(
            environment=ExecutionEnvironment(
                workdir=control,
                execution_workdir=str(execution),
                session_type="local",
                cache_area=tmp_path / "cache",
                session=MagicMock(spec=Session),
            ),
            request=AgentRunRequest(llm_provider=MockLLMProvider()),
        )

    def test_execution_side_tools_use_execution_workdir(self, tmp_path: Path) -> None:
        from matmaster.tools.tool_registry import ToolRegistry

        control = tmp_path / "control"
        execution = tmp_path / "execution"
        control.mkdir()
        execution.mkdir()
        ctx = self._ctx(tmp_path, control=control, execution=execution)
        exp = Exp(ExpConfig(name="test"))
        registry = ToolRegistry()
        exp._init_builtin_tools(ctx, registry, ["*"])
        by_name = {t.name: t for t in registry.all_tools}
        for name in (
            "Bash",
            "Read",
            "Write",
            "Edit",
            "Glob",
            "Grep",
        ):
            assert by_name[name]._workdir == execution, name

    async def test_agent_tool_uses_execution_workdir(self, tmp_path: Path) -> None:
        from matmaster.tools.builtin.agent_tool import AgentTool

        control = tmp_path / "control"
        execution = tmp_path / "execution"
        control.mkdir()
        execution.mkdir()
        ctx = self._ctx(tmp_path, control=control, execution=execution)
        exp = Exp(ExpConfig(name="test", tools=ExpToolsConfig(builtin=["Agent"])))
        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)
        agents = [
            t
            for t in runtime.kernel_runtime.resources.tool_catalog.registry.all_tools
            if isinstance(t, AgentTool)
        ]
        assert len(agents) == 1
        assert agents[0]._workdir == execution


class TestExpCompaction:
    async def test_build_runtime_compaction_defaults_present(self) -> None:
        from matmaster.types.runtime import CompactionConfig

        exp = Exp(ExpConfig(name="test", tools=ExpToolsConfig(builtin=[])))
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        compaction = runtime.kernel_runtime.spec.compaction
        assert isinstance(compaction, CompactionConfig)
        assert "enabled" not in type(compaction).model_fields

    async def test_build_runtime_default_compaction(self) -> None:
        exp = Exp(ExpConfig(name="test", tools=ExpToolsConfig(builtin=[])))
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        assert runtime.kernel_runtime.spec.compaction.strategy == "summary"

    async def test_build_runtime_creates_compactor_when_llm_exists(self) -> None:
        exp = Exp(ExpConfig(name="test", tools=ExpToolsConfig(builtin=[])))
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        compactor = runtime.kernel_runtime.resources.compactor
        assert compactor is not None
        removed_attr = "_summary" + "_provider"
        assert not hasattr(compactor, removed_attr)

    async def test_build_runtime_uses_request_context_limit_for_compaction(
        self,
    ) -> None:
        exp = Exp(ExpConfig(name="test", tools=ExpToolsConfig(builtin=[])))
        env = _make_ctx(with_llm=True).environment
        ctx = AgentRunContext(
            environment=env,
            request=AgentRunRequest(
                llm_provider=MockLLMProvider(),
                context_limit=1_000_000,
            ),
        )

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = await exp.build_runtime(ctx)

        spec_compaction = runtime.kernel_runtime.spec.compaction
        compactor = runtime.kernel_runtime.resources.compactor
        assert spec_compaction.context_limit == 1_000_000
        assert compactor is not None
        assert compactor._config.context_limit == 1_000_000


# ── TestSessionlessBuiltins ────────────────────────────


@pytest.mark.asyncio
async def test_build_runtime_registers_todowrite_without_session(
    tmp_path: Path,
) -> None:
    """TodoWrite (sessionless tool) registers even when ctx.session is None."""
    exp = Exp(
        ExpConfig(
            name="test",
            tools=ExpToolsConfig(builtin=["TodoWrite"]),
        )
    )
    ctx = _make_ctx(
        workdir=tmp_path,
        execution_workdir=str(tmp_path / "exec"),
        session=None,
        with_llm=True,
    )

    with patch("matmaster.core.agent.AgentKernel"):
        runtime = await exp.build_runtime(ctx)

    assert (
        runtime.kernel_runtime.resources.tool_catalog.get_tool("TodoWrite") is not None
    )


@pytest.mark.asyncio
async def test_build_runtime_registers_bohrium_without_session(tmp_path: Path) -> None:
    """Bohrium (sessionless tool) registers even when ctx.session is None."""
    exp = Exp(
        ExpConfig(
            name="test",
            tools=ExpToolsConfig(builtin=["Bohrium"]),
        )
    )
    ctx = _make_ctx(
        workdir=tmp_path,
        execution_workdir=str(tmp_path / "exec"),
        session=None,
        with_llm=True,
    )

    with patch("matmaster.core.agent.AgentKernel"):
        runtime = await exp.build_runtime(ctx)

    assert runtime.kernel_runtime.resources.tool_catalog.get_tool("Bohrium") is not None


@pytest.mark.asyncio
async def test_build_runtime_registers_ask_question_when_bridge_available(
    tmp_path: Path,
) -> None:
    exp = Exp(
        ExpConfig(
            name="test",
            tools=ExpToolsConfig(builtin=["AskQuestion"]),
        )
    )
    ctx = _make_ctx(
        workdir=tmp_path,
        execution_workdir=str(tmp_path / "exec"),
        with_llm=True,
        interaction_bridge=_Bridge(),
    )

    with patch("matmaster.core.agent.AgentKernel"):
        runtime = await exp.build_runtime(ctx)

    assert "AskQuestion" in _tool_names(runtime)


@pytest.mark.asyncio
async def test_build_runtime_hides_ask_question_when_bridge_missing(
    tmp_path: Path,
) -> None:
    exp = Exp(
        ExpConfig(
            name="test",
            tools=ExpToolsConfig(builtin=["AskQuestion"]),
        )
    )
    ctx = AgentRunContext(
        environment=ExecutionEnvironment(
            workdir=tmp_path,
            execution_workdir=str(tmp_path / "exec"),
            session_type="local",
            cache_area=tmp_path / "cache",
        ),
        request=AgentRunRequest(
            interaction_bridge=None,
            llm_provider=MockLLMProvider(),
        ),
    )

    with patch("matmaster.core.agent.AgentKernel"):
        runtime = await exp.build_runtime(ctx)

    assert (
        runtime.kernel_runtime.resources.tool_catalog.get_tool("AskQuestion")
        is not None
    )
    assert "AskQuestion" not in _tool_names(runtime)


@pytest.mark.asyncio
async def test_build_runtime_includes_ask_question_for_builtin_star(
    tmp_path: Path,
) -> None:
    exp = Exp(
        ExpConfig(
            name="test",
            tools=ExpToolsConfig(builtin=["*"]),
        )
    )
    ctx = _make_ctx(
        workdir=tmp_path,
        execution_workdir=str(tmp_path / "exec"),
        with_llm=True,
        interaction_bridge=_Bridge(),
    )

    with patch("matmaster.core.agent.AgentKernel"):
        runtime = await exp.build_runtime(ctx)

    assert "AskQuestion" in _tool_names(runtime)


@pytest.mark.asyncio
async def test_child_runtime_hides_ask_question_even_when_bridge_exists(
    tmp_path: Path,
) -> None:
    exp = Exp(
        ExpConfig(
            name="test",
            tools=ExpToolsConfig(builtin=["AskQuestion"]),
        )
    )
    ctx = _make_ctx(
        workdir=tmp_path,
        execution_workdir=str(tmp_path / "exec"),
        with_llm=True,
        interaction_bridge=_Bridge(),
    )

    with patch("matmaster.core.agent.AgentKernel"):
        runtime = await exp.build_runtime(ctx, spawn_id="child-1")

    assert (
        runtime.kernel_runtime.resources.tool_catalog.get_tool("AskQuestion")
        is not None
    )
    assert "AskQuestion" not in _tool_names(runtime)


@pytest.mark.asyncio
async def test_bohrium_tool_receives_session(tmp_path: Path) -> None:
    """BohriumTool should be constructed with session=ctx.session."""
    from matmaster.tools.builtin.bohrium_tool import BohriumTool

    mock_session = MagicMock(spec=Session)
    exp = Exp(
        ExpConfig(
            name="test",
            tools=ExpToolsConfig(builtin=["Bohrium"]),
        )
    )
    ctx = AgentRunContext(
        environment=ExecutionEnvironment(
            workdir=tmp_path,
            execution_workdir=str(tmp_path / "exec"),
            session_type="local",
            cache_area=tmp_path / "cache",
            session=mock_session,
        ),
        request=AgentRunRequest(llm_provider=MockLLMProvider()),
    )

    with patch("matmaster.core.agent.AgentKernel"):
        runtime = await exp.build_runtime(ctx)

    bohrium_tools = [
        t
        for t in runtime.kernel_runtime.resources.tool_catalog.registry.all_tools
        if isinstance(t, BohriumTool)
    ]
    assert len(bohrium_tools) == 1
    assert bohrium_tools[0]._session is mock_session


@pytest.mark.asyncio
async def test_bohrium_tool_session_none_when_no_session(tmp_path: Path) -> None:
    """BohriumTool should have _session=None when ctx.session is None."""
    from matmaster.tools.builtin.bohrium_tool import BohriumTool

    exp = Exp(
        ExpConfig(
            name="test",
            tools=ExpToolsConfig(builtin=["Bohrium"]),
        )
    )
    ctx = _make_ctx(
        workdir=tmp_path,
        execution_workdir=str(tmp_path / "exec"),
        session=None,
        with_llm=True,
    )

    with patch("matmaster.core.agent.AgentKernel"):
        runtime = await exp.build_runtime(ctx)

    bohrium_tools = [
        t
        for t in runtime.kernel_runtime.resources.tool_catalog.registry.all_tools
        if isinstance(t, BohriumTool)
    ]
    assert len(bohrium_tools) == 1
    assert bohrium_tools[0]._session is None
