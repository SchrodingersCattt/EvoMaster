import pytest

from matmaster.types.messages import (
    AssistantMessage,
    LLMResponse,
    ProviderState,
    StreamChunk,
)


def test_provider_state_is_frozen():
    state = ProviderState(transport="fake", payload={"k": "v"})
    with pytest.raises(Exception):
        state.transport = "other"


def test_provider_state_json_round_trip():
    state = ProviderState(transport="anthropic_messages", payload={"sig": "abc", "n": 1})
    dumped = state.model_dump(mode="json")
    assert dumped == {"transport": "anthropic_messages", "payload": {"sig": "abc", "n": 1}}
    assert ProviderState.model_validate(dumped) == state


def test_assistant_message_carries_provider_state_in_json():
    state = ProviderState(transport="fake", payload={"x": [1, 2, 3]})
    msg = AssistantMessage(content="hi", provider_state=state)
    dumped = msg.model_dump(mode="json")
    assert dumped["provider_state"] == {
        "transport": "fake",
        "payload": {"x": [1, 2, 3]},
    }
    assert AssistantMessage.model_validate(dumped).provider_state == state


def test_defaults_none():
    assert AssistantMessage(content="x").provider_state is None
    assert LLMResponse(content="x").provider_state is None
    assert StreamChunk(content="x").provider_state is None
