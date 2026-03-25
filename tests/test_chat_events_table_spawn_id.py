"""ChatEventsTable spawn_id persistence and get_session_events filtering."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.dao.chat_events_table import ChatEventsTable
from src.services.events_service import ChatEventsService


@pytest.fixture
def table_with_mocks() -> tuple[ChatEventsTable, MagicMock]:
    with patch.object(ChatEventsTable, "init_table", lambda self: None):
        table = ChatEventsTable()
    cursor = MagicMock()
    conn = MagicMock()
    cursor_ctx = MagicMock()
    cursor_ctx.__enter__.return_value = cursor
    cursor_ctx.__exit__.return_value = False
    conn.cursor.return_value = cursor_ctx
    conn_ctx = MagicMock()
    conn_ctx.__enter__.return_value = conn
    conn_ctx.__exit__.return_value = False
    table.get_connection = MagicMock(return_value=conn_ctx)
    return table, cursor


def test_add_event_accepts_spawn_id_keyword_and_passes_to_insert(
    table_with_mocks: tuple[ChatEventsTable, MagicMock],
) -> None:
    table, cursor = table_with_mocks
    cursor.rowcount = 1

    table.add_event(
        "sess-a",
        "Agent",
        "tool_call",
        {"id": "1", "name": "bash"},
        task_id="task-1",
        invocation_id="inv-1",
        spawn_id="child-spawn-001",
    )

    cursor.execute.assert_called_once()
    sql, params = cursor.execute.call_args[0]
    assert "spawn_id" in sql
    assert "child-spawn-001" in params


def test_get_session_events_default_sql_filters_parent_only_rows(
    table_with_mocks: tuple[ChatEventsTable, MagicMock],
) -> None:
    table, cursor = table_with_mocks
    cursor.fetchall.return_value = []

    table.get_session_events("sess-x")

    sql = cursor.execute.call_args[0][0]
    assert "spawn_id IS NULL" in sql


def test_get_session_events_include_spawn_true_omits_spawn_id_filter(
    table_with_mocks: tuple[ChatEventsTable, MagicMock],
) -> None:
    table, cursor = table_with_mocks
    cursor.fetchall.return_value = []

    table.get_session_events("sess-x", include_spawn=True)

    sql = cursor.execute.call_args[0][0]
    assert "spawn_id IS NULL" not in sql


def test_get_session_events_row_dicts_include_spawn_id_key(
    table_with_mocks: tuple[ChatEventsTable, MagicMock],
) -> None:
    table, cursor = table_with_mocks
    ts = datetime(2026, 3, 26, 12, 0, 0)
    cursor.fetchall.return_value = [
        {
            "id": 1,
            "session_id": "s1",
            "source": "Agent",
            "type": "tool_call",
            "content": '{"id": "c1", "call_id": "c1", "name": "bash", "args": {}}',
            "task_id": "t1",
            "invocation_id": "i1",
            "spawn_id": "sub-aaa",
            "created_at": ts,
        },
        {
            "id": 2,
            "session_id": "s1",
            "source": "Agent",
            "type": "run_result",
            "content": '{"content": null, "status": "completed", "reason": ""}',
            "task_id": "t1",
            "invocation_id": "i1",
            "spawn_id": None,
            "created_at": ts,
        },
    ]

    out = table.get_session_events("s1", include_spawn=True)
    assert len(out) == 2
    assert out[0].get("spawn_id") == "sub-aaa"
    assert out[1].get("spawn_id") is None


def test_events_service_get_session_events_passes_include_spawn() -> None:
    table = MagicMock()
    table.get_session_events.return_value = []
    svc = ChatEventsService(events_table=table, sessions_service=MagicMock())

    svc.get_session_events("sid-1")
    table.get_session_events.assert_called_once_with("sid-1", include_spawn=False)

    table.reset_mock()
    svc.get_session_events("sid-2", include_spawn=True)
    table.get_session_events.assert_called_once_with("sid-2", include_spawn=True)
