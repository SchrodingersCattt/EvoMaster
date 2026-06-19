from __future__ import annotations

import logging

from matmaster.context.rendering import render_sections, wrap_tag
from matmaster.context.sections import ContextSection, ContextView, SectionOrder


def test_wrap_tag_strips_outer_whitespace() -> None:
    assert wrap_tag("current-instruction", "  Explain FeO. \n") == (
        "<current-instruction>\nExplain FeO.\n</current-instruction>"
    )


def test_wrap_tag_returns_empty_for_blank_content() -> None:
    assert wrap_tag("current-instruction", " \n ") == ""


def test_wrap_tag_escapes_close_tag(
    caplog,
) -> None:
    with caplog.at_level(logging.WARNING):
        rendered = wrap_tag("current-instruction", "Do not emit </current-instruction>")

    assert "</ current-instruction>" in rendered
    assert "</current-instruction>" not in rendered.removeprefix(
        "<current-instruction>\n"
    ).removesuffix("\n</current-instruction>")
    assert "escaping to avoid breaking section boundary" in caplog.text


def test_render_sections_filters_view_and_sorts_by_order() -> None:
    sections = (
        ContextSection(
            key="turn",
            tag="current-instruction",
            content="Explain FeO.",
            order=SectionOrder.TURN_INSTRUCTION,
            views=frozenset({ContextView.RUNTIME}),
        ),
        ContextSection(
            key="instructions",
            tag="user-instructions",
            content="Use SI units.",
            order=SectionOrder.USER_INSTRUCTIONS,
            views=frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT}),
        ),
    )

    runtime = render_sections(sections, view=ContextView.RUNTIME)
    checkpoint = render_sections(sections, view=ContextView.CHECKPOINT)

    assert runtime == (
        "<user-instructions>\nUse SI units.\n</user-instructions>\n\n"
        "<current-instruction>\nExplain FeO.\n</current-instruction>"
    )
    assert checkpoint == "<user-instructions>\nUse SI units.\n</user-instructions>"
