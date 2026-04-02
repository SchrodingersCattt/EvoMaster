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
"""

from __future__ import annotations

import enum
from typing import Protocol, runtime_checkable

from matmaster.tools.tool_result import ToolResult
from matmaster.types.guards import GuardResult
from matmaster.types.messages import Message, StreamChunk, ToolCallData


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

    async def post_tool_call(
        self, tool_call: ToolCallData, result: ToolResult
    ) -> None: ...

    async def pre_llm_call(self, messages: list[Message], turn: int) -> None: ...

    async def should_continue(self, messages: list[Message], turn: int) -> bool: ...

    async def on_stream_chunk(self, chunk: StreamChunk) -> None: ...

    async def on_segment_complete(
        self, segment_type: str, content: str, stream_id: str | None
    ) -> None: ...

    async def on_guard_blocked(
        self, tool_call: ToolCallData, result: GuardResult
    ) -> None: ...


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

    async def on_guard_blocked(
        self, tool_call: ToolCallData, result: GuardResult
    ) -> None:
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
