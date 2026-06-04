from __future__ import annotations

import pytest

from matmaster.context.compaction import (
    SUMMARY_USER_REQUEST_TEMPLATE,
    _select_tool_safe_tail,
    call_summary_llm_response,
    prepare_messages_for_summary_call,
    validate_summary_response,
)
from matmaster.context.sources.turn_input import TurnInput
from matmaster.types.message_normalization import (
    normalize_and_validate_openai_messages,
)
from matmaster.types.messages import (
    AssistantMessage,
    LLMResponse,
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


def test_select_tool_safe_tail_expands_to_large_parallel_tool_turn() -> None:
    messages = [
        UserMessage(content="old"),
        _assistant("a", "b", "c", "d"),
        _tool("a"),
        _tool("b"),
        _tool("c"),
        _tool("d"),
    ]

    selected = _select_tool_safe_tail(messages, n=3)

    assert selected == messages[1:]
    normalize_and_validate_openai_messages([m.to_api_dict() for m in selected])


def test_prepare_messages_common_case_preserves_message_identity() -> None:
    compact_request = UserMessage(content=SUMMARY_USER_REQUEST_TEMPLATE)
    full_messages = [
        SystemMessage(content="sys"),
        UserMessage(content="old"),
        AssistantMessage(content="answer"),
    ]

    prep = prepare_messages_for_summary_call(
        full_messages=full_messages,
        phase="runtime",
        turn_input=None,
        compact_request=compact_request,
        context_limit=20_000,
        reserved_summary_tokens=1_000,
    )

    assert prep.messages == full_messages
    assert all(left is right for left, right in zip(prep.messages, full_messages))
    assert prep.truncated_tool_call_ids == ()
    assert prep.original_tokens == prep.prepared_tokens
    assert prep.request_tokens > 0
    assert prep.message_budget > prep.prepared_tokens


def test_prepare_messages_preflight_excludes_current_user_when_split_applies() -> None:
    compact_request = UserMessage(content=SUMMARY_USER_REQUEST_TEMPLATE)
    current = TurnInput.from_values(user_text="new request")
    full_messages = [
        SystemMessage(content="sys"),
        UserMessage(content="old"),
        AssistantMessage(content="answer"),
        UserMessage(content="new request"),
    ]

    prep = prepare_messages_for_summary_call(
        full_messages=full_messages,
        phase="preflight",
        turn_input=current,
        compact_request=compact_request,
        context_limit=20_000,
        reserved_summary_tokens=1_000,
    )

    assert prep.messages == full_messages[:-1]
    assert full_messages[-1] not in prep.messages


def test_prepare_messages_runtime_includes_trailing_tool_message() -> None:
    compact_request = UserMessage(content=SUMMARY_USER_REQUEST_TEMPLATE)
    full_messages = [
        SystemMessage(content="sys"),
        UserMessage(content="run"),
        _assistant("a"),
        _tool("a", "large output"),
    ]

    prep = prepare_messages_for_summary_call(
        full_messages=full_messages,
        phase="runtime",
        turn_input=None,
        compact_request=compact_request,
        context_limit=20_000,
        reserved_summary_tokens=1_000,
    )

    assert prep.messages[-1] is full_messages[-1]


def test_prepare_messages_non_positive_budget_raises() -> None:
    compact_request = UserMessage(content=SUMMARY_USER_REQUEST_TEMPLATE)
    full_messages = [SystemMessage(content="sys"), UserMessage(content="old")]

    with pytest.raises(ValueError, match="summary message budget non-positive"):
        prepare_messages_for_summary_call(
            full_messages=full_messages,
            phase="runtime",
            turn_input=None,
            compact_request=compact_request,
            context_limit=1_000,
            reserved_summary_tokens=1_000,
        )


def test_prepare_messages_budget_uses_context_limit_minus_reserved(
    monkeypatch,
) -> None:
    from matmaster.context import compaction

    compact_request = UserMessage(content=SUMMARY_USER_REQUEST_TEMPLATE)
    full_messages = [SystemMessage(content="sys"), UserMessage(content="old")]

    def fake_estimate(messages, safety_margin=1.0):
        if len(messages) == 1 and messages[0] is compact_request:
            return 9_000
        return 100

    monkeypatch.setattr(compaction, "estimate_tokens", fake_estimate)

    prep = prepare_messages_for_summary_call(
        full_messages=full_messages,
        phase="runtime",
        turn_input=None,
        compact_request=compact_request,
        context_limit=20_000,
        reserved_summary_tokens=1_000,
        safety_margin_tokens=8_000,
    )

    assert prep.request_tokens == 9_000
    assert prep.message_budget == 19_000


@pytest.mark.asyncio
async def test_call_summary_llm_passes_tools_without_budgeting_schema() -> None:
    provider = RecordingProvider(content="structured summary")
    full_messages = [
        SystemMessage(content="sys"),
        UserMessage(content="old"),
        AssistantMessage(content="answer"),
    ]
    huge_tool_definitions = [
        {
            "type": "function",
            "function": {
                "name": f"tool_{idx}",
                "description": "x" * 8_000,
                "parameters": {
                    "type": "object",
                    "properties": {"arg": {"type": "string"}},
                },
            },
        }
        for idx in range(8)
    ]

    response = await call_summary_llm_response(
        llm_provider=provider,
        system_prompt="sys",
        full_messages=full_messages,
        phase="runtime",
        turn_input=None,
        tool_definitions=huge_tool_definitions,
        context_limit=6_000,
        reserved_summary_tokens=1_000,
        safety_margin_tokens=500,
    )

    assert validate_summary_response(response) == "structured summary"
    assert provider.calls[0]["tools"] is huge_tool_definitions


def test_prepare_messages_truncates_only_largest_tool_results_needed_to_fit() -> None:
    compact_request = UserMessage(content=SUMMARY_USER_REQUEST_TEMPLATE)
    small = _tool("small", "S" * 600)
    medium = _tool("medium", "M" * 4_000)
    large = _tool("large", "L" * 16_000)
    full_messages = [
        SystemMessage(content="sys"),
        UserMessage(content="run"),
        _assistant("small", "medium", "large"),
        small,
        medium,
        large,
    ]

    prep = prepare_messages_for_summary_call(
        full_messages=full_messages,
        phase="runtime",
        turn_input=None,
        compact_request=compact_request,
        context_limit=6_000,
        reserved_summary_tokens=1_000,
        safety_margin_tokens=500,
    )

    assert prep.prepared_tokens <= prep.message_budget
    assert "large" in prep.truncated_tool_call_ids
    assert prep.messages[3] is small
    assert prep.messages[4] is medium or prep.messages[4].tool_call_id == "medium"
    assert prep.messages[5] is not large
    assert prep.messages[5].tool_call_id == large.tool_call_id
    assert prep.messages[5].tool_name == large.tool_name
    assert "[tool_result truncated before summary call]" in (
        prep.messages[5].content or ""
    )
    assert "tool_name: tool" in (prep.messages[5].content or "")
    assert "tool_call_id: large" in (prep.messages[5].content or "")
    assert "original_chars: 16000" in (prep.messages[5].content or "")
    assert full_messages[5] is large
    assert full_messages[5].content == "L" * 16_000


class RecordingProvider:
    def __init__(
        self,
        content: str | None = "summary",
        *,
        tool_calls: list[ToolCallData] | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls
        self.usage = usage or {}
        self.calls: list[dict[str, object]] = []

    async def chat(self, messages, tools=None, *, tool_choice=None):
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        return LLMResponse(
            content=self.content,
            finish_reason="stop",
            tool_calls=self.tool_calls,
            usage=dict(self.usage),
        )


@pytest.mark.asyncio
async def test_call_summary_llm_uses_real_messages_tools_and_tool_choice_none() -> None:
    provider = RecordingProvider(content="structured summary")
    full_messages = [
        SystemMessage(content="main system"),
        UserMessage(content="old request"),
        AssistantMessage(content="old answer"),
    ]
    tools = [{"type": "function", "function": {"name": "paper_search"}}]

    response = await call_summary_llm_response(
        llm_provider=provider,
        system_prompt="main system",
        full_messages=full_messages,
        phase="runtime",
        turn_input=None,
        tool_definitions=tools,
        context_limit=20_000,
        reserved_summary_tokens=1_000,
    )

    assert validate_summary_response(response) == "structured summary"
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["tools"] is tools
    assert call["tool_choice"] == "none"
    roles = [msg["role"] for msg in call["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert call["messages"][0]["content"] == "main system"
    assert "<compact_request>" in call["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_validate_summary_response_raises_on_empty_response() -> None:
    with pytest.raises(ValueError, match="Summary LLM returned empty content"):
        validate_summary_response(LLMResponse(content="   ", finish_reason="stop"))


@pytest.mark.asyncio
async def test_validate_summary_response_rejects_tool_calls() -> None:
    response = LLMResponse(
        content="summary",
        finish_reason="tool_calls",
        tool_calls=[ToolCallData(id="tc-1", name="tool", arguments={})],
    )

    with pytest.raises(ValueError, match="Summary LLM attempted tool calls"):
        validate_summary_response(response)


@pytest.mark.asyncio
async def test_call_summary_llm_response_preserves_usage() -> None:
    provider = RecordingProvider(
        content="summary text",
        usage={"prompt_tokens": 40, "completion_tokens": 5, "total_tokens": 45},
    )

    response = await call_summary_llm_response(
        llm_provider=provider,
        system_prompt="sys",
        full_messages=[SystemMessage(content="sys"), UserMessage(content="old")],
        phase="runtime",
        turn_input=None,
        tool_definitions=None,
        context_limit=20_000,
        reserved_summary_tokens=1_000,
    )

    assert response.usage == {
        "prompt_tokens": 40,
        "completion_tokens": 5,
        "total_tokens": 45,
    }
    assert validate_summary_response(response) == "summary text"
