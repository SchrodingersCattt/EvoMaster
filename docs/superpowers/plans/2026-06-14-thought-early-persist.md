# Thought 提前入库 (选项 B) 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把绝大多数轮的 `thought(complete)` 入库时机，从「整轮 LLM 流结束后」提前到 reasoning 流刚结束的 transition 点，使 thought complete 靠真实 IO 时间差早于同轮 response complete 入库，并在长 content 流式阶段崩溃时保住完整 reasoning。

**Architecture:** 两层正交职责。流式层 `agent_llm_stream.py` 在 reasoning→content / reasoning→tool_call 两个 transition 点，于现有 `segment_end` 旁额外 yield 一条 `stream_state="complete"` 的纯文本 thought（不带 usage），用流内标志 `reasoning_complete_emitted` 保证一次流最多一条；`finally` 绝不发 complete（异常/取消路径不写半截 reasoning）。内核层 `agent.py` 的 `_run_items` 消费循环拦截这些 complete thought，用 per-turn 标志 `thought_persisted_this_turn` 去重（覆盖同轮所有 retry attempt），并在流末对未提前过的轮（纯 reasoning / reasoning_only）用最终 `response.reasoning_content` 兜底补一条。零 schema 改动，下游 PersistenceHandler / SSEHandler / event_payloads / 回放排序全部不动。

**Tech Stack:** Python ≥3.10、async generator、Pydantic 事件模型、pytest + pytest-asyncio，依赖 `uv` 环境执行。

---

## ⚠️ 给执行者：spec 第 8 节迁移清单不完整（动手前必读）

源 spec (`docs/superpowers/specs/2026-06-14-thought-early-persist-design.md`) 第 8 节「必须迁移的旧基线」只列了一个测试 (`test_segment_end_on_reasoning_to_content`)。**这是 spec 的事实性遗漏。**

实际上还有一整个文件 `tests/matmaster/core/test_agent_kernel_usage_events.py` 里的 **4 个测试**把「complete thought 携带 accepted-turn usage、与同轮 tool_call 共享 turn_index」当作明确契约（连 `ThoughtEvent` docstring 都写 “‘complete’ is the accepted-turn reasoning audit event and **may carry usage**”）。本设计 F2 恰恰要移除 complete thought 的 usage，因此这 4 个测试**必然全部变红**，是本计划 Task 1 的核心迁移对象：

- `test_run_stream_emits_usage_bearing_thought_complete` (line 285)
- `test_reasoning_then_tool_call_thought_and_tool_call_share_turn` (line 380)
- `test_retry_discarded_attempt_does_not_emit_usage_thought_complete` (line 442)
- `test_invalid_finish_reasoning_only_still_emits_thought_complete` (line 499)

**这是不是设计要不要做的疑问？不是。** spec 状态为「已批准(选项 B)」，F2 已论证移除依据（`event_payloads` 对无 usage thought 只存纯文本、无消费方读取 DB thought 的 turn_index、该轮 usage 仍由 response/tool_call/assistant_state 承载）。本计划如实执行该决策，并把这 4 个测试迁移为「断言 complete thought 为纯 reasoning、不带 usage」。若后续有人质疑「为什么动了 usage 测试」，依据就在 spec F2 与这一节。

---

## 背景速读（零上下文工程师）

- 这是一个重 IO 的 agent：reasoning 在 LLM 流的前段就已完整，到 content 流完之间常有数秒到数分钟的工具/网络 IO。当前 thought 的可持久化事件被压到整轮流结束后才产生，白白放弃了这段时间差，导致 `evo_chat_events` 偶发乱序（response 早于同轮 thought 入库）。
- 入库顺序由 `(created_at, id)` 决定，`created_at` 是秒精度，同秒退化为只看自增 `id`，而 thought 的 reasoning payload 最大、INSERT 最慢，系统性地排到 response 之后。提前 emit 让 thought 抢在 content 流式期间就落库。
- 改动**只**碰两个文件的逻辑：`matmaster/core/agent_llm_stream.py`（流式层，判定 reasoning 何时完成）和 `matmaster/core/agent.py`（内核层，去重 + 兜底）。其余都是测试迁移与一处 docstring 同步。
- 关键执行链：`src/services/agent_run_service.py` → `matmaster/core/playground.py` → `matmaster/core/exp.py` → `matmaster/core/agent.py`（本次改这里）→ `matmaster/integration/fanout.py`。`AgentKernel.run_stream()` 内部消费 `_run_items()` 产出的 `_KernelItem`，提取 `.event`（即 `ThoughtEvent`/`ResponseEvent` 等 BusEvent）对外 yield。测试既可直接驱动流式层 `stream_llm_items(...)` 拿 `_KernelItem`，也可驱动 `AgentKernel().run_stream(...)` 拿 BusEvent。

## File Structure

| 文件 | 改动类型 | 责任 |
|------|---------|------|
| `matmaster/core/agent_llm_stream.py` | 修改 | 流式层：两个 transition 点额外 emit 纯文本 `complete` thought；新增 `reasoning_complete_emitted` 流内标志；`finally` 保持不发 complete |
| `matmaster/core/agent.py` | 修改 | 内核层：流末 thought complete 先去 usage（Task 1）→ 再改为「消费循环拦截去重 + 未提前过才兜底」（Task 2）|
| `matmaster/types/events.py` | 修改 | 仅 `ThoughtEvent` docstring 与新行为对齐（complete 为纯 reasoning 快照、不带 usage）|
| `tests/matmaster/core/test_agent_kernel_usage_events.py` | 修改 | 迁移 4 个把 complete thought 当 usage 载体的契约测试 |
| `tests/matmaster/core/test_agent_kernel_stream.py` | 修改 + 新增 | 迁移 `test_segment_end_on_reasoning_to_content`；新增流式层 transition/interleaved/F1 用例 |
| `tests/matmaster/integration/test_event_payloads.py` | 新增 | F2：无 usage 的 complete thought 经 `_public_content_for_event` 序列化为纯文本 |

## 关键代码锚点（改动前的真实代码，供 Edit 精确匹配）

**`matmaster/core/agent_llm_stream.py`** —— `stream_llm_items` 局部变量初始化（约 98–104 行）：

```python
    captured_provider_state = None
    producing_reasoning = False
    producing_content = False
    pending_response_parts: list[str] = []
    response_stream_released = False
```

reasoning→content transition（约 149–157 行）：

```python
            if chunk.content:
                # Segment transition: reasoning -> content
                if producing_reasoning:
                    yield _thought_item(
                        "".join(reasoning_parts), stream_id, "segment_end"
                    )
                    producing_reasoning = False
                content_parts.append(chunk.content)
                producing_content = True
```

reasoning→tool_call transition（约 167–173 行）：

```python
            if chunk.tool_call_deltas:
                # Segment transition: reasoning -> tool_calls
                if producing_reasoning:
                    yield _thought_item(
                        "".join(reasoning_parts), stream_id, "segment_end"
                    )
                    producing_reasoning = False
```

辅助构造器 `_thought_item`（50–61 行，**不要改**，复用即可——它本就不带 usage/turn_index）：

```python
def _thought_item(
    reasoning: str, stream_id: str, stream_state: str | None
) -> _KernelItem:
    return _KernelItem(
        event=ThoughtEvent(
            source="agent",
            content=reasoning,
            stream_state=stream_state,
            stream_id=stream_id,
            reasoning_content=reasoning or None,
        )
    )
```

**`matmaster/core/agent.py`** —— `_run_items` 消费循环（339–353 行）：

```python
            llm_response: LLMResponse | None = None
            try:
                async for item in self._call_llm_streaming(
                    kernel_resources,
                    canonical_messages,
                    tool_defs,
                    cancel_token=cancel_token,
                ):
                    if item.llm_response is not None:
                        llm_response = item.llm_response
                    elif item.event is not None:
                        yield self._with_model_identity(item, state)
            except _KernelStopRequested:
                yield self._terminal(state, "cancelled")
                return
```

流末 thought complete 产生处（373–385 行，唯一产生点）：

```python
            if response.reasoning_content:
                yield _KernelItem(
                    event=ThoughtEvent(
                        source="agent",
                        content=response.reasoning_content,
                        stream_state="complete",
                        reasoning_content=response.reasoning_content,
                        turn_index=turn_index,
                        turn_usage=turn_usage_snapshot,
                        total_usage=total_usage_snapshot,
                        usage_vendor=usage_vendor_snapshot,
                    )
                )
```

> 注：`turn_index` / `turn_usage_snapshot` / `total_usage_snapshot` / `usage_vendor_snapshot` 在 369–372 行计算，后续 response complete (399–401) 与 tool_call (494–496) 仍在用，**不要删除这些变量**，只是 thought 这段不再引用它们。

**`matmaster/types/events.py`** —— `ThoughtEvent` docstring（46–53 行）：

```python
class ThoughtEvent(TurnUsageCarrierEvent):
    """LLM thought/reasoning event.

    Streaming and non-streaming are unified; use ``stream_state`` to
    distinguish: 'start' | 'streaming' | 'segment_end' | 'end' | 'complete' | None.
    'complete' is the accepted-turn reasoning audit event and may carry usage;
    all other states are ephemeral streaming/segment markers without usage.
    """
```

---

## Task 1: complete thought 去除 usage 载荷（内核流末，迁移 usage 契约测试）

**目的：** 先把 complete thought 从「accepted-turn usage 审计事件」降级为「纯 reasoning 快照」，不改变产生时机（仍在流末）。这一步独立可提交，把 4 个 usage 契约测试迁移到新语义，为 Task 2 的「提前」铺路。

**Files:**
- Modify: `matmaster/core/agent.py:373-385`
- Modify: `matmaster/types/events.py:46-53`
- Test: `tests/matmaster/core/test_agent_kernel_usage_events.py`（迁移 4 个测试）

### TDD：先把 4 个 usage 测试改到新语义（红），再改实现（绿）

- [ ] **Step 1: 迁移 `test_run_stream_emits_usage_bearing_thought_complete`**

打开 `tests/matmaster/core/test_agent_kernel_usage_events.py`，定位 line 285 起的测试，整体替换为（改名 + 断言无 usage/turn_index，content 断言保留）：

```python
@pytest.mark.asyncio
async def test_run_stream_thought_complete_is_plain_reasoning() -> None:
    from matmaster.core.agent import AgentKernel

    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(provider=ReasoningThenContentProvider()),
        make_kernel_turn("test task"),
    ):
        events.append(event)

    completes = [
        e
        for e in events
        if isinstance(e, ThoughtEvent) and e.stream_state == "complete"
    ]
    assert len(completes) == 1
    assert completes[0].content == "thinking part 1 part 2"
    assert completes[0].reasoning_content == "thinking part 1 part 2"
    # complete thought 现为纯 reasoning 快照, 不再携带 accepted-turn usage/turn_index
    assert completes[0].turn_index is None
    assert completes[0].turn_usage == {}
    assert completes[0].total_usage == {}
    assert completes[0].usage_vendor is None

    segment_ends = [
        e
        for e in events
        if isinstance(e, ThoughtEvent) and e.stream_state == "segment_end"
    ]
    assert segment_ends
    for ev in segment_ends:
        assert ev.turn_usage == {}
        assert ev.total_usage == {}
```

- [ ] **Step 2: 迁移 `test_reasoning_then_tool_call_thought_and_tool_call_share_turn`**

定位 line 380 起的测试，**保留其内部 `EchoRunner` / `ReasoningToolProvider` 定义与驱动代码不变**，只把末尾断言块（line 427–438）替换为：

```python
    thought_completes = [
        e
        for e in events
        if isinstance(e, ThoughtEvent) and e.stream_state == "complete"
    ]
    tool_call_events = [e for e in events if isinstance(e, ToolCallEvent)]
    assert len(thought_completes) == 1
    assert thought_completes[0].content == "plan the call"
    # thought complete 不再携带 usage/turn_index; 该轮 usage 改由同轮 tool_call 承载
    assert thought_completes[0].turn_usage == {}
    assert thought_completes[0].turn_index is None
    assert len(tool_call_events) == 1
    assert tool_call_events[0].turn_index == 0
    assert tool_call_events[0].turn_usage == {"prompt_tokens": 8}
```

并把函数名 `test_reasoning_then_tool_call_thought_and_tool_call_share_turn` 改为 `test_reasoning_then_tool_call_usage_lives_on_tool_call`（语义不再是「共享 turn」）。

- [ ] **Step 3: 迁移 `test_retry_discarded_attempt_does_not_emit_usage_thought_complete`**

定位 line 442 起的测试，**保留 `ReasoningRetryProvider` 定义与驱动不变**，只改末尾断言（line 485–495）中的 usage 行——把 `completes[0].turn_usage == {"prompt_tokens": 10}` 改为 `== {}`：

```python
    assert provider.call_count == 2
    completes = [
        e
        for e in events
        if isinstance(e, ThoughtEvent) and e.stream_state == "complete"
    ]
    # F3 零污染: incomplete(reasoning_only) retry 后, 入库的是被采纳 attempt 的 reasoning
    assert [e.content for e in completes] == ["kept reasoning"]
    assert completes[0].turn_usage == {}
    for ev in events:
        if isinstance(ev, ThoughtEvent) and ev.stream_state != "complete":
            assert ev.turn_usage == {}
```

把函数名改为 `test_retry_discarded_attempt_thought_complete_is_accepted_reasoning`。

- [ ] **Step 4: 迁移 `test_invalid_finish_reasoning_only_still_emits_thought_complete`**

定位 line 499 起的测试，**保留 `ReasoningOnlyProvider` 定义与驱动不变**，只改末尾断言（line 536–548）中的 thought usage 行——把 `completes[0].turn_usage == {"prompt_tokens": 4}` 改为 `== {}`；`run_result.usage` 断言保留（usage 仍在 run_result/terminal）：

```python
    assert provider.call_count == 2
    completes = [
        e
        for e in events
        if isinstance(e, ThoughtEvent) and e.stream_state == "complete"
    ]
    assert [e.content for e in completes] == ["attempt 2"]
    # reasoning_only 轮的 usage 不在 thought 上; 仍由 run_result/terminal 承载
    assert completes[0].turn_usage == {}
    run_result = next(e for e in events if isinstance(e, RunResultEvent))
    assert run_result.status == "failed"
    assert run_result.reason == "invalid_finish"
    assert run_result.usage == {"prompt_tokens": 4}
    assert not [e for e in events if isinstance(e, ToolCallEvent)]
```

函数名保留（reasoning_only 仍 emit thought complete，语义仍准确）。

- [ ] **Step 5: 跑这 4 个测试，确认变红**

Run:
```bash
uv run --extra dev pytest tests/matmaster/core/test_agent_kernel_usage_events.py -v -k "plain_reasoning or usage_lives_on_tool_call or accepted_reasoning or reasoning_only_still_emits"
```
Expected: 4 FAILED —— 失败处都在新加的 `turn_usage == {}` / `turn_index is None` 断言（当前实现仍带 usage）。

- [ ] **Step 6: 改实现——流末 thought complete 去掉 usage 字段**

在 `matmaster/core/agent.py`，把 373–385 行整段替换为：

```python
            if response.reasoning_content:
                yield _KernelItem(
                    event=ThoughtEvent(
                        source="agent",
                        content=response.reasoning_content,
                        stream_state="complete",
                        reasoning_content=response.reasoning_content,
                    )
                )
```

（删除 `turn_index` / `turn_usage_snapshot` / `total_usage_snapshot` / `usage_vendor_snapshot` 四个入参；这些变量本身不删，后续 response/tool_call 仍用。）

- [ ] **Step 7: 同步 `ThoughtEvent` docstring**

在 `matmaster/types/events.py`，把 46–53 行的 docstring 替换为：

```python
class ThoughtEvent(TurnUsageCarrierEvent):
    """LLM thought/reasoning event.

    Streaming and non-streaming are unified; use ``stream_state`` to
    distinguish: 'start' | 'streaming' | 'segment_end' | 'end' | 'complete' | None.
    'complete' is the persisted reasoning snapshot (plain text, no usage); the
    accepted-turn usage lives on the sibling response / tool_call /
    assistant_state events. All other states are ephemeral streaming markers.
    """
```

- [ ] **Step 8: 跑测试，确认 4 个转绿，且未波及其它**

Run:
```bash
uv run --extra dev pytest tests/matmaster/core/test_agent_kernel_usage_events.py tests/matmaster/core/test_agent_kernel_stream.py -q
```
Expected: 全部 PASS。`test_segment_end_on_reasoning_to_content`（仍断言流式层 `thought_completes == []`）此刻仍 PASS——本任务没碰流式层。`test_llm_output_events_scope_model_identity` 仍 PASS（complete thought 仍产生，且本就不带 model identity）。

- [ ] **Step 9: import 与 lint**

Run:
```bash
uv run python -c "import matmaster.core.agent, matmaster.types.events"
uv run ruff check matmaster/core/agent.py matmaster/types/events.py
```
Expected: 无 import 错误；ruff 无新增告警。

- [ ] **Step 10: 提交**

```bash
git add matmaster/core/agent.py matmaster/types/events.py tests/matmaster/core/test_agent_kernel_usage_events.py
git commit -m "refactor: drop usage payload from thought complete event"
```

---

## Task 2: 流式层 transition 提前 emit + 内核去重拦截（实现「提前入库」）

**目的：** 让 thought complete 在 reasoning→content / reasoning→tool_call transition 点就 emit（content 流式期间即落库），内核按 per-turn 去重并对未提前过的轮兜底。这是 spec 的核心目标。流式层与内核层的改动**必须同批提交**——只改流式层会让内核产生双 complete，只改内核拦截没有上游 complete 可拦截。

**Files:**
- Modify: `matmaster/core/agent_llm_stream.py`（初始化 + 两个 transition 点）
- Modify: `matmaster/core/agent.py`（消费循环拦截 + 流末兜底改条件）
- Test: `tests/matmaster/core/test_agent_kernel_stream.py`（迁移 1 个 + 新增 6 个）

### TDD：先迁移/新增能区分「提前 vs 流末」的测试（红），再改实现（绿）

- [ ] **Step 1: 迁移 `test_segment_end_on_reasoning_to_content`（流式层基线）**

在 `tests/matmaster/core/test_agent_kernel_stream.py`，把 line 317–319 的三行断言：

```python
        assert thought_completes == []
        assert len(thought_segment_ends) >= 1
        assert "thinking part 1" in thought_segment_ends[0].event.content
```

替换为：

```python
        # transition 点现在在 segment_end 旁额外提前 emit 恰好一条 complete
        assert len(thought_completes) == 1
        assert thought_completes[0].event.content == "thinking part 1 part 2"
        assert thought_completes[0].event.reasoning_content == "thinking part 1 part 2"
        assert len(thought_segment_ends) >= 1
        assert "thinking part 1" in thought_segment_ends[0].event.content
```

- [ ] **Step 2: 新增流式层「reasoning→tool_call 提前」用例**

在 `TestStreamLlmItems` 类内（紧随 `test_segment_end_on_reasoning_to_content` 之后）新增。它用 `StreamingProvider`（文件已 import），断言 complete thought 在流末 `llm_response` item 之前 yield：

```python
    @pytest.mark.asyncio
    async def test_reasoning_to_toolcall_emits_complete_before_final(self) -> None:
        """reasoning->tool_call transition 提前 emit complete, 早于流末 llm_response。"""
        from matmaster.core.kernel_items import _KernelItem
        from matmaster.types.messages import StreamChunk

        provider = StreamingProvider(
            [
                StreamChunk(reasoning_content="plan the call"),
                StreamChunk(
                    tool_call_deltas=[
                        {"index": 0, "id": "c1", "name": "bash", "arguments": "{}"}
                    ]
                ),
                StreamChunk(finish_reason="stop", usage={"prompt_tokens": 8}),
            ]
        )
        kernel_runtime = make_kernel_runtime(provider=provider)

        items: list[_KernelItem] = []
        async for item in stream_llm_items(
            kernel_runtime.resources, [{"role": "user", "content": "test"}], None
        ):
            items.append(item)

        complete_i = next(
            i
            for i, it in enumerate(items)
            if it.event
            and isinstance(it.event, ThoughtEvent)
            and it.event.stream_state == "complete"
        )
        final_i = next(i for i, it in enumerate(items) if it.llm_response is not None)
        assert complete_i < final_i
        assert items[complete_i].event.content == "plan the call"
```

- [ ] **Step 3: 新增流式层「interleaved reasoning 只一条 complete」用例**

验证 `reasoning_complete_emitted` 标志：reasoning→content→reasoning 序列里，content 后再次出现 reasoning 会让 `producing_reasoning` 复真，但标志阻止第二次 emit。仍在 `TestStreamLlmItems` 类内新增：

```python
    @pytest.mark.asyncio
    async def test_interleaved_reasoning_emits_single_complete(self) -> None:
        """reasoning -> content -> reasoning: 流式层最多 emit 一条 complete。"""
        from matmaster.core.kernel_items import _KernelItem
        from matmaster.types.messages import StreamChunk

        provider = StreamingProvider(
            [
                StreamChunk(reasoning_content="first reasoning"),
                StreamChunk(content="visible answer"),
                StreamChunk(reasoning_content="second reasoning"),
                StreamChunk(finish_reason="stop", usage={"prompt_tokens": 5}),
            ]
        )
        kernel_runtime = make_kernel_runtime(provider=provider)

        items: list[_KernelItem] = []
        async for item in stream_llm_items(
            kernel_runtime.resources, [{"role": "user", "content": "test"}], None
        ):
            items.append(item)

        thought_completes = [
            i
            for i in items
            if i.event
            and isinstance(i.event, ThoughtEvent)
            and i.event.stream_state == "complete"
        ]
        # 仅首段 reasoning 在第一个 transition 提前; content 后的 second reasoning 不再触发 emit
        assert len(thought_completes) == 1
        assert thought_completes[0].event.content == "first reasoning"
```

- [ ] **Step 4: 新增流式层 F1「异常中途退出不发 complete」用例**

`finally` 在异常路径会补 `segment_end`，但绝不发 `complete`。仍在 `TestStreamLlmItems` 类内新增：

```python
    @pytest.mark.asyncio
    async def test_finally_does_not_emit_complete_on_midstream_error(self) -> None:
        """F1: reasoning 中途 LLMError, finally 不持久化半截 complete。"""
        from matmaster.core.kernel_items import _KernelItem
        from matmaster.types.errors import LLMError
        from matmaster.types.messages import StreamChunk

        class ReasoningThenErrorProvider(ProviderProtocolAttrs):
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def chat(self, messages, tools=None):
                return LLMResponse(content="not used", finish_reason="stop")

            async def chat_stream(self, messages, tools=None, *, timeout=None):
                yield StreamChunk(reasoning_content="half reasoning")
                raise LLMError("mid-stream failure", retryable=True)

        provider = ReasoningThenErrorProvider()
        kernel_runtime = make_kernel_runtime(provider=provider)

        items: list[_KernelItem] = []
        with pytest.raises(LLMError):
            async for item in stream_llm_items(
                kernel_runtime.resources, [{"role": "user", "content": "test"}], None
            ):
                items.append(item)

        thought_completes = [
            i
            for i in items
            if i.event
            and isinstance(i.event, ThoughtEvent)
            and i.event.stream_state == "complete"
        ]
        assert thought_completes == []
```

- [ ] **Step 5: 新增内核层「reasoning→content 提前且早于 response complete」顺序用例**

这是能区分「提前 vs 流末」的核心断言：transition 提前时，complete thought 落在两条 response streaming 增量**之间**（早于最后一条 streaming）；若压到流末则会晚于所有 streaming。新增一个**模块级** async 测试函数（放在 `test_agent_kernel_stream.py` 文件末尾即可，与现有 `class` 同级风格不冲突——文件内既有 class 内方法也可直接加模块级 `@pytest.mark.asyncio` 函数；若偏好归类，放入新 class `TestThoughtEarlyPersist`）：

```python
@pytest.mark.asyncio
async def test_thought_complete_emitted_during_content_stream_before_response() -> None:
    """提前入库核心: thought complete 在 content 流式增量之间 emit, 早于 response complete。"""
    from matmaster.core.agent import AgentKernel

    from .agent_kernel_test_helpers import make_kernel_turn as _turn

    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(provider=ReasoningThenContentProvider()),
        _turn("test task"),
    ):
        events.append(event)

    thought_complete_i = next(
        i
        for i, e in enumerate(events)
        if isinstance(e, ThoughtEvent) and e.stream_state == "complete"
    )
    response_complete_i = next(
        i
        for i, e in enumerate(events)
        if isinstance(e, ResponseEvent) and e.stream_state == "complete"
    )
    last_response_streaming_i = max(
        i
        for i, e in enumerate(events)
        if isinstance(e, ResponseEvent) and e.stream_state == "streaming"
    )

    # thought complete 早于 response complete
    assert thought_complete_i < response_complete_i
    # 且早于最后一条 response streaming 增量 -> 证明在 content 流式中途提前 emit, 非压到流末
    assert thought_complete_i < last_response_streaming_i
```

> `turn` 在本文件顶部已 `from .agent_kernel_test_helpers import make_kernel_turn as turn` 导入（line 25）；上面为放在模块级函数内显式再取一次别名以求自包含，执行者也可直接复用文件级的 `turn`。

- [ ] **Step 6: 新增内核层 F1「reasoning 阶段取消不入库 complete」用例**

仍放文件末尾（或 `TestThoughtEarlyPersist` 内）：

```python
@pytest.mark.asyncio
async def test_cancel_during_reasoning_does_not_persist_complete() -> None:
    """F1: 取消路径不持久化 complete thought (与异常路径共享 finally, 不发 complete)。"""
    from matmaster.core.agent import AgentKernel
    from matmaster.types.cancellation import CancellationController
    from matmaster.types.events import RunResultEvent

    from .agent_kernel_test_helpers import make_kernel_turn as _turn

    kernel_runtime = make_kernel_runtime(provider=ReasoningThenContentProvider())
    ctrl = CancellationController()
    ctrl.cancel()  # 预先取消: 内核在轮入口即走 cancelled terminal, 不产生任何 complete

    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        kernel_runtime, _turn("test task"), cancel_token=ctrl.token
    ):
        events.append(event)

    assert isinstance(events[-1], RunResultEvent)
    assert events[-1].status == "cancelled"
    thought_completes = [
        e
        for e in events
        if isinstance(e, ThoughtEvent) and e.stream_state == "complete"
    ]
    assert thought_completes == []
```

- [ ] **Step 7: 新增内核层 F3「LLMError 重试入库首个 attempt reasoning」用例**

明确「已接受的残留行为」：content 流中途断连重试时，提前入库的是首个 attempt 的 reasoning（per-turn 标志使后续 attempt 的 complete 被丢弃），与最终采纳 attempt 可能不一致。仍放文件末尾：

```python
@pytest.mark.asyncio
async def test_llm_error_retry_persists_first_attempt_reasoning() -> None:
    """F3 已知 trade-off: LLMError 在 content 流中途重试时, 提前入库首个 attempt 的 reasoning。"""
    from matmaster.core.agent import AgentKernel
    from matmaster.types.errors import LLMError
    from matmaster.types.messages import StreamChunk

    from .agent_kernel_test_helpers import make_kernel_turn as _turn

    class ContentMidStreamErrorProvider(ProviderProtocolAttrs):
        stream_timeout = 10.0
        max_retries = 2
        retry_delay = 0.0

        def __init__(self) -> None:
            self.call_count = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def chat(self, messages, tools=None):
            return LLMResponse(content="not used", finish_reason="stop")

        async def chat_stream(self, messages, tools=None, *, timeout=None):
            self.call_count += 1
            if self.call_count == 1:
                yield StreamChunk(reasoning_content="first attempt reasoning")
                yield StreamChunk(content="partial ")
                raise LLMError("connection dropped mid-content", retryable=True)
            else:
                yield StreamChunk(reasoning_content="second attempt reasoning")
                yield StreamChunk(content="final answer")
                yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 7})

    provider = ContentMidStreamErrorProvider()
    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(provider=provider), _turn("test task")
    ):
        events.append(event)

    assert provider.call_count == 2
    completes = [
        e
        for e in events
        if isinstance(e, ThoughtEvent) and e.stream_state == "complete"
    ]
    # 残留行为: 入库首个 attempt 的 reasoning, 非最终采纳的 "second attempt reasoning"。
    # 若后续引入 DB 覆盖/作废机制, 需重审此断言。
    assert [e.content for e in completes] == ["first attempt reasoning"]
```

- [ ] **Step 8: 跑新增/迁移用例，确认变红**

Run:
```bash
uv run --extra dev pytest tests/matmaster/core/test_agent_kernel_stream.py -v -k "segment_end_on_reasoning_to_content or toolcall_emits_complete_before_final or interleaved_reasoning or finally_does_not_emit_complete or during_content_stream_before_response or cancel_during_reasoning or first_attempt_reasoning"
```
Expected: 多个 FAILED——流式层尚未 emit complete（`next(...)` 抛 `StopIteration`、`thought_completes == []` 等），顺序断言不成立。`test_cancel_during_reasoning_does_not_persist_complete` 可能此刻已 PASS（取消路径本就无 complete），无妨。

- [ ] **Step 9: 改流式层——初始化标志**

在 `matmaster/core/agent_llm_stream.py`，把 `producing_reasoning = False` 那行（约 100 行）改为两行：

```python
    producing_reasoning = False
    reasoning_complete_emitted = False
```

- [ ] **Step 10: 改流式层——reasoning→content transition 提前 emit**

把约 149–157 行的块替换为（在现有 `segment_end` 后、`producing_reasoning = False` 前插入 complete emit）：

```python
            if chunk.content:
                # Segment transition: reasoning -> content
                if producing_reasoning:
                    yield _thought_item(
                        "".join(reasoning_parts), stream_id, "segment_end"
                    )
                    if not reasoning_complete_emitted:
                        reasoning_complete_emitted = True
                        yield _thought_item(
                            "".join(reasoning_parts), stream_id, "complete"
                        )
                    producing_reasoning = False
                content_parts.append(chunk.content)
                producing_content = True
```

- [ ] **Step 11: 改流式层——reasoning→tool_call transition 提前 emit**

把约 167–173 行的块替换为（同样在 `segment_end` 后插入 complete emit）：

```python
            if chunk.tool_call_deltas:
                # Segment transition: reasoning -> tool_calls
                if producing_reasoning:
                    yield _thought_item(
                        "".join(reasoning_parts), stream_id, "segment_end"
                    )
                    if not reasoning_complete_emitted:
                        reasoning_complete_emitted = True
                        yield _thought_item(
                            "".join(reasoning_parts), stream_id, "complete"
                        )
                    producing_reasoning = False
```

> 这两处 emit 块字面相同（4 行），与文件里两处 `segment_end` 本就字面重复的现状一致，按现有风格保持内联，不抽公共 helper（抽取会扩大改动面且无收益）。`finally`（约 206–222 行）**不动**——它只补 `segment_end` / `end`，绝不发 complete，这是 F1 的关键。

- [ ] **Step 12: 改内核层——消费循环拦截 + per-turn 去重**

在 `matmaster/core/agent.py`，把 339–353 行的消费循环替换为：

```python
            llm_response: LLMResponse | None = None
            thought_persisted_this_turn = False
            try:
                async for item in self._call_llm_streaming(
                    kernel_resources,
                    canonical_messages,
                    tool_defs,
                    cancel_token=cancel_token,
                ):
                    if item.llm_response is not None:
                        llm_response = item.llm_response
                    elif (
                        isinstance(item.event, ThoughtEvent)
                        and item.event.stream_state == "complete"
                    ):
                        # 流式层在 transition 点提前 emit 的 complete thought:
                        # 每轮只放行第一条 (覆盖该轮所有 retry attempt 的重复)
                        if not thought_persisted_this_turn:
                            thought_persisted_this_turn = True
                            yield item
                        # else: 同轮 retry 重复, 丢弃
                    elif item.event is not None:
                        yield self._with_model_identity(item, state)
            except _KernelStopRequested:
                yield self._terminal(state, "cancelled")
                return
```

- [ ] **Step 13: 改内核层——流末兜底改为有条件**

把 Task 1 Step 6 改后的流末 thought complete 块（`if response.reasoning_content:` 整段）替换为：

```python
            if not thought_persisted_this_turn and response.reasoning_content:
                # 流末兜底: 该轮未在 transition 点提前 emit 过 (纯 reasoning /
                # reasoning_only 轮) 时, 用最终采纳 attempt 的 reasoning 补一条
                yield _KernelItem(
                    event=ThoughtEvent(
                        source="agent",
                        content=response.reasoning_content,
                        stream_state="complete",
                        reasoning_content=response.reasoning_content,
                    )
                )
```

- [ ] **Step 14: 跑全部相关用例，确认转绿**

Run:
```bash
uv run --extra dev pytest tests/matmaster/core/test_agent_kernel_stream.py tests/matmaster/core/test_agent_kernel_usage_events.py -q
```
Expected: 全部 PASS。重点确认：
- Task 1 迁移的 4 个 usage 测试**仍 PASS**（content 不变、仍不带 usage——reasoning→content/tool_call 提前 emit 的 content 与 `response.reasoning_content` 对这些 provider 相等；reasoning_only 轮走兜底）。
- `test_llm_output_events_scope_model_identity` **仍 PASS**（complete thought 现来自流式层，恰好一条、无 model identity）。

- [ ] **Step 15: import 与 lint**

Run:
```bash
uv run python -c "import matmaster.core.agent, matmaster.core.agent_llm_stream"
uv run ruff check matmaster/core/agent.py matmaster/core/agent_llm_stream.py
```
Expected: 无错误、无新增告警。

- [ ] **Step 16: 提交**

```bash
git add matmaster/core/agent_llm_stream.py matmaster/core/agent.py tests/matmaster/core/test_agent_kernel_stream.py
git commit -m "feat: persist thought complete early at reasoning transition"
```

---

## Task 3: F2 持久化 payload 用例（纯文本、无 turn_index 结构）

**目的：** 锁定 F2 不变量——无 usage 的 complete thought 经 `_public_content_for_event` 序列化为纯字符串 content，不进 DB 任何 `turn_index` 结构。这是 spec 第 8 节列出的 F2 验证点，独立于内核行为，单测 `event_payloads` 适配层即可。

**Files:**
- Test: `tests/matmaster/integration/test_event_payloads.py`（新增 1 个）

- [ ] **Step 1: 新增 payload 用例**

在 `tests/matmaster/integration/test_event_payloads.py` 文件末尾追加（依据：`event_payloads.py:241-247`——thought 事件当 `turn_usage` 与 `total_usage` 都为空时直接返回 `content` 字符串）：

```python
def test_thought_complete_without_usage_serializes_to_plain_text() -> None:
    """F2: 无 usage 的 complete thought 公开 payload 为纯文本, 不含 turn_index 结构。"""
    from matmaster.integration.event_payloads import _public_content_for_event

    payload = {
        "type": "thought",
        "content": "some reasoning",
        "stream_state": "complete",
        "reasoning_content": "some reasoning",
        "turn_index": None,
        "turn_usage": {},
        "total_usage": {},
    }

    content = _public_content_for_event("thought", payload)

    assert content == "some reasoning"
    assert not isinstance(content, dict)
```

- [ ] **Step 2: 跑该用例，确认 PASS**

Run:
```bash
uv run --extra dev pytest tests/matmaster/integration/test_event_payloads.py::test_thought_complete_without_usage_serializes_to_plain_text -v
```
Expected: PASS（此用例不依赖前两个 Task 的代码改动，直接验证既有 `event_payloads` 行为对新 payload 形态成立——是对设计假设的固化）。

- [ ] **Step 3: 提交**

```bash
git add tests/matmaster/integration/test_event_payloads.py
git commit -m "test: lock plain-text payload for usage-free thought complete"
```

---

## Task 4: 全量验证与回归确认

**目的：** 确认改动未波及下游 complete thought 消费方（SSE 模式过滤、devshell 快照、history 恢复）。spec 声明 PersistenceHandler / SSEHandler / 回放排序不动；这一步用回归测试兜住「声明」与「现实」的差。

**Files:** 无改动，仅运行。

- [ ] **Step 1: 核心内核 + 流式回归**

Run:
```bash
uv run --extra dev pytest tests/matmaster/core/ -q
```
Expected: 全部 PASS。

- [ ] **Step 2: 下游 complete thought 消费方回归**

Run:
```bash
uv run --extra dev pytest \
  tests/matmaster/integration/test_event_payloads.py \
  tests/matmaster/integration/test_sse_handler_mode_filter.py \
  tests/matmaster/integration/test_events_to_messages.py \
  tests/matmaster/devshell/test_event_logger.py -q
```
Expected: 全部 PASS。若 `test_complete_thought_filtered_for_supported_modes`（SSE live skip）或 `test_thought_complete_snapshot`（devshell）失败，说明这些消费方隐式依赖了 complete thought 的 usage 字段——回到对应测试核对：本设计只移除 usage、不改 content 与过滤路径，正常不应失败；若失败需把该测试纳入迁移并在此记录。

- [ ] **Step 3: 最终 import + lint 总检**

Run:
```bash
uv run python -c "import matmaster.core.agent, matmaster.core.agent_llm_stream, matmaster.types.events"
uv run ruff check matmaster/core/agent.py matmaster/core/agent_llm_stream.py matmaster/types/events.py
```
Expected: 全部通过。

---

## Self-Review（已对 spec 逐节核对）

**Spec 覆盖：**

| Spec 条目 | 对应 Task/Step |
|-----------|----------------|
| §4.1 流式层 transition 提前 emit + `reasoning_complete_emitted` | Task 2 Step 9–11 |
| §4.1 `finally` 不发 complete | Task 2 Step 11 备注 + Step 4 (F1 用例) |
| §4.1 内核 per-turn 去重 + 流末兜底 | Task 2 Step 12–13 |
| §4.1 删除原 agent.py:373 无条件产生 | Task 1 Step 6 (去 usage) → Task 2 Step 13 (改条件) |
| §4.2 reasoning→content / reasoning→tool_call / 纯 reasoning 三路径 | Task 2 Step 2/5（前两路径）+ Task 1 迁移的 reasoning_only 用例（兜底路径）|
| §4.3 字段统一不带 usage/turn_index | Task 1（流末）+ Task 2（流式层复用 `_thought_item` 本就不带）|
| §5 F3 incomplete/reasoning_only retry 零污染 | Task 1 Step 3 (`kept reasoning`) |
| §5 F3 LLMError retry 残留 = 已知接受 | Task 2 Step 7 |
| §5 两层去重正交 | Task 2 Step 3（流式层标志）+ Step 12（内核标志）|
| §6 / F5 下游不变量不动 | Task 4 Step 2 回归 |
| §8 必须迁移旧基线（含 spec 遗漏的 usage_events 4 个）| Task 1 Step 1–4 + Task 2 Step 1 |
| §8 F1 / F2 / 顺序 / interleaved 用例 | Task 2 Step 3–7 + Task 3 |
| §9 验证命令（`uv run`）| 各 Task 末 + Task 4 |
| §10 F1–F6 修订 | 全部落到上述 Task |

**Placeholder 扫描：** 无 TODO/TBD；每个代码 step 给出完整可粘贴代码与精确 old/new 锚点。

**类型/命名一致性：** `thought_persisted_this_turn`（内核，per-turn）与 `reasoning_complete_emitted`（流式层，per-stream）两个标志全程同名；`_thought_item(reasoning, stream_id, stream_state)` 签名复用一致；`stream_state == "complete"` 判定全程一致；`ThoughtEvent` / `ResponseEvent` / `RunResultEvent` / `ToolCallEvent` 均为已存在的导入符号。

**两处张力已显式标注：**
1. spec §8 迁移清单遗漏 `test_agent_kernel_usage_events.py` 的 4 个测试——见顶部 ⚠️ 节，已纳入 Task 1。
2. interleaved 场景下提前 emit 只含首段 reasoning（Task 2 Step 3 备注）；LLMError retry 残留入库首个 attempt reasoning（Task 2 Step 7）——均为 spec §5 明示的已知 trade-off，测试固化为「当前接受行为」。

---

## 执行交接

计划已保存到 `docs/superpowers/plans/2026-06-14-thought-early-persist.md`。两种执行方式：

1. **Subagent-Driven（推荐）** —— 每个 Task 派新 subagent，Task 间双阶段 review，迭代快。
2. **Inline Execution** —— 本会话内用 executing-plans，分批执行 + checkpoint review。

选哪种？
