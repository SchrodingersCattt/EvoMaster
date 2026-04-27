"""Regression: ``add_history_checkpoint`` SQL placeholders must match args.

Bug: A botched refactor in commit 88f9f5a1 left a duplicated ``session_id``
in the params tuple, so ``cursor.execute`` was called with 8 args against
7 ``%s`` placeholders. pymysql's ``%``-based binding raises ``TypeError``;
``AgentKernel._run_compaction_plan`` swallowed the exception as a warning,
so no ``history_checkpoint`` rows were ever persisted and every subsequent
session fell back to full ``get_session_events`` replay -> LLM context
overflow.

Three tests pin the contract:
  * placeholder count vs. params length
  * column positions (catches future shift-by-one bugs)
  * pymysql-style ``query % args`` does not raise
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.dao.chat_events_table import ChatEventsTable


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


def _call_add_history_checkpoint(table: ChatEventsTable) -> None:
    table.add_history_checkpoint(
        "sess-x",
        task_id="t1",
        invocation_id="inv1",
        spawn_id="spawn-a",
        covered_until_event_id=42,
        base_messages=[{"role": "system", "content": "compacted"}],
        reason="summary",
    )


def test_add_history_checkpoint_param_count_matches_placeholders(
    table_with_mocks: tuple[ChatEventsTable, MagicMock],
) -> None:
    table, cursor = table_with_mocks
    cursor.rowcount = 1

    _call_add_history_checkpoint(table)

    cursor.execute.assert_called_once()
    sql, params = cursor.execute.call_args[0]
    placeholder_count = sql.count("%s")
    assert placeholder_count == len(params), (
        f"SQL has {placeholder_count} %s placeholders but params tuple has "
        f"{len(params)} elements (params={params!r})"
    )


def test_add_history_checkpoint_writes_correct_column_values(
    table_with_mocks: tuple[ChatEventsTable, MagicMock],
) -> None:
    table, cursor = table_with_mocks
    cursor.rowcount = 1

    _call_add_history_checkpoint(table)

    _, params = cursor.execute.call_args[0]
    # INSERT column order:
    # (session_id, source, type, content, task_id, invocation_id, spawn_id)
    assert params[0] == "sess-x"
    assert params[1] == "System"
    assert params[2] == "history_checkpoint"
    content = json.loads(params[3])
    assert content["covered_until_event_id"] == 42
    assert content["base_messages"] == [{"role": "system", "content": "compacted"}]
    assert content["reason"] == "summary"
    assert params[4] == "t1"
    assert params[5] == "inv1"
    assert params[6] == "spawn-a"


def test_add_history_checkpoint_sql_format_string_compatible_with_pymysql(
    table_with_mocks: tuple[ChatEventsTable, MagicMock],
) -> None:
    """pymysql binds args via ``query % escaped_args``; mismatched arity raises.

    Reproduce that exact step here so any future arity drift fails fast.
    """
    table, cursor = table_with_mocks
    cursor.rowcount = 1

    _call_add_history_checkpoint(table)

    sql, params = cursor.execute.call_args[0]
    sanitized = tuple(repr(p) for p in params)
    try:
        sql % sanitized
    except TypeError as exc:  # pragma: no cover - failure path
        pytest.fail(f"pymysql-style % formatting fails: {exc}")
