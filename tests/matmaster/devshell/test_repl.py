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
