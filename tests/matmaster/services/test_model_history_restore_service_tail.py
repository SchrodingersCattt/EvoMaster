from __future__ import annotations

from matmaster.types.messages import (
    AssistantMessage,
    ImageContentPart,
    SystemMessage,
    UserMessage,
)
from src.dao.chat_events_table import ChatEventsTable
from src.services.history_checkpoint_codec import serialize_base_messages
from src.services.model_history_restore_service import ModelHistoryRestoreService


def _compact_user_message(summary: str) -> UserMessage:
    return UserMessage(
        content=(
            "以下是先前对话的压缩摘要，作为历史背景。\n\n"
            f"<previous_session_summary>\n{summary}\n</previous_session_summary>"
        )
    )


def _checkpoint(
    *,
    checkpoint_id: int,
    covered_until_event_id: int,
    base_messages: list,
) -> dict:
    return {
        "id": checkpoint_id,
        "content": {
            "schema_version": "history_checkpoint.v1",
            "covered_until_event_id": covered_until_event_id,
            "base_messages": serialize_base_messages(base_messages),
        },
    }


def _invalid_checkpoint(*, checkpoint_id: int, covered_until_event_id: int) -> dict:
    return {
        "id": checkpoint_id,
        "content": {
            "schema_version": "history_checkpoint.v1",
            "covered_until_event_id": covered_until_event_id,
            "base_messages": serialize_base_messages([AssistantMessage(content="bad")]),
        },
    }


def _user_event(
    content: str,
    *,
    task_id: str | None = None,
    images: list[str] | None = None,
) -> dict:
    event = {
        "source": "User",
        "type": "query",
        "content": content,
        "task_id": task_id,
        "spawn_id": None,
    }
    if images is not None:
        event["images"] = images
    return event


def _utc_event(
    content: str,
    *,
    invocation_id: str = "inv-utc",
    images: list[str] | None = None,
) -> dict:
    return {
        "source": "MatMaster",
        "type": "user_turn_context",
        "content": {
            "schema_version": "user_turn_context.v1",
            "kind": "anchor",
            "message": UserMessage(
                content=content,
                images=[ImageContentPart(url=url) for url in images or []],
            ).model_dump(mode="json"),
            "user_instructions_hash": "sha256:abc",
            "transform": "raw",
            "render_version": "user_context_render.v1",
        },
        "task_id": None,
        "invocation_id": invocation_id,
        "spawn_id": None,
    }


def _response_event(content: str, *, task_id: str | None = None) -> dict:
    return {
        "source": "MatMaster",
        "type": "response",
        "content": content,
        "task_id": task_id,
        "spawn_id": None,
    }


class FakeEventsTable:
    def __init__(
        self,
        *,
        checkpoints: list[dict] | None = None,
        scope_events: list[dict] | None = None,
        session_events: list[dict] | None = None,
    ) -> None:
        self.checkpoints = checkpoints or []
        self.scope_events = scope_events or []
        self.session_events = session_events or []
        self.calls: list[tuple] = []

    def get_history_checkpoints(
        self, session_id: str, spawn_id: str | None, limit: int = 5
    ) -> list[dict]:
        self.calls.append(("get_history_checkpoints", session_id, spawn_id, limit))
        return list(self.checkpoints)

    def get_scope_events_after_id(
        self,
        session_id: str,
        spawn_id: str | None,
        after_id: int | None,
        limit: int | None = None,
    ) -> list[dict]:
        self.calls.append(
            ("get_scope_events_after_id", session_id, spawn_id, after_id, limit)
        )
        return list(self.scope_events)

    def get_session_events(
        self,
        session_id: str,
        limit: int | None = None,
        include_spawn: bool = False,
    ) -> list[dict]:
        self.calls.append(("get_session_events", session_id, limit, include_spawn))
        return list(self.session_events)

    def has_user_turn_context(
        self,
        session_id: str,
        spawn_id: str | None,
    ) -> bool:
        self.calls.append(("has_user_turn_context", session_id, spawn_id))
        return any(
            event.get("type") == "user_turn_context" for event in self.scope_events
        )


def test_row_to_event_includes_event_id() -> None:
    row = {
        "id": 42,
        "source": "MatMaster",
        "type": "history_checkpoint",
        "content": '{"covered_until_event_id": 10, "base_messages": []}',
        "session_id": "sess-1",
        "task_id": None,
        "invocation_id": None,
        "spawn_id": None,
        "created_at": None,
    }

    event = ChatEventsTable._row_to_event(row)

    assert event["id"] == 42


def test_restore_uses_latest_valid_checkpoint() -> None:
    events_table = FakeEventsTable(
        checkpoints=[
            _checkpoint(
                checkpoint_id=20,
                covered_until_event_id=20,
                base_messages=[_compact_user_message("latest")],
            )
        ],
        scope_events=[
            _utc_event("follow up question"),
            _response_event("follow up answer"),
        ],
    )

    service = ModelHistoryRestoreService(events_table)

    history = service.restore_history(
        session_id="sess-1",
        spawn_id=None,
        task_id=None,
    )

    assert isinstance(history[0], UserMessage)
    assert isinstance(history[-1], AssistantMessage)
    assert "latest" in (history[0].content or "")
    assert history[-1].content == "follow up answer"
    assert [type(message) for message in history] == [
        UserMessage,
        UserMessage,
        AssistantMessage,
    ]


def test_restore_falls_back_to_older_checkpoint_when_latest_is_invalid() -> None:
    events_table = FakeEventsTable(
        checkpoints=[
            _invalid_checkpoint(checkpoint_id=30, covered_until_event_id=30),
            _checkpoint(
                checkpoint_id=20,
                covered_until_event_id=20,
                base_messages=[_compact_user_message("older")],
            ),
        ],
        scope_events=[
            _utc_event("question from older checkpoint"),
            _response_event("answer from older checkpoint"),
        ],
    )

    service = ModelHistoryRestoreService(events_table)

    history = service.restore_history(
        session_id="sess-1",
        spawn_id=None,
        task_id=None,
    )

    assert isinstance(history[0], UserMessage)
    assert "older" in (history[0].content or "")
    assert isinstance(history[-1], AssistantMessage)
    assert history[-1].content == "answer from older checkpoint"
    assert [type(message) for message in history] == [
        UserMessage,
        UserMessage,
        AssistantMessage,
    ]


def test_restore_skips_old_system_checkpoint_and_uses_older_valid_checkpoint() -> None:
    events_table = FakeEventsTable(
        checkpoints=[
            _checkpoint(
                checkpoint_id=2,
                covered_until_event_id=20,
                base_messages=[SystemMessage(content="[Compacted Context]\nold")],
            ),
            _checkpoint(
                checkpoint_id=1,
                covered_until_event_id=10,
                base_messages=[_compact_user_message("valid")],
            ),
        ],
        scope_events=[],
    )

    history = ModelHistoryRestoreService(events_table).restore_history(
        session_id="s1",
        spawn_id=None,
        task_id=None,
    )

    assert len(history) == 1
    assert isinstance(history[0], UserMessage)
    assert "valid" in (history[0].content or "")


def test_restore_without_checkpoint_and_without_utc_returns_empty_history() -> None:
    events_table = FakeEventsTable()

    service = ModelHistoryRestoreService(events_table)

    history = service.restore_history(
        session_id="sess-raw",
        spawn_id=None,
        task_id=None,
    )

    assert history == []
    assert not any(call[0] == "get_session_events" for call in events_table.calls)


def test_restore_trims_history_images_by_image_turns() -> None:
    events_table = FakeEventsTable(
        scope_events=[
            _utc_event("img 1", images=["https://oss.example.com/chat/1.png"]),
            _utc_event("text only"),
            _utc_event("img 2", images=["https://oss.example.com/chat/2.png"]),
            _utc_event("img 3", images=["https://oss.example.com/chat/3.png"]),
            _utc_event("img 4", images=["https://oss.example.com/chat/4.png"]),
        ]
    )
    service = ModelHistoryRestoreService(events_table)

    history = service.restore_history(
        session_id="sess-raw",
        spawn_id=None,
        task_id=None,
    )

    image_counts = [len(getattr(message, "images", [])) for message in history]
    assert image_counts == [0, 0, 1, 1, 1]
    assert "[历史图片已裁剪: 1.png]" in history[0].content
