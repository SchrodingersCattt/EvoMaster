"""ChatEventsTable spawn_id persistence and get_session_events filtering."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

from src.dao.chat_events_table import ChatEventsTable
from src.services.events_service import ChatEventsService


def test_add_event_accepts_spawn_id_keyword_and_passes_to_insert(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
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
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchall.return_value = []

    table.get_session_events("sess-x")

    sql = cursor.execute.call_args[0][0]
    assert "spawn_id IS NULL" in sql


def test_get_session_events_include_spawn_true_omits_spawn_id_filter(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchall.return_value = []

    table.get_session_events("sess-x", include_spawn=True)

    sql = cursor.execute.call_args[0][0]
    assert "spawn_id IS NULL" not in sql


def test_get_session_events_exclude_types_adds_not_in_filter(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchall.return_value = []

    table.get_session_events(
        "sess-x",
        include_spawn=True,
        exclude_types=("history_checkpoint", "assistant_state"),
    )

    sql, params = cursor.execute.call_args[0]
    assert "type NOT IN (%s, %s)" in sql
    assert "history_checkpoint" in params
    assert "assistant_state" in params
    assert params[0] == "sess-x"


def test_get_session_events_no_exclude_types_omits_not_in_filter(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchall.return_value = []

    table.get_session_events("sess-x")

    sql = cursor.execute.call_args[0][0]
    assert "type NOT IN" not in sql


def test_get_session_events_row_dicts_include_spawn_id_key(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
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
    table.get_session_events.assert_called_once_with(
        "sid-1", include_spawn=False, exclude_types=None
    )

    table.reset_mock()
    svc.get_session_events("sid-2", include_spawn=True)
    table.get_session_events.assert_called_once_with(
        "sid-2", include_spawn=True, exclude_types=None
    )

    table.reset_mock()
    svc.get_session_events("sid-3", include_spawn=True, exclude_types=("history_checkpoint",))
    table.get_session_events.assert_called_once_with(
        "sid-3", include_spawn=True, exclude_types=("history_checkpoint",)
    )


def test_get_session_user_query_events_filters_parent_user_queries(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchall.return_value = []

    table.get_session_user_query_events("sess-x")

    sql, params = cursor.execute.call_args[0]
    assert "session_id = %s" in sql
    assert "source = 'User'" in sql
    assert "type = 'query'" in sql
    assert "spawn_id IS NULL" in sql
    assert params == ("sess-x",)


def test_get_session_user_query_events_unwraps_attachment_metadata(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    ts = datetime(2026, 3, 26, 12, 0, 0)
    cursor.fetchall.return_value = [
        {
            "id": 1,
            "session_id": "sess-x",
            "source": "User",
            "type": "query",
            "content": (
                '{"content": "old turn",'
                ' "files": ["https://oss.example.com/chat/old.csv"],'
                ' "images": ["https://oss.example.com/chat/old.png"],'
                ' "workspace_paths": ["/share/old.cif"]}'
            ),
            "task_id": "task-1",
            "invocation_id": "inv-1",
            "spawn_id": None,
            "created_at": ts,
        }
    ]

    out = table.get_session_user_query_events("sess-x")

    assert out == [
        {
            "id": 1,
            "source": "User",
            "type": "query",
            "content": "old turn",
            "session_id": "sess-x",
            "task_id": "task-1",
            "invocation_id": "inv-1",
            "spawn_id": None,
            "created_at_ms": int(ts.timestamp() * 1000),
            "files": ["https://oss.example.com/chat/old.csv"],
            "images": ["https://oss.example.com/chat/old.png"],
            "workspace_paths": ["/share/old.cif"],
        }
    ]


def test_events_service_get_session_user_query_events_delegates_to_table() -> None:
    table = MagicMock()
    table.get_session_user_query_events.return_value = []
    svc = ChatEventsService(events_table=table, sessions_service=MagicMock())

    svc.get_session_user_query_events("sid-1")

    table.get_session_user_query_events.assert_called_once_with("sid-1")


def test_get_bohrium_events_pairs_tool_call_and_result(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    ts = datetime(2026, 4, 8, 12, 0, 0)
    cursor.fetchall.return_value = [
        {
            "type": "tool_call",
            "content": (
                '{"id":"c1","call_id":"c1","name":"Bohrium",'
                '"args":{"action":"submit","job_name":"alpha"}}'
            ),
            "created_at": ts,
        },
        {
            "type": "tool_result",
            "content": (
                '{"id":"c1","call_id":"c1","name":"Bohrium","status":"success",'
                '"result":"{\\"job_id\\": \\"job-1\\", \\"status\\": \\"Submitted\\"}"}'
            ),
            "created_at": ts,
        },
        {
            "type": "tool_call",
            "content": (
                '{"id":"c2","call_id":"c2","name":"Bohrium",'
                '"args":{"action":"poll","job_id":"job-1"}}'
            ),
            "created_at": ts,
        },
        {
            "type": "tool_result",
            "content": (
                '{"id":"c2","call_id":"c2","name":"Bohrium","status":"success",'
                '"result":"{\\"job_id\\": \\"job-1\\", \\"status\\": \\"Running\\"}"}'
            ),
            "created_at": ts,
        },
        {
            "type": "tool_call",
            "content": '{"id":"c3","call_id":"c3","name":"bash","args":{"cmd":"pwd"}}',
            "created_at": ts,
        },
        {
            "type": "tool_result",
            "content": (
                '{"id":"c3","call_id":"c3","name":"bash","status":"success",'
                '"result":"ok"}'
            ),
            "created_at": ts,
        },
    ]

    out = table.get_bohrium_events("sess-bohrium")

    assert out == [
        {
            "action": "submit",
            "job_id": "job-1",
            "status": "Submitted",
            "job_name": "alpha",
            "cached": False,
        },
        {
            "action": "poll",
            "job_id": "job-1",
            "status": "Running",
            "job_name": "",
            "cached": False,
        },
    ]
