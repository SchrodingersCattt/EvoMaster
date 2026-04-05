"""Tests for REPL builtin command parsing and routing."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


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


class TestCliRunMode:
    def test_run_single_uses_drain_result_fields(self, capsys, tmp_path: Path) -> None:
        from matmaster.core.stream_drain import DrainResult
        from matmaster.devshell.cli import _run_single, parse_args

        args = parse_args(
            [
                "run",
                "--workdir",
                str(tmp_path / "ws"),
                "--log-dir",
                str(tmp_path / "logs"),
                "-p",
                "hello",
            ]
        )
        drain_result = DrainResult(
            status="completed",
            reason="natural",
            final_content="OK",
            num_turns=1,
            usage={"total_tokens": 3},
            messages=[],
        )
        resolved = SimpleNamespace(model="m", profile_key="p", route_key="r")

        with patch(
            "matmaster.devshell.cli._run_with_event_log",
            return_value=(drain_result, tmp_path / "logs" / "events.jsonl"),
        ):
            rc = _run_single(args, runner=object(), resolved=resolved)

        captured = capsys.readouterr()
        assert rc == 0
        assert json.loads(captured.out) == {
            "model": "m",
            "profile_key": "p",
            "route_key": "r",
            "status": "completed",
            "reason": "natural",
            "final_content": "OK",
            "num_turns": 1,
            "usage": {"total_tokens": 3},
        }

    def test_bootstrap_runner_silences_run_mode_stream_output(
        self, tmp_path: Path
    ) -> None:
        from matmaster.devshell.cli import _bootstrap_runner, parse_args

        args = parse_args(
            [
                "run",
                "--workdir",
                str(tmp_path / "ws"),
                "--log-dir",
                str(tmp_path / "logs"),
                "-p",
                "hello",
            ]
        )
        fake_llm_config = SimpleNamespace(
            resolve_route=lambda **_: SimpleNamespace(
                model="m",
                profile_key="p",
                route_key="r",
            )
        )
        captured: dict[str, object] = {}

        class FakeRunner:
            def __init__(self, **kwargs) -> None:
                captured.update(kwargs)

        with (
            patch(
                "matmaster.devshell.cli._load_agents_general_llm",
                return_value="opus",
            ),
            patch(
                "matmaster.config.loader.load_llm_config",
                return_value=fake_llm_config,
            ),
            patch(
                "matmaster.providers.llm_factory.build_provider",
                return_value=object(),
            ),
            patch("matmaster.devshell.runner.DevRunner", FakeRunner),
        ):
            _bootstrap_runner(args)

        stream_hook = captured["stream_hook"]
        assert stream_hook._out is not sys.stdout


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
        mock_catalog = MagicMock()
        mock_catalog.registry = mock_registry
        mock_runtime.spec.tool_catalog = mock_catalog

        with patch("matmaster.core.exp.Exp") as MockExp:
            MockExp.return_value.build_runtime = AsyncMock(return_value=mock_runtime)
            MockExp.return_value._run_cleanup_callbacks = AsyncMock()
            _show_tools(mock_runner)  # Should not raise

        MockExp.return_value._run_cleanup_callbacks.assert_called_once()


class TestDevStreamHookSegment:
    def test_on_event_thought_streaming_writes_content(self) -> None:
        import io

        from matmaster.devshell.stream_hook import DevStreamHook
        from matmaster.types.events import ThoughtEvent

        out = io.StringIO()
        hook = DevStreamHook(output=out, verbose=True)
        hook.on_event(
            ThoughtEvent(
                source="agent",
                content="some thought",
                stream_state="streaming",
                stream_id="s1",
            )
        )
        assert out.getvalue() == "some thought"

    def test_on_event_thought_end_writes_newline(self) -> None:
        import io

        from matmaster.devshell.stream_hook import DevStreamHook
        from matmaster.types.events import ThoughtEvent

        out = io.StringIO()
        hook = DevStreamHook(output=out, verbose=False)
        hook.on_event(
            ThoughtEvent(
                source="agent",
                content="",
                stream_state="end",
                stream_id="s1",
            )
        )
        assert out.getvalue() == "\n"

    def test_on_event_response_complete_writes_content(self) -> None:
        import io

        from matmaster.devshell.stream_hook import DevStreamHook
        from matmaster.types.events import ResponseEvent

        out = io.StringIO()
        hook = DevStreamHook(output=out, verbose=True)
        hook.on_event(
            ResponseEvent(
                source="agent",
                content="content",
                stream_state="complete",
                stream_id="s1",
            )
        )
        assert out.getvalue() == "content"
