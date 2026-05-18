"""Regression: ``add_history_checkpoint`` SQL placeholders must match args.

Bug: A botched refactor in commit 88f9f5a1 left a duplicated ``session_id``
in the params tuple, so ``cursor.execute`` was called with 8 args against
7 ``%s`` placeholders. pymysql's ``%``-based binding raises ``TypeError``;
``run_compaction_plan`` swallowed the exception as a warning,
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


def test_add_history_checkpoint_writes_v1_metadata_fields(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.rowcount = 1

    table.add_history_checkpoint(
        "sess-x",
        task_id="t1",
        invocation_id="inv1",
        spawn_id="spawn-a",
        covered_until_event_id=42,
        base_messages=[{"role": "user", "content": "compacted"}],
        reason="summary",
        schema_version="history_checkpoint.v1",
        render_version="user_context_render.v1",
        user_instructions_text="Use SI units.",
        user_instructions_hash="sha256:abc",
    )

    sql, params = cursor.execute.call_args[0]
    columns = _parse_insert_param_columns(sql)
    row = dict(zip(columns, params, strict=True))
    row["content"] = json.loads(row["content"])

    assert row["content"]["schema_version"] == "history_checkpoint.v1"
    assert row["content"]["render_version"] == "user_context_render.v1"
    assert row["content"]["user_instructions_text"] == "Use SI units."
    assert row["content"]["user_instructions_hash"] == "sha256:abc"


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


def test_query_user_turn_context_by_invocation_returns_existing_row(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = {
        "id": 42,
        "session_id": "sess-x",
        "source": "MatMaster",
        "type": "user_turn_context",
        "content": '{"schema_version": "user_turn_context.v1"}',
        "task_id": "task-1",
        "invocation_id": "inv-1",
        "spawn_id": None,
        "created_at": None,
    }

    event = table.query_user_turn_context_by_invocation("sess-x", "inv-1", None)

    assert event is not None
    assert event["id"] == 42
    assert event["type"] == "user_turn_context"
    assert event["invocation_id"] == "inv-1"
    assert event["content"] == {"schema_version": "user_turn_context.v1"}
    sql, params = cursor.execute.call_args[0]
    assert "type = 'user_turn_context'" in sql
    assert "spawn_id IS NULL" in sql
    assert params == ("sess-x", "inv-1")


def test_get_recent_context_anchor_events_returns_checkpoint_and_utc_rows(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchall.return_value = [
        {
            "id": 43,
            "session_id": "sess-x",
            "source": "System",
            "type": "history_checkpoint",
            "content": '{"covered_until_event_id": 42}',
            "task_id": "task-2",
            "invocation_id": "inv-2",
            "spawn_id": None,
            "created_at": None,
        },
        {
            "id": 42,
            "session_id": "sess-x",
            "source": "MatMaster",
            "type": "user_turn_context",
            "content": '{"schema_version": "user_turn_context.v1"}',
            "task_id": "task-1",
            "invocation_id": "inv-1",
            "spawn_id": None,
            "created_at": None,
        },
    ]

    events = table.get_recent_context_anchor_events("sess-x", None, limit=50)

    assert [event["id"] for event in events] == [43, 42]
    assert events[0]["content"] == {"covered_until_event_id": 42}
    assert events[1]["content"] == {"schema_version": "user_turn_context.v1"}
    sql, params = cursor.execute.call_args[0]
    assert "type IN ('user_turn_context', 'history_checkpoint')" in sql
    assert "spawn_id IS NULL" in sql
    assert "ORDER BY id DESC" in sql
    assert "LIMIT 50" in sql
    assert params == ("sess-x",)


def test_get_recent_context_anchor_events_filters_non_root_spawn(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchall.return_value = []

    events = table.get_recent_context_anchor_events("sess-x", "spawn-a", limit=0)

    assert events == []
    sql, params = cursor.execute.call_args[0]
    assert "type IN ('user_turn_context', 'history_checkpoint')" in sql
    assert "spawn_id = %s" in sql
    assert "ORDER BY id DESC" in sql
    assert "LIMIT" not in sql
    assert params == ("sess-x", "spawn-a")


def test_query_user_turn_context_by_invocation_returns_none_when_missing(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = None

    event = table.query_user_turn_context_by_invocation("sess-x", "inv-1", "spawn-a")

    assert event is None
    sql, params = cursor.execute.call_args[0]
    assert "type = 'user_turn_context'" in sql
    assert "spawn_id = %s" in sql
    assert params == ("sess-x", "inv-1", "spawn-a")


def test_has_user_turn_context_returns_true_when_row_exists(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = {"?column?": 1}

    exists = table.has_user_turn_context("sess-x", None)

    assert exists is True
    sql, params = cursor.execute.call_args[0]
    assert "SELECT 1" in sql
    assert "type = 'user_turn_context'" in sql
    assert "spawn_id IS NULL" in sql
    assert params == ("sess-x",)


def test_has_user_turn_context_returns_false_when_missing(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = None

    exists = table.has_user_turn_context("sess-x", "spawn-a")

    assert exists is False
    sql, params = cursor.execute.call_args[0]
    assert "SELECT 1" in sql
    assert "type = 'user_turn_context'" in sql
    assert "spawn_id = %s" in sql
    assert params == ("sess-x", "spawn-a")


def test_query_context_events_builds_filtered_desc_query(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchall.return_value = [
        {
            "id": 10,
            "session_id": "sess-x",
            "source": "MatMaster",
            "type": "user_turn_context",
            "content": '{"kind": "anchor"}',
            "task_id": "task-1",
            "invocation_id": "inv-1",
            "spawn_id": None,
            "created_at": None,
        }
    ]

    events = table.query_context_events(
        session_id="sess-x",
        spawn_id=None,
        until_event_id=20,
        event_types=("user_turn_context", "history_checkpoint"),
        limit=50,
        order="desc",
    )

    sql, params = cursor.execute.call_args[0]
    assert events[0]["id"] == 10
    assert events[0]["content"] == {"kind": "anchor"}
    assert "spawn_id IS NULL" in sql
    assert "id <= %s" in sql
    assert "type IN (%s, %s)" in sql
    assert "ORDER BY id DESC" in sql
    assert "LIMIT 50" in sql
    assert params == (
        "sess-x",
        20,
        "user_turn_context",
        "history_checkpoint",
    )


def test_query_context_events_supports_spawn_scope_and_ascending_order(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchall.return_value = []

    events = table.query_context_events(
        session_id="sess-x",
        spawn_id="spawn-1",
        until_event_id=None,
        event_types=None,
        limit=None,
        order="asc",
    )

    sql, params = cursor.execute.call_args[0]
    assert events == []
    assert "spawn_id = %s" in sql
    assert "ORDER BY id ASC" in sql
    assert "type IN" not in sql
    assert "LIMIT" not in sql
    assert params == ("sess-x", "spawn-1")


def test_query_context_events_supports_zero_limit(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchall.return_value = []

    events = table.query_context_events(
        session_id="sess-x",
        spawn_id=None,
        limit=0,
    )

    sql, params = cursor.execute.call_args[0]
    assert events == []
    assert "LIMIT 0" in sql
    assert params == ("sess-x",)


def test_query_context_events_preserves_user_query_raw_payload(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchall.return_value = [
        {
            "id": 11,
            "session_id": "sess-x",
            "source": "User",
            "type": "query",
            "content": json.dumps(
                {
                    "content": "Explain FeO.",
                    "files": ["https://oss.example.com/input.cif"],
                    "images": ["https://oss.example.com/image.png"],
                    "workspace_paths": ["/share/result.xyz"],
                },
                ensure_ascii=False,
            ),
            "task_id": "task-1",
            "invocation_id": "inv-1",
            "spawn_id": None,
            "created_at": None,
        }
    ]

    events = table.query_context_events(
        session_id="sess-x",
        spawn_id=None,
        until_event_id=None,
        event_types=None,
        limit=None,
        order="asc",
    )

    assert events[0]["content"]["content"] == "Explain FeO."
    assert events[0]["content"]["files"] == ["https://oss.example.com/input.cif"]
    assert events[0]["content"]["images"] == ["https://oss.example.com/image.png"]
    assert events[0]["content"]["workspace_paths"] == ["/share/result.xyz"]
    assert "files" not in events[0]
    assert "images" not in events[0]
