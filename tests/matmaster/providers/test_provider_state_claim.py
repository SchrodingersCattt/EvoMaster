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
