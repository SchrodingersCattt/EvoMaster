from __future__ import annotations

from dataclasses import dataclass

from matmaster.context.sections import ContextSection, ContextView, SectionOrder

_VIEWS = frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})


@dataclass(frozen=True)
class UserInstructionsSource:
    text: str = ""

    def to_sections(self) -> tuple[ContextSection, ...]:
        if not self.text.strip():
            return ()
        return (
            ContextSection(
                key="user_instructions",
                tag="user_instructions",
                content=self.text,
                order=SectionOrder.USER_INSTRUCTIONS,
                views=_VIEWS,
            ),
        )
