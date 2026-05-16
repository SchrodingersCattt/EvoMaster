from __future__ import annotations

from dataclasses import dataclass

from matmaster.context.sections import ContextSection, ContextView, SectionOrder

_VIEWS = frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})


@dataclass(frozen=True)
class SessionWorkspaceSource:
    """Placeholder workspace source.

    Phase 2A uses a simple text field so composition wiring is testable. Real
    workspace fields are owned by the later workspace/artifact integration.
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
                views=_VIEWS,
            ),
        )
