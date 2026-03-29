"""Hook Protocol, BaseHook defaults, HookAction enum, and run_* helper functions.

Hooks allow external code to observe and intercept the kernel execution loop.
Seven hook points are defined:

- pre_tool_call: intercept before tool execution (CONTINUE/SKIP)
- post_tool_call: observe after tool execution (no return)
- pre_llm_call: observe before LLM call (no return)
- should_continue: intercept loop continuation (True/False)
- on_stream_chunk: observe streaming chunks (no return)
- on_segment_complete: observe completed logical thought/response segments
- on_guard_blocked: observe guard denials (no return)

Intercepting hooks (pre_tool_call, should_continue) short-circuit on the first
non-default return. Observation hooks (post_tool_call, pre_llm_call,
on_stream_chunk, on_segment_complete, on_guard_blocked) execute all hooks
without short-circuit.

EventEmitterHook bridges hook events to the MessageBus for SSE delivery.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from matmaster.tools.tool_result import ToolResult
from matmaster.types.events import (
    ResponseEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from matmaster.types.guards import GuardResult
from matmaster.types.messages import Message, StreamChunk, ToolCallData

if TYPE_CHECKING:
    from matmaster.core.bus import MessageBus


class HookAction(enum.Enum):
    """Action returned by intercepting hooks."""

    CONTINUE = "continue"
    SKIP = "skip"


@runtime_checkable
class Hook(Protocol):
    """Hook interface for observing and intercepting kernel execution.

    All hook methods are async. Use BaseHook as a base class
    to get default implementations for all hook points.
    """

    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction: ...

    async def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None: ...

    async def pre_llm_call(self, messages: list[Message], turn: int) -> None: ...

    async def should_continue(self, messages: list[Message], turn: int) -> bool: ...

    async def on_stream_chunk(self, chunk: StreamChunk) -> None: ...

    async def on_segment_complete(
        self, segment_type: str, content: str, stream_id: str | None
    ) -> None: ...

    async def on_guard_blocked(self, tool_call: ToolCallData, result: GuardResult) -> None: ...


class BaseHook:
    """Default hook implementation -- all methods are async no-ops or return defaults.

    Subclass and override specific methods to customize behavior.
    """

    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        """Default: allow tool call to proceed."""
        return HookAction.CONTINUE

    async def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None:
        """Default: no-op observation."""

    async def pre_llm_call(self, messages: list[Message], turn: int) -> None:
        """Default: no-op observation."""

    async def should_continue(self, messages: list[Message], turn: int) -> bool:
        """Default: continue execution."""
        return True

    async def on_stream_chunk(self, chunk: StreamChunk) -> None:
        """Default: no-op observation."""

    async def on_segment_complete(
        self, segment_type: str, content: str, stream_id: str | None
    ) -> None:
        """Default: no-op observation."""

    async def on_guard_blocked(self, tool_call: ToolCallData, result: GuardResult) -> None:
        """Default: no-op observation."""


# ── run_* helper functions ────────────────────────────


async def run_pre_tool_call(hooks: list[Hook], tool_call: ToolCallData) -> HookAction:
    """Run pre_tool_call on all hooks with short-circuit on SKIP.

    If any hook returns SKIP, immediately return SKIP without calling
    remaining hooks. If all return CONTINUE, return CONTINUE.
    """
    for hook in hooks:
        action = await hook.pre_tool_call(tool_call)
        if action == HookAction.SKIP:
            return HookAction.SKIP
    return HookAction.CONTINUE


async def run_should_continue(
    hooks: list[Hook], messages: list[Message], turn: int
) -> bool:
    """Run should_continue on all hooks with short-circuit on False.

    If any hook returns False, immediately return False without calling
    remaining hooks. If all return True, return True.
    """
    for hook in hooks:
        if not await hook.should_continue(messages, turn):
            return False
    return True


async def run_pre_llm_call(
    hooks: list[Hook], messages: list[Message], turn: int
) -> None:
    """Run pre_llm_call on all hooks (observation, no short-circuit)."""
    for hook in hooks:
        await hook.pre_llm_call(messages, turn)


async def run_post_tool_call(
    hooks: list[Hook], tool_call: ToolCallData, result: ToolResult
) -> None:
    """Run post_tool_call on all hooks (observation, no short-circuit)."""
    for hook in hooks:
        await hook.post_tool_call(tool_call, result)


async def run_on_stream_chunk(hooks: list[Hook], chunk: StreamChunk) -> None:
    """Run on_stream_chunk on all hooks (observation, no short-circuit)."""
    for hook in hooks:
        await hook.on_stream_chunk(chunk)


async def run_on_segment_complete(
    hooks: list[Hook], segment_type: str, content: str, stream_id: str | None
) -> None:
    """Run on_segment_complete on all hooks (observation, no short-circuit)."""
    for hook in hooks:
        await hook.on_segment_complete(segment_type, content, stream_id)


async def run_guard_blocked(
    hooks: list[Hook], tool_call: ToolCallData, result: GuardResult
) -> None:
    """Run on_guard_blocked on all hooks (observation, no short-circuit)."""
    for hook in hooks:
        await hook.on_guard_blocked(tool_call, result)


# ── EventEmitterHook ──────────────────────────────────


class EventEmitterHook(BaseHook):
    """Hook that emits kernel events to the MessageBus for SSE delivery.

    Bridges hook calls to BusEvent types:
    - pre_tool_call -> ToolCallEvent (returns CONTINUE)
    - post_tool_call -> ToolResultEvent
    - on_stream_chunk -> ThoughtEvent / ResponseEvent
    - on_segment_complete -> persisted ThoughtEvent / ResponseEvent snapshot

    """

    def __init__(
        self,
        bus: MessageBus,
        source: str,
        *,
        spawn_id: str | None = None,
    ) -> None:
        self._bus = bus
        self._source = source
        self._spawn_id = spawn_id

    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        """Emit ToolCallEvent and continue execution."""
        await self._bus.emit(
            ToolCallEvent(
                source=self._source,
                spawn_id=self._spawn_id,
                call_id=tool_call.id,
                tool_name=tool_call.name,
                arguments=tool_call.arguments,
            )
        )
        return HookAction.CONTINUE

    async def post_tool_call(self, tool_call: ToolCallData, result: ToolResult) -> None:
        """Emit ToolResultEvent after tool execution."""
        await self._bus.emit(
            ToolResultEvent(
                source=self._source,
                spawn_id=self._spawn_id,
                call_id=tool_call.id,
                tool_name=tool_call.name,
                result=result.content,
                status=result.status,
                info=result.info,
            )
        )

    async def on_stream_chunk(self, chunk: StreamChunk) -> None:
        """Emit ThoughtEvent for reasoning and ResponseEvent for visible content."""
        if chunk.reasoning_content:
            await self._bus.emit(
                ThoughtEvent(
                    source=self._source,
                    spawn_id=self._spawn_id,
                    content=chunk.reasoning_content,
                    stream_state=chunk.stream_state,
                    stream_id=chunk.stream_id,
                    reasoning_content=chunk.reasoning_content,
                )
            )
        if chunk.content:
            await self._bus.emit(
                ResponseEvent(
                    source=self._source,
                    spawn_id=self._spawn_id,
                    content=chunk.content,
                    stream_state=chunk.stream_state,
                    stream_id=chunk.stream_id,
                )
            )

    async def on_segment_complete(
        self, segment_type: str, content: str, stream_id: str | None
    ) -> None:
        """Emit a persisted snapshot when a logical segment is complete."""
        if segment_type == "thought":
            await self._bus.emit(
                ThoughtEvent(
                    source=self._source,
                    spawn_id=self._spawn_id,
                    content=content,
                    stream_state="complete",
                    stream_id=stream_id,
                    reasoning_content=content,
                )
            )
            return

        if segment_type == "response":
            await self._bus.emit(
                ResponseEvent(
                    source=self._source,
                    spawn_id=self._spawn_id,
                    content=content,
                    stream_state="complete",
                    stream_id=stream_id,
                )
            )

    async def on_guard_blocked(self, tool_call: ToolCallData, result: GuardResult) -> None:
        """Guard blocks are not emitted to the bus by default."""
