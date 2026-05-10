from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from matmaster.core.context_builder import ContextBuilder
from matmaster.types.messages import AssistantMessage, SystemMessage, UserMessage
from src.services.history_checkpoint_codec import serialize_base_messages
from src.services.history_checkpoint_service import HistoryCheckpointService
from src.services.history_restore_service import HistoryRestoreService


def _compact_user_message(summary: str) -> UserMessage:
    return UserMessage(
        content=ContextBuilder().build_compact_bundle(summary=summary)
    )


def _user_event(
    content: str,
    *,
    task_id: str | None = None,
    spawn_id: str | None = None,
) -> dict[str, Any]:
    return {
        "source": "User",
        "type": "query",
        "content": content,
        "task_id": task_id,
        "spawn_id": spawn_id,
    }


def _response_event(
    content: str,
    *,
    task_id: str | None = None,
    spawn_id: str | None = None,
) -> dict[str, Any]:
    return {
        "source": "MatMaster",
        "type": "response",
        "content": content,
        "task_id": task_id,
        "spawn_id": spawn_id,
    }


class InMemoryEventsTable:
    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._next_id = 1
        self.calls: list[tuple[Any, ...]] = []

    def add_event(
        self,
        session_id: str,
        source: str,
        event_type: str,
        content: Any,
        *,
        task_id: str | None = None,
        invocation_id: str | None = None,
        spawn_id: str | None = None,
    ) -> int:
        event = {
            "id": self._next_id,
            "session_id": session_id,
            "source": source,
            "type": event_type,
            "content": content,
            "task_id": task_id,
            "invocation_id": invocation_id,
            "spawn_id": spawn_id,
        }
        self._next_id += 1
        self._events.append(event)
        return event["id"]

    def add_history_checkpoint(
        self,
        session_id: str,
        *,
        task_id: str | None,
        invocation_id: str | None,
        spawn_id: str | None,
        covered_until_event_id: int,
        base_messages: list[dict[str, Any]],
        reason: str = "summary",
    ) -> bool:
        self.calls.append(
            (
                "add_history_checkpoint",
                session_id,
                task_id,
                invocation_id,
                spawn_id,
                covered_until_event_id,
                reason,
            )
        )
        self.add_event(
            session_id,
            "System",
            "history_checkpoint",
            {
                "covered_until_event_id": covered_until_event_id,
                "base_messages": base_messages,
                "reason": reason,
            },
            task_id=task_id,
            invocation_id=invocation_id,
            spawn_id=spawn_id,
        )
        return True

    def get_history_checkpoints(
        self,
        session_id: str,
        spawn_id: str | None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        self.calls.append(("get_history_checkpoints", session_id, spawn_id, limit))
        rows = [
            event
            for event in self._events
            if event["session_id"] == session_id
            and event["type"] == "history_checkpoint"
            and event.get("spawn_id") == spawn_id
        ]
        rows.sort(key=lambda event: int(event["id"]), reverse=True)
        return rows[:limit]

    def get_scope_events_after_id(
        self,
        session_id: str,
        spawn_id: str | None,
        after_id: int,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            ("get_scope_events_after_id", session_id, spawn_id, after_id, limit)
        )
        rows = [
            event
            for event in self._events
            if event["session_id"] == session_id
            and event.get("spawn_id") == spawn_id
            and int(event["id"]) > after_id
            and event["type"]
            not in {"history_checkpoint", "compaction", "context_compaction"}
        ]
        rows.sort(key=lambda event: int(event["id"]))
        if limit is not None:
            return rows[:limit]
        return rows

    def get_latest_scope_event_id(self, session_id: str, spawn_id: str | None) -> int:
        self.calls.append(("get_latest_scope_event_id", session_id, spawn_id))
        ids = [
            int(event["id"])
            for event in self._events
            if event["session_id"] == session_id
            and event.get("spawn_id") == spawn_id
            and event["type"]
            not in {"history_checkpoint", "compaction", "context_compaction"}
        ]
        return max(ids, default=0)

    def get_session_events(
        self,
        session_id: str,
        limit: int | None = None,
        include_spawn: bool = False,
    ) -> list[dict[str, Any]]:
        self.calls.append(("get_session_events", session_id, limit, include_spawn))
        rows = [
            event
            for event in self._events
            if event["session_id"] == session_id
            and (include_spawn or event.get("spawn_id") is None)
        ]
        rows.sort(key=lambda event: int(event["id"]))
        if limit is not None:
            return rows[:limit]
        return rows

    def history_checkpoints(self, *, spawn_id: str | None) -> list[dict[str, Any]]:
        return [
            event
            for event in self._events
            if event["type"] == "history_checkpoint"
            and event.get("spawn_id") == spawn_id
        ]

    def compact_boundaries(self, *, spawn_id: str | None) -> list[dict[str, Any]]:
        return [
            event
            for event in self._events
            if event["type"] == "compact_boundary" and event.get("spawn_id") == spawn_id
        ]


def _seed_scope_events(
    events_table: InMemoryEventsTable,
    *,
    session_id: str,
    spawn_id: str | None = None,
    task_id: str | None = None,
    user_content: str,
    response_content: str,
) -> None:
    user_event = _user_event(user_content, task_id=task_id, spawn_id=spawn_id)
    events_table.add_event(
        session_id,
        user_event["source"],
        user_event["type"],
        user_event["content"],
        task_id=task_id,
        spawn_id=spawn_id,
    )
    response_event = _response_event(
        response_content, task_id=task_id, spawn_id=spawn_id
    )
    events_table.add_event(
        session_id,
        response_event["source"],
        response_event["type"],
        response_event["content"],
        task_id=task_id,
        spawn_id=spawn_id,
    )


@pytest.mark.asyncio
async def test_restore_with_checkpoint_plus_incremental_events() -> None:
    session_id = "sess-recovery"
    events_table = InMemoryEventsTable()
    fanout = Mock()
    fanout.flush_persistence_barrier = AsyncMock()

    _seed_scope_events(
        events_table,
        session_id=session_id,
        user_content="old question before compaction",
        response_content="old answer before compaction",
    )

    checkpoint_sink = HistoryCheckpointService(events_table).build_checkpoint_sink(
        fanout=fanout,
        session_id=session_id,
        task_id="task-compaction",
        invocation_id="inv-compaction",
        spawn_id=None,
    )
    checkpoint_base_messages = serialize_base_messages(
        [
            _compact_user_message("Recovered summary"),
        ]
    )

    await checkpoint_sink(
        payload={"durability": "durable", "strategy": "summary"},
        base_messages=checkpoint_base_messages,
    )

    _seed_scope_events(
        events_table,
        session_id=session_id,
        task_id="task-follow-up",
        user_content="incremental follow-up question",
        response_content="incremental follow-up answer",
    )

    history = HistoryRestoreService(events_table).restore_history(
        session_id=session_id,
        spawn_id=None,
        task_id=None,
    )

    assert [type(message) for message in history] == [
        UserMessage,
        UserMessage,
        AssistantMessage,
    ]
    assert "Recovered summary" in (history[0].content or "")
    assert [message.content for message in history[1:]] == [
        "incremental follow-up question",
        "incremental follow-up answer",
    ]
    fanout.flush_persistence_barrier.assert_awaited_once()
    assert (
        "get_scope_events_after_id",
        session_id,
        None,
        2,
        None,
    ) in events_table.calls


@pytest.mark.asyncio
async def test_ephemeral_compaction_does_not_trigger_checkpoint_sink() -> None:
    session_id = "sess-ephemeral"
    events_table = InMemoryEventsTable()
    fanout = Mock()
    fanout.flush_persistence_barrier = AsyncMock()

    _seed_scope_events(
        events_table,
        session_id=session_id,
        user_content="question before fallback",
        response_content="answer before fallback",
    )

    checkpoint_sink = HistoryCheckpointService(events_table).build_checkpoint_sink(
        fanout=fanout,
        session_id=session_id,
        task_id="task-runtime",
        invocation_id="inv-runtime",
        spawn_id=None,
    )
    base_messages = serialize_base_messages(
        [SystemMessage(content="[Compacted Context]\nShould never persist")]
    )

    await checkpoint_sink(
        payload={"durability": "ephemeral", "strategy": "sliding_window"},
        base_messages=base_messages,
    )
    await checkpoint_sink(
        payload={"durability": "ephemeral", "strategy": "tool_truncation"},
        base_messages=base_messages,
    )

    fanout.flush_persistence_barrier.assert_not_awaited()
    assert events_table.history_checkpoints(spawn_id=None) == []
    assert events_table.compact_boundaries(spawn_id=None) == []
    assert not any(
        call[0] == "get_latest_scope_event_id" for call in events_table.calls
    )
    assert not any(call[0] == "add_checkpoint_pair" for call in events_table.calls)


@pytest.mark.asyncio
async def test_spawn_id_checkpoint_does_not_affect_parent_restore() -> None:
    session_id = "sess-spawn-scope"
    child_spawn_id = "child-1"
    events_table = InMemoryEventsTable()
    fanout = Mock()
    fanout.flush_persistence_barrier = AsyncMock()

    _seed_scope_events(
        events_table,
        session_id=session_id,
        user_content="parent raw question",
        response_content="parent raw answer",
    )
    _seed_scope_events(
        events_table,
        session_id=session_id,
        spawn_id=child_spawn_id,
        user_content="child question before checkpoint",
        response_content="child answer before checkpoint",
    )

    child_sink = HistoryCheckpointService(events_table).build_checkpoint_sink(
        fanout=fanout,
        session_id=session_id,
        task_id="task-child",
        invocation_id="inv-child",
        spawn_id=child_spawn_id,
    )
    child_base_messages = serialize_base_messages(
        [
            _compact_user_message("child summary"),
        ]
    )

    await child_sink(
        payload={"durability": "durable", "strategy": "summary"},
        base_messages=child_base_messages,
    )

    _seed_scope_events(
        events_table,
        session_id=session_id,
        spawn_id=child_spawn_id,
        task_id="task-child-follow-up",
        user_content="child incremental question",
        response_content="child incremental answer",
    )

    restore_service = HistoryRestoreService(events_table)
    parent_history = restore_service.restore_history(
        session_id=session_id,
        spawn_id=None,
        task_id=None,
    )
    child_history = restore_service.restore_history(
        session_id=session_id,
        spawn_id=child_spawn_id,
        task_id=None,
    )

    assert [type(message) for message in parent_history] == [
        UserMessage,
        AssistantMessage,
    ]
    assert [message.content for message in parent_history] == [
        "parent raw question",
        "parent raw answer",
    ]
    assert all("child" not in str(message.content) for message in parent_history)
    assert [type(message) for message in child_history] == [
        UserMessage,
        UserMessage,
        AssistantMessage,
    ]
    assert "child summary" in (child_history[0].content or "")
    assert [message.content for message in child_history[1:]] == [
        "child incremental question",
        "child incremental answer",
    ]


@pytest.mark.asyncio
async def test_restore_after_midrun_crash_uses_written_checkpoint() -> None:
    session_id = "sess-midrun-crash"
    events_table = InMemoryEventsTable()
    fanout = Mock()
    fanout.flush_persistence_barrier = AsyncMock()

    _seed_scope_events(
        events_table,
        session_id=session_id,
        task_id="task-before-crash",
        user_content="question before checkpoint",
        response_content="answer before checkpoint",
    )

    checkpoint_sink = HistoryCheckpointService(events_table).build_checkpoint_sink(
        fanout=fanout,
        session_id=session_id,
        task_id="task-before-crash",
        invocation_id="inv-before-crash",
        spawn_id=None,
    )
    checkpoint_base_messages = serialize_base_messages(
        [
            _compact_user_message("checkpoint before crash"),
        ]
    )

    await checkpoint_sink(
        payload={"durability": "durable", "strategy": "summary"},
        base_messages=checkpoint_base_messages,
    )

    _seed_scope_events(
        events_table,
        session_id=session_id,
        task_id="task-crashed-run",
        user_content="question emitted before crash",
        response_content="partial answer emitted before crash",
    )

    history = HistoryRestoreService(events_table).restore_history(
        session_id=session_id,
        spawn_id=None,
        task_id="task-retry-after-crash",
    )

    assert [type(message) for message in history] == [
        UserMessage,
        UserMessage,
        AssistantMessage,
    ]
    assert "checkpoint before crash" in (history[0].content or "")
    assert [message.content for message in history[1:]] == [
        "question emitted before crash",
        "partial answer emitted before crash",
    ]
    assert fanout.flush_persistence_barrier.await_count == 1
    assert events_table.history_checkpoints(spawn_id=None)[0]["content"] == {
        "covered_until_event_id": 2,
        "base_messages": checkpoint_base_messages,
        "reason": "summary",
    }


@pytest.mark.asyncio
async def test_compaction_events_replay_but_do_not_enter_restore_tail() -> None:
    session_id = "sess-compaction"
    events_table = InMemoryEventsTable()
    fanout = Mock()
    fanout.flush_persistence_barrier = AsyncMock()

    _seed_scope_events(
        events_table,
        session_id=session_id,
        user_content="question before compaction",
        response_content="answer before compaction",
    )

    checkpoint_sink = HistoryCheckpointService(events_table).build_checkpoint_sink(
        fanout=fanout,
        session_id=session_id,
        task_id="task-1",
        invocation_id="inv-1",
        spawn_id=None,
    )

    covered_until = await checkpoint_sink(
        payload={"durability": "durable", "strategy": "summary"},
        base_messages=serialize_base_messages(
            [
                _compact_user_message("summary"),
            ]
        ),
    )

    events_table.add_event(
        session_id,
        "MatMaster",
        "compaction",
        {
            "compaction_id": "task-1:root:1",
            "status": "complete",
            "phase": "runtime",
            "strategy": "summary",
            "durability": "durable",
            "checkpoint_written": True,
            "covered_until_event_id": covered_until,
        },
        task_id="task-1",
    )

    restored = HistoryRestoreService(events_table).restore_history(
        session_id=session_id,
        spawn_id=None,
        task_id="task-2",
    )

    assert [type(msg).__name__ for msg in restored] == [
        "UserMessage",
    ]
