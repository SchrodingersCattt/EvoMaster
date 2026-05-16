"""History restore + runtime ports wiring extracted from agent_run_service.

Phase 0 refactor (DESIGN.md §14 Phase 0a): move history restore +
attachment manifest + checkpoint covered_until lookup + the inner
``_RunSessionEventHistory`` adapter + ``PlaygroundRuntimePorts`` assembly
out of ``run_agent`` so ``agent_run_service.py`` stays under the
800-line target.

Model restore is delegated directly to ``ModelHistoryRestoreService``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from matmaster.context.sources.attachments import (
    format_entries_text,
    scan_legacy_attachment_entries,
)
from matmaster.types.runtime_ports import (
    PlaygroundCompactionPort,
    PlaygroundRuntimePorts,
)
from src.services.model_history_restore_service import ModelHistoryRestoreService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistoryWiringResult:
    """Bundle of history-related values produced for a single run_agent call."""

    history: list
    attachment_text: str
    runtime_ports: PlaygroundRuntimePorts
    bohrium_rebuild_events: list[dict]


def build_history_wiring(
    *,
    events_table: Any | None,
    session_id: str,
    task_id: str,
    raw_history_limit: int,
    child_event_sink: Callable,
    checkpoint_sink_factory: Callable,
    pre_compaction_barrier: Callable,
) -> HistoryWiringResult:
    """Assemble history + attachments + runtime_ports for a single run."""
    history = (
        ModelHistoryRestoreService(events_table).restore_history(
            session_id=session_id,
            spawn_id=None,
            task_id=task_id,
            raw_limit=raw_history_limit,
        )
        if events_table is not None
        else []
    )

    query_events: list[dict] = []
    if events_table is not None:
        try:
            raw_query_events = events_table.get_session_user_query_events(session_id)
            query_events = (
                raw_query_events if isinstance(raw_query_events, list) else []
            )
        except Exception:
            logger.warning(
                "attachment manifest: get_session_user_query_events failed for session_id=%s",
                session_id,
                exc_info=True,
            )
    entries = scan_legacy_attachment_entries(query_events)
    attachment_text = format_entries_text(entries)

    def _get_query_events() -> list[dict]:
        return list(query_events)

    def _get_all_events() -> list[dict]:
        if events_table is None:
            return []
        try:
            events = events_table.get_session_events(
                session_id,
                limit=raw_history_limit,
            )
            return events if isinstance(events, list) else []
        except Exception:
            logger.warning("manifest: get_session_events failed", exc_info=True)
            return []

    def _get_latest_checkpoint_covered_until_event_id() -> int | None:
        if events_table is None:
            return None
        try:
            checkpoints = events_table.get_history_checkpoints(
                session_id, None, limit=1
            )
        except Exception:
            logger.warning(
                "manifest: get_history_checkpoints failed",
                exc_info=True,
            )
            return None
        if not isinstance(checkpoints, list):
            return None
        for checkpoint in checkpoints:
            if not isinstance(checkpoint, dict):
                continue
            content = checkpoint.get("content")
            if isinstance(content, dict):
                raw = content.get("covered_until_event_id")
                if raw is not None:
                    try:
                        return int(raw)
                    except (TypeError, ValueError):
                        return None
        return None

    def _get_latest_scope_event_id() -> int | None:
        if events_table is None:
            return None
        try:
            raw = events_table.get_latest_scope_event_id(session_id, None)
        except Exception:
            logger.warning("manifest: get_latest_scope_event_id failed", exc_info=True)
            return None
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    def _query_context_events(
        *,
        spawn_id: str | None,
        until_event_id: int | None = None,
        event_types: tuple[str, ...] | None = None,
        limit: int | None = None,
        order: str = "asc",
    ) -> list[dict[str, Any]]:
        if events_table is None:
            return []
        try:
            events = events_table.query_context_events(
                session_id=session_id,
                spawn_id=spawn_id,
                until_event_id=until_event_id,
                event_types=event_types,
                limit=limit,
                order=order,
            )
            return events if isinstance(events, list) else []
        except Exception:
            logger.warning("manifest: query_context_events failed", exc_info=True)
            return []

    class _RunSessionEventHistory:
        def query_events(self) -> list[dict[str, Any]]:
            return _get_query_events()

        def all_events(self) -> list[dict[str, Any]]:
            return _get_all_events()

        def query_context_events(
            self,
            *,
            spawn_id: str | None,
            until_event_id: int | None = None,
            event_types: tuple[str, ...] | None = None,
            limit: int | None = None,
            order: str = "asc",
        ) -> list[dict[str, Any]]:
            return _query_context_events(
                spawn_id=spawn_id,
                until_event_id=until_event_id,
                event_types=event_types,
                limit=limit,
                order=order,
            )

        def latest_checkpoint_covered_until_event_id(self) -> int | None:
            return _get_latest_checkpoint_covered_until_event_id()

        def latest_scope_event_id(self) -> int | None:
            return _get_latest_scope_event_id()

    runtime_ports = PlaygroundRuntimePorts(
        child_event_forward_sink=child_event_sink,
        compaction=PlaygroundCompactionPort(
            history=_RunSessionEventHistory(),
            checkpoint_sink_factory=checkpoint_sink_factory,
            pre_compaction_barrier=pre_compaction_barrier,
        ),
    )

    bohrium_rebuild_events: list[dict] = []
    try:
        if events_table is not None:
            bohrium_rebuild_events = events_table.get_bohrium_events(session_id)
    except Exception:
        logger.warning(
            'Failed to load Bohrium events for registry rebuild',
            exc_info=True,
        )

    return HistoryWiringResult(
        history=history,
        attachment_text=attachment_text,
        runtime_ports=runtime_ports,
        bohrium_rebuild_events=bohrium_rebuild_events,
    )
