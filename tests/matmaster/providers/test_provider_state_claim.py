from matmaster.providers.transports.anthropic_messages import AnthropicMessagesTransport
from matmaster.providers.transports.chat_completions import ChatCompletionsTransport
from matmaster.types.messages import (
    AssistantMessage,
    ProviderState,
    ToolCallData,
    ToolMessage,
)


def _make_transport():
    return ChatCompletionsTransport.__new__(ChatCompletionsTransport)


def test_claim_returns_payload_when_tag_matches():
    t = _make_transport()
    msg = AssistantMessage(
        content="x",
        provider_state=ProviderState(transport="chat_completions", payload={"k": 1}),
    )
    assert t._claim_provider_state(msg) == {"k": 1}


def test_claim_returns_none_when_tag_mismatch():
    t = _make_transport()
    msg = AssistantMessage(
        content="x",
        provider_state=ProviderState(transport="anthropic_messages", payload={"k": 1}),
    )
    assert t._claim_provider_state(msg) is None


def test_claim_returns_none_when_no_state():
    t = _make_transport()
    assert t._claim_provider_state(AssistantMessage(content="x")) is None


def test_anthropic_convert_discards_chat_completions_state() -> None:
    t = AnthropicMessagesTransport(model="claude-opus-4-6", api_key="sk-test")
    msg = AssistantMessage(
        content="visible",
        provider_state=ProviderState(
            transport="chat_completions",
            payload={
                "thinking": [
                    {"type": "thinking", "thinking": "wrong", "signature": "x"}
                ]
            },
        ),
        tool_calls=[ToolCallData(id="toolu_1", name="search", arguments={})],
    )

    assert t.convert_messages(
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
