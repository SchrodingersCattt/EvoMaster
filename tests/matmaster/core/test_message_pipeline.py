"""Tests for IncrementalMessagePipeline (E3 fix layer 2)."""

from __future__ import annotations

import pytest

from matmaster.core.message_pipeline import IncrementalMessagePipeline
from matmaster.types.errors import LLMError
from matmaster.types.message_normalization import (
    canonicalize_messages_for_provider,
    normalize_and_validate_openai_messages,
    validate_openai_messages,
    validate_openai_tool_turn_sequence,
)
from matmaster.types.messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


def _pure_pipeline(messages):
    """Reference implementation for the pre-E3 main-loop call."""
    return normalize_and_validate_openai_messages(
        canonicalize_messages_for_provider(messages)
    )


def test_empty_then_first_feed():
    """Empty input returns empty output; first non-empty feed equals pure path."""
    p = IncrementalMessagePipeline()
    assert p.feed_tail([]) == []

    msgs = [SystemMessage(content="hi"), UserMessage(content="hello")]
    out = p.feed_tail(msgs)
    assert out == _pure_pipeline(msgs)


def test_append_only_growth():
    """Multiple append-only feeds stay equivalent to the pure path."""
    p = IncrementalMessagePipeline()
    msgs = [
        SystemMessage(content="sys"),
        UserMessage(content="u1"),
    ]
    out1 = p.feed_tail(msgs)
    assert out1 == _pure_pipeline(msgs)

    msgs.append(UserMessage(content="u2"))
    out2 = p.feed_tail(msgs)
    assert out2 == _pure_pipeline(msgs)

    msgs.append(SystemMessage(content="more system"))
    out3 = p.feed_tail(msgs)
    assert out3 == _pure_pipeline(msgs)


def test_user_merge_at_cache_boundary():
    """Cache tail user plus new user merges both canonical and API caches."""
    p = IncrementalMessagePipeline()
    msgs = [SystemMessage(content="sys"), UserMessage(content="u1")]
    p.feed_tail(msgs)

    msgs.append(UserMessage(content="u2"))
    out = p.feed_tail(msgs)

    assert len(p._canonical_cache) == 2
    assert isinstance(p._canonical_cache[-1], UserMessage)
    assert p._canonical_cache[-1].content == "u1\n\nu2"
    assert p._api_cache[-1]["content"] == "u1\n\nu2"
    assert out == _pure_pipeline(msgs)


def test_user_merge_within_tail():
    """Adjacent users inside a single tail segment collapse to one message."""
    p = IncrementalMessagePipeline()
    msgs = [
        SystemMessage(content="sys"),
        UserMessage(content="a"),
        UserMessage(content="b"),
        UserMessage(content="c"),
    ]
    out = p.feed_tail(msgs)
    assert out == _pure_pipeline(msgs)
    assert sum(1 for m in p._canonical_cache if isinstance(m, UserMessage)) == 1


def test_tool_call_assistant_then_tool_messages_across_feeds():
    """Unclosed tool turn raises, then complete sequence succeeds after reset."""
    p = IncrementalMessagePipeline()
    msgs = [
        SystemMessage(content="sys"),
        UserMessage(content="u"),
        AssistantMessage(
            content=None,
            tool_calls=[
                ToolCallData(id="c1", name="search", arguments={"q": "x"}),
            ],
        ),
        ToolMessage(tool_call_id="c1", tool_name="search", content="result"),
    ]

    with pytest.raises(LLMError, match="missing tool_result ids"):
        p.feed_tail(msgs[:3])

    out = p.feed_tail(msgs)
    assert out == _pure_pipeline(msgs)


def test_explicit_reset_drops_cache():
    p = IncrementalMessagePipeline()
    msgs = [SystemMessage(content="s"), UserMessage(content="u")]
    p.feed_tail(msgs)
    assert p._source_len == 2

    p.reset()
    assert p._source_len == 0
    assert p._canonical_cache == []
    assert p._api_cache == []
    assert p._prefix_fingerprint is None

    out = p.feed_tail(msgs)
    assert out == _pure_pipeline(msgs)


def test_prefix_truncation_auto_reset(caplog):
    p = IncrementalMessagePipeline()
    msgs = [
        SystemMessage(content="s"),
        UserMessage(content="u1"),
        UserMessage(content="u2"),
    ]
    p.feed_tail(msgs)

    shorter = msgs[:1]
    with caplog.at_level("WARNING"):
        out = p.feed_tail(shorter)

    assert any("pipeline prefix shrunk" in rec.message for rec in caplog.records)
    assert out == _pure_pipeline(shorter)


def test_prefix_replacement_auto_reset(caplog):
    p = IncrementalMessagePipeline()
    msgs = [SystemMessage(content="s"), UserMessage(content="u1")]
    p.feed_tail(msgs)

    replaced = [
        SystemMessage(content="s"),
        UserMessage(content="u1"),
        UserMessage(content="u2"),
    ]
    with caplog.at_level("WARNING"):
        out = p.feed_tail(replaced)

    assert any(
        "pipeline prefix mutation detected" in rec.message for rec in caplog.records
    )
    assert out == _pure_pipeline(replaced)


def test_revalidate_full_matches_pure_validators_on_normalized_payloads():
    """revalidate_full forwards to the two pure validators for normalized input."""
    p = IncrementalMessagePipeline()
    msgs = [
        SystemMessage(content="s"),
        UserMessage(content="u"),
        AssistantMessage(
            content=None,
            tool_calls=[ToolCallData(id="c1", name="t", arguments={})],
        ),
        ToolMessage(tool_call_id="c1", tool_name="t", content="r"),
    ]
    api = p.feed_tail(msgs)

    p.revalidate_full(api)
    validate_openai_messages(api)
    validate_openai_tool_turn_sequence(api)


def test_pending_tool_call_raises_on_feed_tail_exit():
    """Ending with assistant(tool_calls) but no tool results raises and resets."""
    p = IncrementalMessagePipeline()
    msgs = [
        SystemMessage(content="s"),
        UserMessage(content="u"),
        AssistantMessage(
            content=None,
            tool_calls=[ToolCallData(id="c1", name="t", arguments={})],
        ),
    ]
    with pytest.raises(LLMError, match="missing tool_result ids"):
        p.feed_tail(msgs)
    assert p._source_len == 0
    assert p._canonical_cache == []


def test_invalid_tool_id_raises_lazily_with_cache_reset():
    """Mismatched tool_call_id raises, resets cache, and later valid input works."""
    p = IncrementalMessagePipeline()
    bad = [
        SystemMessage(content="s"),
        UserMessage(content="u"),
        AssistantMessage(
            content=None,
            tool_calls=[ToolCallData(id="c1", name="t", arguments={})],
        ),
        ToolMessage(tool_call_id="WRONG", tool_name="t", content="r"),
    ]
    with pytest.raises(LLMError):
        p.feed_tail(bad)
    assert p._source_len == 0

    good = [
        SystemMessage(content="s"),
        UserMessage(content="u"),
        AssistantMessage(
            content=None,
            tool_calls=[ToolCallData(id="c1", name="t", arguments={})],
        ),
        ToolMessage(tool_call_id="c1", tool_name="t", content="r"),
    ]
    out = p.feed_tail(good)
    assert out == _pure_pipeline(good)


def test_top_level_caller_mutation_does_not_pollute_cache():
    """Mutating a returned top-level dict does not modify pipeline cache."""
    p = IncrementalMessagePipeline()
    msgs = [SystemMessage(content="sys"), UserMessage(content="hello")]
    out1 = p.feed_tail(msgs)
    assert out1[0]["content"] == "sys"

    out1[0]["content"] = "POLLUTED"

    out2 = p.feed_tail(msgs)
    assert out2[0]["content"] == "sys"


def _build_complex_fixture() -> list:
    fixture: list = [
        SystemMessage(content="You are a helpful assistant."),
        UserMessage(content="user request"),
    ]
    for turn in range(3):
        fixture.append(
            AssistantMessage(
                content="thinking..." if turn == 0 else None,
                tool_calls=[
                    ToolCallData(id=f"c{turn}_0", name="tool_a", arguments={"q": turn}),
                    ToolCallData(
                        id=f"c{turn}_1",
                        name="tool_b",
                        arguments={"q": -turn},
                    ),
                ],
            )
        )
        fixture.append(
            ToolMessage(
                tool_call_id=f"c{turn}_0",
                tool_name="tool_a",
                content=f"a{turn}",
            )
        )
        fixture.append(
            ToolMessage(
                tool_call_id=f"c{turn}_1",
                tool_name="tool_b",
                content=f"b{turn}",
            )
        )
    fixture.append(AssistantMessage(content="done", tool_calls=None))
    return fixture


def _clean_boundary_indices(messages: list) -> list[int]:
    boundaries: list[int] = []
    pending = 0
    for i, message in enumerate(messages, start=1):
        if isinstance(message, AssistantMessage) and message.tool_calls:
            pending = len(message.tool_calls)
        elif isinstance(message, ToolMessage):
            pending = max(0, pending - 1)
        if pending == 0:
            boundaries.append(i)
    return boundaries


def _pending_boundary_indices(messages: list) -> list[int]:
    pending_lens: list[int] = []
    pending = 0
    for i, message in enumerate(messages, start=1):
        if isinstance(message, AssistantMessage) and message.tool_calls:
            pending = len(message.tool_calls)
        elif isinstance(message, ToolMessage):
            pending = max(0, pending - 1)
        if pending > 0:
            pending_lens.append(i)
    return pending_lens


def test_pipeline_output_equals_pure_pipeline_for_clean_prefixes():
    """Pipeline and pure path are byte-for-byte equivalent on legal prefixes."""
    msgs = _build_complex_fixture()
    p = IncrementalMessagePipeline()
    for prefix_len in _clean_boundary_indices(msgs):
        out = p.feed_tail(msgs[:prefix_len])
        assert out == _pure_pipeline(msgs[:prefix_len]), (
            f"divergence at clean prefix_len={prefix_len}"
        )


def test_pipeline_and_pure_both_raise_on_pending_tool_boundary():
    """Pipeline and pure path both reject prefixes cut mid tool turn."""
    msgs = _build_complex_fixture()
    for prefix_len in _pending_boundary_indices(msgs):
        p = IncrementalMessagePipeline()
        with pytest.raises(LLMError, match="missing tool_result ids"):
            p.feed_tail(msgs[:prefix_len])
        with pytest.raises(LLMError, match="missing tool_result ids"):
            _pure_pipeline(msgs[:prefix_len])
