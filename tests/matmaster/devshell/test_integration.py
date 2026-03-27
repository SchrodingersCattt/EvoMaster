"""Integration test: DevRunner -> Exp -> AgentKernel with mock LLM.

Smoke tests the full pipeline end-to-end: DevRunner assembles Exp,
Exp builds runtime with AgentKernel, kernel runs with mock providers,
DevStreamHook captures terminal output, EventLogger captures bus events.
"""

from __future__ import annotations

import io
import queue
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import MagicMock, patch

from matmaster.core.bus import MessageBus
from matmaster.devshell.config import DevConfig
from matmaster.devshell.event_logger import EventLogger
from matmaster.devshell.runner import DevRunner
from matmaster.devshell.stream_hook import DevStreamHook
from matmaster.types.messages import StreamChunk

# ── Mock Providers ──────────────────────────────────────


class SimpleProvider:
    """Mock that always returns a text reply (no tool calls)."""

    def chat(self, messages, tools=None):
        from matmaster.types.messages import LLMResponse

        return LLMResponse(content='unused', finish_reason='stop')

    def chat_with_retry(self, messages, tools=None, *, max_retries=3, retry_delay=1.0):
        return self.chat(messages, tools)

    def chat_stream(
        self, messages, tools=None, *, timeout=None
    ) -> Iterator[StreamChunk]:
        yield StreamChunk(
            content=f'Reply to msg #{len(messages)}', finish_reason='stop'
        )


class ToolCallingProvider:
    """Mock that calls a tool on first turn, then finishes with text."""

    def __init__(self) -> None:
        self._call_count = 0

    def chat(self, messages, tools=None):
        from matmaster.types.messages import LLMResponse

        return LLMResponse(content='unused', finish_reason='stop')

    def chat_with_retry(self, messages, tools=None, *, max_retries=3, retry_delay=1.0):
        return self.chat(messages, tools)

    def chat_stream(
        self, messages, tools=None, *, timeout=None
    ) -> Iterator[StreamChunk]:
        self._call_count += 1
        if self._call_count == 1:
            # Emit tool call delta (same format as agent_kernel tests)
            yield StreamChunk(
                tool_call_deltas=[
                    {
                        'index': 0,
                        'id': 'tc-1',
                        'name': 'bash',
                        'arguments': '{"command": "echo hello"}',
                    }
                ],
            )
            yield StreamChunk(finish_reason='stop')
        else:
            yield StreamChunk(
                content='Done! I executed the command.', finish_reason='stop'
            )


# ── Helpers ─────────────────────────────────────────────


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
        mock_session.return_value = MagicMock()
        return DevRunner(
            config=config,
            workdir=workdir,
            llm_provider=provider or SimpleProvider(),
            stream_hook=stream_hook,
        )


# ── Tests ───────────────────────────────────────────────


class TestDevShellIntegration:
    """End-to-end integration: DevRunner -> Exp -> AgentKernel."""

    def test_full_run_with_tool_call(self, tmp_path: Path) -> None:
        """Tool-calling provider triggers bash tool, DevStreamHook captures output."""
        output = io.StringIO()
        stream_hook = DevStreamHook(output=output)
        runner = _make_runner(
            tmp_path,
            provider=ToolCallingProvider(),
            stream_hook=stream_hook,
        )

        bus = MessageBus()
        log_file = tmp_path / 'events.jsonl'
        event_logger = EventLogger(log_file, run_id='run-001')

        result = runner.run('echo hello', bus=bus)

        # Drain bus into event logger
        while True:
            try:
                event = bus.get_nowait()
                event_logger.log_event(event)
            except queue.Empty:
                break
        event_logger.close()

        # Verify result
        assert result.result.reason == 'natural'
        assert result.result.final_content == 'Done! I executed the command.'

        # Verify terminal output contains tool call
        terminal_output = output.getvalue()
        assert 'tool_call: bash' in terminal_output

        # Verify history accumulated (UserMessage + AssistantMessage(tool_calls) + ToolMessage + AssistantMessage)
        assert len(runner.history) > 0

        # Verify event log file was written
        assert log_file.exists()
        log_content = log_file.read_text()
        assert 'tool_call' in log_content
        assert 'tool_result' in log_content

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
        # SimpleProvider returns "Reply to msg #N" -- content should appear
        assert 'Reply to msg' in terminal_output

    def test_bus_events_emitted(self, tmp_path: Path) -> None:
        """MessageBus receives events when bus is provided."""
        runner = _make_runner(tmp_path, provider=SimpleProvider())

        bus = MessageBus()
        runner.run('test', bus=bus)

        # Should have at least a RunResultEvent
        events = []
        while True:
            try:
                events.append(bus.get_nowait())
            except queue.Empty:
                break

        event_types = [getattr(e, 'type', None) for e in events]
        # EventEmitterHook emits response events for visible content chunks
        assert 'response' in event_types

    def test_cancelled_run_does_not_accumulate_history(self, tmp_path: Path) -> None:
        """Cancelled runs should not add messages to history."""
        import threading

        runner = _make_runner(tmp_path, provider=SimpleProvider())

        stop = threading.Event()
        stop.set()
        result = runner.run('should cancel', stop_event=stop)

        assert result.result.reason == 'cancelled'
        assert len(runner.history) == 0
