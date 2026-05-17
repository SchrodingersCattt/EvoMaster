from __future__ import annotations

import pytest

from matmaster.context.compaction import (
    _select_tool_safe_tail,
    estimate_json_tokens,
)
from matmaster.types.message_normalization import (
    normalize_and_validate_openai_messages,
)
from matmaster.types.messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


def _assistant(*ids: str) -> AssistantMessage:
    return AssistantMessage(
        content="",
        tool_calls=[
            ToolCallData(id=tool_id, name="tool", arguments={"value": tool_id})
            for tool_id in ids
        ],
    )


def _tool(tool_id: str, content: str = "result") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=tool_id, tool_name="tool")


def test_estimate_json_tokens_counts_serialized_schema() -> None:
    schema = [
        {
            "type": "function",
            "function": {
                "name": "paper_search",
                "description": "Search literature",
                "parameters": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                },
            },
        }
    ]

    assert estimate_json_tokens(schema) > estimate_json_tokens([])
    assert estimate_json_tokens({"中文": "保留非 ASCII"}, safety_margin=1.5) >= (
        estimate_json_tokens({"中文": "保留非 ASCII"})
    )


def test_select_tool_safe_tail_keeps_complete_assistant_tool_pair() -> None:
    messages = [
        UserMessage(content="run"),
        _assistant("a", "b"),
        _tool("a"),
        _tool("b"),
    ]

    selected = _select_tool_safe_tail(messages, n=3)

    assert selected == messages[1:]
    normalize_and_validate_openai_messages([m.to_api_dict() for m in selected])


def test_select_tool_safe_tail_expands_backward_to_owner() -> None:
    messages = [
        UserMessage(content="old"),
        _assistant("a", "b"),
        _tool("a"),
        _tool("b"),
        AssistantMessage(content="done"),
    ]

    selected = _select_tool_safe_tail(messages, n=3)

    assert selected == messages[1:]
    normalize_and_validate_openai_messages([m.to_api_dict() for m in selected])


def test_select_tool_safe_tail_excludes_orphan_tool_messages() -> None:
    messages = [
        UserMessage(content="old"),
        _tool("missing-owner"),
        AssistantMessage(content="safe"),
    ]

    selected = _select_tool_safe_tail(messages, n=2)

    assert selected == [AssistantMessage(content="safe")]
    normalize_and_validate_openai_messages([m.to_api_dict() for m in selected])


def test_select_tool_safe_tail_returns_empty_for_all_orphans() -> None:
    assert _select_tool_safe_tail([_tool("a"), _tool("b")], n=2) == []
