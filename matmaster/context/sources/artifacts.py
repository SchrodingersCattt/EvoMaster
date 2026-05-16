from __future__ import annotations

from dataclasses import dataclass

from matmaster.context.sections import ALL_VIEWS, ContextSection, SectionOrder


@dataclass(frozen=True)
class SessionArtifactsSource:
    """Simple text carrier for the session-artifacts section.

    Real artifact integration may later replace this carrier with typed fields.
    """

    text: str = ""

    def to_sections(self) -> tuple[ContextSection, ...]:
        if not self.text.strip():
            return ()
        return (
            ContextSection(
                key="session_artifacts",
                tag="session_artifacts",
                content=self.text,
                order=SectionOrder.SESSION_ARTIFACTS,
                views=ALL_VIEWS,
            ),
        )
