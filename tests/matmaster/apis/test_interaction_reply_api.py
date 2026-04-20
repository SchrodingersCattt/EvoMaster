from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from src.apis.chat_api import ask_question_reply, router
from src.models.chat import ChatAskQuestionReplyRequest
from src.utils.exceptions import ConflictErrorResponse, ForbiddenErrorResponse


class _ReplyQueue:
    def __init__(self) -> None:
        self.values: list[str] = []

    def put_content(self, content: str) -> None:
        self.values.append(content)


class _ChatSvc:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    def can_access_session(self, session_id: str, user_id: str | None) -> bool:
        return self.allowed


class _StreamSvc:
    def __init__(self, queue: _ReplyQueue | None = None) -> None:
        self.queue = queue
        self.published: list[tuple[str, dict]] = []

    def get_reply_queue(self, session_id: str):
        return self.queue

    def get_run_context(self, session_id: str) -> dict:
        return {"task_id": "task-1", "invocation_id": "inv-1"}

    def publish_reply_event(self, session_id: str, payload: dict) -> None:
        self.published.append((session_id, payload))


class _EventsSvc:
    def __init__(self) -> None:
        self.history: list[tuple[str, dict, str | None]] = []

    def add_history_event(
        self,
        session_id: str,
        payload: dict,
        *,
        user_id: str | None = None,
    ) -> None:
        self.history.append((session_id, payload, user_id))


def _ask_question_req() -> ChatAskQuestionReplyRequest:
    return ChatAskQuestionReplyRequest(
        request_id="aq_1",
        answers={"Q1": "A1"},
        annotations={"Q1": {"freeform": "notes"}},
    )


def test_ask_question_reply_publishes_structured_content_and_json_queue_value() -> None:
    queue = _ReplyQueue()
    stream = _StreamSvc(queue)
    events = _EventsSvc()

    asyncio.run(
        ask_question_reply(
            session_id="sess-1",
            req=_ask_question_req(),
            user_id="user-1",
            chat_svc=_ChatSvc(),
            stream_svc=stream,
            events_svc=events,
        )
    )

    payload = stream.published[0][1]
    assert payload["type"] == "ask_question_reply"
    assert payload["content"] == {
        "request_id": "aq_1",
        "answers": {"Q1": "A1"},
        "annotations": {"Q1": {"freeform": "notes"}},
    }
    assert json.loads(queue.values[0]) == {"payload": payload["content"]}
    assert events.history == [("sess-1", payload, "user-1")]


def test_ask_question_reply_rejects_empty_request_id() -> None:
    with pytest.raises(ValidationError, match="request_id"):
        ChatAskQuestionReplyRequest(request_id=" ", answers={"Q1": "A1"})


def test_ask_question_reply_requires_answers_or_annotations() -> None:
    with pytest.raises(ValidationError, match="answers"):
        ChatAskQuestionReplyRequest(request_id="aq_1")


def test_reply_endpoint_rejects_inaccessible_session() -> None:
    with pytest.raises(ForbiddenErrorResponse):
        asyncio.run(
            ask_question_reply(
                session_id="sess-1",
                req=_ask_question_req(),
                user_id="user-1",
                chat_svc=_ChatSvc(allowed=False),
                stream_svc=_StreamSvc(_ReplyQueue()),
                events_svc=_EventsSvc(),
            )
        )


def test_reply_endpoint_rejects_missing_active_run() -> None:
    with pytest.raises(ConflictErrorResponse):
        asyncio.run(
            ask_question_reply(
                session_id="sess-1",
                req=_ask_question_req(),
                user_id="user-1",
                chat_svc=_ChatSvc(),
                stream_svc=_StreamSvc(queue=None),
                events_svc=_EventsSvc(),
            )
        )


def test_legacy_reply_route_is_removed() -> None:
    paths = {getattr(route, "path", "") for route in router.routes}
    removed_path = "/{session_id}/" + "confirmation" + "_reply"

    assert removed_path not in paths
    assert "/{session_id}/ask_question_reply" in paths
