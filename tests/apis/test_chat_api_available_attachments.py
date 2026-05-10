from unittest.mock import MagicMock

import pytest

from src.apis import chat_api
from src.apis.chat_api import _build_agent_prompt, chat_stream
from src.models.chat import ChatSendRequest


def test_build_agent_prompt_appends_available_attachments() -> None:
    events = [
        {
            "source": "User",
            "type": "query",
            "content": "old turn",
            "files": ["https://oss.example.com/chat/old.csv"],
            "images": ["https://oss.example.com/chat/old.png"],
            "workspace_paths": ["/share/old.cif"],
            "session_id": "sess-attachments",
            "task_id": "task-old",
        },
        {
            "source": "User",
            "type": "query",
            "content": "new turn",
            "files": ["https://oss.example.com/chat/new.csv"],
            "images": ["https://oss.example.com/chat/new.png"],
            "workspace_paths": ["/share/new.cif"],
            "session_id": "sess-attachments",
            "task_id": "task-new",
        },
    ]

    prompt = _build_agent_prompt("new turn", events)

    assert "[Available attachments]" in prompt
    assert "file_1 old.csv https://oss.example.com/chat/old.csv" in prompt
    assert "file_2 new.csv https://oss.example.com/chat/new.csv" in prompt
    assert "image_1 old.png https://oss.example.com/chat/old.png" in prompt
    assert "image_2 new.png https://oss.example.com/chat/new.png" in prompt
    assert "workspace_1 /share/old.cif" in prompt
    assert "workspace_2 /share/new.cif" in prompt


@pytest.mark.asyncio
async def test_chat_stream_builds_attachment_prompt_from_user_query_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat_api, "REDIS_URL", "redis://unit-test")
    monkeypatch.setattr(chat_api, "check_quota", MagicMock())

    request = MagicMock()
    request.url.path = "/api/v1/chat/sessions/sess-attachments/stream"
    request.headers = {}
    request.method = "POST"

    chat_svc = MagicMock()
    chat_svc.can_access_session.return_value = True

    stream_svc = MagicMock()
    stream_svc.prepare_send_message.return_value = MagicMock()
    stream_svc.generate_send_stream.return_value = iter(())

    events_svc = MagicMock()
    events_svc.get_session_user_query_events.return_value = [
        {
            "source": "User",
            "type": "query",
            "content": "old turn",
            "files": ["https://oss.example.com/chat/old.csv"],
        },
        {
            "source": "MatMaster",
            "type": "response",
            "content": "should not be requested by this path",
        },
    ]
    events_svc.get_session_events.side_effect = AssertionError(
        "chat_stream should not load full session events for attachment manifest"
    )

    await chat_stream(
        request=request,
        session_id="sess-attachments",
        req=ChatSendRequest(content="new turn", mode="direct"),
        user_id=None,
        org_id=None,
        chat_svc=chat_svc,
        stream_svc=stream_svc,
        events_svc=events_svc,
    )

    events_svc.get_session_user_query_events.assert_called_once_with("sess-attachments")
    events_svc.get_session_events.assert_not_called()
    stream_svc.generate_send_stream.assert_called_once()
    user_prompt = stream_svc.generate_send_stream.call_args.args[1]
    assert "new turn" in user_prompt
    assert "file_1 old.csv https://oss.example.com/chat/old.csv" in user_prompt
