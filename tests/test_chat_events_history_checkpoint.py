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
  * column-name-keyed value mapping (catches shift-by-one bugs while
    tolerating harmless consistent reorderings)
  * pymysql-style ``query % args`` does not raise
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from src.dao.chat_events_table import ChatEventsTable

_INSERT_COLUMNS_RE = re.compile(
    r"\(\s*([^)]+)\s*\)\s*VALUES", re.IGNORECASE | re.DOTALL
)


def _parse_insert_param_columns(sql: str) -> list[str]:
    """Return the parameterized columns from an INSERT statement.

    Drops ``created_at`` because the DAO binds it via a literal ``NOW()`` in
    the VALUES clause, not via a ``%s`` placeholder, so it is absent from the
    params tuple.
    """
    match = _INSERT_COLUMNS_RE.search(sql)
    assert match, f"Could not parse INSERT columns from SQL: {sql!r}"
    columns = [c.strip() for c in match.group(1).split(",")]
    return [c for c in columns if c != "created_at"]


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
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
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
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.rowcount = 1

    _call_add_history_checkpoint(table)

    sql, params = cursor.execute.call_args[0]
    columns = _parse_insert_param_columns(sql)
    row = dict(zip(columns, params, strict=True))

    assert row["session_id"] == "sess-x"
    assert row["source"] == "System"
    assert row["type"] == "history_checkpoint"
    content = json.loads(row["content"])
    assert content["covered_until_event_id"] == 42
    assert content["base_messages"] == [{"role": "system", "content": "compacted"}]
    assert content["reason"] == "summary"
    assert row["task_id"] == "t1"
    assert row["invocation_id"] == "inv1"
    assert row["spawn_id"] == "spawn-a"


def test_add_history_checkpoint_sql_format_string_compatible_with_pymysql(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    """pymysql binds args via ``query % escaped_args``; mismatched arity raises.

    Reproduce that exact step here so any future arity drift fails fast.
    """
    table, cursor = chat_events_table_with_mocks
    cursor.rowcount = 1

    _call_add_history_checkpoint(table)

    sql, params = cursor.execute.call_args[0]
    # repr(p) stands in for pymysql's conn.literal(arg). Both produce one
    # string per element, so arity drift fails identically; only the arity
    # invariant matters for this regression test.
    sanitized = tuple(repr(p) for p in params)
    try:
        sql % sanitized
    except TypeError as exc:
        pytest.fail(f"pymysql-style % formatting fails: {exc}")
