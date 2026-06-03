from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from src.dao.user_llm_config_table import UserLLMConfigTable


def _make_table() -> tuple[UserLLMConfigTable, MagicMock, MagicMock]:
    with patch.object(UserLLMConfigTable, "init_table", lambda self: None):
        table = UserLLMConfigTable()
    cursor = MagicMock()
    conn = MagicMock()

    @contextmanager
    def get_connection():
        yield conn

    cursor_ctx = MagicMock()
    cursor_ctx.__enter__.return_value = cursor
    cursor_ctx.__exit__.return_value = False
    conn.cursor.return_value = cursor_ctx
    table.get_connection = get_connection
    return table, cursor, conn


def test_create_scopes_user_and_serializes_json() -> None:
    table, cursor, conn = _make_table()
    cursor.lastrowid = 12

    config_id = table.create(
        "user-1",
        display_name="Research Proxy",
        base_url="https://api.example.com/v1",
        model="model-a",
        api_key_cipher="cipher-token",
        api_key_hint="sk-...cdef",
        key_version="v1",
        params={"temperature": 0.2},
        extra_body={"metadata": {"team": "计算"}},
        prompt_cache={"type": "ephemeral"},
        supports_streaming=True,
        supports_tool_calling=True,
        supports_vision=False,
    )

    sql, params = cursor.execute.call_args[0]
    assert config_id == 12
    assert "INSERT INTO user_llm_config" in sql
    assert "user_id" in sql
    assert params[0] == "user-1"
    assert '{"temperature":0.2}' in params
    assert '{"metadata":{"team":"计算"}}' in params
    assert '{"type":"ephemeral"}' in params
    conn.commit.assert_called_once()


def test_get_scopes_by_user_and_parses_json() -> None:
    table, cursor, _conn = _make_table()
    cursor.fetchone.return_value = {
        "id": 12,
        "user_id": "user-1",
        "params": '{"temperature":0.2}',
        "extra_body": '{"metadata":{"team":"lab"}}',
        "prompt_cache": '{"type":"ephemeral"}',
    }

    row = table.get("user-1", 12)

    sql, params = cursor.execute.call_args[0]
    assert "WHERE user_id = %s AND id = %s" in sql
    assert params == ("user-1", 12)
    assert row["params"] == {"temperature": 0.2}
    assert row["extra_body"] == {"metadata": {"team": "lab"}}
    assert row["prompt_cache"] == {"type": "ephemeral"}


def test_list_by_user_orders_by_updated_at_and_id() -> None:
    table, cursor, _conn = _make_table()
    cursor.fetchall.return_value = [
        {"id": 13, "params": None, "extra_body": None, "prompt_cache": None}
    ]

    rows = table.list_by_user("user-1")

    sql, params = cursor.execute.call_args[0]
    assert "WHERE user_id = %s" in sql
    assert "ORDER BY updated_at DESC, id DESC" in sql
    assert params == ("user-1",)
    assert rows[0]["params"] == {}
    assert rows[0]["extra_body"] == {}
    assert rows[0]["prompt_cache"] == {}


def test_update_increments_version_for_runtime_fields() -> None:
    table, cursor, conn = _make_table()
    cursor.rowcount = 1

    ok = table.update(
        "user-1",
        12,
        model="model-b",
        params={"temperature": 0.4},
    )

    sql, params = cursor.execute.call_args[0]
    assert ok is True
    assert "version = version + 1" in sql
    assert "updated_at = NOW()" in sql
    assert "WHERE user_id = %s AND id = %s" in sql
    assert '{"temperature":0.4}' in params
    assert params[-2:] == ("user-1", 12)
    conn.commit.assert_called_once()


def test_delete_scopes_by_user() -> None:
    table, cursor, conn = _make_table()
    cursor.rowcount = 1

    ok = table.delete("user-1", 12)

    sql, params = cursor.execute.call_args[0]
    assert ok is True
    assert "DELETE FROM user_llm_config" in sql
    assert "WHERE user_id = %s AND id = %s" in sql
    assert params == ("user-1", 12)
    conn.commit.assert_called_once()
