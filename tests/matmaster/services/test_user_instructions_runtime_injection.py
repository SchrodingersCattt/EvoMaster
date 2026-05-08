"""Unit tests for runtime user-instructions injection."""

from __future__ import annotations

from matmaster.types.messages import (
    AssistantMessage,
    ImageContentPart,
    SystemMessage,
    UserMessage,
)
from src.services.agent_run_service import (
    _USER_INSTRUCTIONS_END,
    _USER_INSTRUCTIONS_START,
    _apply_user_instructions_to_initial_user_query,
    _strip_user_instructions_prefix,
)


def test_strip_returns_original_when_marker_absent() -> None:
    assert _strip_user_instructions_prefix("plain query") == "plain query"


def test_strip_removes_complete_wrapper() -> None:
    wrapped = (
        f"{_USER_INSTRUCTIONS_START}\n"
        "old instructions\n"
        f"{_USER_INSTRUCTIONS_END}\n"
        "\n"
        "plain query"
    )

    assert _strip_user_instructions_prefix(wrapped) == "plain query"


def test_applies_to_current_prompt_when_history_has_no_user_message() -> None:
    prompt, history = _apply_user_instructions_to_initial_user_query(
        user_prompt="first question",
        user_instructions="Prefer concise answers.",
        history=[SystemMessage(content="existing context")],
    )

    assert history == [SystemMessage(content="existing context")]
    assert prompt.startswith(_USER_INSTRUCTIONS_START)
    assert "Prefer concise answers." in prompt
    assert prompt.endswith("first question")


def test_applies_to_first_user_message_in_history() -> None:
    original_history = [
        SystemMessage(content="compacted context"),
        UserMessage(content="first question"),
        AssistantMessage(content="first answer"),
        UserMessage(content="second question"),
    ]

    prompt, history = _apply_user_instructions_to_initial_user_query(
        user_prompt="current question",
        user_instructions="Prefer concise answers.",
        history=original_history,
    )

    assert prompt == "current question"
    assert history[1].content.startswith(_USER_INSTRUCTIONS_START)
    assert "Prefer concise answers." in history[1].content
    assert history[1].content.endswith("first question")
    assert history[3].content == "second question"
    assert original_history[1].content == "first question"


def test_preserves_user_message_images_and_other_history_messages() -> None:
    image = ImageContentPart(url="oss://bucket/image.png")
    assistant = AssistantMessage(content="answer")
    original_history = [
        UserMessage(content="first question", images=[image]),
        assistant,
    ]

    _prompt, history = _apply_user_instructions_to_initial_user_query(
        user_prompt="current question",
        user_instructions="Use SI units.",
        history=original_history,
    )

    assert isinstance(history[0], UserMessage)
    assert history[0].images == [image]
    assert history[1] is assistant


def test_strips_existing_wrapper_before_reapplying() -> None:
    _prompt, first_history = _apply_user_instructions_to_initial_user_query(
        user_prompt="current question",
        user_instructions="Old preference.",
        history=[UserMessage(content="first question")],
    )

    _prompt, second_history = _apply_user_instructions_to_initial_user_query(
        user_prompt="current question",
        user_instructions="New preference.",
        history=first_history,
    )

    content = second_history[0].content or ""
    assert content.count(_USER_INSTRUCTIONS_START) == 1
    assert "Old preference." not in content
    assert "New preference." in content
    assert content.endswith("first question")


def test_empty_user_instructions_strips_stale_wrapper_from_history() -> None:
    _prompt, wrapped_history = _apply_user_instructions_to_initial_user_query(
        user_prompt="current question",
        user_instructions="Old preference.",
        history=[UserMessage(content="first question")],
    )

    prompt, history = _apply_user_instructions_to_initial_user_query(
        user_prompt="current question",
        user_instructions="",
        history=wrapped_history,
    )

    assert prompt == "current question"
    assert history[0].content == "first question"


def test_empty_user_instructions_strips_stale_wrapper_from_current_prompt() -> None:
    wrapped_prompt, _history = _apply_user_instructions_to_initial_user_query(
        user_prompt="first question",
        user_instructions="Old preference.",
        history=[],
    )

    prompt, history = _apply_user_instructions_to_initial_user_query(
        user_prompt=wrapped_prompt,
        user_instructions=None,
        history=[],
    )

    assert prompt == "first question"
    assert history == []


def test_malformed_wrapper_is_left_unchanged() -> None:
    malformed = f"{_USER_INSTRUCTIONS_START}\nold instructions\nplain query"

    assert _strip_user_instructions_prefix(malformed) == malformed
