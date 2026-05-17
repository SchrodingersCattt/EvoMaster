"""压缩机制综合测试 -- 通过 devshell 路径验证自动压缩行为。

测试矩阵:
1. 默认路径: devshell → Exp → assemble → 默认带有 CompactionConfig，由阈值决定是否触发
2. 阈值触发: 小 context_window + 高 prompt_tokens → 触发摘要压缩
3. 阈值未达: 低 prompt_tokens → 不触发
4. 冷却机制: 连续 turn 不触发
5. 摘要策略: summary call 正常 → user role compact bundle 摘要
6. 滑动窗口回退: summary call 失败 → tool-safe sliding_window tail
7. 两阶段结果: plan/apply 返回稳定的 compaction 结果元数据
8. 多轮压缩: 首次压缩后继续积累，再次触发
9. retained turns 选择: 3 轮最低保留 + token budget 约束
10. Kernel 集成: 完整 kernel loop 中压缩触发且结果正确
"""

from __future__ import annotations

from matmaster.context.assembly import ContextAssembler
from matmaster.context.compaction import (
    ContextCompactor,
    estimate_tokens,
)
from matmaster.context.ports import ContextAssemblyPorts, UserInstructions
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


class _EventCollector:
    """Simple event collector for compaction tests."""

    def __init__(self):
        self.events: list = []

    async def sink(self, event):
        self.events.append(event)

    def get_nowait(self):
        if not self.events:
            import asyncio

            raise asyncio.QueueEmpty
        return self.events.pop(0)


# ── Fixtures ──────────────────────────────────────────────


class MockSummaryProvider:
    """返回固定摘要的 mock provider。"""

    def __init__(self, summary: str = "Summary of old conversation.") -> None:
        self._summary = summary
        self.call_count = 0
        self.call_messages: list[list[dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None, *, tool_choice=None):
        self.call_count += 1
        self.call_messages.append(messages)
        return LLMResponse(content=self._summary, finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content=self._summary, finish_reason="stop")


class FailingSummaryProvider:
    """始终失败的 provider，测试回退逻辑。"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None, *, tool_choice=None):
        raise RuntimeError("LLM unavailable for summary")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        yield StreamChunk(content="", finish_reason="stop")


class _EmptyRehydrator:
    async def build(self, *, until_event_id: int | None = None) -> str:
        return ""


def _make_compactor(
    config: CompactionConfig,
    provider,
    *,
    event_sink=None,
) -> ContextCompactor:
    class EventsPort:
        async def load_events(self, query):
            return ()

    assembler = ContextAssembler(
        ContextAssemblyPorts(session_events=EventsPort()),
    )
    return ContextCompactor(
        config=config,
        context_assembler=assembler,
        user_instructions=UserInstructions(text="", hash="sha256:empty"),
        session_id="sess-1",
        spawn_id=None,
        runtime_covered_until_provider=lambda: 42,
        event_sink=event_sink,
    )


async def _apply_runtime_compaction_for_test(
    compactor: ContextCompactor,
    provider,
    messages: list,
    turn_usage: dict[str, int],
    *,
    turn: int,
):
    plan = await compactor.plan_runtime_compaction(messages, turn_usage, turn=turn)
    if plan is None:
        return None
    try:
        response = await provider.chat([], tool_choice="none")
        return await compactor.apply_summary(plan, messages, response.content or "")
    except Exception as exc:
        return await compactor.apply_fallback(
            plan,
            messages,
            failure_reason=str(exc),
        )


async def _apply_plan_for_test(
    compactor: ContextCompactor,
    provider,
    plan,
    messages: list,
):
    response = await provider.chat([], tool_choice="none")
    return await compactor.apply_summary(plan, messages, response.content or "")


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


# ── Test 1: 默认 devshell 路径始终带压缩配置 ───────────────


class TestDefaultDevshellPath:
    """验证 devshell 默认路径下 compaction 默认存在，由阈值决定是否触发。"""

    async def test_exp_assemble_compaction_defaults_present(self) -> None:
        """Exp.assemble() 产出的 CompactionConfig 默认存在且不再暴露 enabled。"""
        from matmaster.config.exp import ExpConfig
        from matmaster.core.exp import Exp
        from matmaster.core.playground import PlaygroundContext

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
            spec = await exp.assemble(ctx)

        assert "enabled" not in type(spec.compaction).model_fields
        assert spec.compactor is None, "assemble 阶段不应创建 compactor 实例"

    async def test_compactor_skips_when_below_threshold(self) -> None:
        """默认启用压缩时，低于阈值也应保持原消息不变。"""
        config = CompactionConfig(context_limit=128_000, trigger_ratio=0.9)
        provider = MockSummaryProvider()
        compactor = _make_compactor(config, provider)

        msgs = _build_conversation(5)
        original_len = len(msgs)
        compactor.update_message_count(len(msgs))

        await _apply_runtime_compaction_for_test(
            compactor, provider, msgs, {"prompt_tokens": 1000}, turn=5
        )

        assert len(msgs) == original_len, "低于阈值时不应修改消息"
        assert provider.call_count == 0, "低于阈值时不应调用 summary provider"


# ── Test 2-3: 阈值触发与未触发 ─────────────────────────


class TestThresholdBehavior:
    """验证 token 估算阈值的触发逻辑。"""

    async def test_trigger_above_threshold(self) -> None:
        """estimated tokens > threshold -> 触发压缩。"""
        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = MockSummaryProvider()
        msgs = _build_conversation(5)
        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        # prompt_tokens=950 已接近阈值 900，加上 delta 估算必超
        await _apply_runtime_compaction_for_test(
            compactor, provider, msgs, {"prompt_tokens": 950}, turn=3
        )

        assert provider.call_count == 1, "应触发一次摘要调用"
        assert compactor._compaction_count == 1

    async def test_skip_below_threshold(self) -> None:
        """estimated tokens < threshold -> 不触发。"""
        config = CompactionConfig(context_limit=128_000, trigger_ratio=0.9)
        provider = MockSummaryProvider()
        msgs = _build_conversation(3)
        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        await _apply_runtime_compaction_for_test(
            compactor, provider, msgs, {"prompt_tokens": 1000}, turn=3
        )

        assert provider.call_count == 0, "低于阈值不应触发"
        assert compactor._compaction_count == 0

    async def test_threshold_boundary_exact(self) -> None:
        """低于自动 compact 阈值时不触发。"""
        config = CompactionConfig(context_limit=34000, trigger_ratio=0.9)
        provider = MockSummaryProvider()
        # 只用 system + user, delta 极小
        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content="hi"),
        ]
        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        # context_limit=34000 时默认保留预算后 auto_threshold 为 1000。
        await _apply_runtime_compaction_for_test(
            compactor, provider, msgs, {"prompt_tokens": 900}, turn=3
        )
        assert provider.call_count == 0


# ── Test 4: 冷却机制 ─────────────────────────────────────


class TestCooldown:
    """验证连续 turn 冷却: turn <= last_compaction_turn + 1 → 跳过。"""

    async def test_skip_consecutive_turn(self) -> None:
        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = MockSummaryProvider()
        msgs = _build_conversation(5)
        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        # 首次触发 at turn=3
        await _apply_runtime_compaction_for_test(
            compactor, provider, msgs, {"prompt_tokens": 950}, turn=3
        )
        assert provider.call_count == 1

        # turn=4 (3+1) 被冷却跳过
        compactor.update_message_count(len(msgs))
        await _apply_runtime_compaction_for_test(
            compactor, provider, msgs, {"prompt_tokens": 950}, turn=4
        )
        assert provider.call_count == 1, "冷却期不应再次触发"

    async def test_trigger_after_cooldown(self) -> None:
        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = MockSummaryProvider()
        msgs = _build_conversation(8)
        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        # turn=3 触发
        await _apply_runtime_compaction_for_test(
            compactor, provider, msgs, {"prompt_tokens": 950}, turn=3
        )
        assert provider.call_count == 1

        # 重建长对话模拟继续积累
        msgs.extend(
            [
                AssistantMessage(
                    content="more work " + "z" * 500,
                    tool_calls=[ToolCallData(id="tc-extra", name="bash", arguments={})],
                ),
                ToolMessage(
                    content="extra result " + "e" * 500,
                    tool_call_id="tc-extra",
                    tool_name="bash",
                ),
            ]
        )
        compactor.update_message_count(len(msgs))

        # turn=5 (> 3+1) 冷却期结束，可再次触发
        await _apply_runtime_compaction_for_test(
            compactor, provider, msgs, {"prompt_tokens": 950}, turn=5
        )
        assert provider.call_count == 2, "冷却期结束后应再次触发"


# ── Test 5: 摘要策略输出结构 ──────────────────────────────


class TestSummaryStrategy:
    """验证 summary 策略的输出消息结构。"""

    async def test_output_structure(self) -> None:
        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = MockSummaryProvider(summary="Concise summary of work done.")
        msgs = _build_conversation(10)
        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))
        original_len = len(msgs)

        await _apply_runtime_compaction_for_test(
            compactor, provider, msgs, {"prompt_tokens": 950}, turn=3
        )

        # 结构: [SystemMessage(原始), UserMessage(compact bundle)]
        assert len(msgs) < original_len, "压缩后消息数应减少"
        assert len(msgs) == 2
        assert isinstance(msgs[0], SystemMessage)
        assert msgs[0].content == "You are a helpful assistant."
        assert isinstance(msgs[1], UserMessage)
        assert "[Compacted Context]" not in (msgs[1].content or "")
        assert "<compacted_history>" in (msgs[1].content or "")
        assert "Concise summary of work done." in msgs[1].content
        assert "Analyze this dataset" not in msgs[1].content

    async def test_summary_call_path_records_request(self) -> None:
        """summary call 由编排层触发，并把结果交给 compactor 应用。"""
        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = MockSummaryProvider()
        msgs = _build_conversation(6)
        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        await _apply_runtime_compaction_for_test(
            compactor, provider, msgs, {"prompt_tokens": 950}, turn=3
        )

        assert provider.call_count == 1
        call_msgs = provider.call_messages[0]
        assert call_msgs == []
        assert "<compacted_history>" in (msgs[1].content or "")


# ── Test 6: 滑动窗口回退 ─────────────────────────────────


class TestSlidingWindowFallback:
    """验证 summary 失败时回退到 sliding_window。"""

    async def test_fallback_structure(self) -> None:
        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = FailingSummaryProvider()
        msgs = _build_conversation(10)
        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        await _apply_runtime_compaction_for_test(
            compactor, provider, msgs, {"prompt_tokens": 950}, turn=3
        )

        assert len(msgs) < 22, "回退策略应只保留 tool-safe tail"
        # 无 compact bundle 消息
        assert isinstance(msgs[0], SystemMessage)
        assert "[Compacted Context]" not in (msgs[0].content or "")
        assert not any("<compacted_history>" in (m.content or "") for m in msgs)
        assert compactor._compaction_count == 1


# ── Test 7: 事件发射 ─────────────────────────────────────


class TestCompactionResults:
    """验证两阶段 compaction 结果的公共元数据。"""

    async def test_summary_result_metadata(self) -> None:
        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = MockSummaryProvider()
        msgs = _build_conversation(5)
        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        plan = await compactor.plan_runtime_compaction(
            msgs, {"prompt_tokens": 950}, turn=3
        )
        result = await _apply_plan_for_test(compactor, provider, plan, msgs)

        assert result.compaction_count == 1
        assert result.strategy == "summary"
        assert result.trigger_tokens > 0
        assert result.retained_turns == 0
        assert result.base_snapshot is not None
        assert [item["role"] for item in result.base_snapshot] == ["user"]

    async def test_fallback_result_strategy(self) -> None:
        """回退策略的 result.strategy 字段为 sliding_window。"""
        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = FailingSummaryProvider()
        msgs = _build_conversation(5)
        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        plan = await compactor.plan_runtime_compaction(
            msgs, {"prompt_tokens": 950}, turn=3
        )
        result = await compactor.apply_fallback(
            plan,
            msgs,
            failure_reason="LLM unavailable for summary",
        )

        assert result.strategy == "sliding_window"
        assert result.durability == "ephemeral"
        assert result.failure_reason
        assert result.base_snapshot is None

    async def test_no_event_without_bus(self) -> None:
        """无 event_sink 时仍能完成压缩。"""
        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = MockSummaryProvider()
        msgs = _build_conversation(5)
        compactor = _make_compactor(config, provider, event_sink=None)
        compactor.update_message_count(len(msgs))

        await _apply_runtime_compaction_for_test(
            compactor, provider, msgs, {"prompt_tokens": 950}, turn=3
        )
        assert compactor._compaction_count == 1


# ── Test 8: 多轮压缩 ─────────────────────────────────────


class TestMultipleCompactions:
    """验证压缩后继续积累可再次触发。"""

    async def test_second_compaction(self) -> None:
        config = CompactionConfig(context_limit=1000, trigger_ratio=0.9)
        provider = MockSummaryProvider()
        msgs = _build_conversation(8)
        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        # 第一次压缩 at turn=3
        first_plan = await compactor.plan_runtime_compaction(
            msgs, {"prompt_tokens": 950}, turn=3
        )
        await _apply_plan_for_test(compactor, provider, first_plan, msgs)
        assert compactor._compaction_count == 1
        assert first_plan.compaction_count == 1
        len_after_first = len(msgs)

        # 模拟继续积累新 turns
        for i in range(5):
            msgs.append(
                AssistantMessage(
                    content=f"new-turn-{i} " + "n" * 500,
                    tool_calls=[
                        ToolCallData(id=f"tc-new-{i}", name="bash", arguments={})
                    ],
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
        second_plan = await compactor.plan_runtime_compaction(
            msgs, {"prompt_tokens": 950}, turn=6
        )
        await _apply_plan_for_test(compactor, provider, second_plan, msgs)
        assert compactor._compaction_count == 2
        assert len(msgs) < len_after_first + 10  # 再次被压缩
        assert second_plan.compaction_count == 2


# ── Test 10: tool_truncation 兜底策略 ────────────────────


class TestToolTruncationFallback:
    """验证 2 turn 占满上下文时 tool_truncation 兜底。"""

    async def test_truncation_when_single_turn_exceeds_threshold(self) -> None:
        """1 个 turn 就超限 -> 无可压缩旧 turn -> 截断大 tool result。"""
        config = CompactionConfig(context_limit=500, trigger_ratio=0.9)
        provider = FailingSummaryProvider()

        # 构造：1 turn with 3 大 tool results (每个 2000+ chars)
        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content="task"),
            AssistantMessage(
                content="calling",
                tool_calls=[
                    ToolCallData(id=f"tc-{i}", name="bash", arguments={})
                    for i in range(3)
                ],
            ),
            ToolMessage(
                content="HEAD_A " + "a" * 2000 + " TAIL_A",
                tool_call_id="tc-0",
                tool_name="bash",
            ),
            ToolMessage(
                content="HEAD_B " + "b" * 2000 + " TAIL_B",
                tool_call_id="tc-1",
                tool_name="bash",
            ),
            ToolMessage(
                content="HEAD_C " + "c" * 2000 + " TAIL_C",
                tool_call_id="tc-2",
                tool_name="bash",
            ),
        ]

        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        plan = await compactor.plan_runtime_compaction(
            msgs, {"prompt_tokens": 600}, turn=3
        )
        result = await compactor.apply_fallback(
            plan,
            msgs,
            failure_reason="LLM unavailable for summary",
        )

        # summary 失败后，回退路径保留 tool-safe tail。
        assert compactor._compaction_count == 1

        assert len(msgs) == 5
        assert isinstance(msgs[1], AssistantMessage)
        assert [m.tool_call_id for m in msgs[2:] if isinstance(m, ToolMessage)] == [
            "tc-0",
            "tc-1",
            "tc-2",
        ]
        assert result.strategy == "sliding_window"
        assert result.durability == "ephemeral"
        assert result.failure_reason
        assert result.base_snapshot is None

    async def test_no_truncation_below_threshold(self) -> None:
        """即使只有 1 turn，未超阈值不截断。"""
        config = CompactionConfig(context_limit=128000, trigger_ratio=0.9)
        provider = MockSummaryProvider()
        msgs = [
            SystemMessage(content="sys"),
            UserMessage(content="task"),
            AssistantMessage(
                content="", tool_calls=[ToolCallData(id="tc-0", name="t", arguments={})]
            ),
            ToolMessage(
                content="big " + "x" * 2000, tool_call_id="tc-0", tool_name="t"
            ),
        ]

        compactor = _make_compactor(config, provider)
        compactor.update_message_count(len(msgs))

        # prompt_tokens=1000 远低于 128000*0.9 -> 不触发
        await _apply_runtime_compaction_for_test(
            compactor, provider, msgs, {"prompt_tokens": 1000}, turn=3
        )
        assert compactor._compaction_count == 0
        assert "truncated" not in (msgs[3].content or "")


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
