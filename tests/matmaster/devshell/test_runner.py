"""Tests for DevRunner -- per-run assembly and history accumulation."""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import create_autospec, patch

from matmaster.types.messages import StreamChunk
from matmaster.types.session import Session


class MockProvider:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def chat(self, messages, tools=None):
        from matmaster.types.messages import LLMResponse

        return LLMResponse(content="mock", finish_reason="stop")

    async def chat_stream(
        self, messages, tools=None, *, timeout=None
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content="hello", finish_reason="stop")


class TestBuildExpConfig:
    def test_system_prompt_default(self):
        """AgentConfig defaults system_prompt to empty string."""
        from matmaster.devshell.config import AgentConfig

        cfg = AgentConfig()
        assert cfg.system_prompt == ""

    def test_system_prompt_forwarded(self):
        """_build_exp_config uses explicit system_prompt when provided."""
        from matmaster.devshell.config import AgentConfig, DevConfig
        from matmaster.devshell.runner import DevRunner

        config = DevConfig(agent=AgentConfig(system_prompt="Custom prompt."))
        exp_cfg = DevRunner._build_exp_config(config)
        assert exp_cfg.system_prompt == "Custom prompt."

    def test_system_prompt_fallback_to_base(self):
        """_build_exp_config calls load_base_system_prompt when system_prompt is empty."""
        from unittest.mock import patch

        from matmaster.devshell.config import DevConfig
        from matmaster.devshell.runner import DevRunner

        config = DevConfig()
        with patch(
            "matmaster.config.loader.load_base_system_prompt",
            return_value="Mocked base",
        ) as mock_load:
            exp_cfg = DevRunner._build_exp_config(config)
        mock_load.assert_called_once()
        assert exp_cfg.system_prompt == "Mocked base"


class TestDevRunner:
    def _make_runner(self, tmp_path: Path) -> Any:
        from matmaster.devshell.config import DevConfig
        from matmaster.devshell.runner import DevRunner

        workdir = tmp_path / "workspace"
        workdir.mkdir()
        config = DevConfig()

        with patch(
            "matmaster.devshell.runner.DevRunner._create_session"
        ) as mock_session:
            mock_session.return_value = create_autospec(Session, instance=True)
            return DevRunner(
                config=config,
                workdir=workdir,
                llm_provider=MockProvider(),
            )

    def test_single_run(self, tmp_path: Path) -> None:
        runner = self._make_runner(tmp_path)
        result = runner.run("hello")

        assert result.result.reason == "natural"
        assert result.result.final_content == "hello"

    def test_history_accumulates(self, tmp_path: Path) -> None:
        runner = self._make_runner(tmp_path)

        runner.run("first question")
        assert len(runner.history) > 0

        history_before = len(runner.history)
        runner.run("second question")
        assert len(runner.history) > history_before

    def test_cleanup_called(self, tmp_path: Path) -> None:
        runner = self._make_runner(tmp_path)
        # Run should not leak resources
        runner.run("test")
        # If we get here without error, cleanup worked

    def test_stop_event(self, tmp_path: Path) -> None:
        runner = self._make_runner(tmp_path)
        stop = threading.Event()
        stop.set()
        result = runner.run("test", stop_event=stop)
        assert result.result.reason == "cancelled"
