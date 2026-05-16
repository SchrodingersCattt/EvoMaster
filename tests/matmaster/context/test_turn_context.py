from __future__ import annotations

import pytest

from matmaster.context.sections import ContextSection, ContextView, SectionOrder
from matmaster.context.turn_context import UserTurnContext
from matmaster.types.messages import ImageContentPart, UserMessage


def _section(
    key: str,
    tag: str,
    content: str,
    order: int,
    views: frozenset[ContextView],
) -> ContextSection:
    return ContextSection(
        key=key,
        tag=tag,
        content=content,
        order=order,
        views=views,
    )


def test_from_sources_rejects_duplicate_keys() -> None:
    first = (_section("same", "a", "one", 1, frozenset({ContextView.RUNTIME})),)
    second = (_section("same", "b", "two", 2, frozenset({ContextView.RUNTIME})),)

    with pytest.raises(ValueError, match="Duplicate section key 'same'"):
        UserTurnContext.from_sources(first, second)


def test_render_and_to_message_preserve_images() -> None:
    context = UserTurnContext.from_sources(
        (
            _section(
                "instructions",
                "user_instructions",
                "Use SI units.",
                SectionOrder.USER_INSTRUCTIONS,
                frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT}),
            ),
        ),
        (
            _section(
                "turn",
                "current_instruction",
                "Explain FeO.",
                SectionOrder.TURN_INSTRUCTION,
                frozenset({ContextView.RUNTIME}),
            ),
        ),
        images=(ImageContentPart(url="https://example.com/feo.png"),),
    )

    runtime = context.to_message(ContextView.RUNTIME)
    checkpoint = context.to_message(ContextView.CHECKPOINT)

    assert isinstance(runtime, UserMessage)
    assert runtime.content == (
        "<user_instructions>\nUse SI units.\n</user_instructions>\n\n"
        "<current_instruction>\nExplain FeO.\n</current_instruction>"
    )
    assert runtime.images == [ImageContentPart(url="https://example.com/feo.png")]
    assert checkpoint.content == (
        "<user_instructions>\nUse SI units.\n</user_instructions>"
    )
    assert checkpoint.images == [ImageContentPart(url="https://example.com/feo.png")]
