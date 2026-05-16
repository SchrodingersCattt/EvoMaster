from __future__ import annotations

from dataclasses import dataclass

from matmaster.context.sections import ALL_VIEWS, ContextSection, SectionOrder


@dataclass(frozen=True)
class SessionWorkspaceSource:
    """Simple text carrier for the session-workspace section.

    The real workspace fields are owned by the later workspace/artifact
    integration; this dataclass keeps composition wiring testable.
    """

    text: str = ""

    def to_sections(self) -> tuple[ContextSection, ...]:
        if not self.text.strip():
            return ()
        return (
            ContextSection(
                key="session_workspace",
                tag="session_workspace",
                content=self.text,
                order=SectionOrder.SESSION_WORKSPACE,
                views=ALL_VIEWS,
            ),
        )
