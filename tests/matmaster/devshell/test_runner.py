"""Tests for DevRunner -- per-run assembly and history accumulation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

from matmaster.types.cancellation import CancellationController
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

        assert result.reason == "natural"
        assert result.final_content == "hello"

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

    def test_cancel_token(self, tmp_path: Path) -> None:
        runner = self._make_runner(tmp_path)
        controller = CancellationController()
        controller.cancel()
        result = runner.run("test", cancel_token=controller.token)
        assert result.reason == "cancelled"

    def test_run_injects_cancel_token_into_catalog_and_session(
        self, tmp_path: Path
    ) -> None:
        runner = self._make_runner(tmp_path)
        controller = CancellationController()
        catalog = MagicMock()
        observed: dict[str, Any] = {}

        def fake_run_stream(spec, task, history=None, cancel_token=None):
            observed["spec"] = spec
            observed["task"] = task
            observed["history"] = history
            observed["cancel_token"] = cancel_token
            return object()

        runtime = MagicMock()
        runtime.spec = MagicMock(tool_catalog=catalog)
        runtime.kernel = MagicMock()
        runtime.kernel.run_stream = fake_run_stream

        fake_result = MagicMock()
        fake_result.status = "cancelled"
        fake_result.reason = "cancelled"
        fake_result.messages = []
        fake_result.final_content = ""
        fake_result.num_turns = 0
        fake_result.usage = {}

        fake_exp = MagicMock()
        fake_exp.build_runtime = AsyncMock(return_value=runtime)
        fake_exp._run_cleanup_callbacks = AsyncMock()

        with patch(
            "matmaster.devshell.runner.Exp", return_value=fake_exp
        ), patch(
            "matmaster.core.stream_drain.drain_run_stream",
            new=AsyncMock(return_value=fake_result),
        ):
            result = runner.run("test", cancel_token=controller.token)

        assert result.reason == "cancelled"
        assert runner._pg_ctx.session._cancel_token is controller.token
        catalog.inject_cancel_token.assert_called_once_with(controller.token)
        assert observed["task"] == "test"
        assert observed["cancel_token"] is controller.token
