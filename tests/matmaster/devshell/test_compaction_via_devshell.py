"""压缩机制综合测试 -- 通过 devshell 路径验证自动压缩行为。

测试矩阵:
1. 默认路径: devshell → Exp → assemble → CompactionConfig(enabled=False) → 不触发
2. 阈值触发: 小 context_window + 高 prompt_tokens → 触发摘要压缩
3. 阈值未达: 低 prompt_tokens → 不触发
4. 冷却机制: 连续 turn 不触发
5. 摘要策略: summary provider 正常 → [Compacted Context] 摘要
6. 滑动窗口回退: summary provider 失败 → sliding_window 截断
7. 事件发射: MessageBus 收到 ContextCompactionEvent
8. 多轮压缩: 首次压缩后继续积累，再次触发
9. retained turns 选择: 3 轮最低保留 + token budget 约束
10. Kernel 集成: 完整 kernel loop 中压缩触发且结果正确
"""

from __future__ import annotations

import queue

import pytest

from matmaster.core.bus import MessageBus
from matmaster.core.context_compactor import ContextCompactor, estimate_tokens, parse_turns
from matmaster.types.events import ContextCompactionEvent
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


# ── Fixtures ──────────────────────────────────────────────


class MockSummaryProvider:
    """返回固定摘要的 mock provider。"""

    def __init__(self, summary: str = "Summary of old conversation.") -> None:
        self._summary = summary
        self.call_count = 0
        self.call_messages: list[list[dict]] = []

    def chat(self, messages, tools=None):
        self.call_count += 1
        self.call_messages.append(messages)
        return LLMResponse(content=self._summary, finish_reason="stop")

    def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content=self._summary, finish_reason="stop")


class FailingSummaryProvider:
    """始终失败的 provider，测试回退逻辑。"""

    def chat(self, messages, tools=None):
        raise RuntimeError("LLM unavailable for summary")

    def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="", finish_reason="stop")


def _build_conversation(n_turns: int, content_size: int = 500) -> list:
    """构建包含 n 个完整 turn 的对话。"""
    msgs = [
        SystemMessage(content="You are a helpful assistant."),
        UserMessage(content="Analyze this dataset: " + "x" * 200),
    ]
    for i in range(n_turns):
        msgs.append(
            AssistantMessage(
                content=f"Turn {i}: analyzing... " + "a" * content_size,
                tool_calls=[
                    ToolCallData(id=f"tc-{i}", name="bash", arguments={"cmd": "ls"})
                ],
            )
        )
        msgs.append(
            ToolMessage(
                content="result: " + "r" * content_size,
                tool_call_id=f"tc-{i}",
                tool_name="bash",
            )
        )
    return msgs


# ── Test 1: 默认 devshell 路径压缩不启用 ─────────────────


class TestDefaultDevshellPath:
    """验证 devshell 默认路径下 compaction 为 disabled。"""

    def test_exp_assemble_compaction_disabled(self) -> None:
        """Exp.assemble() 产出的 CompactionConfig.enabled 默认为 False。"""
        from matmaster.config.exp import ExpConfig
        from matmaster.core.exp import Exp
        from matmaster.types.context import PlaygroundContext

        config = ExpConfig(name="test", max_turns=5)
        exp = Exp(config)

        # 构造最小 PlaygroundContext
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            ctx = PlaygroundContext(
                workdir=workdir,
                session_type="local",
                cache_area=workdir / ".cache",
                session=None,
                llm_provider=None,
                config_dir=None,
                llm_config=None,
                run_meta={},
            )
            spec = exp.assemble(ctx)

        assert spec.compaction.enabled is False, (
            "默认 CompactionConfig 应为 disabled"
        )
        assert spec.compactor is None, "assemble 阶段不应创建 compactor 实例"

    def test_compactor_skips_when_disabled(self) -> None:
        """enabled=False 时 compact_if_needed 直接返回，不修改消息。"""
        config = CompactionConfig(enabled=False)
        provider = MockSummaryProvider()
        compactor = ContextCompactor(config=config, summary_provider=provider)

        msgs = _build_conversation(5)
        original_len = len(msgs)
        compactor.update_message_count(len(msgs))

        compactor.compact_if_needed(msgs, {"prompt_tokens": 999999}, turn=5)

        assert len(msgs) == original_len, "disabled 时不应修改消息"
        assert provider.call_count == 0, "disabled 时不应调用 summary provider"


# ── Test 2-3: 阈值触发与未触发 ─────────────────────────


class TestThresholdBehavior:
    """验证 token 估算阈值的触发逻辑。"""

    def test_trigger_above_threshold(self) -> None:
        """estimated tokens > threshold → 触发压缩。"""
        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        msgs = _build_conversation(5)
        compactor = ContextCompactor(config=config, summary_provider=provider)
        compactor.update_message_count(len(msgs))

        # prompt_tokens=950 已接近阈值 900，加上 delta 估算必超
        compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=3)

        assert provider.call_count == 1, "应触发一次摘要调用"
        assert compactor._compaction_count == 1

    def test_skip_below_threshold(self) -> None:
        """estimated tokens < threshold → 不触发。"""
        config = CompactionConfig(
            enabled=True, context_window_tokens=128_000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        msgs = _build_conversation(3)
        compactor = ContextCompactor(config=config, summary_provider=provider)
        compactor.update_message_count(len(msgs))

        compactor.compact_if_needed(msgs, {"prompt_tokens": 1000}, turn=3)

        assert provider.call_count == 0, "低于阈值不应触发"
        assert compactor._compaction_count == 0

    def test_threshold_boundary_exact(self) -> None:
        """精确边界: estimated == threshold - 1 → 不触发。"""
        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        # 只用 system + user, delta 极小
        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content="hi"),
        ]
        compactor = ContextCompactor(config=config, summary_provider=provider)
        compactor.update_message_count(len(msgs))

        # prompt_tokens 刚好不足
        compactor.compact_if_needed(msgs, {"prompt_tokens": 800}, turn=3)
        assert provider.call_count == 0


# ── Test 4: 冷却机制 ─────────────────────────────────────


class TestCooldown:
    """验证连续 turn 冷却: turn <= last_compaction_turn + 1 → 跳过。"""

    def test_skip_consecutive_turn(self) -> None:
        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        msgs = _build_conversation(5)
        compactor = ContextCompactor(config=config, summary_provider=provider)
        compactor.update_message_count(len(msgs))

        # 首次触发 at turn=3
        compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=3)
        assert provider.call_count == 1

        # turn=4 (3+1) 被冷却跳过
        compactor.update_message_count(len(msgs))
        compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=4)
        assert provider.call_count == 1, "冷却期不应再次触发"

    def test_trigger_after_cooldown(self) -> None:
        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        msgs = _build_conversation(8)
        compactor = ContextCompactor(config=config, summary_provider=provider)
        compactor.update_message_count(len(msgs))

        # turn=3 触发
        compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=3)
        assert provider.call_count == 1

        # 重建长对话模拟继续积累
        msgs.extend([
            AssistantMessage(content="more work " + "z" * 500,
                             tool_calls=[ToolCallData(id="tc-extra", name="bash", arguments={})]),
            ToolMessage(content="extra result " + "e" * 500, tool_call_id="tc-extra", tool_name="bash"),
        ])
        compactor.update_message_count(len(msgs))

        # turn=5 (> 3+1) 冷却期结束，可再次触发
        compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=5)
        assert provider.call_count == 2, "冷却期结束后应再次触发"


# ── Test 5: 摘要策略输出结构 ──────────────────────────────


class TestSummaryStrategy:
    """验证 summary 策略的输出消息结构。"""

    def test_output_structure(self) -> None:
        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider(summary="Concise summary of work done.")
        msgs = _build_conversation(10)
        compactor = ContextCompactor(config=config, summary_provider=provider)
        compactor.update_message_count(len(msgs))
        original_len = len(msgs)

        compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=3)

        # 结构: [SystemMessage(原始), SystemMessage([Compacted Context]), UserMessage(task), ...recent]
        assert len(msgs) < original_len, "压缩后消息数应减少"
        assert isinstance(msgs[0], SystemMessage)
        assert msgs[0].content == "You are a helpful assistant."
        assert isinstance(msgs[1], SystemMessage)
        assert "[Compacted Context]" in msgs[1].content
        assert "Concise summary of work done." in msgs[1].content
        assert isinstance(msgs[2], UserMessage)
        assert "Analyze this dataset" in msgs[2].content

    def test_summary_provider_receives_old_messages(self) -> None:
        """摘要 provider 收到的是被压缩的旧消息，不含 recent turns。"""
        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        msgs = _build_conversation(6)
        compactor = ContextCompactor(config=config, summary_provider=provider)
        compactor.update_message_count(len(msgs))

        compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=3)

        assert provider.call_count == 1
        # provider.chat 收到的 messages 中应包含 SUMMARY_SYSTEM_PROMPT
        call_msgs = provider.call_messages[0]
        assert any("summarizer" in str(m.get("content", "")).lower() for m in call_msgs)


# ── Test 6: 滑动窗口回退 ─────────────────────────────────


class TestSlidingWindowFallback:
    """验证 summary 失败时回退到 sliding_window。"""

    def test_fallback_structure(self) -> None:
        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = FailingSummaryProvider()
        msgs = _build_conversation(10)
        original_len = len(msgs)
        compactor = ContextCompactor(config=config, summary_provider=provider)
        compactor.update_message_count(len(msgs))

        compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=3)

        assert len(msgs) < original_len, "回退策略也应减少消息数"
        # 无 [Compacted Context] 消息
        assert isinstance(msgs[0], SystemMessage)
        assert "[Compacted Context]" not in (msgs[0].content or "")
        # msgs[1] 应为 initial task UserMessage
        assert isinstance(msgs[1], UserMessage)
        assert compactor._compaction_count == 1


# ── Test 7: 事件发射 ─────────────────────────────────────


class TestEventEmission:
    """验证 MessageBus 收到 ContextCompactionEvent。"""

    def test_emits_compaction_event(self) -> None:
        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        bus = MessageBus()
        msgs = _build_conversation(5)
        compactor = ContextCompactor(config=config, summary_provider=provider, bus=bus)
        compactor.update_message_count(len(msgs))

        compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=3)

        event = bus.get_nowait()
        assert isinstance(event, ContextCompactionEvent)
        assert event.payload["compaction_count"] == 1
        assert event.payload["strategy"] == "summary"
        assert event.payload["trigger_tokens"] > 0
        assert event.payload["retained_turns"] >= 3

    def test_fallback_event_strategy(self) -> None:
        """回退策略的事件 strategy 字段为 sliding_window。"""
        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = FailingSummaryProvider()
        bus = MessageBus()
        msgs = _build_conversation(5)
        compactor = ContextCompactor(config=config, summary_provider=provider, bus=bus)
        compactor.update_message_count(len(msgs))

        compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=3)

        event = bus.get_nowait()
        assert isinstance(event, ContextCompactionEvent)
        assert event.payload["strategy"] == "sliding_window"

    def test_no_event_without_bus(self) -> None:
        """无 bus 时不抛异常。"""
        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        msgs = _build_conversation(5)
        compactor = ContextCompactor(config=config, summary_provider=provider, bus=None)
        compactor.update_message_count(len(msgs))

        # 应正常执行不抛异常
        compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=3)
        assert compactor._compaction_count == 1


# ── Test 8: 多轮压缩 ─────────────────────────────────────


class TestMultipleCompactions:
    """验证压缩后继续积累可再次触发。"""

    def test_second_compaction(self) -> None:
        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        bus = MessageBus()
        provider = MockSummaryProvider()
        msgs = _build_conversation(8)
        compactor = ContextCompactor(config=config, summary_provider=provider, bus=bus)
        compactor.update_message_count(len(msgs))

        # 第一次压缩 at turn=3
        compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=3)
        assert compactor._compaction_count == 1
        len_after_first = len(msgs)

        # 模拟继续积累新 turns
        for i in range(5):
            msgs.append(
                AssistantMessage(
                    content=f"new-turn-{i} " + "n" * 500,
                    tool_calls=[ToolCallData(id=f"tc-new-{i}", name="bash", arguments={})],
                )
            )
            msgs.append(
                ToolMessage(
                    content="new result " + "q" * 500,
                    tool_call_id=f"tc-new-{i}",
                    tool_name="bash",
                )
            )
        compactor.update_message_count(len(msgs))

        # 第二次压缩 at turn=6 (> 3+1)
        compactor.compact_if_needed(msgs, {"prompt_tokens": 950}, turn=6)
        assert compactor._compaction_count == 2
        assert len(msgs) < len_after_first + 10  # 再次被压缩

        # bus 应有两个事件
        events = []
        while True:
            try:
                events.append(bus.get_nowait())
            except queue.Empty:
                break
        assert len(events) == 2
        assert events[0].payload["compaction_count"] == 1
        assert events[1].payload["compaction_count"] == 2


# ── Test 9: retained turns 选择逻辑 ─────────────────────


class TestRetainedTurnsSelection:
    """验证 _select_recent_turns 的保留逻辑。"""

    def test_minimum_3_turns_retained(self) -> None:
        config = CompactionConfig(
            enabled=True, context_window_tokens=1000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        msgs = _build_conversation(10)
        compactor = ContextCompactor(config=config, summary_provider=provider)

        turns = parse_turns(msgs)
        selected, count = compactor._select_recent_turns(turns)
        assert count >= 3, f"至少保留 3 个 turn，实际保留 {count}"

    def test_small_conversation_retains_all(self) -> None:
        """对话 turn 数 < 3 时全部保留。"""
        config = CompactionConfig(
            enabled=True, context_window_tokens=100000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        msgs = _build_conversation(2)
        compactor = ContextCompactor(config=config, summary_provider=provider)

        turns = parse_turns(msgs)
        selected, count = compactor._select_recent_turns(turns)
        assert count == len(turns)

    def test_budget_constraint(self) -> None:
        """大 content_size 时 budget_40 约束生效。"""
        config = CompactionConfig(
            enabled=True, context_window_tokens=2000, trigger_ratio=0.9
        )
        provider = MockSummaryProvider()
        # 每个 turn 的 content 很大
        msgs = _build_conversation(20, content_size=2000)
        compactor = ContextCompactor(config=config, summary_provider=provider)

        turns = parse_turns(msgs)
        selected, count = compactor._select_recent_turns(turns)
        # 受 budget_40 限制，不可能保留全部 20 个 turn
        assert count < len(turns), f"budget 约束应限制保留数，实际保留 {count}/{len(turns)}"
        assert count >= 3, "最低仍应保留 3 个"


# ── Test 10: Kernel 集成端到端 ───────────────────────────


class TestKernelIntegration:
    """完整 kernel loop 中压缩触发且结果正确。"""

    def test_kernel_triggers_compaction(self) -> None:
        from matmaster.core.agent import AgentKernel
        from matmaster.tools.tool_registry import ToolRegistry

        compaction_cfg = CompactionConfig(
            enabled=True,
            context_window_tokens=500,
            trigger_ratio=0.9,
        )

        call_count = 0
        summary_calls = 0

        class KernelTestProvider:
            def chat(self, messages, tools=None):
                nonlocal summary_calls
                summary_calls += 1
                return LLMResponse(
                    content="Summary of conversation so far.",
                    finish_reason="stop",
                )

            def chat_stream(self, messages, tools=None, *, timeout=None):
                nonlocal call_count
                call_count += 1
                if call_count <= 5:
                    yield StreamChunk(
                        tool_call_deltas=[
                            {
                                "index": 0,
                                "id": f"tc-{call_count}",
                                "name": "tool",
                                "arguments": "{}",
                            }
                        ],
                    )
                    yield StreamChunk(
                        finish_reason="stop",
                        usage={
                            "prompt_tokens": 480,
                            "completion_tokens": 50,
                            "total_tokens": 530,
                        },
                    )
                else:
                    yield StreamChunk(
                        content="final answer",
                        finish_reason="stop",
                        usage={
                            "prompt_tokens": 200,
                            "completion_tokens": 20,
                            "total_tokens": 220,
                        },
                    )

        provider = KernelTestProvider()
        registry = ToolRegistry()

        class DummyTool:
            @property
            def name(self):
                return "tool"

            @property
            def description(self):
                return "test tool"

            @property
            def json_schema(self):
                return {"type": "object", "properties": {}}

            def execute(self, arguments):
                return "result " + "x" * 200

        registry.register(DummyTool(), source="test")

        bus = MessageBus()
        compactor = ContextCompactor(
            config=compaction_cfg,
            summary_provider=provider,
            bus=bus,
        )

        from matmaster.types.runtime import AgentRuntimeSpec

        spec = AgentRuntimeSpec(
            llm_provider=provider,
            tool_registry=registry,
            max_turns=10,
            system_prompt="You are helpful.",
            compaction=compaction_cfg,
            compactor=compactor,
        )

        kernel = AgentKernel()
        result = kernel.run(spec, "do something complex")

        assert result.result.status == "completed"
        assert result.result.reason == "natural"
        assert result.result.final_content == "final answer"
        assert summary_calls > 0, "kernel loop 应触发摘要压缩"
        assert compactor._compaction_count > 0

        # 验证 bus 事件
        events = []
        while True:
            try:
                events.append(bus.get_nowait())
            except queue.Empty:
                break
        compaction_events = [e for e in events if isinstance(e, ContextCompactionEvent)]
        assert len(compaction_events) > 0, "bus 应收到压缩事件"

    def test_kernel_without_compaction(self) -> None:
        """compactor=None 时 kernel 正常运行。"""
        from matmaster.core.agent import AgentKernel
        from matmaster.tools.tool_registry import ToolRegistry
        from matmaster.types.runtime import AgentRuntimeSpec

        call_count = 0

        class SimpleProvider:
            def chat(self, messages, tools=None):
                return LLMResponse(content="done", finish_reason="stop")

            def chat_stream(self, messages, tools=None, *, timeout=None):
                nonlocal call_count
                call_count += 1
                yield StreamChunk(
                    content="done",
                    finish_reason="stop",
                    usage={"prompt_tokens": 100, "completion_tokens": 10},
                )

        spec = AgentRuntimeSpec(
            llm_provider=SimpleProvider(),
            tool_registry=ToolRegistry(),
            max_turns=5,
            system_prompt="test",
        )
        kernel = AgentKernel()
        result = kernel.run(spec, "hello")

        assert result.result.status == "completed"
        assert result.result.reason == "natural"
        assert call_count == 1


# ── Test: token 估算准确性 ───────────────────────────────


class TestTokenEstimation:
    """验证 token 估算的合理性。"""

    def test_short_message(self) -> None:
        msgs = [UserMessage(content="hello")]
        tokens = estimate_tokens(msgs)
        assert 3 <= tokens <= 20, f"短消息 token 估算异常: {tokens}"

    def test_long_message(self) -> None:
        msgs = [UserMessage(content="x" * 10000)]
        tokens = estimate_tokens(msgs)
        assert tokens > 1000, f"长消息 token 估算偏低: {tokens}"

    def test_safety_margin(self) -> None:
        msgs = [UserMessage(content="test message")]
        base = estimate_tokens(msgs, safety_margin=1.0)
        inflated = estimate_tokens(msgs, safety_margin=1.5)
        assert inflated > base, "safety_margin 应放大估算值"
        assert abs(inflated - int(base * 1.5)) <= 1

    def test_tool_message_with_large_content(self) -> None:
        msgs = [ToolMessage(content="y" * 5000, tool_call_id="tc", tool_name="t")]
        tokens = estimate_tokens(msgs)
        assert tokens > 500


# ── Test: parse_turns 边界 ───────────────────────────────


class TestParseTurnsBoundary:
    """parse_turns 的边界条件。"""

    def test_no_assistant_messages(self) -> None:
        msgs = [SystemMessage(content="sys"), UserMessage(content="task")]
        turns = parse_turns(msgs)
        assert len(turns) == 0

    def test_single_complete_turn(self) -> None:
        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content="task"),
            AssistantMessage(content="reply"),
        ]
        turns = parse_turns(msgs)
        assert len(turns) == 1
        assert isinstance(turns[0][0], AssistantMessage)

    def test_multi_tool_single_turn(self) -> None:
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
        assert len(turns[0]) == 3  # Assistant + 2 ToolMessages
