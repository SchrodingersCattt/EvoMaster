from __future__ import annotations

import asyncio

import pytest

from matmaster.core.stream_drain import drain_run_stream
from matmaster.types.events import ResponseEvent, RunResultEvent


@pytest.mark.asyncio
async def test_drain_run_stream_awaits_async_on_event_in_order() -> None:
    seen: list[str] = []

    async def stream():
        yield ResponseEvent(source="agent", content="a")
        yield ResponseEvent(source="agent", content="b")
        yield RunResultEvent(source="agent", status="completed", reason="natural")

    async def on_event(event) -> None:
        await asyncio.sleep(0)
        seen.append(event.content)

    await drain_run_stream(stream(), on_event=on_event)

    assert seen == ["a", "b"]


@pytest.mark.asyncio
async def test_drain_run_stream_keeps_sync_on_event_compatibility() -> None:
    seen: list[str] = []

    async def stream():
        yield ResponseEvent(source="agent", content="child")
        yield RunResultEvent(source="agent", status="completed", reason="natural")

    def on_event(event) -> None:
        seen.append(event.content)

    await drain_run_stream(stream(), on_event=on_event)

    assert seen == ["child"]


@pytest.mark.asyncio
async def test_drain_run_stream_does_not_forward_terminal_run_result() -> None:
    seen: list[str] = []

    async def stream():
        yield ResponseEvent(source="agent", content="child")
        yield RunResultEvent(
            source="agent",
            status="completed",
            reason="natural",
            final_content="done",
        )

    def on_event(event) -> None:
        seen.append(type(event).__name__)

    result = await drain_run_stream(stream(), on_event=on_event)

    assert seen == ["ResponseEvent"]
    assert result.final_content == "done"
