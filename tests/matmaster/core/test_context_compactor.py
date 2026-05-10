"""Tests for ContextCompactor."""

from __future__ import annotations

import pytest

from matmaster.core.context_builder import ContextBuilder
from matmaster.types.messages import (
    AssistantMessage,
    LLMResponse,
    StreamChunk,
    SystemMessage,
    ToolCallData,
    ToolMessage,
    UserMessage,
)
from matmaster.types.runtime import CompactionConfig


class FakeRehydrator:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.calls = 0

    async def build(self) -> str:
        self.calls += 1
        return self.text


class TestEstimateTokens:
    """Token estimation for messages."""

    def test_empty_list(self) -> None:
        from matmaster.core.context_compactor import estimate_tokens

        assert estimate_tokens([]) == 0

    def test_single_message(self) -> None:
        from matmaster.core.context_compactor import estimate_tokens

        msgs = [UserMessage(content="hello world")]
        tokens = estimate_tokens(msgs)
        assert tokens > 0
        assert tokens < 20

    def test_multiple_messages(self) -> None:
        from matmaster.core.context_compactor import estimate_tokens

        msgs = [
            SystemMessage(content="You are helpful"),
            UserMessage(content="What is 2+2?"),
            AssistantMessage(content="4"),
        ]
        total = estimate_tokens(msgs)
        single = estimate_tokens([msgs[0]])
        assert total > single

    def test_tool_message_content(self) -> None:
        from matmaster.core.context_compactor import estimate_tokens

        long_result = "x" * 4000
        msgs = [ToolMessage(content=long_result, tool_call_id="tc-1", tool_name="t")]
        tokens = estimate_tokens(msgs)
        assert tokens > 500


class TestParseTurns:
    """Turn boundary detection for retention rule."""

    def test_simple_assistant_tool_turn(self) -> None:
        from matmaster.core.context_compactor import parse_turns

        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content="task"),
            AssistantMessage(
                content="thinking",
                tool_calls=[ToolCallData(id="tc1", name="bash", arguments={})],
            ),
            ToolMessage(content="result", tool_call_id="tc1", tool_name="bash"),
            AssistantMessage(content="done"),
        ]
        turns = parse_turns(msgs)
        assert len(turns) == 2
        assert len(turns[0]) == 2
        assert len(turns[1]) == 1

    def test_turn_includes_preceding_user_message(self) -> None:
        from matmaster.core.context_compactor import parse_turns

        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content="task1"),
            AssistantMessage(content="reply1"),
            UserMessage(content="task2"),
            AssistantMessage(content="reply2"),
        ]
        turns = parse_turns(msgs)
        assert len(turns) == 2
        assert len(turns[0]) == 1
        assert isinstance(turns[0][0], AssistantMessage)
        assert len(turns[1]) == 2
        assert isinstance(turns[1][0], UserMessage)
        assert isinstance(turns[1][1], AssistantMessage)

    def test_empty_after_immutable(self) -> None:
        from matmaster.core.context_compactor import parse_turns

        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content="task"),
        ]
        turns = parse_turns(msgs)
        assert len(turns) == 0

    def test_multi_tool_calls_in_one_turn(self) -> None:
        from matmaster.core.context_compactor import parse_turns

        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content="task"),
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCallData(id="tc1", name="a", arguments={}),
                    ToolCallData(id="tc2", name="b", arguments={}),
                ],
            ),
            ToolMessage(content="r1", tool_call_id="tc1", tool_name="a"),
            ToolMessage(content="r2", tool_call_id="tc2", tool_name="b"),
        ]
        turns = parse_turns(msgs)
        assert len(turns) == 1
        assert len(turns[0]) == 3


class MockSummaryProvider:
    """Provider that returns a fixed summary."""

    def __init__(self, summary: str = "Summary of old conversation.") -> None:
        self._summary = summary
        self.calls: list[list[dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None):
        self.calls.append(messages)
        return LLMResponse(content=self._summary, finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content=self._summary, finish_reason="stop")


class FailingSummaryProvider:
    """Provider that always raises."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None):
        raise RuntimeError("LLM unavailable")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="", finish_reason="stop")


class DummySummaryProvider:
    """Minimal summary provider that returns text or raises a configured error."""

    def __init__(self, response: str | Exception) -> None:
        self._response = response
        self.calls: list[list[dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None):
        self.calls.append(messages)
        if isinstance(self._response, Exception):
            raise self._response
        return LLMResponse(content=self._response, finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        if isinstance(self._response, Exception):
            raise self._response
        yield StreamChunk(content=self._response, finish_reason="stop")


def _make_compactor(
    config,
    provider,
    *,
    rehydrated: str = "",
    event_sink=None,
    compaction_scope: str = "root",
):
    from matmaster.core.context_compactor import ContextCompactor

    return ContextCompactor(
        config=config,
        summary_provider=provider,
        rehydrator=FakeRehydrator(rehydrated),
        context_builder=ContextBuilder(),
        event_sink=event_sink,
        compaction_scope=compaction_scope,
    )


def _build_long_conversation(n_turns: int = 10) -> list:
    """Build a conversation with SystemMessage + UserMessage + N turns."""
    msgs = [
        SystemMessage(content="You are helpful"),
        UserMessage(content="Analyze this data: " + "x" * 200),
    ]
    for i in range(n_turns):
        msgs.append(
            AssistantMessage(
                content=f"Turn {i} thinking",
                tool_calls=[
                    ToolCallData(
                        id=f"tc-{i}",
                        name="bash",
                        arguments={"cmd": "ls"},
                    )
                ],
            )
        )
        msgs.append(
            ToolMessage(
                content="large result " + "y" * 500,
                tool_call_id=f"tc-{i}",
                tool_name="bash",
            )
        )
    return msgs


def _make_multi_turn_messages() -> list:
    return _build_long_conversation(5)


class TestCompactorThreshold:
    async def test_skip_when_below_threshold(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(context_limit=128000, trigger_ratio=0.9)
        provider = MockSummaryProvider()
        compactor = _make_compactor(config, provider)
        msgs = [SystemMessage(content="sys"), UserMessage(content="task")]
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 1000}, turn=2)
        assert len(provider.calls) == 0

    async def test_trigger_when_above_threshold(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = MockSummaryProvider()
        msgs = _build_long_conversation(5)
        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)
        assert len(provider.calls) == 1

    async def test_cooldown_skips_consecutive_turn(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = MockSummaryProvider()
        msgs = _build_long_conversation(5)
        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)
        assert len(provider.calls) == 1

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=3)
        assert len(provider.calls) == 1


class TestCompactorPlanApply:
    async def test_plan_runtime_compaction_returns_running_metadata(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor
        from matmaster.types.runtime import CompactionConfig

        provider = DummySummaryProvider("summary text")
        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        compactor = _make_compactor(
            config,
            provider,
            event_sink=None,
            compaction_scope="task-1:root",
        )
        msgs = _make_multi_turn_messages()
        compactor.update_message_count(len(msgs))

        plan = await compactor.plan_runtime_compaction(
            msgs,
            {"prompt_tokens": 950},
            turn=2,
        )

        assert plan is not None
        assert plan.compaction_id == "task-1:root:1"
        assert plan.phase == "runtime"
        assert plan.trigger_tokens == 950
        assert plan.strategy is None

    async def test_apply_compaction_plan_reports_fallback_strategy(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor
        from matmaster.types.runtime import CompactionConfig

        provider = DummySummaryProvider(RuntimeError("summary down"))
        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        compactor = _make_compactor(
            config,
            provider,
            event_sink=None,
            compaction_scope="task-1:root",
        )
        msgs = _make_multi_turn_messages()
        compactor.update_message_count(len(msgs))

        plan = await compactor.plan_runtime_compaction(
            msgs, {"prompt_tokens": 950}, turn=2
        )
        result = await compactor.apply_compaction_plan(plan, msgs)

        assert result.compaction_id == "task-1:root:1"
        assert result.strategy == "sliding_window"
        assert result.durability == "ephemeral"
        assert result.failure_reason == "summary down"


class TestCompactorOutput:
    async def test_output_structure(self) -> None:
        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = MockSummaryProvider(summary="Summarized content.")
        msgs = _build_long_conversation(10)
        compactor = _make_compactor(
            config,
            provider,
            rehydrated="<attachments>\nfile_1 a.csv https://oss/a.csv\n</attachments>",
        )
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)

        assert isinstance(msgs[0], SystemMessage)
        assert msgs[0].content == "You are helpful"
        assert isinstance(msgs[1], UserMessage)
        assert "[Compacted Context]" not in (msgs[1].content or "")
        assert "<previous_session_summary>" in (msgs[1].content or "")
        assert "Summarized content." in msgs[1].content
        assert "<rehydrated_context>" in (msgs[1].content or "")
        assert "Analyze this data" not in (msgs[1].content or "")
        assert len(msgs) == 2

    async def test_apply_summary_outputs_system_plus_single_user_bundle(self) -> None:
        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = MockSummaryProvider(summary="Summarized content.")
        msgs = _build_long_conversation(10)
        compactor = _make_compactor(
            config,
            provider,
            rehydrated="<attachments>\nfile_1 a.csv https://oss/a.csv\n</attachments>",
        )
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)

        assert len(msgs) == 2
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[1], UserMessage)
        assert "[Compacted Context]" not in (msgs[1].content or "")
        assert "<previous_session_summary>" in (msgs[1].content or "")
        assert "Summarized content." in (msgs[1].content or "")
        assert "<rehydrated_context>" in (msgs[1].content or "")
        assert "Analyze this data" not in (msgs[1].content or "")

    async def test_base_snapshot_contains_only_user_bundle(self) -> None:
        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = MockSummaryProvider(summary="summary")
        msgs = _build_long_conversation(5)
        compactor = _make_compactor(config, provider)

        plan = compactor.plan_preflight_compaction(msgs)
        result = await compactor.apply_compaction_plan(plan, msgs)

        assert result.base_snapshot is not None
        assert [item["role"] for item in result.base_snapshot] == ["user"]
        assert "<previous_session_summary>" in result.base_snapshot[0]["content"]

    async def test_second_compact_summarizes_first_bundle_without_special_case(
        self,
    ) -> None:
        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = MockSummaryProvider(summary="second summary")
        first_bundle = ContextBuilder().build_compact_bundle(summary="first summary")
        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content=first_bundle),
            AssistantMessage(content="tail answer"),
            UserMessage(content="tail question"),
        ]
        compactor = _make_compactor(config, provider)

        plan = compactor.plan_preflight_compaction(msgs)
        await compactor.apply_compaction_plan(plan, msgs)

        prompt_text = provider.calls[0][1]["content"]
        assert "<previous_session_summary>" in prompt_text
        assert "first summary" in prompt_text
        assert "second summary" in (msgs[1].content or "")

    async def test_fallback_on_summary_failure(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = FailingSummaryProvider()
        msgs = _build_long_conversation(10)
        original_len = len(msgs)
        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)

        assert len(msgs) == original_len
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[1], UserMessage)
        assert "[Compacted Context]" not in (msgs[0].content or "")
        assert any(
            isinstance(message, ToolMessage) and "truncated" in (message.content or "")
            for message in msgs
        )


class TestCompactorMessageCount:
    def test_update_message_count(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(context_limit=128000)
        provider = MockSummaryProvider()
        compactor = _make_compactor(config, provider)

        compactor.update_message_count(5)
        assert compactor._last_llm_message_count == 5

        compactor.update_message_count(8)
        assert compactor._last_llm_message_count == 8


class TestCompactorResultMetadata:
    async def test_apply_runtime_plan_reports_summary_result(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = MockSummaryProvider()
        msgs = _build_long_conversation(5)
        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        plan = await compactor.plan_runtime_compaction(
            msgs, {"prompt_tokens": 950}, turn=2
        )
        result = await compactor.apply_compaction_plan(plan, msgs)

        assert result.compaction_count == 1
        assert result.strategy == "summary"
        assert result.trigger_tokens > 0

    async def test_preflight_summary_returns_durable_result(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = MockSummaryProvider()
        msgs = _build_long_conversation(5)
        compactor = _make_compactor(config, provider)

        plan = compactor.plan_preflight_compaction(msgs)
        result = await compactor.apply_compaction_plan(plan, msgs)

        assert result.phase == "preflight"
        assert result.strategy == "summary"
        assert result.durability == "durable"

    async def test_runtime_sliding_window_result_is_ephemeral(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = FailingSummaryProvider()
        msgs = _build_long_conversation(10)
        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        plan = await compactor.plan_runtime_compaction(
            msgs, {"prompt_tokens": 950}, turn=2
        )
        result = await compactor.apply_compaction_plan(plan, msgs)

        assert result.phase == "runtime"
        assert result.strategy == "sliding_window"
        assert result.durability == "ephemeral"

    async def test_preflight_summary_failure_raises_instead_of_silent_fallback(
        self,
    ) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = FailingSummaryProvider()
        msgs = _build_long_conversation(5)
        compactor = _make_compactor(config, provider)

        with pytest.raises(RuntimeError, match="LLM unavailable"):
            await compactor.preflight_if_needed(msgs)

    async def test_summary_input_contains_tool_name_and_call_id(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = MockSummaryProvider()
        msgs = _build_long_conversation(5)
        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)

        prompt_text = provider.calls[0][1]["content"]
        assert "tool_call_id" in prompt_text
        assert "tool_name" in prompt_text
        assert "bash" in prompt_text

    async def test_compact_if_needed_succeeds_without_event_sink(self) -> None:
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = MockSummaryProvider()
        msgs = _build_long_conversation(5)
        compactor = _make_compactor(config, provider, event_sink=None)
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)
        assert compactor._compaction_count == 1


class TestToolTruncationFallback:
    """Tool result truncation when no old turns to compress."""

    async def test_preflight_summarizes_single_turn_without_old_turns(
        self,
    ) -> None:
        """Preflight uses the same summary-to-bundle path for any compact window."""
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(context_limit=500, trigger_ratio=0.9)
        provider = MockSummaryProvider()
        received: list = []

        async def sink(event):
            received.append(event)

        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content="task"),
            AssistantMessage(
                content="calling tools",
                tool_calls=[ToolCallData(id="tc-0", name="bash", arguments={})],
            ),
            ToolMessage(
                content="big result " + "A" * 4000,
                tool_call_id="tc-0",
                tool_name="bash",
            ),
        ]

        compactor = _make_compactor(config, provider, event_sink=sink)

        await compactor.preflight_if_needed(msgs)

        assert len(provider.calls) == 1
        assert len(msgs) == 2
        assert isinstance(msgs[1], UserMessage)
        assert "<previous_session_summary>" in (msgs[1].content or "")
        assert len(received) == 0

    async def test_summarizes_when_no_compressible_turns(self) -> None:
        """1 turn with huge tool results -> summary bundle on success."""
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(context_limit=500, trigger_ratio=0.9)
        provider = MockSummaryProvider()

        # 1 turn: Assistant with 3 tool calls + 3 large ToolMessages
        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content="task"),
            AssistantMessage(
                content="calling tools",
                tool_calls=[
                    ToolCallData(id=f"tc-{i}", name="bash", arguments={})
                    for i in range(3)
                ],
            ),
            ToolMessage(
                content="big result " + "A" * 2000,
                tool_call_id="tc-0",
                tool_name="bash",
            ),
            ToolMessage(
                content="big result " + "B" * 2000,
                tool_call_id="tc-1",
                tool_name="bash",
            ),
            ToolMessage(
                content="big result " + "C" * 2000,
                tool_call_id="tc-2",
                tool_name="bash",
            ),
        ]

        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        plan = await compactor.plan_runtime_compaction(
            msgs, {"prompt_tokens": 600}, turn=3
        )
        result = await compactor.apply_compaction_plan(plan, msgs)

        assert len(provider.calls) == 1
        assert compactor._compaction_count == 1
        assert len(msgs) == 2
        assert isinstance(msgs[1], UserMessage)
        assert result.strategy == "summary"
        assert result.durability == "durable"

    async def test_no_truncation_for_small_tool_results_on_summary_failure(self) -> None:
        """Small tool results (< 500 chars) are not truncated."""
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(context_limit=500, trigger_ratio=0.9)
        provider = FailingSummaryProvider()

        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content="task"),
            AssistantMessage(
                content="",
                tool_calls=[ToolCallData(id="tc-0", name="t", arguments={})],
            ),
            ToolMessage(content="short result", tool_call_id="tc-0", tool_name="t"),
        ]

        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 600}, turn=3)

        # Small content -> not truncated (truncation skips content < 500 chars)
        assert "truncated" not in (msgs[3].content or "")

    async def test_truncation_preserves_head_and_tail_on_summary_failure(self) -> None:
        """Fallback truncation keeps head 200 + tail 100 chars."""
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(context_limit=200, trigger_ratio=0.9)
        provider = FailingSummaryProvider()
        original_content = "HEAD_MARKER_" + "x" * 2000 + "_TAIL_MARKER"

        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content="task"),
            AssistantMessage(
                content="",
                tool_calls=[ToolCallData(id="tc-0", name="t", arguments={})],
            ),
            ToolMessage(content=original_content, tool_call_id="tc-0", tool_name="t"),
        ]

        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 300}, turn=3)

        result_content = msgs[3].content or ""
        assert "HEAD_MARKER_" in result_content  # head preserved
        assert "_TAIL_MARKER" in result_content  # tail preserved
        assert "truncated" in result_content  # marker present
        assert len(result_content) < len(original_content)


class TestCompactorCompatibility:
    """Compatibility checks for the compactor surface."""

    async def test_no_event_when_no_sink(self) -> None:
        """Compactor with event_sink=None still works."""
        from matmaster.core.context_compactor import ContextCompactor

        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = MockSummaryProvider()
        msgs = _build_long_conversation(5)
        compactor = _make_compactor(config, provider, event_sink=None)
        compactor.update_message_count(len(msgs))

        await compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=2)
        assert compactor._compaction_count == 1

    async def test_no_bus_import_in_compactor(self) -> None:
        """ContextCompactor module should not import MessageBus."""
        import ast
        import inspect

        from matmaster.core import context_compactor

        source = inspect.getsource(context_compactor)
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "bus" in node.module:
                    # Check it's specifically importing MessageBus
                    for alias in node.names:
                        assert (
                            alias.name != "MessageBus"
                        ), "ContextCompactor should not import MessageBus"
