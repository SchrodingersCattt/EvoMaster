"""Shared mocks and builders for AgentKernel tests.

Not a test module: helpers only (see .pre-commit-config name-tests-test exclude).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from matmaster.tools.tool_catalog import ToolCatalog
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.tools.tool_result import ToolResult
from matmaster.types.errors import LLMError
from matmaster.types.messages import (
    LLMResponse,
    StreamChunk,
    ToolCallData,
)
from matmaster.types.runtime import AgentRuntimeSpec
from matmaster.types.topology import ToolPlane

from .conftest import MockLLMProvider


class _CatchAllTool:
    """Tool that accepts any name and records calls for test assertions."""

    def __init__(self, result: str = 'tool result') -> None:
        self._result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.resource_claims = ()
        self.capabilities = frozenset()
        self.effect_level = "none"
        self.fast_path_eligible = True
        self.max_result_chars = 12000
        self.plane = ToolPlane.CONTROL_PLANE
        self.state_mode = "stateless"
        self.stop_mode = "cancellable"
        self.exposed_to_model = True

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return 'catch-all test tool'

    @property
    def json_schema(self) -> dict[str, Any]:
        return {'type': 'object', 'properties': {}}

    def describe(self, ctx: Any) -> str:
        return self.description

    def prompt(self, ctx: Any | None = None) -> str | None:
        return None

    async def execute(self, arguments: dict[str, Any]) -> str:
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

    async def __aenter__(self) -> StreamingProvider:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content='not used', finish_reason='stop')

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        for chunk in self._chunks:
            yield chunk


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

    async def __aenter__(self) -> ToolCallingProvider:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content='not used', finish_reason='stop')

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        self._call_count += 1
        if self._call_count <= self._max_tool_turns:
            for i, tc in enumerate(self._tool_calls):
                yield StreamChunk(
                    tool_call_deltas=[
                        {
                            'index': i,
                            'id': tc.id,
                            'name': tc.name,
                            'arguments': str(tc.arguments).replace("'", '"'),
                        }
                    ],
                )
            yield StreamChunk(finish_reason='stop')
        else:
            yield StreamChunk(content=self._final_content, finish_reason='stop')


class _SimpleTestToolRunner:
    """Minimal ToolRunner for kernel tests."""

    def __init__(self, catalog: ToolCatalog) -> None:
        self._catalog = catalog

    async def execute_batch(
        self,
        tool_calls: list[ToolCallData],
        ctx: Any,
        *,
        on_result: Any = None,
    ) -> list[tuple[ToolCallData, ToolResult]]:
        from matmaster.tools.tool_result import normalize_tool_result

        results: list[tuple[ToolCallData, ToolResult]] = []
        for tc in tool_calls:
            # Cancel check
            if ctx.cancel_token is not None and ctx.cancel_token.is_cancelled:
                tr = ToolResult(status="cancelled", content="Run cancelled.")
                results.append((tc, tr))
                if on_result:
                    await on_result(tc, tr)
                continue

            # Execute
            raw_tool = self._catalog.registry._tools.get(tc.name)
            if raw_tool is None:
                available = ", ".join(sorted(self._catalog.registry._tools))
                tr = ToolResult(
                    status="error",
                    content=f"Error: Tool '{tc.name}' not found. Available: {available}",
                )
            else:
                try:
                    raw = await raw_tool.execute(tc.arguments)
                    tr = normalize_tool_result(raw)
                except Exception as e:
                    tr = ToolResult.from_error(tc.name, e)

            results.append((tc, tr))

            if on_result:
                await on_result(tc, tr)
        return results


def _make_spec(
    *,
    provider: Any | None = None,
    tool_registry: ToolRegistry | None = None,
    max_turns: int = 10,
    system_prompt: str = 'You are a test agent',
) -> AgentRuntimeSpec:
    if tool_registry is None:
        tool_registry, _ = _make_tool_registry()
    catalog = ToolCatalog(tool_registry)
    runner = _SimpleTestToolRunner(catalog)
    return AgentRuntimeSpec(
        llm_provider=provider or MockLLMProvider(),
        tool_catalog=catalog,
        tool_runner=runner,
        runtime_topology=catalog._topology,
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

    async def __aenter__(self) -> ErrorThenSuccessProvider:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        return LLMResponse(content='not used', finish_reason='stop')

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        timeout: float | None = None,
    ) -> AsyncIterator[StreamChunk]:
        self._call_count += 1
        if self._call_count <= self._fail_count:
            raise self._error
        yield StreamChunk(content='recovered', finish_reason='stop')
