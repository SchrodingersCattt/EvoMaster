from matmaster.types.messages import AssistantMessage, ProviderState
from src.services.chat_history import ChatHistoryConverter


def _assistant_state_event(state: dict) -> dict:
    return {
        "source": "MatMaster",
        "type": "assistant_state",
        "content": state,
        "session_id": "sess-1",
        "task_id": "task-1",
    }


def test_events_to_messages_restores_provider_state_no_tool_calls():
    msg = AssistantMessage(
        content="hi",
        provider_state=ProviderState(transport="fake", payload={"sig": "z"}),
    )
    events = [
        {"source": "User", "type": "query", "content": "q"},
        _assistant_state_event(msg.model_dump(mode="json")),
    ]

    restored = ChatHistoryConverter.events_to_messages(events)

    assistants = [m for m in restored if isinstance(m, AssistantMessage)]
    assert assistants, "expected an assistant message"
    assert assistants[-1].provider_state == ProviderState(
        transport="fake", payload={"sig": "z"}
    )
