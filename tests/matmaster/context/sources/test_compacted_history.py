from __future__ import annotations

from matmaster.context.sections import ContextView, SectionOrder
from matmaster.context.sources.compacted_history import CompactedHistorySource


def test_compacted_history_empty_returns_no_sections() -> None:
    assert CompactedHistorySource(summary="").to_sections() == ()


def test_compacted_history_source_returns_checkpoint_visible_section() -> None:
    section = CompactedHistorySource(summary="Earlier turns mention FeO.").to_sections()[0]

    assert section.key == "compacted_history"
    assert section.tag == "compacted_history"
    assert section.content == "Earlier turns mention FeO."
    assert section.order == SectionOrder.COMPACTED_HISTORY
    assert section.views == frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})
