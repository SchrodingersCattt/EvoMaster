from __future__ import annotations

import pytest

from matmaster.providers.transports.anthropic_messages import AnthropicMessagesTransport
from matmaster.providers.transports.chat_completions import ChatCompletionsTransport
from matmaster.providers.transports.responses import ResponsesTransport
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


def _provider(**kwargs) -> ResponsesTransport:
    return ResponsesTransport(
        model="matmaster/gpt-5.5",
        api_key="sk-test",
        reasoning_effort="xhigh",
        reasoning_summary="detailed",
        **kwargs,
    )


def test_build_kwargs_extracts_instructions_and_sets_stateless_flags() -> None:
    kwargs = _provider().build_kwargs(
        [SystemMessage(content="sys"), UserMessage(content="hi")],
        tools=None,
    )

    assert kwargs["model"] == "matmaster/gpt-5.5"
    assert kwargs["instructions"] == "sys"
    assert kwargs["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    ]
    assert kwargs["reasoning"] == {"effort": "xhigh", "summary": "detailed"}
    assert kwargs["include"] == ["reasoning.encrypted_content"]
    assert kwargs["store"] is False
    assert "temperature" not in kwargs
    assert "max_output_tokens" not in kwargs
    assert "stream" not in kwargs


def test_multiple_system_messages_join_with_blank_lines() -> None:
    kwargs = _provider().build_kwargs(
        [
            SystemMessage(content="a"),
            SystemMessage(content="b"),
            UserMessage(content="hi"),
        ],
        tools=None,
    )

    assert kwargs["instructions"] == "a\n\nb"


def test_max_output_tokens_only_when_set() -> None:
    kwargs = _provider(max_tokens=4096).build_kwargs(
        [UserMessage(content="hi")], tools=None
    )

    assert kwargs["max_output_tokens"] == 4096


def test_user_text_and_image_convert_to_input_parts_with_detail() -> None:
    msg = UserMessage(
        content="look",
        images=[
            ImageContentPart(url="https://example.com/a.png", detail="high"),
            ImageContentPart(url="data:image/png;base64,AAAA"),
        ],
    )

    assert _provider().convert_messages([msg]) == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "look"},
                {
                    "type": "input_image",
                    "image_url": "https://example.com/a.png",
                    "detail": "high",
                },
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,AAAA",
                    "detail": "auto",
                },
            ],
        }
    ]


def test_assistant_replays_reasoning_then_easy_message_then_function_call() -> None:
    state = ProviderState(
        transport="responses",
        payload={
            "reasoning": [
                {
                    "type": "reasoning",
                    "id": "rs_1",
                    "summary": [],
                    "encrypted_content": "enc",
                },
            ]
        },
    )
    msg = AssistantMessage(
        content="thinking done",
        provider_state=state,
        tool_calls=[ToolCallData(id="call_1", name="search", arguments={"q": "x"})],
    )

    assert _provider().convert_messages(
        [msg, ToolMessage(content="result", tool_call_id="call_1", tool_name="search")]
    ) == [
        {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "enc"},
        {"role": "assistant", "content": "thinking done"},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "search",
            "arguments": '{"q": "x"}',
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "result"},
    ]


def test_empty_assistant_turn_drops_reasoning_to_avoid_orphan() -> None:
    state = ProviderState(
        transport="responses",
        payload={
            "reasoning": [
                {
                    "type": "reasoning",
                    "id": "rs_1",
                    "summary": [],
                    "encrypted_content": "enc",
                }
            ]
        },
    )
    msg = AssistantMessage(content="", provider_state=state)

    assert _provider().convert_messages([msg]) == []


def test_mismatched_provider_state_is_discarded_but_content_and_tools_remain() -> None:
    msg = AssistantMessage(
        content="visible",
        provider_state=ProviderState(
            transport="anthropic_messages",
            payload={
                "thinking": [{"type": "thinking", "thinking": "bad", "signature": "x"}]
            },
        ),
        tool_calls=[ToolCallData(id="call_1", name="search", arguments={})],
    )

    assert _provider().convert_messages(
        [msg, ToolMessage(content="r", tool_call_id="call_1", tool_name="search")]
    ) == [
        {"role": "assistant", "content": "visible"},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "search",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "r"},
    ]


def test_parallel_tool_calls_and_outputs_map_in_order() -> None:
    messages = [
        AssistantMessage(
            content="",
            tool_calls=[
                ToolCallData(id="call_a", name="a", arguments={}),
                ToolCallData(id="call_b", name="b", arguments={}),
            ],
        ),
        ToolMessage(content="A", tool_call_id="call_a", tool_name="a"),
        ToolMessage(content="B", tool_call_id="call_b", tool_name="b"),
        UserMessage(content="next"),
    ]

    assert _provider().convert_messages(messages) == [
        {"type": "function_call", "call_id": "call_a", "name": "a", "arguments": "{}"},
        {"type": "function_call", "call_id": "call_b", "name": "b", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_a", "output": "A"},
        {"type": "function_call_output", "call_id": "call_b", "output": "B"},
        {"role": "user", "content": [{"type": "input_text", "text": "next"}]},
    ]


def test_tools_convert_to_flat_function_with_strict_false() -> None:
    kwargs = _provider().build_kwargs(
        [UserMessage(content="hi")],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "do search",
                    "parameters": {
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                    },
                },
            },
            {"type": "function", "function": {"name": "noargs"}},
        ],
    )

    assert kwargs["tools"] == [
        {
            "type": "function",
            "name": "search",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            "strict": False,
            "description": "do search",
        },
        {
            "type": "function",
            "name": "noargs",
            "parameters": {"type": "object", "properties": {}},
            "strict": False,
        },
    ]
    assert kwargs["tool_choice"] == "auto"


@pytest.mark.parametrize(
    "tool_choice,expected",
    [
        (None, "auto"),
        ("auto", "auto"),
        ("required", "required"),
        ("any", "required"),
        (
            {"type": "function", "function": {"name": "search"}},
            {"type": "function", "name": "search"},
        ),
    ],
)
def test_tool_choice_maps_without_fail_fast(tool_choice, expected) -> None:
    kwargs = _provider().build_kwargs(
        [UserMessage(content="hi")],
        tools=[{"type": "function", "function": {"name": "search"}}],
        tool_choice=tool_choice,
    )

    assert kwargs["tool_choice"] == expected


def test_tool_choice_none_without_tools_omits_tools_and_tool_choice() -> None:
    kwargs = _provider().build_kwargs(
        [UserMessage(content="hi")], tools=None, tool_choice="none"
    )

    assert "tools" not in kwargs
    assert "tool_choice" not in kwargs


def test_tool_choice_none_with_tools_sends_none_alongside_tools() -> None:
    kwargs = _provider().build_kwargs(
        [UserMessage(content="hi")],
        tools=[{"type": "function", "function": {"name": "search"}}],
        tool_choice="none",
    )

    assert kwargs["tool_choice"] == "none"
    assert kwargs["tools"][0]["name"] == "search"


def test_orphan_tool_result_fails_fast() -> None:
    with pytest.raises(LLMError) as exc_info:
        _provider().convert_messages(
            [ToolMessage(content="x", tool_call_id="call_1", tool_name="s")]
        )

    assert exc_info.value.retryable is False
    assert exc_info.value.error_category == "bad_request"


def test_responses_discards_chat_completions_tag_keeps_content_and_tools() -> None:
    msg = AssistantMessage(
        content="visible",
        provider_state=ProviderState(transport="chat_completions", payload={"x": 1}),
        tool_calls=[ToolCallData(id="call_1", name="s", arguments={})],
    )

    assert _provider().convert_messages(
        [msg, ToolMessage(content="r", tool_call_id="call_1", tool_name="s")]
    ) == [
        {"role": "assistant", "content": "visible"},
        {"type": "function_call", "call_id": "call_1", "name": "s", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_1", "output": "r"},
    ]


def test_responses_claims_only_its_own_tag() -> None:
    own = AssistantMessage(
        content="hi",
        provider_state=ProviderState(
            transport="responses",
            payload={
                "reasoning": [
                    {
                        "type": "reasoning",
                        "id": "rs_1",
                        "summary": [],
                        "encrypted_content": "e",
                    }
                ]
            },
        ),
    )
    foreign = AssistantMessage(
        content="hi",
        provider_state=ProviderState(
            transport="anthropic_messages",
            payload={"thinking": [{"type": "thinking"}]},
        ),
    )

    assert _provider()._claim_provider_state(own) == own.provider_state.payload
    assert _provider()._claim_provider_state(foreign) is None


def test_existing_transports_discard_responses_tag() -> None:
    msg = AssistantMessage(
        content="hi",
        provider_state=ProviderState(
            transport="responses",
            payload={
                "reasoning": [
                    {
                        "type": "reasoning",
                        "id": "rs_1",
                        "summary": [],
                        "encrypted_content": "e",
                    }
                ]
            },
        ),
    )

    chat_transport = ChatCompletionsTransport(model="m", api_key="sk-test")
    anthropic_transport = AnthropicMessagesTransport(
        model="claude-opus-4-6",
        api_key="sk-test",
    )

    assert chat_transport._claim_provider_state(msg) is None
    assert anthropic_transport._claim_provider_state(msg) is None
