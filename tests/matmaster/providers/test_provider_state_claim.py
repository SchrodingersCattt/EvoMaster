from matmaster.providers.transports.anthropic_messages import AnthropicMessagesTransport
from matmaster.providers.transports.chat_completions import ChatCompletionsTransport
from matmaster.types.messages import AssistantMessage, ProviderState


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


def test_cross_transport_claim_discards_mismatched_state() -> None:
    chat_transport = _make_transport()
    anthropic_transport = AnthropicMessagesTransport(
        model="claude-opus-4-6", api_key="sk-test"
    )
    chat_state_msg = AssistantMessage(
        content="x",
        provider_state=ProviderState(
            transport="chat_completions", payload={"k": "chat"}
        ),
    )
    anthropic_state_msg = AssistantMessage(
        content="x",
        provider_state=ProviderState(
            transport="anthropic_messages", payload={"k": "anthropic"}
        ),
    )

    assert chat_transport._claim_provider_state(chat_state_msg) == {"k": "chat"}
    assert anthropic_transport._claim_provider_state(chat_state_msg) is None
    assert anthropic_transport._claim_provider_state(anthropic_state_msg) == {
        "k": "anthropic"
    }
    assert chat_transport._claim_provider_state(anthropic_state_msg) is None
