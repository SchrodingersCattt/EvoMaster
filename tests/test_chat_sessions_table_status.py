"""ChatSessionsTable.list_session_ids_by_status 查询行为测试。"""

from unittest.mock import MagicMock, patch


def _table_with_cursor():
    from src.dao.chat_sessions_table import ChatSessionsTable

    with patch.object(ChatSessionsTable, "init_table", lambda self: None):
        table = ChatSessionsTable()
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


def test_returns_session_ids():
    table, cursor = _table_with_cursor()
    cursor.fetchall.return_value = [
        {"session_id": "s1"},
        {"session_id": "s2"},
    ]
    assert table.list_session_ids_by_status("user-1", ["waiting", "active"]) == [
        "s1",
        "s2",
    ]


def test_empty_statuses_short_circuits_without_query():
    table, cursor = _table_with_cursor()
    assert table.list_session_ids_by_status("user-1", []) == []
    cursor.execute.assert_not_called()


def test_query_filters_user_and_status_in():
    table, cursor = _table_with_cursor()
    cursor.fetchall.return_value = []
    table.list_session_ids_by_status("user-9", ["waiting", "active"])
    sql, params = cursor.execute.call_args[0]
    assert "WHERE user_id = %s" in sql
    assert "status IN (%s, %s)" in sql
    assert params == ("user-9", "waiting", "active")


def test_service_lists_live_run_ids():
    from src.services.sessions_service import ChatSessionsService

    table = MagicMock()
    table.list_session_ids_by_status.return_value = ["s1", "s2"]
    svc = ChatSessionsService(table)
    svc.is_session_run_live = MagicMock(return_value=True)
    assert svc.list_live_run_session_ids("user-1") == ["s1", "s2"]
    table.list_session_ids_by_status.assert_called_once_with(
        "user-1", ["waiting", "active"]
    )
    assert svc.is_session_run_live.call_count == 2
    svc.is_session_run_live.assert_any_call("s1")
    svc.is_session_run_live.assert_any_call("s2")
