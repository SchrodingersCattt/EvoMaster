"""Tests for BashTool -- pure execution layer (safety checks in CapabilityPolicy)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matmaster.sessions.local import LocalSession
from matmaster.tools.builtin.bash_tool import BashTool
from matmaster.tools.tool_registry import Tool
from matmaster.types.cancellation import CancellationController
from matmaster.types.topology import ToolPlane


@pytest.fixture()
def mock_session() -> MagicMock:
    """Create a mock session with exec_bash returning success."""
    session = MagicMock()
    session.exec_bash.return_value = {
        "output": "hello world",
        "exit_code": 0,
        "working_dir": "/tmp",
    }
    return session


class TestBashToolBasic:
    """Basic BashTool properties."""

    def test_name_is_execute_bash(self) -> None:
        tool = BashTool()
        assert tool.name == "execute_bash"

    def test_satisfies_tool_protocol(self) -> None:
        tool = BashTool()
        assert isinstance(tool, Tool)

    def test_no_dangerous_patterns_in_bash_tool(self) -> None:
        """Verify bash_tool.py no longer contains danger patterns (migrated to CapabilityPolicy)."""
        import inspect
        import matmaster.tools.builtin.bash_tool as bash_mod

        source = inspect.getsource(bash_mod)
        assert "_DANGEROUS_COMMAND_PATTERNS" not in source
        assert "is_dangerous_bash_command" not in source
        assert "is_dangerous_python_content" not in source

    def test_prompt_returns_usage_guidance(self) -> None:
        prompt = BashTool().prompt()

        assert prompt is not None
        assert "read_file" in prompt
        assert "cat" in prompt

    def test_metadata(self) -> None:
        tool = BashTool()

        assert tool.plane == ToolPlane.SESSION_SHELL
        assert tool.effect_level == "local_mutation"
        assert tool.max_result_chars == 12000


class TestBashToolExecution:
    """BashTool command execution (pure execution, no safety filtering)."""

    async def test_normal_command_returns_output_and_exit_code(
        self, mock_session: MagicMock
    ) -> None:
        tool = BashTool(session=mock_session)
        result = await tool.execute({"command": "echo hello"})
        assert "hello world" in result
        assert "exit code 0" in result
        mock_session.exec_bash.assert_called_once()

    async def test_any_command_executes_now(self, mock_session: MagicMock) -> None:
        """BashTool no longer blocks commands internally; CapabilityPolicy handles safety."""
        tool = BashTool(session=mock_session)
        result = await tool.execute({"command": "env"})
        # BashTool now executes all commands -- safety is CapabilityPolicy's job
        assert "hello world" in result
        mock_session.exec_bash.assert_called_once()

    async def test_session_not_injected_returns_error(self) -> None:
        tool = BashTool()
        result = await tool.execute({"command": "echo hello"})
        assert "Error:" in result
        assert "session" in result.lower()

    async def test_working_dir_in_output(self, mock_session: MagicMock) -> None:
        tool = BashTool(session=mock_session)
        result = await tool.execute({"command": "pwd"})
        assert "/tmp" in result

    async def test_execute_with_context_passes_cancel_token(
        self, mock_session: MagicMock
    ) -> None:
        tool = BashTool(session=mock_session)
        ctrl = CancellationController()
        exec_ctx = type("ExecCtx", (), {"cancel_token": ctrl.token})()

        await tool.execute_with_context({"command": "echo hello"}, exec_ctx)

        call_kwargs = mock_session.exec_bash.call_args.kwargs
        assert call_kwargs["cancel_token"] is ctrl.token


class TestBashToolAsyncSubprocess:
    """BashTool async subprocess path for matmaster LocalSession."""

    def _make_tool_with_local_session(self) -> BashTool:
        """Create BashTool with a matmaster LocalSession instance."""
        session = LocalSession(Path("/tmp"))
        return BashTool(session=session, workdir=Path("/tmp"))

    async def test_normal_command(self) -> None:
        """Async path: create_subprocess_exec called, output contains result."""
        tool = self._make_tool_with_local_session()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"hello world", b""))
        mock_proc.returncode = 0

        with patch(
            "matmaster.tools.builtin.bash_tool.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ) as mock_create:
            result = await tool.execute({"command": "echo hello"})

        assert "hello world" in result
        assert "exit code 0" in result
        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["start_new_session"] is True

    async def test_timeout(self) -> None:
        """Async path: timeout kills process and returns exit code 124."""
        tool = self._make_tool_with_local_session()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch(
            "matmaster.tools.builtin.bash_tool.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            result = await tool.execute({"command": "sleep 999", "timeout": 1})

        assert "timeout" in result.lower()
        assert "exit code 124" in result
        mock_proc.kill.assert_called_once()
        mock_proc.wait.assert_awaited_once()

    async def test_async_path_executes_all_commands(self) -> None:
        """Async path: no longer blocks commands (CapabilityPolicy handles safety)."""
        tool = self._make_tool_with_local_session()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"output", b""))
        mock_proc.returncode = 0

        with patch(
            "matmaster.tools.builtin.bash_tool.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ) as mock_create:
            result = await tool.execute({"command": "env"})

        assert "output" in result
        mock_create.assert_called_once()

    async def test_session_dependent_fallback(self) -> None:
        """Non-LocalSession: falls back to session.exec_bash via base class to_thread."""
        mock_session = MagicMock()
        mock_session.exec_bash.return_value = {
            "output": "fallback result",
            "exit_code": 0,
            "working_dir": "/tmp",
        }
        tool = BashTool(session=mock_session, workdir=Path("/tmp"))

        result = await tool.execute({"command": "echo test"})

        assert "fallback result" in result
        mock_session.exec_bash.assert_called_once()
