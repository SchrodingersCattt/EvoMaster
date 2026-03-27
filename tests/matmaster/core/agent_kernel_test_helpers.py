"""Shared mocks and builders for AgentKernel tests.

Not a test module: helpers only (see .pre-commit-config name-tests-test exclude).
"""

from __future__ import annotations

from typing import Any, Iterator

from matmaster.core.hooks import BaseHook, HookAction
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.tools.tool_result import ToolResult
from matmaster.types.errors import LLMError
from matmaster.types.guards import GuardContext, GuardResult
from matmaster.types.messages import (
    LLMResponse,
    Message,
    StreamChunk,
    ToolCallData,
)
from matmaster.types.runtime import AgentRuntimeSpec

from .conftest import MockLLMProvider


class _CatchAllTool:
    """Tool that accepts any name and records calls for test assertions."""

    def __init__(self, result: str = 'tool result') -> None:
        self._result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return 'catch-all test tool'

    @property
    def json_schema(self) -> dict[str, Any]:
        return {'type': 'object', 'properties': {}}

    def execute(self, arguments: dict[str, Any]) -> str:
        self.calls.append((self._name, arguments))
        return self._result


def _make_tool_registry(
    tool_names: list[str] | None = None,
    result: str = 'tool result',
) -> tuple[ToolRegistry, list[_CatchAllTool]]:
    """Create a ToolRegistry with named catch-all tools.

    Returns (registry, tools) so tests can inspect tool.calls.
    """
    registry = ToolRegistry()
    names = tool_names or [
        'test_tool',
        'some_tool',
        'bad_tool',
        'skip_me',
        'my_tool',
        'fn',
        'tool',
    ]
    tools: list[_CatchAllTool] = []
    for n in names:
        t = _CatchAllTool(result=result)
        t._name = n
        tools.append(t)
        registry.register(t, source='test')
    return registry, tools


class StreamingProvider:
    """Mock provider that yields specific StreamChunk sequences."""

    def __init__(self, chunks: list[StreamChunk]) -> None:
        self._chunks = chunks

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content='not used', finish_reason='stop')

    def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> LLMResponse:
        return self.chat(messages, tools)

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> Iterator[StreamChunk]:
        yield from self._chunks


class ToolCallingProvider:
    """Provider that returns tool_calls for N turns, then natural finish."""

    def __init__(
        self,
        tool_calls: list[ToolCallData],
        max_tool_turns: int = 999,
        final_content: str = 'done',
    ) -> None:
        self._tool_calls = tool_calls
        self._max_tool_turns = max_tool_turns
        self._final_content = final_content
        self._call_count = 0

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content='not used', finish_reason='stop')

    def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> LLMResponse:
        return self.chat(messages, tools)

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> Iterator[StreamChunk]:
        self._call_count += 1
        if self._call_count <= self._max_tool_turns:
            for tc in self._tool_calls:
                yield StreamChunk(
                    tool_call_deltas=[
                        {
                            'index': 0,
                            'id': tc.id,
                            'name': tc.name,
                            'arguments': str(tc.arguments).replace("'", '"'),
                        }
                    ],
                )
            yield StreamChunk(finish_reason='stop')
        else:
            yield StreamChunk(content=self._final_content, finish_reason='stop')


class DenyGuard:
    """Guard that denies a specific tool name."""

    def __init__(self, deny_name: str, reason: str = 'forbidden') -> None:
        self._deny_name = deny_name
        self._reason = reason

    def evaluate(self, ctx: GuardContext) -> GuardResult:
        if ctx.tool_name == self._deny_name:
            return GuardResult(
                allowed=False,
                reason=self._reason,
                guidance='stop',
            )
        return GuardResult(allowed=True)


class SkipHook(BaseHook):
    """Hook that returns SKIP for a specific tool name."""

    def __init__(self, skip_name: str) -> None:
        self._skip_name = skip_name

    def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        if tool_call.name == self._skip_name:
            return HookAction.SKIP
        return HookAction.CONTINUE


class StopHook(BaseHook):
    """Hook that returns False from should_continue."""

    def should_continue(self, messages: list[Message], turn: int) -> bool:
        return False


class RecordingHook(BaseHook):
    """Hook that records all method calls in order."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        self.calls.append('pre_tool_call')
        return HookAction.CONTINUE

    def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None:
        self.calls.append('post_tool_call')

    def pre_llm_call(self, messages: list[Message], turn: int) -> None:
        self.calls.append('pre_llm_call')

    def should_continue(self, messages: list[Message], turn: int) -> bool:
        self.calls.append('should_continue')
        return True

    def on_stream_chunk(self, chunk: StreamChunk) -> None:
        self.calls.append('on_stream_chunk')


class ChunkRecordingHook(BaseHook):
    """Hook that records stream chunks."""

    def __init__(self) -> None:
        self.chunks: list[StreamChunk] = []

    def on_stream_chunk(self, chunk: StreamChunk) -> None:
        self.chunks.append(chunk)


class SegmentRecordingHook(BaseHook):
    """Hook that records completed logical segments."""

    def __init__(self) -> None:
        self.segments: list[tuple[str, str, str | None]] = []

    def on_segment_complete(
        self, segment_type: str, content: str, stream_id: str | None
    ) -> None:
        self.segments.append((segment_type, content, stream_id))


def _make_spec(
    *,
    provider: Any | None = None,
    tool_registry: ToolRegistry | None = None,
    guards: list[Any] | None = None,
    hooks: list[Any] | None = None,
    max_turns: int = 10,
    system_prompt: str = 'You are a test agent',
) -> AgentRuntimeSpec:
    if tool_registry is None:
        tool_registry, _ = _make_tool_registry()
    return AgentRuntimeSpec(
        llm_provider=provider or MockLLMProvider(),
        tool_registry=tool_registry,
        guards=guards or [],
        hooks=hooks or [],
        max_turns=max_turns,
        system_prompt=system_prompt,
    )


class ErrorThenSuccessProvider:
    """Provider that raises LLMError N times, then succeeds."""

    def __init__(self, fail_count: int, error: LLMError) -> None:
        self._fail_count = fail_count
        self._error = error
        self._call_count = 0
        self.stream_timeout = 10.0
        self.max_retries = 3
        self.retry_delay = 0.0  # no sleep in tests

    def chat(self, messages, tools=None):
        return LLMResponse(content='not used', finish_reason='stop')

    def chat_with_retry(self, messages, tools=None, *, max_retries=3, retry_delay=1.0):
        return self.chat(messages, tools)

    def chat_stream(self, messages, tools=None, *, timeout=None):
        self._call_count += 1
        if self._call_count <= self._fail_count:
            raise self._error
        yield StreamChunk(content='recovered', finish_reason='stop')
