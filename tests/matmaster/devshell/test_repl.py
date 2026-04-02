"""Tests for REPL builtin command parsing and routing."""

from __future__ import annotations

from pathlib import Path


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
        from matmaster.devshell.config import DevConfig
        from matmaster.devshell.repl import format_banner

        cfg = DevConfig()
        banner = format_banner(
            cfg,
            workdir="/tmp/ws",
            log_dir="/tmp/logs",
            llm_model="claude-sonnet-4-6",
            llm_profile="sonnet",
        )
        assert "claude-sonnet-4-6" in banner
        assert "sonnet" in banner
        assert "local" in banner

    def test_banner_contains_workdir(self) -> None:
        from matmaster.devshell.config import DevConfig
        from matmaster.devshell.repl import format_banner

        cfg = DevConfig()
        banner = format_banner(
            cfg,
            workdir="/my/workdir",
            log_dir="/my/logs",
            llm_model="m",
            llm_profile="p",
        )
        assert "/my/workdir" in banner
        assert "/my/logs" in banner


def _repl_argv(*parts: str) -> list[str]:
    return ["repl", *parts]


class TestCliParsing:
    def test_parse_required_args(self) -> None:
        from matmaster.devshell.cli import parse_args

        args = parse_args(_repl_argv("--workdir", "/tmp/ws", "--log-dir", "/tmp/logs"))
        assert args.command == "repl"
        assert args.workdir == Path("/tmp/ws")
        assert args.log_dir == Path("/tmp/logs")

    def test_legacy_omitted_repl(self) -> None:
        from matmaster.devshell.cli import parse_args

        args = parse_args(["--workdir", "/tmp/ws", "--log-dir", "/tmp/logs"])
        assert args.command == "repl"
        assert args.workdir == Path("/tmp/ws")

    def test_parse_optional_args(self) -> None:
        from matmaster.devshell.cli import parse_args

        args = parse_args(
            _repl_argv(
                "--workdir",
                "/tmp/ws",
                "--log-dir",
                "/tmp/logs",
                "--exp",
                "explore",
                "--session",
                "docker",
                "--verbose",
            )
        )
        assert args.exp == "explore"
        assert args.session == "docker"
        assert args.verbose is True

    def test_defaults(self) -> None:
        from matmaster.devshell.cli import parse_args

        args = parse_args(_repl_argv("--workdir", "/tmp/ws", "--log-dir", "/tmp/logs"))
        assert args.exp is None
        assert args.session is None
        assert args.verbose is False
        assert args.model is None

    def test_model_arg(self) -> None:
        from matmaster.devshell.cli import parse_args

        args = parse_args(
            _repl_argv(
                "--workdir",
                "/tmp/ws",
                "--log-dir",
                "/tmp/logs",
                "--model",
                "gpt-4o-mini",
            )
        )
        assert args.model == "gpt-4o-mini"

    def test_run_subcommand_prompt(self) -> None:
        from matmaster.devshell.cli import parse_args

        args = parse_args(
            [
                "run",
                "--workdir",
                "/tmp/ws",
                "--log-dir",
                "/tmp/logs",
                "-p",
                "hello",
            ]
        )
        assert args.command == "run"
        assert args.prompt == "hello"
        assert args.json_out is None

    def test_run_prompt_file(self) -> None:
        from matmaster.devshell.cli import parse_args

        args = parse_args(
            [
                "run",
                "--workdir",
                "/tmp/ws",
                "--log-dir",
                "/tmp/logs",
                "--prompt-file",
                "/tmp/p.txt",
                "--json-out",
                "/tmp/out.json",
            ]
        )
        assert args.command == "run"
        assert args.prompt_file == Path("/tmp/p.txt")
        assert args.json_out == Path("/tmp/out.json")


class TestShowTools:
    def test_show_tools_uses_all_tools(self) -> None:
        """Verify _show_tools accesses registry.all_tools, not registry.tools."""
        from unittest.mock import AsyncMock, MagicMock, patch

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
            MockExp.return_value.build_runtime = AsyncMock(return_value=mock_runtime)
            MockExp.return_value._run_cleanup_callbacks = AsyncMock()
            _show_tools(mock_runner)  # Should not raise

        MockExp.return_value._run_cleanup_callbacks.assert_called_once()


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
