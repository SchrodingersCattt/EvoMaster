"""ChatEventsTable.get_last_resolved_model_profile 查询行为测试。"""

import json
from typing import Any

from src.dao.chat_events_table import ChatEventsTable


def test_returns_profile_from_response_event(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = {
        "content": json.dumps(
            {
                "content": "回答",
                "model_profile": "matmaster/qwen3.7-max",
                "model_route": "matmaster/qwen3.7-max",
            }
        )
    }
    assert table.get_last_resolved_model_profile("s1") == "matmaster/qwen3.7-max"


def test_returns_profile_from_assistant_state_event(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = {
        "content": json.dumps(
            {
                "state": {"role": "assistant"},
                "model_profile": "global.anthropic.claude-opus-4-6-v1",
                "model_route": "global.anthropic.claude-opus-4-6-v1",
            }
        )
    }
    assert (
        table.get_last_resolved_model_profile("s1")
        == "global.anthropic.claude-opus-4-6-v1"
    )


def test_skips_byok_when_profile_is_byok(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = {
        "content": json.dumps(
            {"content": "x", "model_profile": "byok", "model_route": "byok:cred-1"}
        )
    }
    assert table.get_last_resolved_model_profile("s1") is None


def test_skips_byok_when_route_has_byok_prefix(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    # model_profile 看似普通，但 model_route 标记 byok -> 仍跳过
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = {
        "content": json.dumps(
            {
                "content": "x",
                "model_profile": "matmaster/qwen3.7-max",
                "model_route": "byok:cred-9",
            }
        )
    }
    assert table.get_last_resolved_model_profile("s1") is None


def test_returns_none_when_profile_missing(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = {"content": json.dumps({"content": "无模型字段"})}
    assert table.get_last_resolved_model_profile("s1") is None


def test_returns_none_when_no_row(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = None
    assert table.get_last_resolved_model_profile("s1") is None


def test_query_filters_parent_scope_and_event_types(
    chat_events_table_with_mocks: tuple[ChatEventsTable, Any],
) -> None:
    table, cursor = chat_events_table_with_mocks
    cursor.fetchone.return_value = None
    table.get_last_resolved_model_profile("sess-x")
    sql, params = cursor.execute.call_args[0]
    assert "spawn_id IS NULL" in sql
    assert "type IN ('response', 'assistant_state')" in sql
    assert "ORDER BY created_at DESC, id DESC" in sql
    assert "LIMIT 1" in sql
    assert params == ("sess-x",)
