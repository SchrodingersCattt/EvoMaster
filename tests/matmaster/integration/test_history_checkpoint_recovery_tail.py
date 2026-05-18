from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from matmaster.types.messages import AssistantMessage, UserMessage
from src.services.history_checkpoint_codec import serialize_base_messages
from src.services.history_checkpoint_service import HistoryCheckpointService
from src.services.model_history_restore_service import ModelHistoryRestoreService
from tests.matmaster.integration.test_history_checkpoint_recovery import (
    InMemoryEventsTable,
    _compact_user_message,
    _seed_scope_events,
)


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
        payload={
            "durability": "durable",
            "strategy": "summary",
            "schema_version": "history_checkpoint.v1",
            "covered_until_event_id": 2,
        },
        base_messages=checkpoint_base_messages,
    )

    _seed_scope_events(
        events_table,
        session_id=session_id,
        task_id="task-crashed-run",
        user_content="question emitted before crash",
        response_content="partial answer emitted before crash",
        write_user_turn_context=True,
    )

    history = ModelHistoryRestoreService(events_table).restore_history(
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
        "schema_version": "history_checkpoint.v1",
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
        payload={
            "durability": "durable",
            "strategy": "summary",
            "schema_version": "history_checkpoint.v1",
            "covered_until_event_id": 2,
        },
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

    restored = ModelHistoryRestoreService(events_table).restore_history(
        session_id=session_id,
        spawn_id=None,
        task_id="task-2",
    )

    assert [type(msg).__name__ for msg in restored] == [
        "UserMessage",
    ]
