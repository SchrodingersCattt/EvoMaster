from __future__ import annotations

from matmaster.context.sections import ContextView, SectionOrder
from matmaster.context.sources.user_instructions import UserInstructionsSource


def test_user_instructions_empty_returns_no_sections() -> None:
    assert UserInstructionsSource(text=" \n ").to_sections() == ()


def test_user_instructions_source_preserves_raw_content() -> None:
    section = UserInstructionsSource(text="Use SI units.\n").to_sections()[0]

    assert section.key == "user-instructions"
    assert section.tag == "user-instructions"
    assert section.content == "Use SI units.\n"
    assert section.order == SectionOrder.USER_INSTRUCTIONS
    assert section.views == frozenset({ContextView.RUNTIME, ContextView.CHECKPOINT})
