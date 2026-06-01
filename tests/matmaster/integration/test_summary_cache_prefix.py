from __future__ import annotations

import json

from matmaster.context.compaction import (
    SUMMARY_USER_REQUEST_TEMPLATE,
    prepare_messages_for_summary_call,
)
from matmaster.context.sources.turn_input import TurnInput
from matmaster.types.message_normalization import canonicalize_messages_for_provider
from matmaster.types.messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


def _api_bytes(messages) -> bytes:
    payload = [m.to_api_dict() for m in canonicalize_messages_for_provider(messages)]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def test_summary_common_case_shares_main_conversation_prefix() -> None:
    history = [
        SystemMessage(content="sys"),
        UserMessage(content="question"),
        AssistantMessage(content="answer"),
    ]
    compact_request = UserMessage(content=SUMMARY_USER_REQUEST_TEMPLATE)

    prep = prepare_messages_for_summary_call(
        full_messages=history,
        phase="runtime",
        turn_input=None,
        compact_request=compact_request,
        context_limit=20_000,
        reserved_summary_tokens=1_000,
    )

    assert prep.messages == history
    assert all(left is right for left, right in zip(prep.messages, history))
    main_prefix = _api_bytes(history)
    summary_prefix = _api_bytes(prep.messages)
    assert summary_prefix == main_prefix


def test_preflight_summary_prefix_excludes_current_instruction_tail() -> None:
    turn_input = TurnInput.from_values(user_text="new instruction")
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="old question"),
        AssistantMessage(content="old answer"),
        UserMessage(content="new instruction"),
    ]
    compact_request = UserMessage(content=SUMMARY_USER_REQUEST_TEMPLATE)

    prep = prepare_messages_for_summary_call(
        full_messages=messages,
        phase="preflight",
        turn_input=turn_input,
        compact_request=compact_request,
        context_limit=20_000,
        reserved_summary_tokens=1_000,
    )

    assert prep.messages == messages[:-1]
    assert _api_bytes(prep.messages) == _api_bytes(messages[:-1])


def test_parallel_oversized_tool_results_truncate_minimum_needed_without_mutation() -> (
    None
):
    tool_a = ToolMessage(
        content="A " * 14_000,
        tool_call_id="a",
        tool_name="paper_search",
    )
    tool_b = ToolMessage(
        content="B" * 10_000,
        tool_call_id="b",
        tool_name="paper_search",
    )
    tool_c = ToolMessage(
        content="C" * 1_000,
        tool_call_id="c",
        tool_name="paper_search",
    )
    messages = [
        SystemMessage(content="sys"),
        UserMessage(content="search"),
        AssistantMessage(
            content="",
            tool_calls=[
                ToolCallData(id="a", name="paper_search", arguments={}),
                ToolCallData(id="b", name="paper_search", arguments={}),
                ToolCallData(id="c", name="paper_search", arguments={}),
            ],
        ),
        tool_a,
        tool_b,
        tool_c,
    ]
    original_contents = [tool_a.content, tool_b.content, tool_c.content]
    compact_request = UserMessage(content=SUMMARY_USER_REQUEST_TEMPLATE)

    prep = prepare_messages_for_summary_call(
        full_messages=messages,
        phase="runtime",
        turn_input=None,
        compact_request=compact_request,
        context_limit=5_000,
        reserved_summary_tokens=1_000,
        safety_margin_tokens=500,
    )

    assert prep.prepared_tokens <= prep.message_budget
    assert prep.truncated_tool_call_ids[0] == "a"
    assert prep.messages[3].tool_call_id == "a"
    assert prep.messages[3].tool_name == "paper_search"
    assert prep.messages[5] is tool_c
    assert [tool_a.content, tool_b.content, tool_c.content] == original_contents
