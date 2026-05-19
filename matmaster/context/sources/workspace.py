from __future__ import annotations

from dataclasses import dataclass

from matmaster.context.sections import (
    ContextSection,
    SectionOrder,
    single_section_or_empty,
)


@dataclass(frozen=True)
class SessionWorkspaceSource:
    """Simple text carrier for the session-workspace section.

    The real workspace fields are owned by the later workspace/artifact
    integration; this dataclass keeps composition wiring testable.
    """

    text: str = ""

    def to_sections(self) -> tuple[ContextSection, ...]:
        return single_section_or_empty(
            key="session_workspace",
            tag="session_workspace",
            content=self.text,
            order=SectionOrder.SESSION_WORKSPACE,
        )
