from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from matmaster.types.events import ResponseEvent, ToolResultEvent
from src.services.figure_coordinator import FigureCoordinator


class _Fanout:
    def __init__(self, *, dispatch_result: bool = True) -> None:
        self.dispatch_result = dispatch_result
        self.events = []
        self.flush_persistence_barrier = AsyncMock()

    async def dispatch(self, event):
        self.events.append(event)

    async def dispatch_and_wait_persistence(self, event):
        self.events.append(event)
        return self.dispatch_result


def _tool_result(*, spawn_id: str | None = None) -> ToolResultEvent:
    return ToolResultEvent(
        source="MatMaster",
        spawn_id=spawn_id,
        call_id="call-band",
        tool_name="Bash",
        result="done",
        payload={
            "figures": [
                {
                    "figure_id": "band",
                    "asset_url": "https://oss.example/band.png",
                    "caption": "band",
                    "importance": "primary",
                    "placement_hint": "sidebar_only",
                }
            ]
        },
    )


@pytest.mark.asyncio
async def test_record_tool_result_flushes_dirty_snapshot_after_barrier() -> None:
    fanout = _Fanout()
    coordinator = FigureCoordinator(
        fanout=fanout,
        session_id="sess-1",
        task_id="task-1",
    )

    await coordinator.record_tool_result(
        _tool_result(),
        include_spawned=False,
        reason="tool_result",
    )

    fanout.flush_persistence_barrier.assert_awaited_once()
    assert [getattr(event, "type", None) for event in fanout.events] == [
        "response_figures"
    ]


@pytest.mark.asyncio
async def test_record_tool_result_marks_emitted_only_after_dispatch_success() -> None:
    failed = _Fanout(dispatch_result=False)
    coordinator = FigureCoordinator(
        fanout=failed,
        session_id="sess-1",
        task_id="task-1",
    )

    await coordinator.record_tool_result(
        _tool_result(),
        include_spawned=False,
        reason="first_attempt",
    )
    await coordinator.flush_if_dirty("retry")

    assert [getattr(event, "type", None) for event in failed.events] == [
        "response_figures",
        "response_figures",
    ]


@pytest.mark.asyncio
async def test_root_stream_ignores_spawned_tool_results_by_default() -> None:
    fanout = _Fanout()
    coordinator = FigureCoordinator(
        fanout=fanout,
        session_id="sess-1",
        task_id="task-1",
    )

    await coordinator.record_tool_result(
        _tool_result(spawn_id="child-1"),
        include_spawned=False,
        reason="root_stream",
    )

    assert fanout.events == []


@pytest.mark.asyncio
async def test_child_event_sink_dispatches_child_event_and_promotes_figures() -> None:
    fanout = _Fanout()
    coordinator = FigureCoordinator(
        fanout=fanout,
        session_id="sess-1",
        task_id="task-1",
    )

    await coordinator.child_event_sink(_tool_result(spawn_id="child-1"))
    await coordinator.child_event_sink(
        ResponseEvent(
            source="MatMaster:direct",
            spawn_id="child-1",
            content="child answer",
        )
    )

    assert [getattr(event, "type", None) for event in fanout.events] == [
        "tool_result",
        "response_figures",
        "response",
    ]


def test_upload_config_is_available() -> None:
    fanout = _Fanout()
    coordinator = FigureCoordinator(
        fanout=fanout,
        session_id="sess-1",
        task_id="task-1",
    )

    assert coordinator.upload_config.session_id == "sess-1"
    assert coordinator.upload_config.task_id == "task-1"
    assert callable(coordinator.upload_config.upload_bytes)
