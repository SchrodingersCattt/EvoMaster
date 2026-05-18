from __future__ import annotations

import logging

from matmaster.context.rendering import render_sections, wrap_tag
from matmaster.context.sections import ContextSection, ContextView, SectionOrder


def test_wrap_tag_strips_outer_whitespace() -> None:
    assert wrap_tag("current_instruction", "  Explain FeO. \n") == (
        "<current_instruction>\nExplain FeO.\n</current_instruction>"
    )


def test_wrap_tag_returns_empty_for_blank_content() -> None:
    assert wrap_tag("current_instruction", " \n ") == ""


def test_wrap_tag_escapes_close_tag(
    caplog,
) -> None:
    with caplog.at_level(logging.WARNING):
        rendered = wrap_tag("current_instruction", "Do not emit </current_instruction>")

    assert "</ current_instruction>" in rendered
    assert "</current_instruction>" not in rendered.removeprefix(
        "<current_instruction>\n"
    ).removesuffix("\n</current_instruction>")
    assert "escaping to avoid breaking section boundary" in caplog.text


def test_render_sections_filters_view_and_sorts_by_order() -> None:
    sections = (
        ContextSection(
            key="turn",
            tag="current_instruction",
            content="Explain FeO.",
            order=SectionOrder.TURN_INSTRUCTION,
            views=frozenset({ContextView.RUNTIME}),
        ),
        ContextSection(
            key="instructions",
            tag="user_instructions",
            content="Use SI units.",
            order=SectionOrder.USER_INSTRUCTIONS,
            views=frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT}),
        ),
    )

    runtime = render_sections(sections, view=ContextView.RUNTIME)
    checkpoint = render_sections(sections, view=ContextView.CHECKPOINT)

    assert runtime == (
        "<user_instructions>\nUse SI units.\n</user_instructions>\n\n"
        "<current_instruction>\nExplain FeO.\n</current_instruction>"
    )
    assert checkpoint == "<user_instructions>\nUse SI units.\n</user_instructions>"
