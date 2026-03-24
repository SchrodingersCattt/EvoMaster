"""Tests for Exp concrete config-driven class."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
    """Exp is a concrete class instantiated with a config dict."""

    def test_exp_is_concrete(self) -> None:
        """Exp can be instantiated directly (not abstract)."""
        exp = Exp({"name": "test"})
        assert isinstance(exp, Exp)

    def test_exp_name_from_config(self) -> None:
        """exp_name reads from config['name']."""
        exp = Exp({"name": "my-experiment"})
        assert exp.exp_name == "my-experiment"

    def test_exp_name_defaults_to_unnamed(self) -> None:
        """Missing name in config defaults to 'unnamed'."""
        exp = Exp({})
        assert exp.exp_name == "unnamed"

    def test_exp_empty_config(self) -> None:
        """Exp accepts empty config dict."""
        exp = Exp({})
        assert exp._config == {}


# ── TestExpAssemble ──────────────────────────────────────


class TestExpAssemble:
    """assemble() transforms config + ctx into AgentRuntimeSpec."""

    def test_returns_agent_runtime_spec(self) -> None:
        """assemble() returns an AgentRuntimeSpec instance."""
        exp = Exp({"name": "test"})
        ctx = _make_ctx()
        spec = exp.assemble(ctx)
        assert isinstance(spec, AgentRuntimeSpec)

    def test_max_turns_from_config(self) -> None:
        """max_turns in config propagates to spec."""
        exp = Exp({"name": "test", "max_turns": 50})
        ctx = _make_ctx()
        spec = exp.assemble(ctx)
        assert spec.max_turns == 50

    def test_max_turns_default(self) -> None:
        """Default max_turns is 100 when not in config."""
        exp = Exp({"name": "test"})
        ctx = _make_ctx()
        spec = exp.assemble(ctx)
        assert spec.max_turns == 100

    def test_guards_from_config(self) -> None:
        """guards in config propagate to spec."""
        mock_guard = MagicMock(spec=["evaluate"])
        mock_guard.evaluate = MagicMock()
        exp = Exp({"name": "test", "guards": [mock_guard]})
        ctx = _make_ctx()
        spec = exp.assemble(ctx)
        assert spec.guards == [mock_guard]

    def test_meta_stores_extra_config(self) -> None:
        """Meta bag captures prompt_template and MCP/skill config."""
        exp = Exp({
            "name": "test",
            "prompt_template": "custom.txt",
            "skills": {"enabled": True},
            "mcp": {"servers": ["s1"]},
        })
        ctx = _make_ctx()
        spec = exp.assemble(ctx)
        assert spec.meta.get("prompt_template") == "custom.txt"
        assert spec.meta.get("skills") == {"enabled": True}
        assert spec.meta.get("mcp") == {"servers": ["s1"]}

    def test_mode_from_config(self) -> None:
        """mode in config propagates to spec."""
        exp = Exp({"name": "test", "mode": "planner"})
        ctx = _make_ctx()
        spec = exp.assemble(ctx)
        assert spec.mode == "planner"

    def test_mode_default(self) -> None:
        """Default mode is 'direct'."""
        exp = Exp({"name": "test"})
        ctx = _make_ctx()
        spec = exp.assemble(ctx)
        assert spec.mode == "direct"

    def test_llm_provider_from_ctx(self) -> None:
        """llm_provider comes from ctx, not config."""
        exp = Exp({"name": "test"})
        ctx = _make_ctx(with_llm=True)
        spec = exp.assemble(ctx)
        assert spec.llm_provider is ctx.llm_provider


# ── TestExpBuildRuntime ──────────────────────────────────


class TestExpBuildRuntime:
    """build_runtime() creates resources and returns AgentRuntime."""

    def test_returns_agent_runtime(self) -> None:
        """build_runtime() returns an AgentRuntime dataclass."""
        exp = Exp({"name": "test"})
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = exp.build_runtime(ctx)

        assert isinstance(runtime, AgentRuntime)

    def test_uses_ctx_llm_provider(self) -> None:
        """Runtime spec uses LLM provider from ctx."""
        exp = Exp({"name": "test"})
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = exp.build_runtime(ctx)

        assert runtime.spec.llm_provider is ctx.llm_provider

    def test_bus_adds_event_emitter_hook(self) -> None:
        """When bus is provided, EventEmitterHook is added to spec.hooks."""
        from matmaster.core.bus import MessageBus
        from matmaster.core.hooks import EventEmitterHook

        exp = Exp({"name": "test"})
        ctx = _make_ctx(with_llm=True)
        bus = MessageBus()

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = exp.build_runtime(ctx, bus=bus)

        emitter_hooks = [h for h in runtime.spec.hooks if isinstance(h, EventEmitterHook)]
        assert len(emitter_hooks) == 1

    def test_no_bus_no_emitter(self) -> None:
        """Without bus, no EventEmitterHook in spec.hooks."""
        from matmaster.core.hooks import EventEmitterHook

        exp = Exp({"name": "test"})
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = exp.build_runtime(ctx)

        emitter_hooks = [h for h in runtime.spec.hooks if isinstance(h, EventEmitterHook)]
        assert len(emitter_hooks) == 0

    def test_runtime_has_cleanup_callable(self) -> None:
        """AgentRuntime.cleanup is a callable."""
        exp = Exp({"name": "test"})
        ctx = _make_ctx(with_llm=True)

        with patch("matmaster.core.agent.AgentKernel"):
            runtime = exp.build_runtime(ctx)

        assert callable(runtime.cleanup)


# ── TestExpRun ──────────────────────────────────────────


class TestExpRun:
    """run() calls build_runtime then kernel.run with proper args."""

    def test_run_calls_build_runtime_then_kernel(self) -> None:
        """run() delegates to build_runtime then kernel.run."""
        exp = Exp({"name": "test"})
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

        mock_br.assert_called_once_with(ctx, bus=None)
        mock_kernel.run.assert_called_once_with(
            mock_spec, "do something", history=None, stop_event=None
        )
        assert result is mock_finish

    def test_run_forwards_bus(self) -> None:
        """run() passes bus to build_runtime."""
        from matmaster.core.bus import MessageBus

        exp = Exp({"name": "test"})
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

        mock_br.assert_called_once_with(ctx, bus=bus)

    def test_run_forwards_history_and_stop_event(self) -> None:
        """run() passes history and stop_event to kernel.run."""
        import threading

        exp = Exp({"name": "test"})
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
        exp = Exp({"name": "test"})
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
        exp = Exp({"name": "test"})
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
        exp = Exp({"name": "test"})
        cb1 = MagicMock(side_effect=ValueError("cb1 broken"))
        cb2 = MagicMock()
        exp._register_cleanup(cb1)
        exp._register_cleanup(cb2)

        exp._run_cleanup_callbacks()

        cb1.assert_called_once()
        cb2.assert_called_once()

    def test_cleanup_clears_list(self) -> None:
        """_run_cleanup_callbacks clears the list after execution."""
        exp = Exp({"name": "test"})
        cb = MagicMock()
        exp._register_cleanup(cb)

        exp._run_cleanup_callbacks()

        assert exp._cleanup_callbacks == []


# ── TestIdentityOverride ────────────────────────────────


class TestIdentityOverride:
    """Identity from config is forwarded to ContextBuilder.build()."""

    def test_identity_from_config(self) -> None:
        config = {"name": "test", "identity": "I am a materials scientist.", "tools": {"builtin": []}}
        exp = Exp(config)
        ctx = _make_ctx(with_llm=True)
        runtime = exp.build_runtime(ctx)

        assert "I am a materials scientist." in runtime.spec.system_prompt

    def test_default_identity_when_not_set(self) -> None:
        config = {"name": "test", "tools": {"builtin": []}}
        exp = Exp(config)
        ctx = _make_ctx(with_llm=True)
        runtime = exp.build_runtime(ctx)

        assert "helpful AI assistant" in runtime.spec.system_prompt
