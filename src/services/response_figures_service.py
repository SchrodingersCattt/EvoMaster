"""回答级图片聚合服务。"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from matmaster.types.events import ResponseFiguresEvent, ToolResultEvent
from matmaster.types.figures import FigureDescriptor

logger = logging.getLogger(__name__)


class ResponseFiguresAccumulator:
    """把多个 tool_result.payload.figures 汇总成可增量发出的回答级快照。"""

    def __init__(self) -> None:
        self._seen_ids: set[str] = set()
        self._source_by_id: dict[str, str | None] = {}
        self._ordered: list[FigureDescriptor] = []
        self._last_emitted_count = 0

    def add_tool_result(self, event: ToolResultEvent) -> bool:
        """吸收父级 tool result 中的图片，保持到达顺序与 first-writer-wins。"""
        if event.spawn_id is not None:
            return False

        raw_items = (event.payload or {}).get('figures') or []
        if not isinstance(raw_items, list):
            return False

        added = False
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
                logger.warning(
                    'Ignoring duplicate response figure_id=%s '
                    'first_tool_call=%s duplicate_tool_call=%s',
                    figure.figure_id,
                    self._source_by_id.get(figure.figure_id),
                    event.call_id,
                )
                continue

            self._seen_ids.add(figure.figure_id)
            self._source_by_id[figure.figure_id] = figure.source_tool_call_id
            self._ordered.append(figure)
            added = True

        return added

    def build_snapshot_event_if_dirty(self) -> ResponseFiguresEvent | None:
        """构造完整 response_figures 快照；不提交 emitted 状态。"""
        if len(self._ordered) <= self._last_emitted_count:
            return None
        return ResponseFiguresEvent(source='System', figures=list(self._ordered))

    def mark_snapshot_emitted(self) -> None:
        """在快照 dispatch 成功返回后提交 emitted 状态。"""
        self._last_emitted_count = len(self._ordered)
