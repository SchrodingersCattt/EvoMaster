"""Phase 2C production helper for ContextAssembler.session_context_factory.

`matmaster.context.assembly.ContextAssembler` takes a callable
`session_context_factory: Callable[[tuple[SessionEvent, ...]], SessionContextBuilder]`.
Service layer owns the platform dependencies that `matmaster.context` cannot know,
including skill registry and MCP capability metadata.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from matmaster.context.assembly import ContextAssembler, ContextRenderOptions
from matmaster.context.ports import (
    ContextAssemblyPorts,
    SessionEvent,
    SessionEventQuery,
)
from matmaster.context.scanner import coerce_session_events
from matmaster.context.session import SessionContextBuilder
from src.services.context_assembly_ports import AppSessionEventsPort, AppSessionJobsPort

SessionContextFactory = Callable[[tuple[SessionEvent, ...]], SessionContextBuilder]


def build_session_context_factory(
    *,
    skill_registry: Any | None,
    legal_mcp_servers: set[str] | None,
    schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None,
) -> SessionContextFactory:
    """Bind service-level dependencies to a SessionContextBuilder factory."""

    def factory(events: tuple[SessionEvent, ...]) -> SessionContextBuilder:
        return SessionContextBuilder(
            events=events,
            skill_registry=skill_registry,
            legal_mcp_servers=legal_mcp_servers,
            schemas_by_server=schemas_by_server,
        )

    return factory


def build_context_assembler(
    *,
    events_table: object,
    skill_registry: Any | None,
    legal_mcp_servers: set[str] | None,
    schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None,
    split_turn_attachments: bool = False,
) -> tuple[ContextAssembler, ContextAssemblyPorts]:
    ports = ContextAssemblyPorts(
        session_events=AppSessionEventsPort(events_table=events_table),
        session_jobs=AppSessionJobsPort(),
    )
    assembler = ContextAssembler(
        ports=ports,
        session_context_factory=build_session_context_factory(
            skill_registry=skill_registry,
            legal_mcp_servers=legal_mcp_servers,
            schemas_by_server=schemas_by_server,
        ),
        render_options=ContextRenderOptions(
            split_turn_attachments=split_turn_attachments,
        ),
    )
    return assembler, ports


class RuntimeHistorySessionEventsPort:
    def __init__(self, history_port: Any) -> None:
        self._history_port = history_port

    async def load_events(self, query: SessionEventQuery):
        rows = self._history_port.query_context_events(
            spawn_id=query.spawn_id,
            until_event_id=query.until_event_id,
            event_types=query.event_types,
            limit=query.limit,
            order=query.order,
        )
        return coerce_session_events(rows)
