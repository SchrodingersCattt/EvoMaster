from __future__ import annotations

from dataclasses import dataclass

from matmaster.context.sections import ALL_VIEWS, ContextSection, SectionOrder


@dataclass(frozen=True)
class CompactedHistorySource:
    summary: str = ""

    def to_sections(self) -> tuple[ContextSection, ...]:
        if not self.summary.strip():
            return ()
        return (
            ContextSection(
                key="compacted_history",
                tag="compacted_history",
                content=self.summary,
                order=SectionOrder.COMPACTED_HISTORY,
                views=ALL_VIEWS,
            ),
        )
