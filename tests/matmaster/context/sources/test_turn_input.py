from __future__ import annotations

from matmaster.context.rendering import wrap_tag
from matmaster.context.sections import ContextView, SectionOrder
from matmaster.context.sources.turn_input import (
    TurnAttachmentsSource,
    TurnInput,
    TurnInstructionSource,
)
from matmaster.types.current_input import (
    CurrentInputContext,
    build_current_instruction_block,
)
from matmaster.types.messages import ImageContentPart


def test_turn_instruction_source_returns_runtime_only_section() -> None:
    sections = TurnInstructionSource(user_text=" Explain FeO. ").to_sections()

    assert len(sections) == 1
    section = sections[0]
    assert section.key == "current_instruction"
    assert section.tag == "current_instruction"
    assert section.content == "Explain FeO."
    assert section.order == SectionOrder.TURN_INSTRUCTION
    assert section.views == frozenset({ContextView.RUNTIME})


def test_turn_instruction_source_deferred_uses_last_order() -> None:
    section = TurnInstructionSource(user_text="Continue.", deferred=True).to_sections()[0]

    assert section.order == SectionOrder.TURN_INSTRUCTION_LAST


def test_turn_attachments_source_renders_future_split_section() -> None:
    sections = TurnAttachmentsSource(
        files=("https://oss.example.com/input.cif",),
        images=("https://oss.example.com/image.png",),
        workspace_paths=("/share/result.xyz",),
    ).to_sections()

    assert len(sections) == 1
    section = sections[0]
    assert section.key == "turn_attachments"
    assert section.tag == "turn_attachments"
    assert section.order == SectionOrder.TURN_ATTACHMENTS
    assert section.content == (
        "file_1 input.cif https://oss.example.com/input.cif\n"
        "workspace_1 /share/result.xyz\n"
        "image_1 image.png https://oss.example.com/image.png"
    )


def test_turn_input_default_merges_attachments_into_current_instruction() -> None:
    turn_input = TurnInput(
        instruction=TurnInstructionSource(user_text="Explain FeO."),
        attachments=TurnAttachmentsSource(
            files=("https://oss.example.com/input.cif",),
            images=("https://oss.example.com/image.png",),
            workspace_paths=("/share/result.xyz",),
        ),
        pre_turn_history_event_id=9,
    )

    sections = turn_input.to_sections()

    assert len(sections) == 1
    assert sections[0].key == "current_instruction"
    assert sections[0].content == (
        "Explain FeO.\n\n"
        "[Current attachments]\n"
        "file_1 input.cif https://oss.example.com/input.cif\n"
        "workspace_1 /share/result.xyz\n"
        "image_1 image.png https://oss.example.com/image.png"
    )


def test_turn_input_default_shape_matches_existing_current_input_renderer() -> None:
    """Pin Phase 2A default prompt shape to Phase 1 ground truth."""
    turn_input = TurnInput(
        instruction=TurnInstructionSource(user_text="Explain FeO."),
        attachments=TurnAttachmentsSource(
            files=("https://oss.example.com/input.cif",),
            images=("https://oss.example.com/image.png",),
            workspace_paths=("/share/result.xyz",),
        ),
    )
    legacy_context = CurrentInputContext.from_values(
        user_text="Explain FeO.",
        files=("https://oss.example.com/input.cif",),
        images=("https://oss.example.com/image.png",),
        workspace_paths=("/share/result.xyz",),
    )

    section = turn_input.to_sections()[0]

    assert wrap_tag(section.tag, section.content) == build_current_instruction_block(
        legacy_context
    )


def test_turn_input_can_split_attachments_for_future_ab() -> None:
    turn_input = TurnInput(
        instruction=TurnInstructionSource(user_text="Explain FeO."),
        attachments=TurnAttachmentsSource(files=("https://oss.example.com/input.cif",)),
    )

    sections = turn_input.to_sections(split_attachments=True)

    assert [section.key for section in sections] == [
        "current_instruction",
        "turn_attachments",
    ]


def test_turn_input_has_effective_input_and_images_as_parts() -> None:
    empty = TurnInput()
    with_image = TurnInput(
        attachments=TurnAttachmentsSource(images=("https://example.com/a.png",))
    )

    assert empty.has_effective_input() is False
    assert with_image.has_effective_input() is True
    assert with_image.attachments.images_as_parts() == (
        ImageContentPart(url="https://example.com/a.png"),
    )
