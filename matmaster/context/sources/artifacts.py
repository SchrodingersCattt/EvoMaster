from __future__ import annotations

from dataclasses import dataclass

from matmaster.context.sections import (
    ContextSection,
    SectionOrder,
    single_section_or_empty,
)


@dataclass(frozen=True)
class SessionArtifactsSource:
    """Simple text carrier for the session-artifacts section.

    Real artifact integration may later replace this carrier with typed fields.
    """

    text: str = ""

    def to_sections(self) -> tuple[ContextSection, ...]:
        return single_section_or_empty(
            key="session-artifacts",
            tag="session-artifacts",
            content=self.text,
            order=SectionOrder.SESSION_ARTIFACTS,
        )
