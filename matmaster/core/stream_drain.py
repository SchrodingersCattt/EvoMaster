"""Shared drain helper for consuming run_stream() to completion."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable
from typing import Any

from matmaster.types.stream_drain import DrainResult


async def drain_run_stream(
    stream: AsyncIterator[Any],
    on_event: Callable[[Any], Any] | None = None,
    *,
    forward_terminal: bool = False,
) -> DrainResult:
    """Consume run_stream() to completion, return structured result.

    Args:
        stream: AsyncIterator from kernel.run_stream() or Exp.run_stream().
        on_event: Optional callback invoked for each intermediate event
            as it arrives. Use this for real-time forwarding (e.g. DevShell
            terminal output, event logging) without breaking the drain.
        forward_terminal: When True, the terminal RunResultEvent is also
            passed to ``on_event`` before returning, so sinks that mirror
            the stream (subagent fanout) receive the closing signal.

    Collects all intermediate events and extracts terminal RunResultEvent.
    Raises RuntimeError if stream ends without a terminal event.
    """
    from matmaster.types.events import RunResultEvent

    events: list[Any] = []
    async for event in stream:
        if isinstance(event, RunResultEvent):
            if forward_terminal and on_event is not None:
                result = on_event(event)
                if inspect.isawaitable(result):
                    await result
            return DrainResult(
                status=event.status,
                reason=event.reason,
                final_content=event.final_content,
                num_turns=event.num_turns,
                usage=event.usage,
                usage_vendor_by_turn=tuple(
                    dict(item) for item in (event.usage_vendor_by_turn or [])
                ),
                messages=event.messages,
                finish_detail=event.finish_detail,
                events=events,
            )
        events.append(event)
        if on_event is not None:
            result = on_event(event)
            if inspect.isawaitable(result):
                await result
    raise RuntimeError("run_stream ended without RunResultEvent")
