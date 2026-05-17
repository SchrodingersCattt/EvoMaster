from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from matmaster.context.ports import SessionEvent
from matmaster.context.sections import ContextSection
from matmaster.context.sources.attachments import SessionAttachmentsSource
from matmaster.context.sources.skills import SessionSkillsSource
from matmaster.context.sources.tools import SessionToolsSource


@dataclass(frozen=True)
class SessionContextBuilder:
    """Compose session-level sections from a typed SessionEvent tuple."""

    events: tuple[SessionEvent, ...]
    skill_registry: Any
    legal_mcp_servers: set[str] | None = None
    schemas_by_server: Mapping[str, list[Mapping[str, Any]]] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.events, tuple):
            raise TypeError(
                "SessionContextBuilder.events must be a tuple of SessionEvent; "
                "service-layer callers should decode raw rows before constructing it"
            )

    def build_sections(
        self,
        *,
        until_event_id: int | None,
        include_attachments: bool,
    ) -> tuple[ContextSection, ...]:
        if until_event_id is not None:
            scoped_events = tuple(
                event for event in self.events if event.id <= until_event_id
            )
        else:
            scoped_events = self.events

        skills_source = SessionSkillsSource.from_events(
            scoped_events,
            skill_registry=self.skill_registry,
        )
        tools_source = SessionToolsSource.from_skills(
            skills_source.skills,
            legal_servers=self.legal_mcp_servers,
            schemas_by_server=self.schemas_by_server,
        )

        sections: list[ContextSection] = []
        sections.extend(skills_source.to_sections())
        sections.extend(tools_source.to_sections())
        if include_attachments:
            attachments_source = SessionAttachmentsSource.from_events(
                scoped_events,
                until_event_id=until_event_id,
            )
            sections.extend(attachments_source.to_sections())
        sections.sort(key=lambda section: section.order)
        return tuple(sections)
