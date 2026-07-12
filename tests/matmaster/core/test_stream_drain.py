from __future__ import annotations

import asyncio

import pytest

from matmaster.core.stream_drain import drain_run_stream
from matmaster.types.events import FinishDetail, ResponseEvent, RunResultEvent


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


@pytest.mark.asyncio
async def test_drain_run_stream_copies_finish_detail() -> None:
    detail = FinishDetail(
        kind="output_length_exceeded",
        provider_finish_reason="length",
        message="Model output was truncated by the provider output-token limit.",
    )

    async def stream():
        yield RunResultEvent(
            source="agent",
            status="failed",
            reason="invalid_finish",
            finish_detail=detail,
        )

    result = await drain_run_stream(stream())

    assert result.finish_detail is detail


@pytest.mark.asyncio
async def test_drain_run_stream_closes_stream_before_returning() -> None:
    # 终止事件触发提前 return 时,生成器必须在 drain 返回前于当前任务内被
    # aclose;否则会留给事件循环的 GC finalizer 在新 Context 里收尾,导致
    # 生成器内 ContextVar token reset 跨 Context 报错(billing_scope 场景)。
    closed = False

    async def stream():
        nonlocal closed
        try:
            yield ResponseEvent(source="agent", content="child")
            yield RunResultEvent(source="agent", status="completed", reason="natural")
        finally:
            closed = True

    result = await drain_run_stream(stream())

    assert result.status == "completed"
    assert closed


@pytest.mark.asyncio
async def test_drain_run_stream_forward_terminal_forwards_run_result_last() -> None:
    seen: list[str] = []

    async def stream():
        yield ResponseEvent(source="agent", content="child")
        yield RunResultEvent(
            source="agent",
            status="completed",
            reason="natural",
            final_content="done",
        )

    async def on_event(event) -> None:
        seen.append(type(event).__name__)

    result = await drain_run_stream(stream(), on_event=on_event, forward_terminal=True)

    assert seen == ["ResponseEvent", "RunResultEvent"]
    assert result.final_content == "done"
