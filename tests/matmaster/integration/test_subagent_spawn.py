"""Integration tests for SubAgent spawn lifecycle.

Tests cover:
- spawn_fn closure creation and child agent execution
- Cleanup guarantee via finally block (success and error paths)
- Shared context propagation (SUBA-03)
- Source prefix for child EventEmitterHook (D-02)
- stop_event propagation through SpawnTool -> spawn_fn -> child kernel (SUBA-05)
- Recursion guard: child exp (explore) has no spawn tool (D-04)
- Plan 01 test backward compatibility
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from matmaster.config.exp import ExpConfig, ExpToolsConfig
from matmaster.core.exp import Exp
from matmaster.tools.builtin.spawn_tool import SpawnTool
from matmaster.types.context import PlaygroundContext
from matmaster.types.runtime import AgentRuntime, AgentRuntimeSpec, KernelResult, KernelRunResult


def _make_ctx(*, with_session: bool = True) -> PlaygroundContext:
    """Create a minimal PlaygroundContext for spawn tests."""
    from tests.matmaster.core.conftest import MockLLMProvider

    kwargs: dict = dict(
        workdir=Path("/tmp/test"),
        session_type="local",
        cache_area=Path("/tmp/cache"),
        llm_provider=MockLLMProvider(),
    )
    if with_session:
        kwargs["session"] = MagicMock()
    return PlaygroundContext(**kwargs)


class TestSpawnFnLifecycle:
    """Tests for Exp._make_spawn_fn closure behavior."""

    def test_spawn_fn_creates_child_and_returns_result(self) -> None:
        """spawn_fn loads child config, builds runtime, runs kernel, returns result."""
        ctx = _make_ctx()
        mock_kr = KernelResult(
            status="completed", reason="natural", final_content="found 3 files"
        )
        mock_run_result = KernelRunResult(result=mock_kr, messages=[])

        mock_kernel = MagicMock()
        mock_kernel.run.return_value = mock_run_result
        mock_cleanup = MagicMock()
        mock_runtime = AgentRuntime(
            kernel=mock_kernel,
            spec=MagicMock(),
            cleanup=mock_cleanup,
        )

        with (
            patch("matmaster.config.loader.load_exp_config") as mock_load,
            patch.object(Exp, "build_runtime", return_value=mock_runtime),
        ):
            mock_load.return_value = ExpConfig(name="explore")
            spawn_fn = Exp._make_spawn_fn(ctx, bus=None, source_prefix="MatMaster")
            result = spawn_fn("explore", "find test files")

        assert result == "found 3 files"

    def test_spawn_fn_cleanup_called(self) -> None:
        """Child cleanup is called after successful kernel.run."""
        ctx = _make_ctx()
        mock_kr = KernelResult(
            status="completed", reason="natural", final_content="ok"
        )
        mock_run_result = KernelRunResult(result=mock_kr, messages=[])

        mock_kernel = MagicMock()
        mock_kernel.run.return_value = mock_run_result
        mock_cleanup = MagicMock()
        mock_runtime = AgentRuntime(
            kernel=mock_kernel,
            spec=MagicMock(),
            cleanup=mock_cleanup,
        )

        with (
            patch("matmaster.config.loader.load_exp_config") as mock_load,
            patch.object(Exp, "build_runtime", return_value=mock_runtime),
        ):
            mock_load.return_value = ExpConfig(name="explore")
            spawn_fn = Exp._make_spawn_fn(ctx, bus=None, source_prefix="MatMaster")
            spawn_fn("explore", "task")

        mock_cleanup.assert_called_once()

    def test_spawn_fn_cleanup_on_error(self) -> None:
        """Child cleanup is called even when kernel.run raises."""
        ctx = _make_ctx()
        mock_kernel = MagicMock()
        mock_kernel.run.side_effect = RuntimeError("kernel crashed")
        mock_cleanup = MagicMock()
        mock_runtime = AgentRuntime(
            kernel=mock_kernel,
            spec=MagicMock(),
            cleanup=mock_cleanup,
        )

        with (
            patch("matmaster.config.loader.load_exp_config") as mock_load,
            patch.object(Exp, "build_runtime", return_value=mock_runtime),
        ):
            mock_load.return_value = ExpConfig(name="explore")
            spawn_fn = Exp._make_spawn_fn(ctx, bus=None, source_prefix="MatMaster")
            with pytest.raises(RuntimeError, match="kernel crashed"):
                spawn_fn("explore", "task")

        mock_cleanup.assert_called_once()

    def test_spawn_fn_shared_context(self) -> None:
        """spawn_fn passes parent ctx to child build_runtime (SUBA-03)."""
        ctx = _make_ctx()
        mock_kr = KernelResult(
            status="completed", reason="natural", final_content="ok"
        )
        mock_run_result = KernelRunResult(result=mock_kr, messages=[])
        mock_kernel = MagicMock()
        mock_kernel.run.return_value = mock_run_result
        mock_runtime = AgentRuntime(
            kernel=mock_kernel, spec=MagicMock(), cleanup=MagicMock()
        )

        with (
            patch("matmaster.config.loader.load_exp_config") as mock_load,
            patch.object(Exp, "build_runtime", return_value=mock_runtime) as mock_br,
        ):
            mock_load.return_value = ExpConfig(name="explore")
            spawn_fn = Exp._make_spawn_fn(ctx, bus=None, source_prefix="MatMaster")
            spawn_fn("explore", "task")

        # Verify build_runtime was called with the SAME ctx object
        call_args = mock_br.call_args
        assert call_args[0][0] is ctx

    def test_spawn_fn_source_prefix(self) -> None:
        """spawn_fn passes source_override='MatMaster:{exp_name}' to child build_runtime (D-02)."""
        ctx = _make_ctx()
        mock_kr = KernelResult(
            status="completed", reason="natural", final_content="ok"
        )
        mock_run_result = KernelRunResult(result=mock_kr, messages=[])
        mock_kernel = MagicMock()
        mock_kernel.run.return_value = mock_run_result
        mock_runtime = AgentRuntime(
            kernel=mock_kernel, spec=MagicMock(), cleanup=MagicMock()
        )

        with (
            patch("matmaster.config.loader.load_exp_config") as mock_load,
            patch.object(Exp, "build_runtime", return_value=mock_runtime) as mock_br,
        ):
            mock_load.return_value = ExpConfig(name="explore")
            spawn_fn = Exp._make_spawn_fn(ctx, bus=None, source_prefix="MatMaster")
            spawn_fn("explore", "task")

        # Verify source_override is "MatMaster:explore"
        call_kwargs = mock_br.call_args[1]
        assert call_kwargs["source_override"] == "MatMaster:explore"

    def test_spawn_fn_passes_fresh_child_spawn_id_to_build_runtime(self) -> None:
        """Each spawn generates uuid.uuid4().hex[:16] and passes it to child build_runtime."""
        ctx = _make_ctx()
        mock_kr = KernelResult(
            status="completed", reason="natural", final_content="ok"
        )
        mock_run_result = KernelRunResult(result=mock_kr, messages=[])
        mock_kernel = MagicMock()
        mock_kernel.run.return_value = mock_run_result
        mock_runtime = AgentRuntime(
            kernel=mock_kernel, spec=MagicMock(), cleanup=MagicMock()
        )
        hex16 = re.compile(r"^[0-9a-f]{16}$")

        with (
            patch("matmaster.config.loader.load_exp_config") as mock_load,
            patch.object(Exp, "build_runtime", return_value=mock_runtime) as mock_br,
        ):
            mock_load.return_value = ExpConfig(name="explore")
            spawn_fn = Exp._make_spawn_fn(ctx, bus=None, source_prefix="MatMaster")
            spawn_fn("explore", "task")

        call_kwargs = mock_br.call_args[1]
        assert "spawn_id" in call_kwargs
        sid1 = call_kwargs["spawn_id"]
        assert sid1 is not None
        assert hex16.match(sid1)

        with (
            patch("matmaster.config.loader.load_exp_config") as mock_load,
            patch.object(Exp, "build_runtime", return_value=mock_runtime) as mock_br,
        ):
            mock_load.return_value = ExpConfig(name="explore")
            spawn_fn = Exp._make_spawn_fn(ctx, bus=None, source_prefix="MatMaster")
            spawn_fn("explore", "task")

        sid2 = mock_br.call_args[1]["spawn_id"]
        assert sid2 is not None
        assert hex16.match(sid2)
        assert sid1 != sid2


class TestStopEventPropagation:
    """Tests for stop_event flow: SpawnTool._stop_event -> spawn_fn -> child kernel (SUBA-05)."""

    def test_stop_event_propagation(self) -> None:
        """stop_event passed to spawn_fn reaches child kernel.run."""
        ctx = _make_ctx()
        stop_event = threading.Event()
        mock_kr = KernelResult(
            status="completed", reason="natural", final_content="ok"
        )
        mock_run_result = KernelRunResult(result=mock_kr, messages=[])
        mock_kernel = MagicMock()
        mock_kernel.run.return_value = mock_run_result
        mock_runtime = AgentRuntime(
            kernel=mock_kernel, spec=MagicMock(), cleanup=MagicMock()
        )

        with (
            patch("matmaster.config.loader.load_exp_config") as mock_load,
            patch.object(Exp, "build_runtime", return_value=mock_runtime),
        ):
            mock_load.return_value = ExpConfig(name="explore")
            spawn_fn = Exp._make_spawn_fn(ctx, bus=None, source_prefix="MatMaster")
            spawn_fn("explore", "task", stop_event)

        # Verify kernel.run received the stop_event
        mock_kernel.run.assert_called_once()
        call_kwargs = mock_kernel.run.call_args[1]
        assert call_kwargs["stop_event"] is stop_event

    def test_sub_agent_tool_stop_event_injection(self) -> None:
        """SpawnTool._execute passes _stop_event as 3rd arg to spawn_fn."""
        stop_event = threading.Event()
        mock_spawn = Mock(return_value="result")
        tool = SpawnTool(spawn_fn=mock_spawn)
        tool._stop_event = stop_event

        result = tool.execute({"exp_name": "explore", "task": "find files"})

        assert result == "result"
        mock_spawn.assert_called_once_with("explore", "find files", stop_event)


class TestRecursionGuard:
    """Tests for recursion guard: child exp has no spawn tool."""

    def test_recursion_guard_child_no_spawn(self) -> None:
        """explore.toml does not include spawn in tools.builtin."""
        from matmaster.config.loader import load_exp_config

        cfg = load_exp_config("explore")
        assert "spawn" not in cfg.tools.builtin


class TestPlan01Compat:
    """Verify Plan 01 test suite still passes after spawn_tool.py changes."""

    def test_plan01_tests_still_pass(self) -> None:
        """Run Plan 01 tests inline to confirm backward compat.

        Plan 01 tests use Mock(return_value=...) which accepts any args,
        so adding _stop_event as 3rd arg should not break them.
        """
        mock_spawn = Mock(return_value="exploration result: found 3 files")
        tool = SpawnTool(spawn_fn=mock_spawn)

        result = tool.execute({"exp_name": "explore", "task": "find files"})

        assert result == "exploration result: found 3 files"
        # Mock accepts any args -- 3rd arg (_stop_event) is None by default
        mock_spawn.assert_called_once()
