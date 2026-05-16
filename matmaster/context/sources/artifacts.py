from __future__ import annotations

from dataclasses import dataclass

from matmaster.context.sections import ContextSection, ContextView, SectionOrder

_VIEWS = frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})


@dataclass(frozen=True)
class SessionArtifactsSource:
    """Placeholder artifact source.

    Phase 2A uses a simple text field only; future artifact integration may
    replace this carrier with typed fields.
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
                views=_VIEWS,
            ),
        )
