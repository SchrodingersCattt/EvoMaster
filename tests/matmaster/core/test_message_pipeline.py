"""Tests for IncrementalMessagePipeline canonical Message output."""

from __future__ import annotations

from matmaster.core.message_pipeline import IncrementalMessagePipeline
from matmaster.types.message_normalization import canonicalize_messages_for_provider
from matmaster.types.messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)


def _pure_pipeline(messages):
    return canonicalize_messages_for_provider(messages)


def test_empty_then_first_feed():
    p = IncrementalMessagePipeline()
    assert p.feed_tail([]) == []

    msgs = [SystemMessage(content="hi"), UserMessage(content="hello")]
    out = p.feed_tail(msgs)
    assert out == _pure_pipeline(msgs)


def test_append_only_growth():
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
    p = IncrementalMessagePipeline()
    msgs = [SystemMessage(content="sys"), UserMessage(content="u1")]
    p.feed_tail(msgs)

    msgs.append(UserMessage(content="u2"))
    out = p.feed_tail(msgs)

    assert len(p._canonical_cache) == 2
    assert isinstance(p._canonical_cache[-1], UserMessage)
    assert p._canonical_cache[-1].content == "u1\n\nu2"
    assert out == _pure_pipeline(msgs)


def test_user_merge_within_tail():
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


def test_tool_call_assistant_then_tool_messages_are_preserved():
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


def test_top_level_caller_mutation_does_not_pollute_cache():
    p = IncrementalMessagePipeline()
    msgs = [SystemMessage(content="sys"), UserMessage(content="hello")]
    out1 = p.feed_tail(msgs)
    assert out1[0].content == "sys"

    out1.clear()

    out2 = p.feed_tail(msgs)
    assert out2[0].content == "sys"
