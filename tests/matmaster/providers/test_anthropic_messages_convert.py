from __future__ import annotations

import pytest

from matmaster.providers.transports.anthropic_messages import (
    AnthropicMessagesTransport,
    BedrockAnthropicTransport,
    _tool_result_block,
)
from matmaster.types.errors import LLMError
from matmaster.types.messages import (
    AssistantMessage,
    ImageContentPart,
    ProviderState,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


def _provider(**kwargs) -> AnthropicMessagesTransport:
    return AnthropicMessagesTransport(
        model="claude-opus-4-6",
        api_key="sk-test",
        reasoning_effort="max",
        **kwargs,
    )


def test_build_kwargs_extracts_system_and_omits_temperature() -> None:
    kwargs = _provider().build_kwargs(
        [SystemMessage(content="sys"), UserMessage(content="hi")],
        tools=None,
    )

    assert kwargs["model"] == "claude-opus-4-6"
    assert kwargs["system"] == "sys"
    assert kwargs["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    ]
    assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert kwargs["output_config"] == {"effort": "max"}
    assert "temperature" not in kwargs
    assert "max_tokens" not in kwargs


def test_user_text_and_images_convert_to_anthropic_blocks() -> None:
    msg = UserMessage(
        content="look",
        images=[
            ImageContentPart(url="data:image/png;base64,AAAA", mime_type="image/png"),
            ImageContentPart(url="https://example.com/a.png"),
        ],
    )

    assert _provider().convert_messages([msg]) == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "AAAA",
                    },
                },
                {
                    "type": "image",
                    "source": {"type": "url", "url": "https://example.com/a.png"},
                },
            ],
        }
    ]


def test_bedrock_user_url_image_is_inlined_as_base64(monkeypatch) -> None:
    import matmaster.providers.transports.anthropic_messages as transport_module

    def fake_inline(url: str) -> tuple[str, str]:
        assert url == "https://oss.example.com/chat/a.png"
        return ("image/png", "aGVsbG8=")

    monkeypatch.setattr(
        transport_module,
        "inline_image_url_as_base64",
        fake_inline,
        raising=False,
    )
    msg = UserMessage(
        content="look",
        images=[ImageContentPart(url="https://oss.example.com/chat/a.png")],
    )

    wire = BedrockAnthropicTransport(model="m", api_key="k").convert_messages([msg])

    assert wire[0]["content"][1]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": "aGVsbG8=",
    }


def test_bedrock_data_uri_image_does_not_fetch(monkeypatch) -> None:
    import matmaster.providers.transports.anthropic_messages as transport_module

    def fail_inline(url: str) -> tuple[str, str]:
        raise AssertionError(f"unexpected fetch for {url}")

    monkeypatch.setattr(
        transport_module,
        "inline_image_url_as_base64",
        fail_inline,
        raising=False,
    )
    msg = UserMessage(
        content="look",
        images=[ImageContentPart(url="data:image/png;base64,aGVsbG8=")],
    )

    wire = BedrockAnthropicTransport(model="m", api_key="k").convert_messages([msg])

    assert wire[0]["content"][1]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": "aGVsbG8=",
    }


def test_bedrock_url_inline_failure_is_non_retryable_llm_error(monkeypatch) -> None:
    import matmaster.providers.transports.anthropic_messages as transport_module
    from matmaster.providers.image_payloads import ImagePayloadError

    def fail_inline(url: str) -> tuple[str, str]:
        raise ImagePayloadError("image size is unknown")

    monkeypatch.setattr(
        transport_module,
        "inline_image_url_as_base64",
        fail_inline,
    )
    msg = UserMessage(
        content="look",
        images=[ImageContentPart(url="https://oss.example.com/chat/a.png")],
    )

    with pytest.raises(LLMError) as exc_info:
        BedrockAnthropicTransport(model="m", api_key="k").convert_messages([msg])

    assert exc_info.value.retryable is False
    assert exc_info.value.error_category == "bad_request"
    assert "could not be inlined" in str(exc_info.value)


def test_assistant_replays_matching_thinking_before_tool_use() -> None:
    provider = _provider()
    state = ProviderState(
        transport="anthropic_messages",
        payload={
            "thinking": [
                {"type": "thinking", "thinking": "plan", "signature": "sig-1"},
            ]
        },
    )
    msg = AssistantMessage(
        content="",
        provider_state=state,
        tool_calls=[ToolCallData(id="toolu_1", name="search", arguments={"q": "x"})],
    )

    assert provider.convert_messages(
        [msg, ToolMessage(content="result", tool_call_id="toolu_1", tool_name="search")]
    ) == [
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "plan", "signature": "sig-1"},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "search",
                    "input": {"q": "x"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": "result",
                }
            ],
        },
    ]


def test_mismatched_provider_state_is_discarded_but_content_and_tools_remain() -> None:
    msg = AssistantMessage(
        content="visible",
        provider_state=ProviderState(
            transport="chat_completions",
            payload={
                "thinking": [{"type": "thinking", "thinking": "bad", "signature": "x"}]
            },
        ),
        tool_calls=[ToolCallData(id="toolu_1", name="search", arguments={})],
    )

    assert _provider().convert_messages(
        [msg, ToolMessage(content="result", tool_call_id="toolu_1", tool_name="search")]
    ) == [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "visible"},
                {"type": "tool_use", "id": "toolu_1", "name": "search", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": "result",
                }
            ],
        },
    ]


def test_parallel_tool_results_are_merged_into_single_user_message_before_text() -> (
    None
):
    messages = [
        AssistantMessage(
            content="",
            tool_calls=[
                ToolCallData(id="toolu_a", name="a", arguments={}),
                ToolCallData(id="toolu_b", name="b", arguments={}),
            ],
        ),
        ToolMessage(content="A", tool_call_id="toolu_a", tool_name="a"),
        ToolMessage(content="B", tool_call_id="toolu_b", tool_name="b"),
        UserMessage(content="next"),
    ]

    assert _provider().convert_messages(messages) == [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_a", "name": "a", "input": {}},
                {"type": "tool_use", "id": "toolu_b", "name": "b", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_a", "content": "A"},
                {"type": "tool_result", "tool_use_id": "toolu_b", "content": "B"},
                {"type": "text", "text": "next"},
            ],
        },
    ]


def test_tool_result_with_images_becomes_block_array() -> None:
    transport = AnthropicMessagesTransport(model="m", api_key="k")
    messages = [
        UserMessage(content="看一下图"),
        AssistantMessage(
            content=None,
            tool_calls=[
                ToolCallData(id="tc1", name="Read", arguments={"file_path": "/a.png"})
            ],
        ),
        ToolMessage(
            tool_call_id="tc1",
            tool_name="Read",
            content="Read image: /a.png (image/png, 1 KB)",
            images=[
                ImageContentPart(
                    url="data:image/png;base64,aGVsbG8=", mime_type="image/png"
                )
            ],
        ),
    ]
    wire = transport.convert_messages(messages)
    result_block = wire[-1]["content"][0]
    assert result_block["type"] == "tool_result"
    blocks = result_block["content"]
    assert blocks[0] == {
        "type": "text",
        "text": "Read image: /a.png (image/png, 1 KB)",
    }
    assert blocks[1]["type"] == "image"
    assert blocks[1]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": "aGVsbG8=",
    }


def test_tool_result_without_images_stays_string() -> None:
    block = _tool_result_block(
        ToolMessage(tool_call_id="tc1", tool_name="Read", content="plain")
    )
    assert block["content"] == "plain"


@pytest.mark.parametrize(
    "tool_choice",
    ["required", "any", {"type": "function", "function": {"name": "x"}}],
)
def test_tool_choice_forced_modes_fail_fast_under_thinking(tool_choice) -> None:
    with pytest.raises(LLMError) as exc_info:
        _provider().build_kwargs(
            [UserMessage(content="hi")], tools=[], tool_choice=tool_choice
        )

    assert exc_info.value.retryable is False
    assert exc_info.value.error_category == "bad_request"


def test_assistant_tool_call_without_tool_result_fails_fast() -> None:
    with pytest.raises(LLMError) as exc_info:
        _provider().convert_messages(
            [
                AssistantMessage(
                    content="",
                    tool_calls=[
                        ToolCallData(id="toolu_1", name="search", arguments={})
                    ],
                )
            ]
        )

    assert exc_info.value.retryable is False
    assert exc_info.value.error_category == "bad_request"


def test_duplicate_assistant_tool_call_ids_fail_fast() -> None:
    with pytest.raises(LLMError) as exc_info:
        _provider().convert_messages(
            [
                AssistantMessage(
                    content="",
                    tool_calls=[
                        ToolCallData(id="toolu_1", name="search", arguments={}),
                        ToolCallData(id="toolu_1", name="lookup", arguments={}),
                    ],
                )
            ]
        )

    assert exc_info.value.retryable is False
    assert exc_info.value.error_category == "bad_request"


@pytest.mark.parametrize(
    "messages",
    [
        [ToolMessage(content="orphan", tool_call_id="toolu_1", tool_name="search")],
        [
            AssistantMessage(
                content="",
                tool_calls=[ToolCallData(id="toolu_a", name="search", arguments={})],
            ),
            ToolMessage(content="mismatch", tool_call_id="toolu_b", tool_name="search"),
        ],
    ],
)
def test_orphan_or_mismatched_tool_result_fails_fast(messages) -> None:
    with pytest.raises(LLMError) as exc_info:
        _provider().convert_messages(messages)

    assert exc_info.value.retryable is False
    assert exc_info.value.error_category == "bad_request"
