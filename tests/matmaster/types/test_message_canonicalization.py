from matmaster.types.message_normalization import canonicalize_messages_for_provider
from matmaster.types.messages import (
    AssistantMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


def test_canonicalize_merges_adjacent_text_user_messages() -> None:
    messages = [
        UserMessage(content="bundle"),
        UserMessage(
            content="current\n\n[Available attachments]\nfile_1 a.csv https://oss/a.csv"
        ),
    ]

    merged = canonicalize_messages_for_provider(messages)

    assert len(merged) == 1
    assert isinstance(merged[0], UserMessage)
    assert merged[0].content == (
        "bundle\n\ncurrent\n\n"
        "[Available attachments]\nfile_1 a.csv https://oss/a.csv"
    )


def test_canonicalize_preserves_images_when_merging_user_messages() -> None:
    messages = [
        UserMessage(content="bundle"),
        UserMessage(content="current", images=[{"url": "https://oss/img.png"}]),
    ]

    merged = canonicalize_messages_for_provider(messages)

    assert len(merged) == 1
    assert isinstance(merged[0], UserMessage)
    assert merged[0].content == "bundle\n\ncurrent"
    assert [image.url for image in merged[0].images] == ["https://oss/img.png"]


def test_canonicalize_does_not_cross_assistant_tool_pairs() -> None:
    messages = [
        UserMessage(content="bundle"),
        AssistantMessage(
            content=None,
            tool_calls=[ToolCallData(id="call-1", name="Read", arguments={})],
        ),
        ToolMessage(tool_call_id="call-1", tool_name="Read", content="ok"),
        UserMessage(content="current"),
    ]

    merged = canonicalize_messages_for_provider(messages)

    assert [message.role.value for message in merged] == [
        "user",
        "assistant",
        "tool",
        "user",
    ]

