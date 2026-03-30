"""AgentKernel tests: tool errors, usage, compactor, kernel result fields, LLM retry."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from matmaster.tools.tool_registry import ToolRegistry
from matmaster.types.errors import LLMError
from matmaster.types.messages import (
    LLMResponse,
    StreamChunk,
    ToolCallData,
    UserMessage,
)
from matmaster.types.runtime import AgentRuntimeSpec

from .agent_kernel_test_helpers import (
    ErrorThenSuccessProvider,
    SegmentRecordingHook,
    StopHook,
    StreamingProvider,
    ToolCallingProvider,
    _make_spec,
    _make_tool_registry,
)


class TestToolExecutionException:
    """Tool that raises exception -> error ToolMessage, run continues."""

    def test_tool_exception_becomes_error_message(self) -> None:
        from matmaster.core.agent import AgentKernel

        class ExplodingTool:
            @property
            def name(self) -> str:
                return 'boom'

            @property
            def description(self) -> str:
                return 'explodes'

            @property
            def json_schema(self) -> dict[str, Any]:
                return {'type': 'object', 'properties': {}}

            def execute(self, arguments: dict[str, Any]) -> str:
                raise RuntimeError('kaboom!')

        registry = ToolRegistry()
        registry.register(ExplodingTool(), source='test')

        tc = ToolCallData(id='tc-1', name='boom', arguments={})
        provider = ToolCallingProvider(
            tool_calls=[tc], max_tool_turns=1, final_content='recovered'
        )
        spec = _make_spec(provider=provider, tool_registry=registry, max_turns=5)
        kernel = AgentKernel()
        result = kernel.run(spec, 'test')

        assert result.result.reason == 'natural'
        assert result.result.final_content == 'recovered'


class TestCallLlmUsageCapture:
    """_call_llm captures usage from StreamChunk into LLMResponse."""

    def test_usage_captured_from_stream(self) -> None:
        from matmaster.core.agent import AgentKernel

        usage_data = {
            'prompt_tokens': 500,
            'completion_tokens': 100,
            'total_tokens': 600,
        }

        class UsageProvider:
            def chat(self, messages, tools=None):
                return LLMResponse(content='unused', finish_reason='stop')

            def chat_with_retry(
                self,
                messages,
                tools=None,
                *,
                max_retries=3,
                retry_delay=1.0,
            ):
                return self.chat(messages, tools)

            def chat_stream(
                self, messages, tools=None, *, timeout: float | None = None
            ):
                yield StreamChunk(content='hello')
                yield StreamChunk(finish_reason='stop', usage=usage_data)

        spec = _make_spec(provider=UsageProvider())
        kernel = AgentKernel()
        result = kernel.run(spec, 'test')

        assert result.result.reason == 'natural'
        response = kernel._call_llm(spec, [UserMessage(content='test')])
        assert response.usage == usage_data

    def test_segment_complete_hooks_run_for_reasoning_and_response(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider(
            [
                StreamChunk(reasoning_content='think '),
                StreamChunk(content='answer'),
                StreamChunk(finish_reason='stop'),
            ]
        )
        segment_hook = SegmentRecordingHook()
        spec = _make_spec(provider=provider, hooks=[segment_hook])
        kernel = AgentKernel()

        response = kernel._call_llm(spec, [UserMessage(content='test')])

        assert response.reasoning_content == 'think '
        assert response.content == 'answer'
        assert segment_hook.segments == [
            ('thought', 'think ', 'turn-1'),
            ('response', 'answer', 'turn-1'),
        ]


class TestCompactorIntegration:
    """Kernel calls compactor.compact_if_needed and update_message_count."""

    def test_compactor_called_each_turn(self) -> None:
        from matmaster.core.agent import AgentKernel

        call_log: list[tuple[int, int]] = []

        class SpyCompactor:
            _last_llm_message_count = 0

            def compact_if_needed(self, messages, last_usage, turn):
                call_log.append((len(messages), turn))

            def update_message_count(self, count):
                self._last_llm_message_count = count

        tc = ToolCallData(id='tc-1', name='tool', arguments={})
        provider = ToolCallingProvider(
            tool_calls=[tc], max_tool_turns=2, final_content='done'
        )
        tool_reg, _ = _make_tool_registry(['tool'])
        spec = _make_spec(provider=provider, tool_registry=tool_reg, max_turns=10)
        spec = spec.model_copy(update={'compactor': SpyCompactor()})

        kernel = AgentKernel()
        result = kernel.run(spec, 'test')

        assert result.result.reason == 'natural'
        assert len(call_log) == 3
        assert [turn for _, turn in call_log] == [1, 2, 3]

    def test_last_usage_passed_to_compactor(self) -> None:
        from matmaster.core.agent import AgentKernel

        usage_log: list[dict] = []

        class UsageSpyCompactor:
            _last_llm_message_count = 0

            def compact_if_needed(self, messages, last_usage, turn):
                usage_log.append(dict(last_usage))

            def update_message_count(self, count):
                self._last_llm_message_count = count

        usage_data = {
            'prompt_tokens': 500,
            'completion_tokens': 100,
            'total_tokens': 600,
        }

        class UsageTrackingProvider:
            def chat(self, messages, tools=None):
                return LLMResponse(content='unused', finish_reason='stop')

            def chat_with_retry(
                self,
                messages,
                tools=None,
                *,
                max_retries=3,
                retry_delay=1.0,
            ):
                return self.chat(messages, tools)

            def chat_stream(
                self, messages, tools=None, *, timeout: float | None = None
            ):
                yield StreamChunk(
                    content='done', finish_reason='stop', usage=usage_data
                )

        spec = _make_spec(provider=UsageTrackingProvider())
        spec = spec.model_copy(update={'compactor': UsageSpyCompactor()})

        kernel = AgentKernel()
        kernel.run(spec, 'test')

        assert usage_log[0] == {}

    def test_no_compactor_no_error(self) -> None:
        from matmaster.core.agent import AgentKernel

        spec = _make_spec()
        assert spec.compactor is None
        kernel = AgentKernel()
        result = kernel.run(spec, 'test')
        assert result.result.reason == 'natural'


class TestKernelResultFields:
    """KernelResult carries num_turns, stop_reason, and last LLM-call usage."""

    def test_natural_finish_has_num_turns(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider(
            [
                StreamChunk(content='Hello', finish_reason='stop'),
            ]
        )
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        result = kernel.run(spec, 'test')

        assert result.result.num_turns == 1
        assert result.result.stop_reason == 'stop'

    def test_multi_turn_reports_last_usage_only(self) -> None:
        from matmaster.core.agent import AgentKernel

        class UsageTrackingToolProvider:
            def __init__(self) -> None:
                self._call_count = 0

            def chat(self, messages, tools=None):
                return LLMResponse(content='unused', finish_reason='stop')

            def chat_with_retry(
                self, messages, tools=None, *, max_retries=3, retry_delay=1.0
            ):
                return self.chat(messages, tools)

            def chat_stream(
                self, messages, tools=None, *, timeout: float | None = None
            ):
                self._call_count += 1
                if self._call_count == 1:
                    yield StreamChunk(
                        tool_call_deltas=[
                            {
                                'index': 0,
                                'id': 'tc-1',
                                'name': 'tool',
                                'arguments': '{}',
                            }
                        ],
                    )
                    yield StreamChunk(
                        finish_reason='stop',
                        usage={'prompt_tokens': 100, 'completion_tokens': 50},
                    )
                else:
                    yield StreamChunk(
                        content='done',
                        finish_reason='stop',
                        usage={'prompt_tokens': 200, 'completion_tokens': 30},
                    )

        tool_reg, _ = _make_tool_registry(['tool'])
        spec = _make_spec(provider=UsageTrackingToolProvider(), tool_registry=tool_reg)
        kernel = AgentKernel()
        result = kernel.run(spec, 'test')

        assert result.result.num_turns == 2
        assert result.result.usage['prompt_tokens'] == 200
        assert result.result.usage['completion_tokens'] == 30

    def test_max_turns_has_correct_num_turns(self) -> None:
        from matmaster.core.agent import AgentKernel

        tc = ToolCallData(id='tc-1', name='some_tool', arguments={'x': 1})
        provider = ToolCallingProvider(tool_calls=[tc], max_tool_turns=999)
        spec = _make_spec(provider=provider, max_turns=3)
        kernel = AgentKernel()
        result = kernel.run(spec, 'test')

        assert result.result.reason == 'max_turns'
        assert result.result.num_turns == 3

    def test_cancelled_has_zero_turns_when_immediate(self) -> None:
        from matmaster.core.agent import AgentKernel

        stop_event = threading.Event()
        stop_event.set()
        spec = _make_spec()
        kernel = AgentKernel()
        result = kernel.run(spec, 'test', stop_event=stop_event)

        assert result.result.reason == 'cancelled'
        assert result.result.num_turns == 0

    def test_hook_stopped_has_correct_num_turns(self) -> None:
        from matmaster.core.agent import AgentKernel

        spec = _make_spec(hooks=[StopHook()])
        kernel = AgentKernel()
        result = kernel.run(spec, 'test')

        assert result.result.reason == 'hook_stopped'
        # hook_stopped fires after turn += 1 but before LLM call
        assert result.result.num_turns == 0

    def test_invalid_finish_has_correct_fields(self) -> None:
        from matmaster.core.agent import AgentKernel

        provider = StreamingProvider(
            [
                StreamChunk(content='partial'),
                StreamChunk(finish_reason='length'),
            ]
        )
        spec = _make_spec(provider=provider)
        kernel = AgentKernel()
        result = kernel.run(spec, 'test')

        assert result.result.reason == 'invalid_finish'
        assert result.result.num_turns == 1
        assert result.result.stop_reason == 'length'


class TestCallLlmRetry:
    def test_retry_on_retryable_error(self) -> None:
        """_call_llm retries on retryable LLMError and succeeds."""
        provider = ErrorThenSuccessProvider(
            fail_count=1,
            error=LLMError('timeout', retryable=True),
        )
        spec = AgentRuntimeSpec(
            llm_provider=provider,
            system_prompt='test',
        )
        from matmaster.core.agent import AgentKernel

        kernel = AgentKernel()
        response = kernel._call_llm(spec, [UserMessage(content='hi')])
        assert response.content == 'recovered'
        assert provider._call_count == 2

    def test_no_retry_on_non_retryable_error(self) -> None:
        """_call_llm raises immediately on non-retryable LLMError."""
        provider = ErrorThenSuccessProvider(
            fail_count=1,
            error=LLMError('auth failed', retryable=False),
        )
        spec = AgentRuntimeSpec(
            llm_provider=provider,
            system_prompt='test',
        )
        from matmaster.core.agent import AgentKernel

        kernel = AgentKernel()
        with pytest.raises(LLMError, match='auth failed'):
            kernel._call_llm(spec, [UserMessage(content='hi')])
        assert provider._call_count == 1

    def test_all_retries_exhausted(self) -> None:
        """_call_llm raises RuntimeError after all retries exhausted."""
        provider = ErrorThenSuccessProvider(
            fail_count=99,
            error=LLMError('timeout', retryable=True),
        )
        spec = AgentRuntimeSpec(
            llm_provider=provider,
            system_prompt='test',
        )
        from matmaster.core.agent import AgentKernel

        kernel = AgentKernel()
        with pytest.raises(RuntimeError, match='LLM stream failed'):
            kernel._call_llm(spec, [UserMessage(content='hi')])
        assert provider._call_count == 3  # max_retries default

    def test_timeout_doubles_on_retry(self) -> None:
        """Each retry doubles the timeout passed to chat_stream."""
        timeouts_seen: list[float | None] = []

        class TimeoutTracker:
            stream_timeout = 10.0
            max_retries = 3
            retry_delay = 0.0

            def chat(self, messages, tools=None):
                return LLMResponse(content='', finish_reason='stop')

            def chat_with_retry(self, messages, tools=None, **kw):
                return self.chat(messages, tools)

            def chat_stream(self, messages, tools=None, *, timeout=None):
                timeouts_seen.append(timeout)
                if len(timeouts_seen) < 3:
                    raise LLMError('timeout', retryable=True)
                yield StreamChunk(content='ok', finish_reason='stop')

        spec = AgentRuntimeSpec(
            llm_provider=TimeoutTracker(),
            system_prompt='test',
        )
        from matmaster.core.agent import AgentKernel

        kernel = AgentKernel()
        kernel._call_llm(spec, [UserMessage(content='hi')])
        assert timeouts_seen == [10.0, 20.0, 40.0]
