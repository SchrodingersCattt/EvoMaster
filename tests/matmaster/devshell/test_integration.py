"""Integration test: DevRunner -> Exp -> AgentKernel with mock LLM.

Smoke tests the full pipeline end-to-end: DevRunner assembles Exp,
Exp builds runtime with AgentKernel, kernel runs with mock providers,
DevStreamHook captures terminal output, DevEventObserver captures events.
"""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import create_autospec, patch

from matmaster.devshell.config import DevConfig
from matmaster.devshell.event_logger import EventLogger
from matmaster.devshell.event_observer import DevEventObserver
from matmaster.devshell.runner import DevRunner
from matmaster.devshell.stream_hook import DevStreamHook
from matmaster.types.cancellation import CancellationController
from matmaster.types.messages import StreamChunk
from matmaster.types.session import Session

# -- Mock Providers --


class SimpleProvider:
    """Mock that always returns a text reply (no tool calls)."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def chat(self, messages, tools=None):
        from matmaster.types.messages import LLMResponse

        return LLMResponse(content='unused', finish_reason='stop')

    async def chat_stream(
        self, messages, tools=None, *, timeout=None
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(
            content=f'Reply to msg #{len(messages)}', finish_reason='stop'
        )


class ToolCallingProvider:
    """Mock that calls a tool on first turn, then finishes with text."""

    def __init__(self) -> None:
        self._call_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def chat(self, messages, tools=None):
        from matmaster.types.messages import LLMResponse

        return LLMResponse(content='unused', finish_reason='stop')

    async def chat_stream(
        self, messages, tools=None, *, timeout=None
    ) -> AsyncIterator[StreamChunk]:
        self._call_count += 1
        if self._call_count == 1:
            yield StreamChunk(
                tool_call_deltas=[
                    {
                        'index': 0,
                        'id': 'tc-1',
                        'name': 'Bash',
                        'arguments': '{"command": "echo hello"}',
                    }
                ],
            )
            yield StreamChunk(finish_reason='stop')
        else:
            yield StreamChunk(
                content='Done! I executed the command.', finish_reason='stop'
            )


# -- Helpers --


def _make_runner(
    tmp_path: Path,
    provider: Any = None,
    stream_hook: DevStreamHook | None = None,
) -> DevRunner:
    """Build a DevRunner with mocked session."""
    workdir = tmp_path / 'workspace'
    workdir.mkdir(exist_ok=True)
    config = DevConfig()

    with patch('matmaster.devshell.runner.DevRunner._create_session') as mock_session:
        mock_session.return_value = create_autospec(Session, instance=True)
        return DevRunner(
            config=config,
            workdir=workdir,
            llm_provider=provider or SimpleProvider(),
            stream_hook=stream_hook,
        )


# -- Tests --


class TestDevShellIntegration:
    """End-to-end integration: DevRunner -> Exp -> AgentKernel."""

    def test_full_run_with_tool_call(self, tmp_path: Path) -> None:
        """Tool-calling provider triggers tool, result propagates correctly.

        Note: DevShell uses kernel.run() (not run_stream()), and FullToolRunner
        does not dispatch hook callbacks for pre_tool_call/post_tool_call (D-01).
        Tool call events are only available via the generator path. DevStreamHook
        captures stream content and segment completions, but not tool call text.
        """
        output = io.StringIO()
        stream_hook = DevStreamHook(output=output)
        runner = _make_runner(
            tmp_path,
            provider=ToolCallingProvider(),
            stream_hook=stream_hook,
        )

        observer = DevEventObserver()
        log_file = tmp_path / 'events.jsonl'
        event_logger = EventLogger(log_file, run_id='run-001')

        result = runner.run('echo hello', event_observer=observer)

        # Drain observer into event logger
        for event in observer.drain():
            event_logger.log_event(event)
        event_logger.close()

        # Verify result (tool call happened because provider returns text on 2nd call)
        assert result.reason == 'natural'
        assert result.final_content == 'Done! I executed the command.'

        # Verify terminal output contains the final response
        terminal_output = output.getvalue()
        assert 'Done! I executed the command.' in terminal_output

        # Verify history accumulated
        assert len(runner.history) > 0

        # Verify event log file was written with run_result
        assert log_file.exists()
        log_content = log_file.read_text()
        assert 'run_result' in log_content

    def test_multi_turn_history(self, tmp_path: Path) -> None:
        """Multi-turn conversation accumulates history correctly."""
        runner = _make_runner(tmp_path, provider=SimpleProvider())

        runner.run('first')
        assert len(runner.history) == 2  # UserMessage + AssistantMessage

        runner.run('second')
        assert len(runner.history) == 4  # 2 turns * (User + Assistant)

        runner.run('third')
        assert len(runner.history) == 6  # 3 turns * (User + Assistant)

    def test_stream_hook_captures_content(self, tmp_path: Path) -> None:
        """DevStreamHook writes LLM content to output stream."""
        output = io.StringIO()
        stream_hook = DevStreamHook(output=output)
        runner = _make_runner(
            tmp_path, provider=SimpleProvider(), stream_hook=stream_hook
        )

        runner.run('hello')

        terminal_output = output.getvalue()
        assert 'Reply to msg' in terminal_output

    def test_observer_collects_events(self, tmp_path: Path) -> None:
        """DevEventObserver collects events via hook callbacks."""
        runner = _make_runner(tmp_path, provider=SimpleProvider())

        observer = DevEventObserver()
        runner.run('test', event_observer=observer)

        events = observer.drain()
        event_types = [getattr(e, 'type', None) for e in events]
        # Observer hook emits response events for visible content segments
        assert 'response' in event_types

    def test_cancelled_run_does_not_accumulate_history(self, tmp_path: Path) -> None:
        """Cancelled runs should not add messages to history."""
        runner = _make_runner(tmp_path, provider=SimpleProvider())

        controller = CancellationController()
        controller.cancel()
        result = runner.run('should cancel', cancel_token=controller.token)

        assert result.reason == 'cancelled'
        assert len(runner.history) == 0

    def test_run_without_observer_works(self, tmp_path: Path) -> None:
        """Run without observer still works (observer is optional)."""
        runner = _make_runner(tmp_path, provider=SimpleProvider())

        result = runner.run('hello')
        assert result.reason == 'natural'
