from __future__ import annotations

from dataclasses import dataclass

from matmaster.context.sections import (
    ContextSection,
    SectionOrder,
    single_section_or_empty,
)


@dataclass(frozen=True)
class UserInstructionsSource:
    text: str = ""

    def to_sections(self) -> tuple[ContextSection, ...]:
        return single_section_or_empty(
            key="user_instructions",
            tag="user_instructions",
            content=self.text,
            order=SectionOrder.USER_INSTRUCTIONS,
        )
