from __future__ import annotations

from dataclasses import dataclass

from matmaster.context.sections import ContextSection, ContextView, SectionOrder

_VIEWS = frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})


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
                views=_VIEWS,
            ),
        )
