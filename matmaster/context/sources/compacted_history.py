from __future__ import annotations

from dataclasses import dataclass

from matmaster.context.sections import (
    ContextSection,
    SectionOrder,
    single_section_or_empty,
)


@dataclass(frozen=True)
class CompactedHistorySource:
    summary: str = ""

    def to_sections(self) -> tuple[ContextSection, ...]:
        return single_section_or_empty(
            key="compacted_history",
            tag="compacted_history",
            content=self.summary,
            order=SectionOrder.COMPACTED_HISTORY,
        )
