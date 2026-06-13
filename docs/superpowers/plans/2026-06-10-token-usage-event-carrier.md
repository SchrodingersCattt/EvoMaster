# Token Usage Event Carrier 迁移实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 LLM token usage 从 `tool_result` 事件迁移到模型输出侧事件(`thought.complete` / `response.complete` / `tool_call`),并让 accepted thought 由 kernel 在 retry gate 之后生成。

**Architecture:** 五个自包含任务。Task 1 给 `ThoughtEvent` / `ToolCallEvent` 加 usage 字段;Task 2 把流式 pre-gate thought complete 降级为 segment_end,由 kernel 在 accepted LLM response 后生成 usage-bearing `thought.complete` 并给 `ToolCallEvent` 填 usage;Task 3 删除 `ToolResultEvent` 的 usage 字段(模型、dispatch 构造点、测试同步改);Task 4 改 public payload 映射与 SSE normalization;Task 5 全量验证。每个任务结束时测试全绿、单独提交。

**Tech Stack:** Python 3.x、Pydantic v2、pytest(asyncio)、uv、pre-commit。

**Spec:** `docs/superpowers/specs/2026-06-10-token-usage-event-carrier-design.md`

---

## 背景知识(执行前必读)

- 事件模型在 [matmaster/types/events.py](../../../matmaster/types/events.py)。`EventBase` 是 Pydantic v2 `BaseModel`,**未设 `extra="forbid"`**,构造时传未知 kwargs 会被静默忽略(不报错)。
- 内核主循环在 `matmaster/core/agent.py` 的 `AgentKernel._run_items()`。accepted `LLMResponse` 的处理从第 356 行开始(`response = llm_response`)。
- 流式事件由 `matmaster/core/agent_llm_stream.py` 的 `stream_llm_items()` 产生;`call_llm_streaming()`(同文件,无下划线前缀)是它的 retry 包装,streaming 事件**立即** yield(被 retry 丢弃的 attempt 的流式事件也已流出)。
- 工具结果事件由 `matmaster/core/agent_tool_dispatch.py` 的 `dispatch_tool_calls()` 产生。
- public payload 映射在 `matmaster/integration/event_payloads.py`。`normalize_response_sse_payload()` 定义于此文件(spec 中提到的 `stream_sse_filter` 只是 replay 侧调用方,改这一个函数 live 与 replay 同时生效)。
- 全仓库事件构造点唯一:`ThoughtEvent` 只在 `agent_llm_stream.py` 的 `_thought_item()`、`ToolCallEvent` 只在 `agent.py:465`、`ToolResultEvent` 只在 `agent_tool_dispatch.py:120`。没有其他非测试代码读取事件实例的 `.turn_usage` / `.total_usage`。
- Pydantic v2 验证 `dict[str, int]` 字段时会拷贝输入 dict(已验证),所以同一个快照 dict 变量可以安全地传给多个事件构造。
- 测试 mock providers 在 `tests/matmaster/core/agent_kernel_test_helpers.py`(`ProviderProtocolAttrs`、`ToolCallingProvider`、`make_kernel_runtime`、`make_kernel_turn`)和 `tests/matmaster/core/test_agent_kernel_stream.py`(`ReasoningThenContentProvider` 等)。
- `is_incomplete_response()`(`matmaster/core/finish_diagnostics.py:51`)= 无 tool_calls 且无可见内容。**reasoning-only response 算 incomplete,会触发 retry** —— Task 2 的 retry 测试利用这一点。
- 运行测试一律用 `uv run pytest`(workspace 虚拟环境)。

**对 spec 任务划分的一处调整:** spec 的 Task 1 同时做三个模型改动,但删除 `ToolResultEvent` 字段会让 `tests/matmaster/core/test_agent_tool_dispatch.py` 与 `tests/matmaster/core/test_agent_kernel_usage_events.py` 中断言 `event.turn_usage` 的测试立刻 AttributeError。本计划把"`ToolResultEvent` 删字段"挪到 Task 3,与 dispatch 构造点、相关测试同步修改,保证任务边界处测试全绿。

---

### Task 1: ThoughtEvent / ToolCallEvent 新增 usage 字段

**Files:**
- Modify: `matmaster/types/events.py:32-70`
- Test: `tests/matmaster/types/test_events.py`

- [ ] **Step 1: 写失败测试**

在 `tests/matmaster/types/test_events.py` 中,`TestThoughtEvent` 类(第 42 行起)之后插入:

```python
class TestThoughtEventUsage:
    def test_thought_usage_fields(self) -> None:
        evt = ThoughtEvent(
            source="agent",
            content="reasoning",
            stream_state="complete",
            turn_index=2,
            turn_usage={"prompt_tokens": 10, "completion_tokens": 4},
            total_usage={"prompt_tokens": 30, "completion_tokens": 9},
            usage_vendor={"inputTokens": 10, "outputTokens": 4},
        )

        assert evt.turn_index == 2
        assert evt.turn_usage == {"prompt_tokens": 10, "completion_tokens": 4}
        assert evt.total_usage == {"prompt_tokens": 30, "completion_tokens": 9}
        assert evt.usage_vendor == {"inputTokens": 10, "outputTokens": 4}

    def test_thought_usage_defaults(self) -> None:
        evt = ThoughtEvent(source="agent")
        assert evt.turn_index is None
        assert evt.turn_usage == {}
        assert evt.total_usage == {}
        assert evt.usage_vendor is None
```

`TestToolCallEvent` 类(第 106 行起)之后插入:

```python
class TestToolCallEventUsage:
    def test_tool_call_usage_fields(self) -> None:
        evt = ToolCallEvent(
            source="agent",
            call_id="c1",
            tool_name="bash",
            arguments={"cmd": "ls"},
            turn_index=0,
            turn_usage={"prompt_tokens": 100, "completion_tokens": 20},
            total_usage={"prompt_tokens": 100, "completion_tokens": 20},
            usage_vendor={"inputTokens": 100, "outputTokens": 20},
        )

        assert evt.turn_index == 0
        assert evt.turn_usage == {"prompt_tokens": 100, "completion_tokens": 20}
        assert evt.total_usage == {"prompt_tokens": 100, "completion_tokens": 20}
        assert evt.usage_vendor == {"inputTokens": 100, "outputTokens": 20}

    def test_tool_call_usage_defaults(self) -> None:
        evt = ToolCallEvent(
            source="agent", call_id="c1", tool_name="bash", arguments={}
        )
        assert evt.turn_index is None
        assert evt.turn_usage == {}
        assert evt.total_usage == {}
        assert evt.usage_vendor is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/types/test_events.py -q -k "Usage"`
Expected: 4 个新测试 FAIL,报 `AttributeError: ... object has no attribute 'turn_index'`(Pydantic 忽略未知构造 kwargs,字段访问才报错)。现有 `TestResponseEventUsage` 2 个测试 PASS。

- [ ] **Step 3: 实现字段**

`matmaster/types/events.py` 中 `ThoughtEvent`(第 32-45 行)改为:

```python
class ThoughtEvent(EventBase):
    """LLM thought/reasoning event.

    Streaming and non-streaming are unified; use ``stream_state`` to
    distinguish: 'start' | 'streaming' | 'segment_end' | 'end' | 'complete' | None.
    'complete' is the accepted-turn reasoning audit event and may carry usage;
    all other states are ephemeral streaming/segment markers without usage.
    """

    type: Literal["thought"] = "thought"
    content: str = ""
    stream_state: str | None = None  # 'start' | 'streaming' | 'segment_end' | 'end' | 'complete' | None
    stream_id: str | None = None
    token_count: int = 0
    context: str | None = None  # e.g. 'step_execution'
    reasoning_content: str | None = None
    turn_index: int | None = None
    turn_usage: dict[str, int] = Field(default_factory=dict)
    total_usage: dict[str, int] = Field(default_factory=dict)
    usage_vendor: dict[str, Any] | None = None
```

`ToolCallEvent`(第 64-70 行)改为:

```python
class ToolCallEvent(EventBase):
    """Tool call event -- emitted when the LLM requests a tool invocation.

    Usage fields describe the accepted LLM turn that requested the calls,
    not the tool execution itself. Multiple tool calls from one turn share
    the same turn_index and identical usage snapshots; consumers must
    deduplicate by turn_index.
    """

    type: Literal["tool_call"] = "tool_call"
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    turn_index: int | None = None
    turn_usage: dict[str, int] = Field(default_factory=dict)
    total_usage: dict[str, int] = Field(default_factory=dict)
    usage_vendor: dict[str, Any] | None = None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/types/test_events.py -q`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add matmaster/types/events.py tests/matmaster/types/test_events.py
git commit -m "feat(types): thought and tool_call events carry accepted-turn usage"
```

---

### Task 2: accepted thought 移入 kernel,tool_call 填 usage

**Files:**
- Modify: `matmaster/core/agent_llm_stream.py:152,168,205`(3 处 `"complete"` → `"segment_end"`)
- Modify: `matmaster/core/agent.py:44-50`(import)、`:356-383`(快照 + accepted thought)、`:463-471`(tool_call usage)
- Test: `tests/matmaster/core/test_agent_kernel_stream.py:288-315`(更新现有断言)
- Test: `tests/matmaster/core/test_agent_kernel_usage_events.py`(新增 5 个测试)

- [ ] **Step 1: 更新 stream 层现有测试为 segment_end 语义**

`tests/matmaster/core/test_agent_kernel_stream.py` 中 `test_segment_complete_on_reasoning_to_content`(第 288-315 行)整体替换为:

```python
    @pytest.mark.asyncio
    async def test_segment_end_on_reasoning_to_content(self) -> None:
        """ThoughtEvent(segment_end) emitted when transitioning from reasoning to content."""
        from matmaster.core.kernel_items import _KernelItem

        provider = ReasoningThenContentProvider()
        kernel_runtime = make_kernel_runtime(provider=provider)

        api_messages = [{"role": "user", "content": "test"}]
        items: list[_KernelItem] = []
        async for item in stream_llm_items(
            kernel_runtime.resources, api_messages, None
        ):
            items.append(item)

        thought_completes = [
            i
            for i in items
            if i.event
            and isinstance(i.event, ThoughtEvent)
            and i.event.stream_state == "complete"
        ]
        thought_segment_ends = [
            i
            for i in items
            if i.event
            and isinstance(i.event, ThoughtEvent)
            and i.event.stream_state == "segment_end"
        ]
        assert thought_completes == []
        assert len(thought_segment_ends) >= 1
        assert "thinking part 1" in thought_segment_ends[0].event.content
```

注意:同文件 `test_model_identity_fields`(第 549-583 行)**不需要修改**——它在 kernel `run_stream` 层取 complete thought,改动后该事件改由 kernel 生成,依然存在;`ThoughtEvent` 本就没有 model 字段,`MODEL_IDENTITY_FIELDS.isdisjoint(...)` 断言依旧成立。

- [ ] **Step 2: 在 kernel 层新增失败测试**

`tests/matmaster/core/test_agent_kernel_usage_events.py` 文件顶部 import 区改为(新增 `ThoughtEvent`、`ToolCallEvent`):

```python
from matmaster.types.events import (
    ResponseEvent,
    RunResultEvent,
    ThoughtEvent,
    ToolCallEvent,
)
```

文件末尾追加 5 个测试:

```python
@pytest.mark.asyncio
async def test_run_stream_emits_usage_bearing_thought_complete() -> None:
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
    assert completes[0].turn_index == 0
    assert completes[0].turn_usage == {"prompt_tokens": 10, "completion_tokens": 5}
    assert completes[0].total_usage == {"prompt_tokens": 10, "completion_tokens": 5}

    segment_ends = [
        e
        for e in events
        if isinstance(e, ThoughtEvent) and e.stream_state == "segment_end"
    ]
    assert segment_ends
    for ev in segment_ends:
        assert ev.turn_usage == {}
        assert ev.total_usage == {}


@pytest.mark.asyncio
async def test_tool_call_events_carry_parent_turn_usage() -> None:
    from matmaster.core.agent import AgentKernel
    from matmaster.tools.tool_result import ToolResult
    from matmaster.types.messages import StreamChunk, ToolCallData

    from .agent_kernel_test_helpers import ToolCallingProvider

    class EchoRunner:
        async def execute_batch(self, tool_calls, ctx, *, on_result=None):
            del ctx, on_result
            return [
                (tc, ToolResult(status="success", content="ok"))
                for tc in tool_calls
            ]

    class TwoToolCallProvider(ToolCallingProvider):
        async def chat_stream(self, messages, tools=None, *, timeout=None):
            self._call_count += 1
            if self._call_count == 1:
                yield StreamChunk(
                    tool_call_deltas=[
                        {
                            "index": 0,
                            "id": "call-1",
                            "name": "bash",
                            "arguments": '{"cmd": "ls"}',
                        },
                        {
                            "index": 1,
                            "id": "call-2",
                            "name": "bash",
                            "arguments": '{"cmd": "pwd"}',
                        },
                    ],
                )
                yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 5})
                return
            yield StreamChunk(content="done", finish_reason="stop")
            yield StreamChunk(usage={"prompt_tokens": 7})

    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(
            provider=TwoToolCallProvider(
                [ToolCallData(id="unused", name="bash", arguments={})],
                max_tool_turns=1,
            ),
            tool_runner=EchoRunner(),
        ),
        make_kernel_turn("test task"),
    ):
        events.append(event)

    tool_call_events = [e for e in events if isinstance(e, ToolCallEvent)]
    assert len(tool_call_events) == 2
    for tc_event in tool_call_events:
        assert tc_event.turn_index == 0
        assert tc_event.turn_usage == {"prompt_tokens": 5}
        assert tc_event.total_usage == {"prompt_tokens": 5}


@pytest.mark.asyncio
async def test_reasoning_then_tool_call_thought_and_tool_call_share_turn() -> None:
    from matmaster.core.agent import AgentKernel
    from matmaster.tools.tool_result import ToolResult
    from matmaster.types.messages import StreamChunk, ToolCallData

    from .agent_kernel_test_helpers import ToolCallingProvider

    class EchoRunner:
        async def execute_batch(self, tool_calls, ctx, *, on_result=None):
            del ctx, on_result
            return [
                (tc, ToolResult(status="success", content="ok"))
                for tc in tool_calls
            ]

    class ReasoningToolProvider(ToolCallingProvider):
        async def chat_stream(self, messages, tools=None, *, timeout=None):
            self._call_count += 1
            if self._call_count == 1:
                yield StreamChunk(reasoning_content="plan the call")
                yield StreamChunk(
                    tool_call_deltas=[
                        {
                            "index": 0,
                            "id": "call-r1",
                            "name": "bash",
                            "arguments": '{"cmd": "ls"}',
                        }
                    ],
                )
                yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 8})
                return
            yield StreamChunk(content="done", finish_reason="stop")
            yield StreamChunk(usage={"prompt_tokens": 6})

    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(
            provider=ReasoningToolProvider(
                [ToolCallData(id="unused", name="bash", arguments={})],
                max_tool_turns=1,
            ),
            tool_runner=EchoRunner(),
        ),
        make_kernel_turn("test task"),
    ):
        events.append(event)

    thought_completes = [
        e
        for e in events
        if isinstance(e, ThoughtEvent) and e.stream_state == "complete"
    ]
    tool_call_events = [e for e in events if isinstance(e, ToolCallEvent)]
    assert len(thought_completes) == 1
    assert thought_completes[0].content == "plan the call"
    assert thought_completes[0].turn_usage == {"prompt_tokens": 8}
    assert len(tool_call_events) == 1
    assert tool_call_events[0].turn_index == thought_completes[0].turn_index
    assert tool_call_events[0].turn_usage == {"prompt_tokens": 8}


@pytest.mark.asyncio
async def test_retry_discarded_attempt_does_not_emit_usage_thought_complete() -> None:
    from matmaster.core.agent import AgentKernel
    from matmaster.types.messages import LLMResponse, StreamChunk

    from .agent_kernel_test_helpers import ProviderProtocolAttrs

    class ReasoningRetryProvider(ProviderProtocolAttrs):
        """First attempt reasoning-only (incomplete, retried); second accepted."""

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
                yield StreamChunk(reasoning_content="discarded reasoning")
                yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 3})
            else:
                yield StreamChunk(reasoning_content="kept reasoning")
                yield StreamChunk(content="answer")
                yield StreamChunk(
                    finish_reason="stop", usage={"prompt_tokens": 10}
                )

    provider = ReasoningRetryProvider()
    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(provider=provider),
        make_kernel_turn("test task"),
    ):
        events.append(event)

    assert provider.call_count == 2
    completes = [
        e
        for e in events
        if isinstance(e, ThoughtEvent) and e.stream_state == "complete"
    ]
    assert [e.content for e in completes] == ["kept reasoning"]
    assert completes[0].turn_usage == {"prompt_tokens": 10}
    for ev in events:
        if isinstance(ev, ThoughtEvent) and ev.stream_state != "complete":
            assert ev.turn_usage == {}


@pytest.mark.asyncio
async def test_invalid_finish_reasoning_only_still_emits_thought_complete() -> None:
    """Spec 'invalid finish': retry 耗尽后的 reasoning-only accepted response
    仍发 usage-bearing thought.complete 作为审计证据,run 以 invalid_finish 失败。"""
    from matmaster.core.agent import AgentKernel
    from matmaster.types.messages import LLMResponse, StreamChunk

    from .agent_kernel_test_helpers import ProviderProtocolAttrs

    class ReasoningOnlyProvider(ProviderProtocolAttrs):
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
            yield StreamChunk(reasoning_content=f"attempt {self.call_count}")
            yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 4})

    provider = ReasoningOnlyProvider()
    events: list[Any] = []
    async for event in AgentKernel().run_stream(
        make_kernel_runtime(provider=provider),
        make_kernel_turn("test task"),
    ):
        events.append(event)

    assert provider.call_count == 2
    completes = [
        e
        for e in events
        if isinstance(e, ThoughtEvent) and e.stream_state == "complete"
    ]
    assert [e.content for e in completes] == ["attempt 2"]
    assert completes[0].turn_usage == {"prompt_tokens": 4}
    run_result = next(e for e in events if isinstance(e, RunResultEvent))
    assert run_result.status == "failed"
    assert run_result.reason == "invalid_finish"
    assert run_result.usage == {"prompt_tokens": 4}
    assert not [e for e in events if isinstance(e, ToolCallEvent)]
```

- [ ] **Step 3: 跑新测试确认失败**

Run: `uv run pytest tests/matmaster/core/test_agent_kernel_usage_events.py tests/matmaster/core/test_agent_kernel_stream.py -q`
Expected: Step 1 改写的 `test_segment_end_on_reasoning_to_content` FAIL(当前发的是 complete 不是 segment_end);Step 2 的 5 个新测试 FAIL(无 usage-bearing thought.complete / tool_call usage 为空)。其余 PASS。

- [ ] **Step 4: 实现 — stream 层降级为 segment_end**

`matmaster/core/agent_llm_stream.py` 中 3 处 `_thought_item(..., "complete")` 全部改为 `"segment_end"`:

第 152 行(reasoning → content 过渡):
```python
                if producing_reasoning:
                    yield _thought_item("".join(reasoning_parts), stream_id, "segment_end")
                    producing_reasoning = False
```

第 168 行(reasoning → tool_calls 过渡):
```python
                if producing_reasoning:
                    yield _thought_item("".join(reasoning_parts), stream_id, "segment_end")
                    producing_reasoning = False
```

第 203-205 行(finally 块,注释一并更新):
```python
        # Emit segment_end for any in-progress segments
        if producing_reasoning:
            yield _thought_item("".join(reasoning_parts), stream_id, "segment_end")
```

- [ ] **Step 5: 实现 — kernel 层 accepted thought 与 tool_call usage**

`matmaster/core/agent.py` import 块(第 44-50 行)加入 `ThoughtEvent`:

```python
from matmaster.types.events import (
    AssistantStateEvent,
    CheckpointEvent,
    FinishDetail,
    ResponseEvent,
    ThoughtEvent,
    ToolCallEvent,
)
```

`_run_items()` 中 accepted response 段(第 356-383 行)改为——在 `turn_index` 计算后建立快照,先发 accepted thought,再走原 response 分支:

```python
            response = llm_response
            state.turn_usage = response.usage
            accumulate_usage(state.total_usage, response.usage)
            state.usage_vendor_by_turn.append(
                dict(response.usage_vendor) if response.usage_vendor else {}
            )
            turn_index = state.turn - 1
            turn_usage_snapshot = dict(state.turn_usage)
            total_usage_snapshot = dict(state.total_usage)
            usage_vendor_snapshot = response.usage_vendor or None
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
            is_root_run = kernel_spec.run_identity.spawn_id is None
            if (
                is_root_run
                and response.content
                and not is_trivial_response_text(response.content)
            ):
                state.last_emitted_content = response.content
                yield _KernelItem(
                    event=ResponseEvent(
                        source="agent",
                        content=response.content,
                        stream_state="complete",
                        turn_index=turn_index,
                        turn_usage=turn_usage_snapshot,
                        total_usage=total_usage_snapshot,
                        usage_vendor=usage_vendor_snapshot,
                        model=state.llm_model,
                        model_profile=state.llm_model_profile,
                        model_route=state.llm_model_route,
                    )
                )
```

要点:accepted thought 在 invalid-finish 检查(`is_valid_natural_finish`)**之前**发出,因此 reasoning-only 异常完成的 run 也会留下 usage-bearing reasoning 审计事件(spec "invalid finish" 一节的要求自然满足)。`AssistantStateEvent` 两处(第 412-423、449-461 行)保持现状不动。

`ToolCallEvent` 构造(第 463-471 行)改为:

```python
            for tc in response.tool_calls:
                yield _KernelItem(
                    event=ToolCallEvent(
                        source="agent",
                        call_id=tc.id,
                        tool_name=tc.name,
                        arguments=tc.arguments,
                        turn_index=turn_index,
                        turn_usage=turn_usage_snapshot,
                        total_usage=total_usage_snapshot,
                        usage_vendor=usage_vendor_snapshot,
                    )
                )
```

(Pydantic v2 验证时拷贝 dict 输入,共享快照变量不会造成事件间状态串扰。)

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/core/test_agent_kernel_usage_events.py tests/matmaster/core/test_agent_kernel_stream.py -q`
Expected: 全部 PASS(含未改动的 `test_model_identity_fields`)。

- [ ] **Step 7: 跑 core 全量回归**

Run: `uv run pytest tests/matmaster/core -q`
Expected: 全部 PASS。若有其他测试依赖 stream 层 thought complete,按 segment_end 语义同步修正断言(预期只有 Step 1 已覆盖的一处)。

- [ ] **Step 8: Commit**

```bash
git add matmaster/core/agent.py matmaster/core/agent_llm_stream.py tests/matmaster/core/test_agent_kernel_stream.py tests/matmaster/core/test_agent_kernel_usage_events.py
git commit -m "feat(core): accepted thought.complete and tool_call carry turn usage"
```

---

### Task 3: ToolResultEvent 去 usage

**Files:**
- Modify: `matmaster/types/events.py:73-84`(删 3 个字段)
- Modify: `matmaster/core/agent_tool_dispatch.py:107,119-131`
- Test: `tests/matmaster/types/test_events.py:143-150`
- Test: `tests/matmaster/core/test_agent_tool_dispatch.py:67-162`
- Test: `tests/matmaster/core/test_agent_kernel_usage_events.py`(`test_agent_tool_usage_delta_reaches_parent_run_result`)

- [ ] **Step 1: 更新测试(先红)**

`tests/matmaster/types/test_events.py`:删除模块级函数 `test_tool_result_turn_index_defaults_to_none`(第 143-150 行),并在 `TestToolResultEvent` 类内追加:

```python
    def test_tool_result_has_no_usage_fields(self) -> None:
        assert "turn_index" not in ToolResultEvent.model_fields
        assert "turn_usage" not in ToolResultEvent.model_fields
        assert "total_usage" not in ToolResultEvent.model_fields
```

`tests/matmaster/core/test_agent_tool_dispatch.py`:两个 dispatch 测试改为只断言 `state.total_usage` 累计(事件不再承载 usage)。`test_dispatch_tool_calls_accumulates_agent_usage_before_event` 的断言段(第 107-115 行)替换为:

```python
    event = items[0].event
    assert isinstance(event, ToolResultEvent)
    assert "turn_usage" not in ToolResultEvent.model_fields
    assert state.total_usage == {
        "prompt_tokens": 15,
        "completion_tokens": 2,
        "total_tokens": 12,
    }
```

`test_dispatch_tool_calls_usage_fields_are_snapshots`(第 118-162 行)整体替换为:

```python
@pytest.mark.asyncio
async def test_dispatch_tool_calls_accumulates_each_agent_usage() -> None:
    state = _KernelState(
        messages=[SystemMessage(content="sys")],
        turn=1,
        turn_usage={"prompt_tokens": 5},
        total_usage={"prompt_tokens": 5},
    )
    tool_calls = [
        ToolCallData(id="call-1", name="Agent", arguments={}),
        ToolCallData(id="call-2", name="Agent", arguments={}),
    ]
    runner = StaticRunner(
        [
            ToolResult(
                status="success",
                content="one",
                payload={"subagent_usage": {"prompt_tokens": 10}},
            ),
            ToolResult(
                status="success",
                content="two",
                payload={"subagent_usage": {"prompt_tokens": 20}},
            ),
        ]
    )

    events = [
        item.event
        async for item in dispatch_tool_calls(
            tool_calls=tool_calls,
            tool_runner=runner,
            max_turns=10,
            state=state,
            cancel_token=None,
        )
        if isinstance(item.event, ToolResultEvent)
    ]

    assert len(events) == 2
    assert state.total_usage == {"prompt_tokens": 35}
```

`tests/matmaster/core/test_agent_kernel_usage_events.py` 中 `test_agent_tool_usage_delta_reaches_parent_run_result`:删除 `tool_event` 的两条 usage 断言(`tool_event.turn_usage == ...` 与 `tool_event.total_usage == ...`),保留 `ToolResultEvent` 存在性与 `run_result` 断言。断言段改为:

```python
    tool_event = next(e for e in events if isinstance(e, ToolResultEvent))
    run_result = next(e for e in events if isinstance(e, RunResultEvent))
    assert tool_event.tool_name == "Agent"
    assert run_result.usage == {
        "prompt_tokens": 22,
        "completion_tokens": 5,
        "total_tokens": 22,
    }
    assert run_result.usage_vendor_by_turn == [{}, {}]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/types/test_events.py tests/matmaster/core/test_agent_tool_dispatch.py tests/matmaster/core/test_agent_kernel_usage_events.py -q`
Expected: `test_tool_result_has_no_usage_fields` 与 `test_dispatch_tool_calls_accumulates_agent_usage_before_event` FAIL(`model_fields` 仍含 usage 字段);其余 PASS。

- [ ] **Step 3: 实现删除**

`matmaster/types/events.py` 中 `ToolResultEvent`(第 73-84 行)改为:

```python
class ToolResultEvent(EventBase):
    """Tool execution result event.

    Carries only the tool execution outcome. LLM token usage lives on the
    model-output-side events (thought.complete / response.complete /
    tool_call); tool payloads may still embed tool-specific evidence such
    as ``payload["subagent_usage"]``.
    """

    type: Literal["tool_result"] = "tool_result"
    call_id: str
    tool_name: str
    result: Any  # str | dict
    status: str = "success"
    payload: dict[str, Any] = Field(default_factory=dict)
```

`matmaster/core/agent_tool_dispatch.py`:删除第 107 行 `turn_index = state.turn - 1`,`ToolResultEvent` 构造(第 119-131 行)改为:

```python
        yield _KernelItem(
            event=ToolResultEvent(
                source="agent",
                call_id=tc.id,
                tool_name=tc.name,
                result=tool_result.content,
                status=tool_result.status,
                payload=tool_result.payload,
            )
        )
```

`extract_tool_usage_delta()` 与 `accumulate_usage(state.total_usage, usage_delta)`(第 116-118 行)**保持不动**——`RunResultEvent.usage` 仍要包含 subagent usage。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/types/test_events.py tests/matmaster/core/test_agent_tool_dispatch.py tests/matmaster/core/test_agent_kernel_usage_events.py -q`
Expected: 全部 PASS。

- [ ] **Step 5: 全局确认无残留消费者**

Run: `grep -rn "turn_usage\|total_usage\|turn_index" matmaster src --include="*.py" | grep -i "tool_result" | grep -v __pycache__`
Expected: 只剩 `matmaster/integration/event_payloads.py` 的 `tool_result` 投影分支(第 257-261 行,Task 4 处理)。出现其他文件则逐一检查并处理。

- [ ] **Step 6: Commit**

```bash
git add matmaster/types/events.py matmaster/core/agent_tool_dispatch.py tests/matmaster/types/test_events.py tests/matmaster/core/test_agent_tool_dispatch.py tests/matmaster/core/test_agent_kernel_usage_events.py
git commit -m "feat(types): tool_result no longer carries llm turn usage"
```

---

### Task 4: public payload 映射与 SSE normalization

**Files:**
- Modify: `matmaster/integration/event_payloads.py:53-59,95-123,235-262`
- Test: `tests/matmaster/integration/test_event_payloads.py`

- [ ] **Step 1: 写失败测试**

`tests/matmaster/integration/test_event_payloads.py` 中,现有 `test_thought_with_model_returns_plain_content`(第 208-218 行)**保留不动**(model identity 不触发 thought 结构化,该行为不变)。在它之后插入:

```python
    def test_thought_without_usage_keeps_plain_content(self) -> None:
        payload = {'type': 'thought', 'source': 'Agent', 'content': 'delta'}

        assert _public_content_for_event('thought', payload) == 'delta'

    def test_thought_with_usage_returns_structured_content(self) -> None:
        payload = {
            'type': 'thought',
            'source': 'Agent',
            'content': 'reasoning text',
            'stream_state': 'complete',
            'turn_index': 0,
            'turn_usage': {'prompt_tokens': 100, 'completion_tokens': 20},
            'total_usage': {'prompt_tokens': 100, 'completion_tokens': 20},
            'usage_vendor': {'inputTokens': 100, 'outputTokens': 20},
        }
        assert _public_content_for_event('thought', payload) == {
            'content': 'reasoning text',
            'turn_index': 0,
            'turn_usage': {'prompt_tokens': 100, 'completion_tokens': 20},
            'total_usage': {'prompt_tokens': 100, 'completion_tokens': 20},
            'usage_vendor': {'inputTokens': 100, 'outputTokens': 20},
        }

    def test_tool_call_with_usage_projects_usage_fields(self) -> None:
        payload = {
            'type': 'tool_call',
            'source': 'Agent',
            'call_id': 'call_1',
            'tool_name': 'Bash',
            'arguments': {'cmd': 'ls'},
            'turn_index': 0,
            'turn_usage': {'prompt_tokens': 100, 'completion_tokens': 20},
            'total_usage': {'prompt_tokens': 100, 'completion_tokens': 20},
            'usage_vendor': {'inputTokens': 100, 'outputTokens': 20},
        }
        assert _public_content_for_event('tool_call', payload) == {
            'id': 'call_1',
            'call_id': 'call_1',
            'name': 'Bash',
            'args': {'cmd': 'ls'},
            'turn_index': 0,
            'turn_usage': {'prompt_tokens': 100, 'completion_tokens': 20},
            'total_usage': {'prompt_tokens': 100, 'completion_tokens': 20},
            'usage_vendor': {'inputTokens': 100, 'outputTokens': 20},
        }

    def test_tool_call_without_usage_keeps_minimal_shape(self) -> None:
        payload = {
            'type': 'tool_call',
            'source': 'Agent',
            'call_id': 'call_1',
            'tool_name': 'Bash',
            'arguments': {'cmd': 'ls'},
            'turn_index': None,
            'turn_usage': {},
            'total_usage': {},
            'usage_vendor': None,
        }
        assert _public_content_for_event('tool_call', payload) == {
            'id': 'call_1',
            'call_id': 'call_1',
            'name': 'Bash',
            'args': {'cmd': 'ls'},
        }

    def test_tool_result_does_not_project_usage(self) -> None:
        payload = {
            'type': 'tool_result',
            'source': 'Agent',
            'call_id': 'call-6',
            'tool_name': 'bash',
            'result': 'output',
            'status': 'success',
            'turn_index': 1,
            'turn_usage': {'prompt_tokens': 10},
            'total_usage': {'prompt_tokens': 10},
        }
        assert _public_content_for_event('tool_result', payload) == {
            'id': 'call-6',
            'call_id': 'call-6',
            'name': 'bash',
            'result': 'output',
            'status': 'success',
            'info': {},
        }
```

`test_usage_event_mappings_preserve_turn_index`(第 330-354 行)整体替换为(tool_result 部分已被上面的反向测试取代):

```python
    def test_assistant_state_mapping_preserves_turn_index(self) -> None:
        state = {'role': 'assistant', 'content': None, 'tool_calls': []}
        assistant = _public_content_for_event(
            'assistant_state',
            {
                'state': state,
                'turn_index': 1,
                'turn_usage': {'prompt_tokens': 10},
                'total_usage': {'prompt_tokens': 10},
            },
        )
        assert assistant['turn_index'] == 1
```

在 `test_structured_thought_content_strips_model_identity_for_sse`(第 376-396 行,保留不动)之后插入:

```python
    def test_structured_thought_content_is_unpacked_for_sse(self) -> None:
        payload = {
            'source': 'MatMaster',
            'type': 'thought',
            'content': {
                'content': 'reasoning text',
                'turn_index': 3,
                'turn_usage': {'total_tokens': 12},
                'total_usage': {'total_tokens': 30},
                'usage_vendor': {'inputTokens': 10, 'outputTokens': 2},
            },
            'session_id': 'sess',
            'task_id': 'task',
            'spawn_id': None,
        }
        normalized = normalize_response_sse_payload(payload)
        assert normalized['content'] == 'reasoning text'
        assert normalized['turn_index'] == 3
        assert normalized['turn_usage'] == {'total_tokens': 12}
        assert normalized['total_usage'] == {'total_tokens': 30}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/matmaster/integration/test_event_payloads.py -q`
Expected: 新增的 thought 结构化、tool_call 投影、tool_result 反向、thought unpack 测试 FAIL;`test_thought_without_usage_keeps_plain_content`、`test_tool_call_without_usage_keeps_minimal_shape`、`test_assistant_state_mapping_preserves_turn_index` PASS(现状已满足);其余 PASS。

- [ ] **Step 3: 实现映射改动**

`matmaster/integration/event_payloads.py`:

3a. 第 53-59 行常量改名为共享 usage keys(注释同步):

```python
_CONTENT_META_KEYS = frozenset({'type', 'source', 'timestamp'})
# Shared usage keys projected into structured content for the
# model-output-side carriers (response / thought / tool_call).
_USAGE_KEYS = (
    'turn_index',
    'stream_id',
    'turn_usage',
    'total_usage',
    'usage_vendor',
)
```

3b. `_response_public_content`(第 95-105 行)中 `_RESPONSE_USAGE_KEYS` 引用改为 `_USAGE_KEYS`:

```python
def _response_public_content(payload: dict[str, Any]) -> object | None:
    content = payload.get('content')
    has_usage = bool(payload.get('turn_usage') or payload.get('total_usage'))
    has_model_identity = any(payload.get(key) for key in _MODEL_IDENTITY_KEYS)
    if not has_usage and not has_model_identity:
        return content

    out: dict[str, Any] = {'content': content or ''}
    _copy_nonempty_keys(out, payload, _USAGE_KEYS)
    _copy_nonempty_keys(out, payload, _MODEL_IDENTITY_KEYS)
    return out
```

3c. `normalize_response_sse_payload`(第 108-123 行)对 thought 也解包 usage(model identity 仍仅 response 上提):

```python
def normalize_response_sse_payload(payload: dict[str, Any]) -> dict[str, Any]:
    event_type = payload.get('type')
    if event_type not in _STRUCTURAL_PASSTHROUGH_EVENT_TYPES:
        return payload

    content = payload.get('content')
    if not isinstance(content, dict) or 'content' not in content:
        return payload

    normalized = dict(payload)
    normalized['content'] = str(content.get('content') or '')
    lift_keys = (
        (*_USAGE_KEYS, *_MODEL_IDENTITY_KEYS)
        if event_type == 'response'
        else _USAGE_KEYS
    )
    for key in lift_keys:
        if key in content and content.get(key) is not None:
            normalized[key] = content[key]
    return normalized
```

3d. `_public_content_for_event` 的 `thought` 分支(第 235-236 行)改为:

```python
    if event_type == 'thought':
        content = payload.get('content')
        if not (payload.get('turn_usage') or payload.get('total_usage')):
            return content
        out: dict[str, Any] = {'content': content or ''}
        _copy_nonempty_keys(out, payload, _USAGE_KEYS)
        return out
```

3e. `tool_call` 分支(第 238-245 行)改为:

```python
    if event_type == 'tool_call':
        call_id = payload.get('call_id')
        out: dict[str, Any] = {
            'id': call_id,
            'call_id': call_id,
            'name': payload.get('tool_name'),
            'args': payload.get('arguments') or {},
        }
        _copy_nonempty_keys(out, payload, _USAGE_KEYS)
        return out
```

3f. `tool_result` 分支(第 247-262 行)删除 usage 投影:

```python
    if event_type == 'tool_result':
        call_id = payload.get('call_id')
        return {
            'id': call_id,
            'call_id': call_id,
            'name': payload.get('tool_name'),
            'result': payload.get('result'),
            'status': payload.get('status', 'success'),
            'info': payload.get('info') or payload.get('payload') or {},
        }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/matmaster/integration/test_event_payloads.py -q`
Expected: 全部 PASS(含保留的 `test_thought_with_model_returns_plain_content` 与 `test_structured_thought_content_strips_model_identity_for_sse`——model identity 不在 `_USAGE_KEYS`,thought 行为只对 usage 起反应)。

- [ ] **Step 5: Commit**

```bash
git add matmaster/integration/event_payloads.py tests/matmaster/integration/test_event_payloads.py
git commit -m "feat(integration): usage projection moves to thought/tool_call payloads"
```

---

### Task 5: 全量验证与 pre-commit

**Files:** 无新改动(只跑验证;若回归失败按对应任务的语义修正)。

- [ ] **Step 1: spec 最小验证集 + 受影响测试**

```bash
uv run pytest tests/matmaster/types/test_events.py \
  tests/matmaster/core/test_agent_kernel_usage_events.py \
  tests/matmaster/core/test_agent_kernel_stream.py \
  tests/matmaster/core/test_agent_tool_dispatch.py \
  tests/matmaster/integration/test_event_payloads.py \
  tests/matmaster/integration/test_sse_handler_mode_filter.py -q
```
Expected: 全部 PASS。

- [ ] **Step 2: 更广回归(integration + services 中 replay/history 链路)**

```bash
uv run pytest tests/matmaster -q
uv run pytest tests -q --ignore=tests/matmaster
```
Expected: 全部 PASS。已在计划阶段核实:除 Task 1-4 已更新的 5 个测试文件外,仓库中没有其他测试读取这三类事件的 usage 字段——其余 `ToolResultEvent(...)` 构造点(workspace/figures/devshell/replay 等测试)均不传 usage kwargs;`CompactionEvent.turn_usage`、`FinishDetail.last_turn_usage`、compactor 接口参数 `turn_usage` 同名但属于保留合同,不在本次迁移范围。`ChatHistoryConverter._assistant_content`(`src/services/chat_history.py:284`)已支持 `{content: ...}` dict 提取,thought 结构化 content 不破坏历史恢复;`stream_sse_filter._normalize_replayed_event` 复用改后的 `normalize_response_sse_payload`,replay 自动获得 thought usage 解包。若仍出现意外失败,先核对失败断言属于哪一侧合同再修正,不要回退主改动。

- [ ] **Step 3: changed-files pre-commit**

```bash
uv run --extra dev pre-commit run --files \
  matmaster/types/events.py \
  matmaster/core/agent.py \
  matmaster/core/agent_llm_stream.py \
  matmaster/core/agent_tool_dispatch.py \
  matmaster/integration/event_payloads.py \
  tests/matmaster/types/test_events.py \
  tests/matmaster/core/test_agent_kernel_stream.py \
  tests/matmaster/core/test_agent_kernel_usage_events.py \
  tests/matmaster/core/test_agent_tool_dispatch.py \
  tests/matmaster/integration/test_event_payloads.py
```
Expected: 全部 hook 通过(formatter 若改写文件,重新 add 后 amend 到对应提交或追加一个 style 提交)。

- [ ] **Step 4: 最终合同抽查(人工)**

对照 spec "最终合同"逐条确认:
- `thought.complete` / `response.complete` / `tool_call` 携带 accepted turn usage 快照,共享同一 `turn_index`。
- `tool_result` 模型与 public content 均无 usage;`payload["subagent_usage"]` 仍经 `info` 透出。
- `assistant_state` / `RunResultEvent` 未被本次改动触碰(`git diff main -- matmaster/types/events.py` 确认 `AssistantStateEvent`、`RunResultEvent` 无 diff)。
- streaming states(start/streaming/segment_end/end)全部无 usage、不持久化。

---

## 风险与回归提示(执行中注意)

- **live SSE 行为变化是预期的:** `tool_call` 在 live SSE 不被跳过,改动后前端实时会看到 `tool_call` content 中新增 usage 字段;`tool_result` 不再带 usage。`thought.complete` / `response.complete` 依旧被 SSEHandler 跳过,实时流式显示不变。
- **持久化行为变化是预期的:** 此前 per-segment 的流式 `thought.complete`(含被 retry 丢弃的 attempt)会被持久化;改动后持久化的 thought 是每个 accepted turn 一条、内容为该 turn 全部 reasoning 的合并,replay 中 thought 行数会变化。
- **不做任何兼容:** 旧 DB 中 `tool_result.content.turn_usage` 残留不在主代码处理;如需清理走外部迁移脚本。
- **消费合同:** 多个事件共享同一 `turn_usage` 快照,事件流统计必须按 `turn_index` 去重;最终总量一律以 `RunResultEvent.usage` 为准。
