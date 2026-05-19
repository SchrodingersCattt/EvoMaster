"""History restore + runtime ports wiring for run_agent.

Bundles history restore via ``ModelHistoryRestoreService`` with the
``PlaygroundRuntimePorts`` adapter that exposes raw event lookups to the
compaction subsystem.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, TypeVar

from matmaster.context.ports import SessionEvent, SessionEventQuery
from matmaster.types.runtime_ports import (
    PlaygroundCompactionPort,
    PlaygroundRuntimePorts,
)
from src.services.session_event_codec import decode_session_events
from src.services.model_history_restore_service import ModelHistoryRestoreService

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class HistoryWiringResult:
    """Bundle of history-related values produced for a single run_agent call."""

    history: list
    runtime_ports: PlaygroundRuntimePorts
    bohrium_rebuild_events: list[dict]


def _safe_event_call(
    events_table: Any | None,
    method_name: str,
    default: T,
    *args: Any,
    _log_extra: str = "",
    **kwargs: Any,
) -> Any | T:
    """Call ``events_table.<method_name>(*args, **kwargs)`` defensively.

    Returns ``default`` when the table is missing or the call raises.
    ``_log_extra`` appends caller-specific context to the warning message
    (e.g. session_id, purpose) so error-triage information is preserved.
    """
    if events_table is None:
        return default
    try:
        return getattr(events_table, method_name)(*args, **kwargs)
    except Exception:
        suffix = f" ({_log_extra})" if _log_extra else ""
        logger.warning(
            "history wiring: %s failed%s", method_name, suffix, exc_info=True
        )
        return default


def build_history_wiring(
    *,
    base_runtime_ports: PlaygroundRuntimePorts,
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

    raw_query_events = _safe_event_call(
        events_table,
        "get_session_user_query_events",
        [],
        session_id,
        _log_extra=f"session_id={session_id}",
    )
    query_events: list[dict] = (
        raw_query_events if isinstance(raw_query_events, list) else []
    )

    def _get_query_events() -> list[dict]:
        return list(query_events)

    def _get_all_events() -> list[dict]:
        events = _safe_event_call(
            events_table,
            "get_session_events",
            [],
            session_id,
            limit=raw_history_limit,
        )
        return events if isinstance(events, list) else []

    def _get_latest_checkpoint_covered_until_event_id() -> int | None:
        checkpoints = _safe_event_call(
            events_table, "get_history_checkpoints", None, session_id, None, limit=1
        )
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
        raw = _safe_event_call(
            events_table, "get_latest_scope_event_id", None, session_id, None
        )
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
        events = _safe_event_call(
            events_table,
            "query_context_events",
            [],
            session_id=session_id,
            spawn_id=spawn_id,
            until_event_id=until_event_id,
            event_types=event_types,
            limit=limit,
            order=order,
        )
        return events if isinstance(events, list) else []

    class _RunSessionEventHistory:
        async def load_events(
            self,
            query: SessionEventQuery,
        ) -> tuple[SessionEvent, ...]:
            rows = await asyncio.to_thread(
                _query_context_events,
                spawn_id=query.spawn_id,
                until_event_id=query.until_event_id,
                event_types=query.event_types,
                limit=query.limit,
                order=query.order,
            )
            return decode_session_events(rows)

        def query_events(self) -> list[dict[str, Any]]:
            return _get_query_events()

        def all_events(self) -> list[dict[str, Any]]:
            return _get_all_events()

        def latest_checkpoint_covered_until_event_id(self) -> int | None:
            return _get_latest_checkpoint_covered_until_event_id()

        def latest_scope_event_id(self) -> int | None:
            return _get_latest_scope_event_id()

    runtime_ports = replace(
        base_runtime_ports,
        child_event_forward_sink=child_event_sink,
        compaction=PlaygroundCompactionPort(
            history=_RunSessionEventHistory(),
            checkpoint_sink_factory=checkpoint_sink_factory,
            pre_compaction_barrier=pre_compaction_barrier,
        ),
    )

    bohrium_rebuild_events = _safe_event_call(
        events_table,
        "get_bohrium_events",
        [],
        session_id,
        _log_extra="for registry rebuild",
    )
    if not isinstance(bohrium_rebuild_events, list):
        bohrium_rebuild_events = []

    return HistoryWiringResult(
        history=history,
        runtime_ports=runtime_ports,
        bohrium_rebuild_events=bohrium_rebuild_events,
    )
