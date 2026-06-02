from __future__ import annotations

import asyncio
import logging

from matmaster.integration.fanout import RunEventFanout
from matmaster.types.events import BusEvent, ToolResultEvent
from matmaster.types.figures import FigureUploadConfig
from src.services.agent_run_bohrium_stage import _build_figure_upload_config
from src.services.response_figures_service import ResponseFiguresAccumulator

logger = logging.getLogger(__name__)


class FigureCoordinator:
    def __init__(
        self,
        *,
        fanout: RunEventFanout,
        session_id: str,
        task_id: str,
    ) -> None:
        self._fanout = fanout
        self._accumulator = ResponseFiguresAccumulator()
        self._lock = asyncio.Lock()
        self._upload_config = _build_figure_upload_config(
            session_id=session_id,
            task_id=task_id,
        )

    @property
    def upload_config(self) -> FigureUploadConfig:
        return self._upload_config

    async def child_event_sink(self, event: BusEvent) -> None:
        try:
            await self._fanout.dispatch(event)
            if isinstance(event, ToolResultEvent):
                await self.record_tool_result(
                    event,
                    include_spawned=True,
                    reason="child_tool_result",
                )
        except Exception:
            logger.warning(
                "child event sink failed for event type=%s",
                getattr(event, "type", "?"),
                exc_info=True,
            )

    async def flush_if_dirty(self, reason: str) -> None:
        async with self._lock:
            await self._flush_if_dirty_unlocked(reason)

    async def record_tool_result(
        self,
        event: ToolResultEvent,
        *,
        include_spawned: bool,
        reason: str,
    ) -> None:
        async with self._lock:
            self._accumulator.add_tool_result(
                event,
                include_spawned=include_spawned,
            )
            await self._flush_if_dirty_unlocked(reason)

    async def _flush_if_dirty_unlocked(self, reason: str) -> None:
        response_figures_event = self._accumulator.build_snapshot_event_if_dirty()
        if response_figures_event is None:
            return
        try:
            await self._fanout.flush_persistence_barrier()
            dispatched = await self._fanout.dispatch_and_wait_persistence(
                response_figures_event
            )
        except Exception:
            logger.warning(
                "response_figures dispatch failed reason=%s",
                reason,
                exc_info=True,
            )
            return

        if dispatched:
            self._accumulator.mark_snapshot_emitted()
            return

        logger.warning(
            "response_figures dispatch reported handler failure reason=%s",
            reason,
        )
