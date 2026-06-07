from matmaster.types.message_normalization import restore_persisted_assistant_state
from matmaster.types.messages import AssistantMessage, ProviderState


def test_assistant_state_dump_restore_round_trip_preserves_provider_state():
    msg = AssistantMessage(
        content="answer",
        reasoning_content="because",
        provider_state=ProviderState(transport="fake", payload={"sig": "z", "n": 1}),
    )

    dumped = msg.model_dump(mode="json")
    restored = restore_persisted_assistant_state(dumped)

    assert isinstance(restored, AssistantMessage)
    assert restored.provider_state == ProviderState(
        transport="fake", payload={"sig": "z", "n": 1}
    )


def test_restore_payload_json_serializable_round_trip():
    state = ProviderState(transport="fake", payload={"a": [1, 2], "b": {"c": "d"}})

    assert ProviderState.model_validate(state.model_dump(mode="json")) == state
