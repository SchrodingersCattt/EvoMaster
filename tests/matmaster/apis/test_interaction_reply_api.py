from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.apis.chat_api import interaction_reply, router
from src.models.chat import InteractionReplyRequest
from src.utils.exceptions import (
    ConflictErrorResponse,
    ForbiddenErrorResponse,
    NotFoundErrorResponse,
)


class _ChatSvc:
    def __init__(self, allowed: bool = True, set_result: bool = True) -> None:
        self.allowed = allowed
        self.set_result = set_result
        self.set_calls: list[tuple[str, str | None, bool | None]] = []

    def can_access_session(self, session_id: str, user_id: str | None) -> bool:
        return self.allowed

    def set_bohrium_submit_confirmation(
        self, session_id: str, user_id: str | None, required: bool | None
    ) -> bool:
        self.set_calls.append((session_id, user_id, required))
        return self.set_result


class _StreamSvc:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

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


class _RedisDao:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, str]] = {}
        self.envelopes: list[tuple[str, str]] = []

    def read_pending_interaction(self, request_id: str) -> dict | None:
        return self.records.get(request_id)

    def answer_pending_interaction(self, request_id: str, envelope: str) -> str:
        record = self.records.get(request_id)
        if record is None:
            return "not_found"
        if record.get("state") != "pending":
            return "not_pending"
        record["state"] = "answered"
        self.envelopes.append((request_id, envelope))
        return "ok"


def _pending_record(
    *,
    session_id: str = "sess-1",
    kind: str = "ask_question",
    state: str = "pending",
) -> dict[str, str]:
    return {
        "kind": kind,
        "session_id": session_id,
        "task_id": "task-1",
        "invocation_id": "inv-1",
        "state": state,
        "expires_at": "",
    }


def _reply_req(kind: str = "ask_question") -> InteractionReplyRequest:
    return InteractionReplyRequest(
        kind=kind,
        payload={
            "answers": {"Q1": "A1"},
            "annotations": {"Q1": {"freeform": "notes"}},
        },
    )


def _submit_reply(
    *, decision: str = "submit", disable: bool | None = False
) -> InteractionReplyRequest:
    payload: dict = {
        "decision": decision,
        "submit_arguments": {"action": "submit", "cmd": "run > log 2>&1"},
        "reported_input_file_changes": [],
    }
    if disable is not None:
        payload["disable_future_confirmation"] = disable
    return InteractionReplyRequest(kind="submit_review", payload=payload)


def _run_reply(
    dao: _RedisDao,
    chat: _ChatSvc,
    req: InteractionReplyRequest,
    *,
    request_id: str = "sr_1",
):
    with patch("src.apis.chat_api.get_redis_dao", return_value=dao):
        return asyncio.run(
            interaction_reply(
                session_id="sess-1",
                request_id=request_id,
                req=req,
                user_id="user-1",
                chat_svc=chat,
                stream_svc=_StreamSvc(),
                events_svc=_EventsSvc(),
            )
        )


def test_interaction_reply_publishes_structured_event_and_reply_envelope() -> None:
    dao = _RedisDao()
    dao.records["aq_1"] = _pending_record()
    stream = _StreamSvc()
    events = _EventsSvc()

    with patch("src.apis.chat_api.get_redis_dao", return_value=dao):
        asyncio.run(
            interaction_reply(
                session_id="sess-1",
                request_id="aq_1",
                req=_reply_req(),
                user_id="user-1",
                chat_svc=_ChatSvc(),
                stream_svc=stream,
                events_svc=events,
            )
        )

    payload = stream.published[0][1]
    assert payload["type"] == "interaction_reply"
    assert payload["kind"] == "ask_question"
    assert payload["request_id"] == "aq_1"
    assert payload["payload"] == _reply_req().payload
    assert payload["task_id"] == "task-1"
    assert payload["invocation_id"] == "inv-1"
    assert json.loads(dao.envelopes[0][1]) == {
        "kind": "ask_question",
        "request_id": "aq_1",
        "payload": _reply_req().payload,
    }
    assert events.history == [("sess-1", payload, "user-1")]


def test_interaction_reply_rejects_empty_kind() -> None:
    with pytest.raises(ValidationError, match="kind"):
        InteractionReplyRequest(kind=" ", payload={})


def test_reply_endpoint_rejects_inaccessible_session() -> None:
    with pytest.raises(ForbiddenErrorResponse):
        asyncio.run(
            interaction_reply(
                session_id="sess-1",
                request_id="aq_1",
                req=_reply_req(),
                user_id="user-1",
                chat_svc=_ChatSvc(allowed=False),
                stream_svc=_StreamSvc(),
                events_svc=_EventsSvc(),
            )
        )


def test_reply_404_when_request_not_found() -> None:
    with patch("src.apis.chat_api.get_redis_dao", return_value=_RedisDao()):
        with pytest.raises(NotFoundErrorResponse):
            asyncio.run(
                interaction_reply(
                    session_id="sess-1",
                    request_id="aq_missing",
                    req=_reply_req(),
                    user_id="user-1",
                    chat_svc=_ChatSvc(),
                    stream_svc=_StreamSvc(),
                    events_svc=_EventsSvc(),
                )
            )


def test_reply_409_when_kind_mismatch() -> None:
    dao = _RedisDao()
    dao.records["aq_K"] = _pending_record(kind="ask_question")

    with patch("src.apis.chat_api.get_redis_dao", return_value=dao):
        with pytest.raises(ConflictErrorResponse):
            asyncio.run(
                interaction_reply(
                    session_id="sess-1",
                    request_id="aq_K",
                    req=_reply_req(kind="submit_review"),
                    user_id="user-1",
                    chat_svc=_ChatSvc(),
                    stream_svc=_StreamSvc(),
                    events_svc=_EventsSvc(),
                )
            )


def test_reply_409_when_not_pending() -> None:
    dao = _RedisDao()
    dao.records["aq_L"] = _pending_record(state="timeout")

    with patch("src.apis.chat_api.get_redis_dao", return_value=dao):
        with pytest.raises(ConflictErrorResponse):
            asyncio.run(
                interaction_reply(
                    session_id="sess-1",
                    request_id="aq_L",
                    req=_reply_req(),
                    user_id="user-1",
                    chat_svc=_ChatSvc(),
                    stream_svc=_StreamSvc(),
                    events_svc=_EventsSvc(),
                )
            )


def test_reply_404_when_session_mismatch() -> None:
    dao = _RedisDao()
    dao.records["aq_M"] = _pending_record(session_id="other-session")

    with patch("src.apis.chat_api.get_redis_dao", return_value=dao):
        with pytest.raises(NotFoundErrorResponse):
            asyncio.run(
                interaction_reply(
                    session_id="sess-1",
                    request_id="aq_M",
                    req=_reply_req(),
                    user_id="user-1",
                    chat_svc=_ChatSvc(),
                    stream_svc=_StreamSvc(),
                    events_svc=_EventsSvc(),
                )
            )


def test_legacy_reply_route_is_removed() -> None:
    paths = {getattr(route, "path", "") for route in router.routes}
    removed_path = "/{session_id}/" + "ask_question" + "_reply"

    assert removed_path not in paths
    assert "/{session_id}/interactions/{request_id}/reply" in paths


def test_reply_submit_disable_persists_confirmation_off() -> None:
    dao = _RedisDao()
    dao.records["sr_1"] = _pending_record(kind="submit_review")
    chat = _ChatSvc()

    _run_reply(dao, chat, _submit_reply(decision="submit", disable=True))

    assert chat.set_calls == [("sess-1", "user-1", False)]


def test_reply_submit_without_disable_does_not_persist() -> None:
    dao = _RedisDao()
    dao.records["sr_1"] = _pending_record(kind="submit_review")
    chat = _ChatSvc()

    _run_reply(dao, chat, _submit_reply(decision="submit", disable=False))

    assert chat.set_calls == []


def test_reply_submit_missing_disable_does_not_persist() -> None:
    dao = _RedisDao()
    dao.records["sr_1"] = _pending_record(kind="submit_review")
    chat = _ChatSvc()

    _run_reply(dao, chat, _submit_reply(decision="submit", disable=None))

    assert chat.set_calls == []


def test_reply_reject_with_disable_does_not_persist() -> None:
    dao = _RedisDao()
    dao.records["sr_1"] = _pending_record(kind="submit_review")
    chat = _ChatSvc()

    _run_reply(dao, chat, _submit_reply(decision="reject", disable=True))

    assert chat.set_calls == []


def test_reply_ask_question_with_disable_does_not_persist() -> None:
    dao = _RedisDao()
    dao.records["aq_1"] = _pending_record(kind="ask_question")
    chat = _ChatSvc()
    req = InteractionReplyRequest(
        kind="ask_question",
        payload={"decision": "submit", "disable_future_confirmation": True},
    )

    _run_reply(dao, chat, req, request_id="aq_1")

    assert chat.set_calls == []


def test_reply_submit_disable_set_failure_still_returns_ok() -> None:
    dao = _RedisDao()
    dao.records["sr_1"] = _pending_record(kind="submit_review")
    chat = _ChatSvc(set_result=False)

    result = _run_reply(dao, chat, _submit_reply(decision="submit", disable=True))

    assert result.msg == "ok"
    assert chat.set_calls == [("sess-1", "user-1", False)]
