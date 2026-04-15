"""回答级图片聚合服务。"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from matmaster.types.events import ResponseFiguresEvent, ToolResultEvent
from matmaster.types.figures import FigureDescriptor

logger = logging.getLogger(__name__)


class ResponseFiguresAccumulator:
    """把多个 tool_result.payload.figures 汇总成一次性的回答级事件。"""

    def __init__(self) -> None:
        self._seen_ids: set[str] = set()
        self._ordered: list[FigureDescriptor] = []
        self._emitted = False

    def add_tool_result(self, event: ToolResultEvent) -> None:
        """吸收父级 tool result 中的图片，保持到达顺序与 first-writer-wins。"""
        if event.spawn_id is not None:
            return

        raw_items = (event.payload or {}).get('figures') or []
        if not isinstance(raw_items, list):
            return

        for raw in raw_items:
            try:
                figure = FigureDescriptor.model_validate(raw)
            except ValidationError:
                logger.warning(
                    'Ignoring invalid response figure payload for tool_call=%s',
                    event.call_id,
                    exc_info=True,
                )
                continue

            if figure.source_tool_call_id is None:
                figure = figure.model_copy(
                    update={'source_tool_call_id': event.call_id}
                )

            if figure.figure_id in self._seen_ids:
                continue

            self._seen_ids.add(figure.figure_id)
            self._ordered.append(figure)

    def build_event(self) -> ResponseFiguresEvent | None:
        """构造一次性的 response_figures 事件。"""
        if self._emitted or not self._ordered:
            return None
        self._emitted = True
        return ResponseFiguresEvent(source='System', figures=list(self._ordered))
