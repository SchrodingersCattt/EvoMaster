"""Hook Protocol, BaseHook defaults, HookAction enum, and run_* helper functions.

Hooks allow external code to observe and intercept the kernel execution loop.
Five hook points are defined:

- pre_tool_call: intercept before tool execution (CONTINUE/SKIP)
- post_tool_call: observe after tool execution (no return)
- pre_llm_call: observe before LLM call (no return)
- should_continue: intercept loop continuation (True/False)
- on_stream_chunk: observe streaming chunks (no return)

Intercepting hooks (pre_tool_call, should_continue) short-circuit on the first
non-default return. Observation hooks (post_tool_call, pre_llm_call,
on_stream_chunk) execute all hooks without short-circuit.

EventEmitterHook bridges hook events to the MessageBus for SSE delivery.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from matmaster.types.events import ThoughtEvent, ToolCallEvent, ToolResultEvent
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

    All five methods must be implemented. Use BaseHook as a base class
    to get default implementations for all hook points.
    """

    def pre_tool_call(self, tool_call: ToolCallData) -> HookAction: ...

    def post_tool_call(self, tool_call: ToolCallData, result: str) -> None: ...

    def pre_llm_call(self, messages: list[Message], turn: int) -> None: ...

    def should_continue(self, messages: list[Message], turn: int) -> bool: ...

    def on_stream_chunk(self, chunk: StreamChunk) -> None: ...


class BaseHook:
    """Default hook implementation -- all methods are no-ops or return defaults.

    Subclass and override specific methods to customize behavior.
    """

    def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        """Default: allow tool call to proceed."""
        return HookAction.CONTINUE

    def post_tool_call(self, tool_call: ToolCallData, result: str) -> None:
        """Default: no-op observation."""

    def pre_llm_call(self, messages: list[Message], turn: int) -> None:
        """Default: no-op observation."""

    def should_continue(self, messages: list[Message], turn: int) -> bool:
        """Default: continue execution."""
        return True

    def on_stream_chunk(self, chunk: StreamChunk) -> None:
        """Default: no-op observation."""


# ── run_* helper functions ────────────────────────────


def run_pre_tool_call(hooks: list[Hook], tool_call: ToolCallData) -> HookAction:
    """Run pre_tool_call on all hooks with short-circuit on SKIP.

    If any hook returns SKIP, immediately return SKIP without calling
    remaining hooks. If all return CONTINUE, return CONTINUE.
    """
    for hook in hooks:
        action = hook.pre_tool_call(tool_call)
        if action == HookAction.SKIP:
            return HookAction.SKIP
    return HookAction.CONTINUE


def run_should_continue(
    hooks: list[Hook], messages: list[Message], turn: int
) -> bool:
    """Run should_continue on all hooks with short-circuit on False.

    If any hook returns False, immediately return False without calling
    remaining hooks. If all return True, return True.
    """
    for hook in hooks:
        if not hook.should_continue(messages, turn):
            return False
    return True


def run_pre_llm_call(
    hooks: list[Hook], messages: list[Message], turn: int
) -> None:
    """Run pre_llm_call on all hooks (observation, no short-circuit)."""
    for hook in hooks:
        hook.pre_llm_call(messages, turn)


def run_post_tool_call(
    hooks: list[Hook], tool_call: ToolCallData, result: str
) -> None:
    """Run post_tool_call on all hooks (observation, no short-circuit)."""
    for hook in hooks:
        hook.post_tool_call(tool_call, result)


def run_on_stream_chunk(hooks: list[Hook], chunk: StreamChunk) -> None:
    """Run on_stream_chunk on all hooks (observation, no short-circuit)."""
    for hook in hooks:
        hook.on_stream_chunk(chunk)


# ── EventEmitterHook ──────────────────────────────────


class EventEmitterHook(BaseHook):
    """Hook that emits kernel events to the MessageBus for SSE delivery.

    Bridges hook calls to BusEvent types:
    - pre_tool_call -> ToolCallEvent (returns CONTINUE)
    - post_tool_call -> ToolResultEvent
    - on_stream_chunk -> ThoughtEvent
    """

    def __init__(self, bus: MessageBus, source: str) -> None:
        self._bus = bus
        self._source = source

    def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        """Emit ToolCallEvent and continue execution."""
        self._bus.emit(
            ToolCallEvent(
                source=self._source,
                call_id=tool_call.id,
                tool_name=tool_call.name,
                arguments=tool_call.arguments,
            )
        )
        return HookAction.CONTINUE

    def post_tool_call(self, tool_call: ToolCallData, result: str) -> None:
        """Emit ToolResultEvent after tool execution."""
        self._bus.emit(
            ToolResultEvent(
                source=self._source,
                call_id=tool_call.id,
                tool_name=tool_call.name,
                result=result,
            )
        )

    def on_stream_chunk(self, chunk: StreamChunk) -> None:
        """Emit ThoughtEvent for each streaming chunk."""
        self._bus.emit(
            ThoughtEvent(
                source=self._source,
                content=chunk.content or "",
                stream_state=chunk.stream_state,
                stream_id=chunk.stream_id,
                reasoning_content=chunk.reasoning_content,
            )
        )
