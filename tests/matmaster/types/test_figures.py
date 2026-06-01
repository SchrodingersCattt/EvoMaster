"""Tests for figure-related type contracts."""

from __future__ import annotations

from matmaster.types.figures import FigureDescriptor


def test_figure_descriptor_defaults() -> None:
    entry = FigureDescriptor(
        figure_id="band_structure",
        asset_url="https://oss.example/band.png",
        caption="Si 的能带图",
    )

    assert entry.importance == "secondary"
    assert entry.placement_hint == "sidebar_only"
    assert entry.alt is None
    assert entry.source_tool_call_id is None
