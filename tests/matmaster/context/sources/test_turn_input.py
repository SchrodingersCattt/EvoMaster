from __future__ import annotations

import pytest

from matmaster.context.sections import ContextView, SectionOrder
from matmaster.context.sources.turn_input import (
    TurnAttachmentsSource,
    TurnInput,
    TurnInstructionSource,
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
    section = TurnInstructionSource(user_text="Continue.", deferred=True).to_sections()[
        0
    ]

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


def test_turn_input_default_shape_matches_current_instruction_renderer() -> None:
    turn_input = TurnInput(
        instruction=TurnInstructionSource(user_text="Explain FeO."),
        attachments=TurnAttachmentsSource(
            files=("https://oss.example.com/input.cif",),
            images=("https://oss.example.com/image.png",),
            workspace_paths=("/share/result.xyz",),
        ),
    )

    section = turn_input.to_sections()[0]

    assert section.tag == "current_instruction"
    assert section.content == (
        "Explain FeO.\n\n"
        "[Current attachments]\n"
        "file_1 input.cif https://oss.example.com/input.cif\n"
        "workspace_1 /share/result.xyz\n"
        "image_1 image.png https://oss.example.com/image.png"
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


def test_turn_input_images_as_parts_preserves_image_detail() -> None:
    turn_input = TurnInput.from_values(
        user_text="看图",
        images=["https://oss.example.com/a.png"],
        image_detail="high",
    )

    assert turn_input.attachments.images_as_parts() == (
        ImageContentPart(url="https://oss.example.com/a.png", detail="high"),
    )
    assert TurnInput.from_payload(turn_input.to_payload()) == turn_input


def test_turn_input_rejects_negative_history_boundary() -> None:
    with pytest.raises(ValueError, match="pre_turn_history_event_id must be >= 0"):
        TurnInput(pre_turn_history_event_id=-1)


def test_turn_input_round_trips_payload() -> None:
    turn_input = TurnInput.from_values(
        user_text="analyze current",
        files=["https://oss.example.com/new.cif"],
        images=["https://oss.example.com/image.png"],
        workspace_paths=["/share/current/POSCAR"],
        pre_turn_history_event_id=42,
    )

    assert TurnInput.from_payload(turn_input.to_payload()) == turn_input


def test_turn_input_missing_boundary_defaults_to_zero() -> None:
    turn_input = TurnInput.from_payload({"user_text": "hi"})

    assert turn_input is not None
    assert turn_input.pre_turn_history_event_id == 0


def test_turn_input_invalid_boundary_defaults_to_zero() -> None:
    turn_input = TurnInput.from_payload(
        {"user_text": "hi", "pre_turn_history_event_id": "not-an-int"}
    )

    assert turn_input is not None
    assert turn_input.pre_turn_history_event_id == 0
