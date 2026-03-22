"""Tests for DirectExp -- direct execution mode assembly."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

from matmaster.core.direct_exp import DirectExp
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.core.bus import MessageBus
from matmaster.core.hooks import EventEmitterHook
from matmaster.types.context import PlaygroundContext
from matmaster.types.runtime import AgentRuntimeSpec


class MockLLMProvider:
    """Mock LLM provider satisfying the LLMProvider Protocol."""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        return None

    def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> Any:
        return self.chat(messages, tools)

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[Any]:
        yield None


class MockSession:
    """Minimal session mock for builtin tool tests."""

    def exec_bash(self, cmd: str) -> str:
        return ""


def _make_ctx(*, session: Any = None) -> PlaygroundContext:
    kwargs: dict[str, Any] = {
        "workdir": Path("/tmp/test"),
        "session_type": "local",
        "cache_area": Path("/tmp/cache"),
    }
    if session is not None:
        kwargs["session"] = session
    return PlaygroundContext(**kwargs)


class TestDirectExpAssemble:
    def test_assemble_returns_spec(self) -> None:
        """DirectExp.assemble(ctx) returns AgentRuntimeSpec instance."""
        exp = DirectExp(llm_provider=MockLLMProvider())
        spec = exp.assemble(_make_ctx())
        assert isinstance(spec, AgentRuntimeSpec)

    def test_assemble_spec_has_llm_provider(self) -> None:
        """Returned spec.llm_provider is the provider passed to DirectExp constructor."""
        provider = MockLLMProvider()
        exp = DirectExp(llm_provider=provider)
        spec = exp.assemble(_make_ctx())
        assert spec.llm_provider is provider

    def test_assemble_spec_has_tool_registry(self) -> None:
        """Returned spec has a ToolRegistry (builtin tools from ctx.session)."""
        exp = DirectExp(llm_provider=MockLLMProvider())
        spec = exp.assemble(_make_ctx())
        assert spec.tool_registry is not None
        assert isinstance(spec.tool_registry, ToolRegistry)

    def test_assemble_spec_has_guards(self) -> None:
        """Returned spec.guards contains guards passed to constructor."""
        from matmaster.types.guards import GuardContext, GuardResult

        class StubGuard:
            def evaluate(self, ctx: GuardContext) -> GuardResult:
                return GuardResult(allowed=True)

        guard = StubGuard()
        exp = DirectExp(llm_provider=MockLLMProvider(), guards=[guard])
        spec = exp.assemble(_make_ctx())
        assert guard in spec.guards

    def test_assemble_spec_has_system_prompt(self) -> None:
        """Returned spec.system_prompt is non-empty string."""
        exp = DirectExp(llm_provider=MockLLMProvider())
        spec = exp.assemble(_make_ctx())
        assert isinstance(spec.system_prompt, str)
        assert len(spec.system_prompt) > 0

    def test_assemble_spec_has_hooks(self) -> None:
        """Returned spec.hooks contains at least one hook (EventEmitterHook)."""
        exp = DirectExp(llm_provider=MockLLMProvider())
        spec = exp.assemble(_make_ctx())
        assert len(spec.hooks) >= 1
        assert any(isinstance(h, EventEmitterHook) for h in spec.hooks)

    def test_assemble_spec_mode_is_direct(self) -> None:
        """Returned spec.mode == 'direct'."""
        exp = DirectExp(llm_provider=MockLLMProvider())
        spec = exp.assemble(_make_ctx())
        assert spec.mode == "direct"

    def test_assemble_max_turns(self) -> None:
        """DirectExp(max_turns=50), assemble() returns spec with max_turns=50."""
        exp = DirectExp(llm_provider=MockLLMProvider(), max_turns=50)
        spec = exp.assemble(_make_ctx())
        assert spec.max_turns == 50

    def test_assemble_repeatable(self) -> None:
        """Call assemble() twice, both return valid specs, they are different objects."""
        exp = DirectExp(llm_provider=MockLLMProvider())
        ctx = _make_ctx()
        spec1 = exp.assemble(ctx)
        spec2 = exp.assemble(ctx)
        assert isinstance(spec1, AgentRuntimeSpec)
        assert isinstance(spec2, AgentRuntimeSpec)
        assert spec1 is not spec2

    def test_assemble_with_message_bus(self) -> None:
        """DirectExp accepts optional bus parameter, assemble uses provided bus."""
        bus = MessageBus()
        exp = DirectExp(llm_provider=MockLLMProvider(), bus=bus)
        spec = exp.assemble(_make_ctx())
        # Verify the EventEmitterHook was created with the provided bus
        emitter = [h for h in spec.hooks if isinstance(h, EventEmitterHook)]
        assert len(emitter) == 1
        assert emitter[0]._bus is bus


class TestDirectExpCapabilityOwnership:
    """Tests for Exp-owned capability initialization (skill, MCP)."""

    def test_assemble_without_capabilities_still_valid(self) -> None:
        """DirectExp with no skill/mcp config still produces valid spec."""
        exp = DirectExp(llm_provider=MockLLMProvider())
        spec = exp.assemble(_make_ctx())
        assert isinstance(spec, AgentRuntimeSpec)
        assert spec.mode == "direct"

    def test_skill_tool_registered_via_factory(self) -> None:
        """When skill_registry_factory is provided, SkillTool gets wrapped and registered."""
        from unittest.mock import MagicMock

        fake_registry = MagicMock()
        fake_registry.get_meta_info_context.return_value = "skill info"

        exp = DirectExp(
            llm_provider=MockLLMProvider(),
            skill_config={"enabled": True},
            skill_registry_factory=lambda: fake_registry,
        )
        ctx = _make_ctx(session=MagicMock())
        spec = exp.assemble(ctx)
        skill_tools = spec.tool_registry.get_tools_by_source("skill")
        assert len(skill_tools) == 1
        assert skill_tools[0].name == "use_skill"

    def test_mcp_tool_registered_via_factory(self) -> None:
        """When mcp_manager_factory is provided, MCP tools get wrapped and registered."""
        from unittest.mock import MagicMock

        from evomaster.agent.tools.base import BaseTool, BaseToolParams
        from pydantic import Field
        from typing import ClassVar

        class _FakeMCPParams(BaseToolParams):
            """Fake MCP tool."""
            name: ClassVar[str] = "mcp_search"
            q: str = Field(description="query")

        class _FakeMCPTool(BaseTool):
            name: ClassVar[str] = "mcp_search"
            params_class: ClassVar[type[BaseToolParams]] = _FakeMCPParams
            def execute(self, session, args_json):
                return "ok", {}

        fake_manager = MagicMock()
        fake_evo_registry = MagicMock()
        fake_evo_registry.get_all_tools.return_value = [_FakeMCPTool()]
        fake_manager.get_tool_registry.return_value = fake_evo_registry

        exp = DirectExp(
            llm_provider=MockLLMProvider(),
            mcp_config={"enabled": True},
            mcp_manager_factory=lambda ctx: fake_manager,
        )
        ctx = _make_ctx(session=MagicMock())
        spec = exp.assemble(ctx)
        mcp_tools = spec.tool_registry.get_tools_by_source("mcp")
        assert len(mcp_tools) == 1
        assert mcp_tools[0].name == "mcp_search"

    def test_no_ctx_skill_registry_access(self) -> None:
        """DirectExp.assemble must not access ctx.skill_registry (field removed)."""
        ctx = _make_ctx()
        assert not hasattr(ctx, "skill_registry")
        exp = DirectExp(llm_provider=MockLLMProvider())
        # Should not raise AttributeError
        spec = exp.assemble(ctx)
        assert isinstance(spec, AgentRuntimeSpec)

    def test_mcp_cleanup_registered(self) -> None:
        """When MCP manager is created, a cleanup callback is registered on the Exp."""
        from unittest.mock import MagicMock

        fake_manager = MagicMock()
        fake_evo_registry = MagicMock()
        fake_evo_registry.get_all_tools.return_value = []
        fake_manager.get_tool_registry.return_value = fake_evo_registry

        exp = DirectExp(
            llm_provider=MockLLMProvider(),
            mcp_config={"enabled": True},
            mcp_manager_factory=lambda ctx: fake_manager,
        )
        ctx = _make_ctx(session=MagicMock())
        exp.assemble(ctx)
        assert len(exp._cleanup_callbacks) >= 1


class TestDirectExpBuiltinTools:
    """Tests for builtin tool construction in assemble() from ctx.session."""

    def test_builtin_tools_registered_from_ctx_session(self) -> None:
        """DirectExp.assemble(ctx) with ctx.session set registers 3 builtin tools."""
        from unittest.mock import MagicMock, patch

        # Mock the EvoMaster tool classes at their source modules
        mock_bash_inst = MagicMock()
        mock_bash_inst.name = "bash"
        mock_bash_inst.params_class.__doc__ = "Run bash commands"
        mock_bash_inst.params_class.model_json_schema.return_value = {"type": "object"}

        mock_editor_inst = MagicMock()
        mock_editor_inst.name = "editor"
        mock_editor_inst.params_class.__doc__ = "Edit files"
        mock_editor_inst.params_class.model_json_schema.return_value = {"type": "object"}

        mock_monitor_inst = MagicMock()
        mock_monitor_inst.name = "monitor_job"
        mock_monitor_inst.params_class.__doc__ = "Monitor jobs"
        mock_monitor_inst.params_class.model_json_schema.return_value = {"type": "object"}

        with (
            patch(
                "evomaster.agent.tools.builtin.bash.BashTool",
                return_value=mock_bash_inst,
            ),
            patch(
                "evomaster.agent.tools.builtin.editor.EditorTool",
                return_value=mock_editor_inst,
            ),
            patch(
                "evomaster.agent.tools.builtin.monitor_job.MonitorJobTool",
                return_value=mock_monitor_inst,
            ),
        ):
            exp = DirectExp(llm_provider=MockLLMProvider())
            ctx = _make_ctx(session=MockSession())
            spec = exp.assemble(ctx)

        builtin_tools = spec.tool_registry.get_tools_by_source("builtin")
        assert len(builtin_tools) == 3

    def test_builtin_tools_skipped_when_no_session(self) -> None:
        """DirectExp.assemble(ctx) with ctx.session=None skips builtin registration."""
        exp = DirectExp(llm_provider=MockLLMProvider())
        ctx = _make_ctx()  # session=None by default
        spec = exp.assemble(ctx)
        builtin_tools = spec.tool_registry.get_tools_by_source("builtin")
        assert len(builtin_tools) == 0

    def test_constructor_rejects_old_params(self) -> None:
        """DirectExp constructor rejects session, config_dir, builtin_tools parameters."""
        with pytest.raises(TypeError):
            DirectExp(llm_provider=MockLLMProvider(), session=object())

        with pytest.raises(TypeError):
            DirectExp(llm_provider=MockLLMProvider(), builtin_tools=[])

        with pytest.raises(TypeError):
            DirectExp(llm_provider=MockLLMProvider(), config_dir="/tmp")

    def test_guards_shells_module_removed(self) -> None:
        """matmaster.assembly.guards module no longer exists (deleted in Phase 7)."""
        import importlib

        with pytest.raises((ImportError, ModuleNotFoundError)):
            importlib.import_module("matmaster.assembly.guards")
