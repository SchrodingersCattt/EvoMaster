from __future__ import annotations

from matmaster.providers.transports.anthropic_messages import (
    AnthropicMessagesTransport,
    AnthropicPromptCacheOptions,
    BedrockAnthropicTransport,
)
from matmaster.types.messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


def _provider(
    transport_cls: type[AnthropicMessagesTransport] = AnthropicMessagesTransport,
    **overrides,
) -> AnthropicMessagesTransport:
    values = {
        "system_prompt_breakpoint": True,
        "cache_control": {"type": "ephemeral"},
        "automatic": True,
        "latest_user_breakpoint": True,
        "tool_result_breakpoint": True,
        "flexible_breakpoint": True,
        "max_breakpoints": 4,
        "min_flexible_chars": 10,
    }
    values.update(overrides)
    options = AnthropicPromptCacheOptions(**values)
    return transport_cls(
        model="claude-opus-4-6",
        api_key="sk-test",
        prompt_cache_options=options,
    )


def test_cache_marks_system_latest_user_and_tool_result_with_automatic_slot() -> None:
    provider = _provider()
    kwargs = provider.build_kwargs(
        [
            SystemMessage(content="system prompt"),
            UserMessage(content="older long user content"),
            AssistantMessage(
                content="",
                tool_calls=[ToolCallData(id="toolu_1", name="search", arguments={})],
            ),
            ToolMessage(
                content="tool result", tool_call_id="toolu_1", tool_name="search"
            ),
            UserMessage(content="current"),
        ],
        tools=[
            {
                "type": "function",
                "function": {"name": "search", "parameters": {"type": "object"}},
            }
        ],
    )

    assert kwargs["system"] == [
        {
            "type": "text",
            "text": "system prompt",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert "cache_control" not in kwargs["messages"][0]["content"][0]
    assert kwargs["messages"][2]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["messages"][2]["content"][1]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["extra_body"]["cache_control"] == {"type": "ephemeral"}


def test_cache_dedupes_latest_user_and_tool_result_same_block() -> None:
    provider = _provider()
    kwargs = provider.build_kwargs(
        [
            SystemMessage(content="system prompt"),
            UserMessage(content="older long user content"),
            AssistantMessage(
                content="",
                tool_calls=[ToolCallData(id="toolu_1", name="search", arguments={})],
            ),
            ToolMessage(
                content="tool result", tool_call_id="toolu_1", tool_name="search"
            ),
        ],
        tools=[
            {
                "type": "function",
                "function": {"name": "search", "parameters": {"type": "object"}},
            }
        ],
    )

    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["messages"][2]["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "toolu_1",
            "content": "tool result",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert kwargs["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_cache_uses_flexible_when_fixed_targets_leave_a_slot() -> None:
    provider = _provider(tool_result_breakpoint=False)

    kwargs = provider.build_kwargs(
        [
            SystemMessage(content="system prompt"),
            UserMessage(content="older long user content"),
            UserMessage(content="current"),
        ],
        tools=None,
    )

    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["messages"][-1]["content"][0]["cache_control"] == {
        "type": "ephemeral"
    }
    assert kwargs["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_cache_respects_max_breakpoints_after_automatic_slot() -> None:
    provider = _provider(max_breakpoints=2)

    kwargs = provider.build_kwargs(
        [
            SystemMessage(content="system prompt"),
            UserMessage(content="older long user content"),
            UserMessage(content="current"),
        ],
        tools=None,
    )

    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in kwargs["messages"][-1]["content"][0]
    assert "cache_control" not in kwargs["messages"][0]["content"][0]


def test_bedrock_transport_converts_automatic_to_block_checkpoint() -> None:
    provider = _provider(BedrockAnthropicTransport, max_breakpoints=2)

    kwargs = provider.build_kwargs(
        [
            SystemMessage(content="system prompt"),
            UserMessage(content="older long user content"),
            UserMessage(content="current"),
        ],
        tools=None,
    )

    assert "extra_body" not in kwargs
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["messages"][-1]["content"][0]["cache_control"] == {
        "type": "ephemeral"
    }
    assert "cache_control" not in kwargs["messages"][0]["content"][0]


def test_one_hour_ttl_is_copied_to_all_cache_controls() -> None:
    provider = _provider(cache_control={"type": "ephemeral", "ttl": "1h"})

    kwargs = provider.build_kwargs(
        [SystemMessage(content="system prompt"), UserMessage(content="current")],
        tools=None,
    )

    assert kwargs["system"][0]["cache_control"] == {
        "type": "ephemeral",
        "ttl": "1h",
    }
    assert kwargs["messages"][0]["content"][0]["cache_control"] == {
        "type": "ephemeral",
        "ttl": "1h",
    }
    assert kwargs["extra_body"]["cache_control"] == {
        "type": "ephemeral",
        "ttl": "1h",
    }


def test_no_prompt_cache_options_leaves_payload_unmarked() -> None:
    provider = AnthropicMessagesTransport(model="claude-opus-4-6", api_key="sk-test")

    kwargs = provider.build_kwargs(
        [SystemMessage(content="system prompt"), UserMessage(content="current")],
        tools=None,
    )

    assert kwargs["system"] == "system prompt"
    assert kwargs["messages"][0]["content"] == [{"type": "text", "text": "current"}]
    assert "extra_body" not in kwargs
