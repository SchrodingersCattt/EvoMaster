import json
from unittest.mock import MagicMock

from src.dao.chat_events_table import ChatEventsTable
from src.services.events_service import ChatEventsService


def test_add_history_event_persists_session_directory_metadata_without_files():
    table = MagicMock()
    sessions = MagicMock()
    service = ChatEventsService(events_table=table, sessions_service=sessions)

    service.add_history_event(
        "sess-1",
        {
            "source": "User",
            "type": "query",
            "content": "run",
            "mode": "direct",
            "task_id": "task-1",
            "invocation_id": "inv-1",
            "session_directory": "/share/case",
            "session_directory_source": "request",
        },
        user_id="user-1",
    )

    stored_content = table.add_event.call_args.args[3]
    assert stored_content == {
        "content": "run",
        "session_directory": "/share/case",
        "session_directory_source": "request",
    }


def test_add_history_event_persists_requested_model_metadata():
    table = MagicMock()
    sessions = MagicMock()
    service = ChatEventsService(events_table=table, sessions_service=sessions)

    service.add_history_event(
        "sess-1",
        {
            "source": "User",
            "type": "query",
            "content": "run",
            "mode": "direct",
            "task_id": "task-1",
            "invocation_id": "inv-1",
            "requested_llm": "opus",
            "requested_model": "claude-opus-4-6",
        },
        user_id="user-1",
    )

    stored_content = table.add_event.call_args.args[3]
    assert stored_content == {
        "content": "run",
        "requested_llm": "opus",
        "requested_model": "claude-opus-4-6",
    }


def test_row_to_event_unpacks_session_directory_metadata():
    row = {
        "id": 1,
        "session_id": "sess-1",
        "source": "User",
        "type": "query",
        "content": json.dumps(
            {
                "content": "run",
                "session_directory": "/share/case",
                "session_directory_source": "session",
            }
        ),
        "task_id": "task-1",
        "invocation_id": "inv-1",
        "spawn_id": None,
        "created_at": None,
    }

    event = ChatEventsTable._row_to_event(row)

    assert event["content"] == "run"
    assert event["session_directory"] == "/share/case"
    assert event["session_directory_source"] == "session"


def test_row_to_event_unpacks_requested_model_metadata():
    row = {
        "id": 1,
        "session_id": "sess-1",
        "source": "User",
        "type": "query",
        "content": json.dumps(
            {
                "content": "run",
                "requested_llm": "opus",
                "requested_model": "claude-opus-4-6",
            }
        ),
        "task_id": "task-1",
        "invocation_id": "inv-1",
        "spawn_id": None,
        "created_at": None,
    }

    event = ChatEventsTable._row_to_event(row)

    assert event["content"] == "run"
    assert event["requested_llm"] == "opus"
    assert event["requested_model"] == "claude-opus-4-6"


class _Cursor:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return _Cursor(self._row)


def test_get_last_user_query_returns_session_directory_metadata():
    table = ChatEventsTable.__new__(ChatEventsTable)
    table.get_connection = lambda: _Connection(
        {
            "session_id": "sess-1",
            "source": "User",
            "type": "query",
            "content": json.dumps(
                {
                    "content": "run",
                    "session_directory": "/share/case",
                    "session_directory_source": "request",
                }
            ),
            "task_id": "task-1",
            "invocation_id": "inv-1",
            "created_at": None,
        }
    )

    last = table.get_last_user_query("sess-1")

    assert last["content"] == "run"
    assert last["session_directory"] == "/share/case"
    assert last["session_directory_source"] == "request"
