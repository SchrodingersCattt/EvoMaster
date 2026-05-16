"""Phase 2C production helper for ContextAssembler.session_context_factory.

`matmaster.context.assembly.ContextAssembler` takes a callable
`session_context_factory: Callable[[tuple[SessionEvent, ...]], SessionContextBuilder]`.
Service layer owns the platform dependencies that `matmaster.context` cannot know,
including skill registry and MCP capability metadata.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from matmaster.context.ports import SessionEvent
from matmaster.context.session import SessionContextBuilder

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
