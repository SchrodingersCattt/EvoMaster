"""Tests for REPL builtin command parsing and routing."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import io


class TestBuiltinCommands:
    def test_parse_help(self) -> None:
        from matmaster.devshell.repl import parse_command
        assert parse_command("/help") == ("help", "")

    def test_parse_config(self) -> None:
        from matmaster.devshell.repl import parse_command
        assert parse_command("/config") == ("config", "")

    def test_parse_tools(self) -> None:
        from matmaster.devshell.repl import parse_command
        assert parse_command("/tools") == ("tools", "")

    def test_parse_verbose(self) -> None:
        from matmaster.devshell.repl import parse_command
        assert parse_command("/verbose") == ("verbose", "")

    def test_parse_not_command(self) -> None:
        from matmaster.devshell.repl import parse_command
        assert parse_command("hello world") is None

    def test_parse_unknown_command(self) -> None:
        from matmaster.devshell.repl import parse_command
        assert parse_command("/unknown") == ("unknown", "")

    def test_parse_command_with_args(self) -> None:
        from matmaster.devshell.repl import parse_command
        assert parse_command("/config set model gpt-4") == ("config", "set model gpt-4")

    def test_parse_empty_slash(self) -> None:
        from matmaster.devshell.repl import parse_command
        assert parse_command("/") == ("", "")


class TestFormatBanner:
    def test_banner_contains_model(self) -> None:
        from matmaster.devshell.repl import format_banner
        from matmaster.devshell.config import DevConfig

        cfg = DevConfig()
        banner = format_banner(cfg, workdir="/tmp/ws", log_dir="/tmp/logs")
        assert "gpt-4o" in banner
        assert "local" in banner

    def test_banner_contains_workdir(self) -> None:
        from matmaster.devshell.repl import format_banner
        from matmaster.devshell.config import DevConfig

        cfg = DevConfig()
        banner = format_banner(cfg, workdir="/my/workdir", log_dir="/my/logs")
        assert "/my/workdir" in banner
        assert "/my/logs" in banner


class TestCliParsing:
    def test_parse_required_args(self) -> None:
        from matmaster.devshell.cli import parse_args

        args = parse_args(["--workdir", "/tmp/ws", "--log-dir", "/tmp/logs"])
        assert args.workdir == Path("/tmp/ws")
        assert args.log_dir == Path("/tmp/logs")

    def test_parse_optional_args(self) -> None:
        from matmaster.devshell.cli import parse_args

        args = parse_args([
            "--workdir", "/tmp/ws",
            "--log-dir", "/tmp/logs",
            "--config", "custom.yaml",
            "--session", "docker",
            "--verbose",
        ])
        assert args.config == Path("custom.yaml")
        assert args.session == "docker"
        assert args.verbose is True

    def test_defaults(self) -> None:
        from matmaster.devshell.cli import parse_args

        args = parse_args(["--workdir", "/tmp/ws", "--log-dir", "/tmp/logs"])
        assert args.config is None
        assert args.session is None
        assert args.verbose is False


class TestShowTools:
    def test_show_tools_uses_all_tools(self) -> None:
        """Verify _show_tools accesses registry.all_tools, not registry.tools."""
        from unittest.mock import MagicMock, patch
        from matmaster.devshell.repl import _show_tools

        mock_runner = MagicMock()
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"
        mock_tool.description = "A test tool"

        mock_registry = MagicMock()
        mock_registry.all_tools = [mock_tool]
        # Ensure .tools raises AttributeError (like real ToolRegistry)
        del mock_registry.tools

        mock_runtime = MagicMock()
        mock_runtime.spec.tool_registry = mock_registry

        with patch("matmaster.core.exp.Exp") as MockExp:
            MockExp.return_value.build_runtime.return_value = mock_runtime
            _show_tools(mock_runner)  # Should not raise

        mock_runtime.cleanup.assert_called_once()


class TestDevStreamHookSegment:
    async def test_on_segment_complete_thought_verbose(self) -> None:
        import io
        from matmaster.devshell.stream_hook import DevStreamHook

        out = io.StringIO()
        hook = DevStreamHook(output=out, verbose=True)
        await hook.on_segment_complete("thought", "some thought", "s1")
        assert "thought complete" in out.getvalue()

    async def test_on_segment_complete_thought_non_verbose(self) -> None:
        import io
        from matmaster.devshell.stream_hook import DevStreamHook

        out = io.StringIO()
        hook = DevStreamHook(output=out, verbose=False)
        await hook.on_segment_complete("thought", "some thought", "s1")
        assert out.getvalue() == ""

    async def test_on_segment_complete_response_silent(self) -> None:
        import io
        from matmaster.devshell.stream_hook import DevStreamHook

        out = io.StringIO()
        hook = DevStreamHook(output=out, verbose=True)
        await hook.on_segment_complete("response", "content", "s1")
        assert out.getvalue() == ""
