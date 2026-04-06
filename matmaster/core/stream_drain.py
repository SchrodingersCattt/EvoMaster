"""Shared drain helper for consuming run_stream() to completion."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DrainResult:
    """Structured result from draining a run_stream() to completion."""

    status: str
    reason: str
    final_content: str | None
    num_turns: int
    usage: dict[str, int]
    messages: list[Any]
    usage_vendor_by_turn: tuple[dict[str, Any], ...] = ()
    events: list[Any] = field(default_factory=list)


async def drain_run_stream(
    stream: AsyncIterator[Any],
    on_event: Callable[[Any], Any] | None = None,
) -> DrainResult:
    """Consume run_stream() to completion, return structured result.

    Args:
        stream: AsyncIterator from kernel.run_stream() or Exp.run_stream().
        on_event: Optional callback invoked for each intermediate event
            as it arrives. Use this for real-time forwarding (e.g. DevShell
            terminal output, event logging) without breaking the drain.

    Collects all intermediate events and extracts terminal RunResultEvent.
    Raises RuntimeError if stream ends without a terminal event.
    """
    from matmaster.types.events import RunResultEvent

    events: list[Any] = []
    async for event in stream:
        if isinstance(event, RunResultEvent):
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
                events=events,
            )
        events.append(event)
        if on_event is not None:
            result = on_event(event)
            if inspect.isawaitable(result):
                await result
    raise RuntimeError("run_stream ended without RunResultEvent")
