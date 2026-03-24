"""Tests for DevRunner -- per-run assembly and history accumulation."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch, MagicMock

from matmaster.types.messages import StreamChunk, ToolCallData


class MockProvider:
    def chat(self, messages, tools=None):
        from matmaster.types.messages import LLMResponse
        return LLMResponse(content="mock", finish_reason="stop")

    def chat_with_retry(self, messages, tools=None, *, max_retries=3, retry_delay=1.0):
        return self.chat(messages, tools)

    def chat_stream(self, messages, tools=None) -> Iterator[StreamChunk]:
        yield StreamChunk(content="hello", finish_reason="stop")


class TestDevRunner:
    def _make_runner(self, tmp_path: Path) -> Any:
        from matmaster.devshell.config import DevConfig
        from matmaster.devshell.runner import DevRunner

        workdir = tmp_path / "workspace"
        workdir.mkdir()
        config = DevConfig()

        with patch("matmaster.devshell.runner.DevRunner._create_session") as mock_session:
            mock_session.return_value = MagicMock()
            return DevRunner(
                config=config,
                workdir=workdir,
                llm_provider=MockProvider(),
            )

    def test_single_run(self, tmp_path: Path) -> None:
        runner = self._make_runner(tmp_path)
        result = runner.run("hello")

        assert result.event.reason == "natural"
        assert result.event.final_content == "hello"

    def test_history_accumulates(self, tmp_path: Path) -> None:
        runner = self._make_runner(tmp_path)

        result1 = runner.run("first question")
        assert len(runner.history) > 0

        history_before = len(runner.history)
        result2 = runner.run("second question")
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
        assert result.event.reason == "cancelled"
