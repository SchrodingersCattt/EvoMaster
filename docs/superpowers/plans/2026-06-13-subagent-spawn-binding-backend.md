# Subagent Spawn 绑定事件（后端）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐父子绑定契约——orchestrator 在子事件流开始前发出公共 `SubagentSpawnEvent`（spawn_id ↔ parent_call_id），并把 spawn_id 经 `DrainResult` 回传到父 `tool_result` payload。

**Architecture:** 新增一个 SystemEvent 类型 `subagent_spawn`；`SubagentOrchestrator.spawn()` 在 drain 子流之前经既有 child_event_sink 发绑定事件、drain 结束后回填 `DrainResult.spawn_id`；`AgentTool` 把实现集中到 `execute_with_context`（compiler 首选路径）以拿到 `exec_ctx.tool_call_id`，并在 payload 回传 spawn_id；`event_payloads` 补 content 映射使 SSE 与持久化同构。SSE/持久化 handler 均为黑名单过滤（已核实 `sse_handler._should_skip`、`PersistenceHandler._SKIP_TYPES`），新事件自动透传、自动落库，无需改 handler。

**Tech Stack:** Python 3.10+ / Pydantic 事件模型 / pytest（asyncio_mode=auto）/ uv 环境。

**Spec:** `docs/superpowers/specs/2026-06-13-subagent-spawn-binding-design.md`

**执行须知：**
- 在 `test` 分支最新基线上新建分支（建议 `feat/subagent-spawn-binding`）执行，不要在 `feat/bohrium_job` 上做。
- 本计划文件与 spec 同属 `docs/`，**绝对不进 git 提交**（项目规则）。
- 测试命令一律用仓库 uv 环境：`uv run pytest ...`。

---

## ⚠️ 与 spec 的一处偏差（计划期发现，需求方确认前先按本计划实施）

spec §1 与 §3.2 步骤 6 声称子终止事件 `run_result` "全链路已携带 spawn_id、照旧转发、前端以它收口每条流"。**代码现状并非如此**：`drain_run_stream`（`matmaster/core/stream_drain.py:31`）遇到 `RunResultEvent` 直接 return，不调用 `on_event`，且该行为被既有测试 `test_drain_run_stream_does_not_forward_terminal_run_result` 显式锁定。也就是说子代理的 run_result 目前从未进入 fanout（不到 SSE、不落库），前端没有任何收口信号，回放也没有每个 spawn 的终止行。

spec §5 "不加 subagent_stop 事件" 的理由正是 "子 run_result 已是终止信号"——要让对齐的前端契约成立，必须把子 run_result 真正送进 fanout。本计划用**显式 opt-in**修正：`drain_run_stream` 增加 `forward_terminal: bool = False` 参数，仅 orchestrator 传 True；devshell 与 evaluation 两个既有消费方走默认值、行为不变。这不是新增事件类型，不违反 spec §5。

对应任务：Task 2（drain 开关）+ Task 3（orchestrator 启用）。

---

## 文件结构总览

| 文件 | 改动 | 任务 |
|---|---|---|
| `matmaster/types/events.py` | 新增 `SubagentSpawnEvent`；注册 `SystemEvent`/`BusEvent` union；docstring 计数修正为 23 = 10 + 13（现状文案 21 = 9 + 12 本就漏数了 `CheckpointEvent`） | Task 1 |
| `tests/matmaster/types/test_events.py` | 新事件测试；union/计数测试补 `checkpoint` 与 `subagent_spawn` | Task 1 |
| `matmaster/core/stream_drain.py` | `drain_run_stream` 增加 `forward_terminal` 关键字参数（默认 False） | Task 2 |
| `tests/matmaster/core/test_stream_drain.py` | 新增 forward_terminal 行为测试 | Task 2 |
| `matmaster/types/stream_drain.py` | `DrainResult` 末尾增加 `spawn_id: str \| None = None` | Task 3 |
| `matmaster/core/subagent_orchestrator.py` | `spawn()` 增加 `parent_call_id`/`task_summary` 关键字参数；drain 前发绑定事件；`forward_terminal=True`；回填 `DrainResult.spawn_id`；`make_spawn_fn` 闭包透传 | Task 3 |
| `tests/matmaster/core/test_hook_wiring.py` | 更新 drain 替身签名与 forwarded 计数断言；新增绑定事件行为测试 | Task 3 |
| `matmaster/tools/builtin/agent_tool.py` | `SpawnFn` 放宽为 `Callable[..., Awaitable[DrainResult]]`；实现集中到 `execute_with_context`；spawn_fn 调用增加 kwargs；payload 增加 `spawn_id` | Task 4 |
| `tests/matmaster/tools/builtin/test_agent_tool.py` | 更新 fake_spawn 签名；新增 parent_call_id / payload spawn_id 断言 | Task 4 |
| `matmaster/integration/event_payloads.py` | `_public_content_for_event` 增加 `subagent_spawn` 映射 | Task 5 |
| `tests/matmaster/integration/test_event_payloads.py` | 新增映射与顶层 spawn_id 测试 | Task 5 |
| `src/services/stream_sse_filter.py` | **无改动**（`REPLAY_DISCARDED_EVENT_TYPES` 不收录该类型） | Task 6 |
| `tests/test_chat_stream_subscribe_replay.py` | 新增回放守卫测试 | Task 6 |

无需改动、已核实自动成立的部分（spec §3.4）：

- `SSEHandler._should_skip` 与 `PersistenceHandler._SKIP_TYPES` 均为黑名单，新事件自动推送/落库，顶层与 DB 行自动带 spawn_id。
- `src/services/chat_history.py:130` `exclude_spawn_events` 按 `spawn_id is not None` 排除，绑定事件自动不进父 LLM 对话历史。
- `tool_runner.py` 已注入 `exec_ctx.tool_call_id`（`matmaster/core/tool_runner.py:383`），不动。
- `SUBAGENT_START/STOP` hook 原样保留。

---

### Task 1: 新事件类型 SubagentSpawnEvent

**Files:**
- Modify: `matmaster/types/events.py`
- Test: `tests/matmaster/types/test_events.py`

- [ ] **Step 1.1: 写失败测试**

在 `tests/matmaster/types/test_events.py` 顶部 import 块中加入 `SubagentSpawnEvent`（按字母序排在 `StreamClosedEvent` 与 `SystemEvent` 之间）：

```python
from matmaster.types.events import (
    ...
    StreamClosedEvent,
    SubagentSpawnEvent,
    SystemEvent,
    ...
)
```

在 `class TestSystemEvents`（约 344 行）之后新增测试类：

```python
class TestSubagentSpawnEvent:
    def test_instantiation(self) -> None:
        evt = SubagentSpawnEvent(
            source="MatMaster:direct",
            spawn_id="ab12cd34ef56ab12",
            parent_call_id="call_1",
            exp_name="direct",
            task_summary="trace parser flow",
        )
        assert evt.type == "subagent_spawn"
        assert evt.spawn_id == "ab12cd34ef56ab12"
        assert evt.parent_call_id == "call_1"
        assert evt.exp_name == "direct"
        assert evt.task_summary == "trace parser flow"

    def test_defaults_allow_missing_binding(self) -> None:
        evt = SubagentSpawnEvent(source="MatMaster:direct", exp_name="direct")
        assert evt.parent_call_id is None
        assert evt.task_summary == ""
        assert evt.spawn_id is None
```

`TestSystemEventDiscriminator.test_all_system_types`：在 `payloads` 列表末尾（`response_figures` 之后）追加：

```python
            {"type": "subagent_spawn", "source": "s", "exp_name": "e"},
```

在 `expected_types` 列表末尾（`ResponseFiguresEvent` 之后）追加：

```python
            SubagentSpawnEvent,
```

`TestBusEventUnion.test_validates_all_21_types`：改名为 `test_validates_all_23_types`，docstring 改为 `"""BusEvent union can validate all 23 event types."""`；注释 `# 9 AgentEvent types` 改为 `# 10 AgentEvent types` 并在 agent 段（`tool_progress` 条目之后）补上当前缺失的 checkpoint：

```python
            {"type": "checkpoint", "source": "a"},
```

注释 `# 12 SystemEvent types` 改为 `# 13 SystemEvent types`，并在列表末尾追加：

```python
            {"type": "subagent_spawn", "source": "s", "exp_name": "e"},
```

`TestEventSerializationRoundtrip.test_roundtrip_all_types`：在 `events` 列表末尾追加一个实例：

```python
            SubagentSpawnEvent(
                source="MatMaster:direct",
                spawn_id="ab12cd34ef56ab12",
                parent_call_id="call_1",
                exp_name="direct",
            ),
```

- [ ] **Step 1.2: 运行测试确认失败**

Run: `uv run pytest tests/matmaster/types/test_events.py -q`
Expected: FAIL，`ImportError: cannot import name 'SubagentSpawnEvent'`

- [ ] **Step 1.3: 实现事件类型**

`matmaster/types/events.py`：

模块 docstring（第 3-5 行）更新计数：

```python
Defines all 23 event types in two categories:
- AgentEvent (10 types): emitted by the kernel during agent execution
- SystemEvent (13 types): emitted by service-layer components
```

在 `ResponseFiguresEvent` 类定义之后、`# ── Union definitions` 之前新增：

```python
class SubagentSpawnEvent(EventBase):
    """Spawn 绑定事件：宣告 spawn_id 与父 Agent 工具调用的对应关系。"""

    type: Literal["subagent_spawn"] = "subagent_spawn"
    parent_call_id: str | None = None
    exp_name: str
    task_summary: str = ""
```

`SystemEvent` union 的 `ResponseFiguresEvent,` 之后追加 `SubagentSpawnEvent,`；`BusEvent` union 的 SystemEvent 段同样在 `ResponseFiguresEvent,` 之后追加 `SubagentSpawnEvent,`。

- [ ] **Step 1.4: 运行测试确认通过**

Run: `uv run pytest tests/matmaster/types/test_events.py -q`
Expected: PASS

- [ ] **Step 1.5: Commit**

```bash
git add matmaster/types/events.py tests/matmaster/types/test_events.py
git commit -m "feat(events): add SubagentSpawnEvent binding event type"
```

---

### Task 2: drain_run_stream 终止事件转发开关（spec 偏差修正）

**Files:**
- Modify: `matmaster/core/stream_drain.py`
- Test: `tests/matmaster/core/test_stream_drain.py`

背景约束：既有测试 `test_drain_run_stream_does_not_forward_terminal_run_result` 锁定默认不转发终止事件——保留它（默认行为不变），新行为走显式参数。

- [ ] **Step 2.1: 写失败测试**

在 `tests/matmaster/core/test_stream_drain.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_drain_run_stream_forward_terminal_forwards_run_result_last() -> None:
    seen: list[str] = []

    async def stream():
        yield ResponseEvent(source="agent", content="child")
        yield RunResultEvent(
            source="agent",
            status="completed",
            reason="natural",
            final_content="done",
        )

    async def on_event(event) -> None:
        seen.append(type(event).__name__)

    result = await drain_run_stream(stream(), on_event=on_event, forward_terminal=True)

    assert seen == ["ResponseEvent", "RunResultEvent"]
    assert result.final_content == "done"
```

- [ ] **Step 2.2: 运行测试确认失败**

Run: `uv run pytest tests/matmaster/core/test_stream_drain.py -q`
Expected: FAIL，`TypeError: drain_run_stream() got an unexpected keyword argument 'forward_terminal'`

- [ ] **Step 2.3: 实现**

`matmaster/core/stream_drain.py` 中 `drain_run_stream` 改为：

```python
async def drain_run_stream(
    stream: AsyncIterator[Any],
    on_event: Callable[[Any], Any] | None = None,
    *,
    forward_terminal: bool = False,
) -> DrainResult:
    """Consume run_stream() to completion, return structured result.

    Args:
        stream: AsyncIterator from kernel.run_stream() or Exp.run_stream().
        on_event: Optional callback invoked for each intermediate event
            as it arrives. Use this for real-time forwarding (e.g. DevShell
            terminal output, event logging) without breaking the drain.
        forward_terminal: When True, the terminal RunResultEvent is also
            passed to ``on_event`` before returning, so sinks that mirror
            the stream (subagent fanout) receive the closing signal.

    Collects all intermediate events and extracts terminal RunResultEvent.
    Raises RuntimeError if stream ends without a terminal event.
    """
    from matmaster.types.events import RunResultEvent

    events: list[Any] = []
    async for event in stream:
        if isinstance(event, RunResultEvent):
            if forward_terminal and on_event is not None:
                result = on_event(event)
                if inspect.isawaitable(result):
                    await result
            return DrainResult(
                status=event.status,
                reason=event.reason,
                final_content=event.final_content,
                num_turns=event.num_turns,
                usage=event.usage,
                usage_vendor_by_turn=tuple(
                    dict(item) for item in (event.usage_vendor_by_turn or [])
                ),
                messages=event.messages,
                finish_detail=event.finish_detail,
                events=events,
            )
        events.append(event)
        if on_event is not None:
            result = on_event(event)
            if inspect.isawaitable(result):
                await result
    raise RuntimeError("run_stream ended without RunResultEvent")
```

- [ ] **Step 2.4: 运行测试确认通过（含既有默认行为测试）**

Run: `uv run pytest tests/matmaster/core/test_stream_drain.py -q`
Expected: PASS（全部，含 `test_drain_run_stream_does_not_forward_terminal_run_result`）

- [ ] **Step 2.5: Commit**

```bash
git add matmaster/core/stream_drain.py tests/matmaster/core/test_stream_drain.py
git commit -m "feat(drain): opt-in terminal run_result forwarding"
```

---

### Task 3: DrainResult.spawn_id + orchestrator 绑定事件

**Files:**
- Modify: `matmaster/types/stream_drain.py`
- Modify: `matmaster/core/subagent_orchestrator.py`
- Test: `tests/matmaster/core/test_hook_wiring.py`

- [ ] **Step 3.1: 确认 DrainResult 无位置参数构造点（字段追加安全性）**

Run: `grep -rn -A1 "DrainResult(" matmaster src evaluation tests --include="*.py" | grep -A1 "DrainResult($"`
Expected: 每个构造点的下一行都是 `status=...` 关键字传参（写计划时已核实全部 7 处构造点均为关键字传参：stream_drain.py 1 处、test_agent_tool.py 3 处、test_runner.py 1 处、test_repl.py 2 处），字段追加到末尾安全。若发现新增的位置传参构造点，先改关键字传参再继续。

- [ ] **Step 3.2: 写失败测试**

`tests/matmaster/core/test_hook_wiring.py` 改动如下。

(a) 四处 drain 替身签名统一加 `forward_terminal` 参数。把每一处

```python
        async def fake_drain_run_stream(_stream, on_event=None):
```

改为

```python
        async def fake_drain_run_stream(_stream, on_event=None, *, forward_terminal=False):
```

（涉及 `test_orchestrator_emits_subagent_start_and_stop`、`test_orchestrator_forwards_child_events_with_source_and_spawn_id`、`test_orchestrator_uses_child_event_sink`、`test_orchestrator_without_event_sink_still_returns_child_summary` 四个测试。）

(b) `test_orchestrator_forwards_child_events_with_source_and_spawn_id` 末尾断言改为（绑定事件 + 2 条子事件）：

```python
        assert result.final_content == "child done"
        assert result.usage == {"prompt_tokens": 3}
        assert len(forwarded) == 3
        assert forwarded[0].type == "subagent_spawn"
        assert {event.source for event in forwarded} == {"MatMaster:direct"}
        assert all(event.spawn_id for event in forwarded)
        assert len({event.spawn_id for event in forwarded}) == 1
```

(c) `test_orchestrator_uses_child_event_sink` 末尾断言改为：

```python
        assert result.final_content == "child done"
        assert result.usage == {"prompt_tokens": 3}
        assert len(forwarded) == 2
        assert forwarded[0].type == "subagent_spawn"
        assert forwarded[1].source == "MatMaster:direct"
        assert forwarded[1].spawn_id
```

(d) `test_orchestrator_with_real_factory_threads_spawn_id_end_to_end`（真实 drain 路径，将看到绑定 + response + 终止 run_result）末尾断言改为：

```python
        assert result.final_content == "child done"
        assert result.status == "completed"
        assert received["allow_spawn"] is False
        assert received["spawn_id"]
        assert result.spawn_id == received["spawn_id"]
        assert [event.type for event in forwarded] == [
            "subagent_spawn",
            "response",
            "run_result",
        ]
        assert {event.source for event in forwarded} == {"MatMaster:direct"}
        assert {event.spawn_id for event in forwarded} == {received["spawn_id"]}
```

(e) 在 `TestExpWiring` 类内新增绑定事件行为测试（放在 `test_orchestrator_emits_subagent_start_and_stop` 之后）：

```python
    @pytest.mark.asyncio
    async def test_spawn_emits_binding_event_before_child_events(self) -> None:
        forwarded = []

        async def sink(event) -> None:
            forwarded.append(event)

        async def fake_drain_run_stream(_stream, on_event=None, *, forward_terminal=False):
            if on_event is not None:
                await on_event(ResponseEvent(source="agent", content="child answer"))
            return SimpleNamespace(
                status="completed",
                final_content="child done",
                reason="natural",
                usage={},
                num_turns=1,
                messages=[],
            )

        orchestrator = SubagentOrchestrator(
            child_run_factory=_stub_child_run_factory,
            child_event_sink=sink,
            parent_session_id="session-1",
        )
        with patch(
            "matmaster.core.stream_drain.drain_run_stream",
            side_effect=fake_drain_run_stream,
        ):
            result = await orchestrator.make_spawn_fn()(
                "direct",
                "summarize this task",
                parent_call_id="call_42",
                task_summary="summarize",
            )

        binding = forwarded[0]
        assert binding.type == "subagent_spawn"
        assert binding.parent_call_id == "call_42"
        assert binding.exp_name == "direct"
        assert binding.task_summary == "summarize"
        assert binding.source == "MatMaster:direct"
        assert binding.spawn_id
        assert forwarded[1].spawn_id == binding.spawn_id
        assert result.spawn_id == binding.spawn_id
```

- [ ] **Step 3.3: 运行测试确认失败**

Run: `uv run pytest tests/matmaster/core/test_hook_wiring.py -q`
Expected: FAIL（`unexpected keyword argument 'parent_call_id'`、forwarded 计数不符等）

- [ ] **Step 3.4: 实现**

`matmaster/types/stream_drain.py` 的 `DrainResult` 末尾追加字段（保持在 `events` 之后，避免扰动位置参数顺序）：

```python
@dataclass
class DrainResult:
    """Structured terminal result from draining a run_stream() to completion."""

    status: str
    reason: str
    final_content: str | None
    num_turns: int
    usage: dict[str, int]
    messages: list[Any]
    usage_vendor_by_turn: tuple[dict[str, Any], ...] = ()
    finish_detail: FinishDetail | None = None
    events: list[Any] = field(default_factory=list)
    spawn_id: str | None = None
```

`matmaster/core/subagent_orchestrator.py`：

模块 docstring 职责清单（`* mint a per-child ...` 列表）在 mint 条目后增加一行：

```python
  * announce the binding via a public ``SubagentSpawnEvent`` before any
    child event reaches the sink
```

`make_spawn_fn` 改为：

```python
    def make_spawn_fn(self) -> Callable[..., Awaitable[DrainResult]]:
        """Return the ``spawn_fn`` closure AgentTool forwards LLM calls to."""

        async def spawn_fn(
            exp_name: str,
            task: str,
            cancel_token: CancellationToken | None = None,
            *,
            parent_call_id: str | None = None,
            task_summary: str = "",
        ) -> DrainResult:
            return await self.spawn(
                exp_name,
                task,
                cancel_token=cancel_token,
                parent_call_id=parent_call_id,
                task_summary=task_summary,
            )

        return spawn_fn
```

`spawn` 改为（`_forward_child_event` 内部逻辑不变；绑定事件构造时直接带 source/spawn_id，复用 `_forward_child_event` 既有 try/except——发送失败只告警不中断 spawn）：

```python
    async def spawn(
        self,
        exp_name: str,
        task: str,
        *,
        cancel_token: CancellationToken | None = None,
        parent_call_id: str | None = None,
        task_summary: str = "",
    ) -> DrainResult:
        """Run one child agent and return its drained terminal result."""
        from matmaster.core.stream_drain import drain_run_stream
        from matmaster.types.events import SubagentSpawnEvent

        child_source = f"{self._source_prefix}:{exp_name}"
        spawn_id = uuid.uuid4().hex[:16]

        async def _forward_child_event(event: Any) -> None:
            sink = self._child_event_sink
            if sink is None:
                return
            try:
                forwarded = event.model_copy(
                    update={"source": child_source, "spawn_id": spawn_id}
                )
                result = sink(forwarded)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.warning(
                    "subagent event forwarding failed type=%s spawn_id=%s",
                    getattr(event, "type", "?"),
                    spawn_id,
                    exc_info=True,
                )

        await self._emit(HookEvent.SUBAGENT_START, spawn_id, exp_name, task)
        # 绑定事件必须先于该 spawn 的任何子事件进入 fanout。
        await _forward_child_event(
            SubagentSpawnEvent(
                source=child_source,
                spawn_id=spawn_id,
                parent_call_id=parent_call_id,
                exp_name=exp_name,
                task_summary=task_summary,
            )
        )
        try:
            result = await drain_run_stream(
                self._child_run_factory(
                    exp_name, task, cancel_token=cancel_token, spawn_id=spawn_id
                ),
                on_event=_forward_child_event,
                forward_terminal=True,
            )
            result.spawn_id = spawn_id
            return result
        finally:
            await self._emit(HookEvent.SUBAGENT_STOP, spawn_id, exp_name, task)
```

- [ ] **Step 3.5: 运行测试确认通过**

Run: `uv run pytest tests/matmaster/core/test_hook_wiring.py tests/matmaster/core/test_stream_drain.py -q`
Expected: PASS

- [ ] **Step 3.6: Commit**

```bash
git add matmaster/types/stream_drain.py matmaster/core/subagent_orchestrator.py tests/matmaster/core/test_hook_wiring.py
git commit -m "feat(orchestrator): emit subagent_spawn binding event and return spawn_id"
```

---

### Task 4: AgentTool 传 parent_call_id、payload 回传 spawn_id

**Files:**
- Modify: `matmaster/tools/builtin/agent_tool.py`
- Test: `tests/matmaster/tools/builtin/test_agent_tool.py`

- [ ] **Step 4.1: 写失败测试**

`tests/matmaster/tools/builtin/test_agent_tool.py`：

(a) 顶部 import 增加：

```python
from matmaster.types.tool_spec import ToolExecutionContext
```

(b) 三个既有 fake_spawn 替身（`test_execute_returns_tool_result_payload`、`test_execute_maps_completed_drain_result_to_tool_result_payload`、`test_execute_maps_noncompleted_drain_result_to_status_content`）签名统一改为：

```python
        async def fake_spawn(
            exp_name, task, cancel_token=None, *, parent_call_id=None, task_summary=""
        ):
```

(c) `test_execute_maps_completed_drain_result_to_tool_result_payload` 的 DrainResult 增加 `spawn_id="feedface00000001"`，断言区追加：

```python
        assert result.payload["spawn_id"] == "feedface00000001"
```

(d) 新增两个测试（追加到 `TestAgentValidation` 类末尾）：

```python
    def test_execute_with_context_passes_tool_call_id_as_parent_call_id(self):
        captured: dict[str, object] = {}

        async def fake_spawn(
            exp_name, task, cancel_token=None, *, parent_call_id=None, task_summary=""
        ):
            captured["parent_call_id"] = parent_call_id
            captured["task_summary"] = task_summary
            return DrainResult(
                status="completed",
                reason="natural",
                final_content="ok",
                num_turns=1,
                usage={},
                messages=[],
                spawn_id="feedface00000001",
            )

        tool = AgentTool(spawn_fn=fake_spawn, available_exps=[_meta()])
        result = asyncio.run(
            tool.execute_with_context(
                {
                    "exp_name": "explore",
                    "task_summary": "trace parser flow",
                    "prompt": "Inspect the parser stack.",
                },
                ToolExecutionContext(tool_call_id="call_7"),
            )
        )

        assert captured["parent_call_id"] == "call_7"
        assert captured["task_summary"] == "trace parser flow"
        assert isinstance(result, ToolResult)
        assert result.payload["spawn_id"] == "feedface00000001"

    def test_execute_without_exec_ctx_passes_none_parent_call_id(self):
        captured: dict[str, object] = {}

        async def fake_spawn(
            exp_name, task, cancel_token=None, *, parent_call_id=None, task_summary=""
        ):
            captured["parent_call_id"] = "unset"
            captured["parent_call_id"] = parent_call_id
            return DrainResult(
                status="completed",
                reason="natural",
                final_content="ok",
                num_turns=1,
                usage={},
                messages=[],
            )

        tool = AgentTool(spawn_fn=fake_spawn, available_exps=[_meta()])
        result = asyncio.run(
            tool.execute(
                {
                    "exp_name": "explore",
                    "prompt": "Inspect the parser stack.",
                }
            )
        )

        assert captured["parent_call_id"] is None
        assert isinstance(result, ToolResult)
        assert result.payload["spawn_id"] is None
```

- [ ] **Step 4.2: 运行测试确认失败**

Run: `uv run pytest tests/matmaster/tools/builtin/test_agent_tool.py -q`
Expected: FAIL（spawn_fn 未收到 `parent_call_id` kwarg / payload 无 `spawn_id` 键）

- [ ] **Step 4.3: 实现**

`matmaster/tools/builtin/agent_tool.py`：

(a) 删除现在未使用的 import `from matmaster.types.cancellation import CancellationToken`，类型别名改为：

```python
SpawnFn = Callable[..., Awaitable[DrainResult]]
```

(b) `execute` / `execute_with_context` / `_execute` 三个方法整体替换为（实现集中到 `execute_with_context`，`execute` 仅委托）：

```python
    async def execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        return await self.execute_with_context(arguments, None)

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> str | ToolResult:
        if self._spawn_fn is None:
            return "Error: Agent is not available in this context"

        normalized, error = self._normalize_arguments(arguments)
        if error:
            return f"Error: {error}"

        assert normalized is not None
        drain = await self._spawn_fn(
            normalized["exp_name"],
            normalized["prompt"],
            self._cancel_token_for_exec(),
            parent_call_id=exec_ctx.tool_call_id if exec_ctx is not None else None,
            task_summary=normalized["task_summary"],
        )
        content = (
            drain.final_content
            if drain.status == "completed" and drain.final_content
            else f"SubAgent finished with status={drain.status}, reason={drain.reason}"
        )
        return ToolResult(
            status="success",
            content=content,
            payload={
                "exp_name": normalized["exp_name"],
                "task_summary": normalized["task_summary"],
                "prompt": normalized["prompt"],
                "spawn_id": drain.spawn_id,
                "subagent_usage": dict(drain.usage or {}),
                "subagent_status": drain.status,
                "subagent_reason": drain.reason,
                "subagent_num_turns": drain.num_turns,
            },
        )

    def _execute(self, arguments: dict[str, Any]) -> str:
        raise NotImplementedError("AgentTool uses async execute() directly")
```

- [ ] **Step 4.4: 运行测试确认通过**

Run: `uv run pytest tests/matmaster/tools/builtin/test_agent_tool.py -q`
Expected: PASS

- [ ] **Step 4.5: Commit**

```bash
git add matmaster/tools/builtin/agent_tool.py tests/matmaster/tools/builtin/test_agent_tool.py
git commit -m "feat(agent-tool): thread parent_call_id into spawn and return spawn_id in payload"
```

---

### Task 5: event_payloads 的 subagent_spawn content 映射

**Files:**
- Modify: `matmaster/integration/event_payloads.py`
- Test: `tests/matmaster/integration/test_event_payloads.py`

说明：该映射同时服务 live SSE（`SSEHandler._emit_event`）与持久化（`PersistenceHandler.handle` 用同一函数取 content），保证线上形态与回放同构；不加映射则会落到兜底分支并打 warning。注意此文件使用单引号风格。

- [ ] **Step 5.1: 写失败测试**

`tests/matmaster/integration/test_event_payloads.py` 末尾追加：

```python
class TestSubagentSpawnContent:
    def test_subagent_spawn_maps_binding_fields(self) -> None:
        payload = {
            'type': 'subagent_spawn',
            'source': 'MatMaster:direct',
            'spawn_id': 'ab12cd34ef56ab12',
            'parent_call_id': 'call_x',
            'exp_name': 'direct',
            'task_summary': 'summarize logs',
        }

        assert _public_content_for_event('subagent_spawn', payload) == {
            'parent_call_id': 'call_x',
            'exp_name': 'direct',
            'task_summary': 'summarize logs',
        }

    def test_build_public_sse_payload_carries_top_level_spawn_id(self) -> None:
        from matmaster.types.events import SubagentSpawnEvent

        event = SubagentSpawnEvent(
            source='MatMaster:direct',
            spawn_id='ab12cd34ef56ab12',
            parent_call_id='call_x',
            exp_name='direct',
            task_summary='summarize logs',
        )

        out = build_public_sse_payload_from_bus_dump(
            event.model_dump(mode='json'),
            session_id='sess-1',
            task_id='task-1',
            invocation_id='inv-1',
            spawn_id=event.spawn_id,
        )

        assert out['type'] == 'subagent_spawn'
        assert out['source'] == 'MatMaster:direct'
        assert out['spawn_id'] == 'ab12cd34ef56ab12'
        assert out['content'] == {
            'parent_call_id': 'call_x',
            'exp_name': 'direct',
            'task_summary': 'summarize logs',
        }
```

- [ ] **Step 5.2: 运行测试确认失败**

Run: `uv run pytest tests/matmaster/integration/test_event_payloads.py -q`
Expected: FAIL（无映射时 content 走兜底分支，包含 spawn_id 等额外键，与期望 dict 不等）

- [ ] **Step 5.3: 实现**

`matmaster/integration/event_payloads.py` 的 `_public_content_for_event` 中，`if event_type == 'exp_run':` 分支之后、`raw_content = payload.get('content')` 兜底之前插入：

```python
    if event_type == 'subagent_spawn':
        return {
            'parent_call_id': payload.get('parent_call_id'),
            'exp_name': payload.get('exp_name'),
            'task_summary': payload.get('task_summary', ''),
        }
```

- [ ] **Step 5.4: 运行测试确认通过**

Run: `uv run pytest tests/matmaster/integration/test_event_payloads.py -q`
Expected: PASS

- [ ] **Step 5.5: Commit**

```bash
git add matmaster/integration/event_payloads.py tests/matmaster/integration/test_event_payloads.py
git commit -m "feat(sse): map subagent_spawn binding fields into public content"
```

---

### Task 6: 回放守卫——subagent_spawn 不被回放丢弃

**Files:**
- Modify: `src/services/stream_sse_filter.py` —— **无代码改动**，本任务只加守卫测试，防止未来有人把该类型加进丢弃集合
- Test: `tests/test_chat_stream_subscribe_replay.py`

- [ ] **Step 6.1: 写守卫测试（应直接通过——这是回归守卫，不是新行为）**

`tests/test_chat_stream_subscribe_replay.py` 末尾追加：

```python
def test_generate_subscribe_stream_replays_subagent_spawn_binding():
    from src.services.stream_service import ChatStreamService

    assert 'subagent_spawn' not in REPLAY_DISCARDED_EVENT_TYPES

    sessions_service = MagicMock()
    sessions_service.get_session_status_payload.return_value = {
        'source': 'System',
        'type': 'status',
        'content': '',
        'session_id': 'sess-1',
        'status': 'idle',
    }
    sessions_service.is_session_running_on_this_pod.return_value = False
    sessions_service.is_session_run_on_another_pod.return_value = False

    events_service = MagicMock()
    events_service.get_session_events.return_value = [
        {
            'source': 'MatMaster:direct',
            'type': 'subagent_spawn',
            'content': {
                'parent_call_id': 'call_x',
                'exp_name': 'direct',
                'task_summary': 'summarize logs',
            },
            'session_id': 'sess-1',
            'task_id': 'task-0',
            'spawn_id': 'ab12cd34ef56ab12',
        }
    ]

    service = ChatStreamService(
        sessions_service=sessions_service,
        events_service=events_service,
        deploy_state_service=MagicMock(),
    )

    async def _collect_frames() -> list[dict]:
        frames = []
        gen = service.generate_subscribe_stream('sess-1')
        try:
            async for frame in gen:
                frames.append(_decode_sse_payload(frame))
        finally:
            await gen.aclose()
        return frames

    with patch('src.services.stream_service.REDIS_URL', None):
        frames = asyncio.run(_collect_frames())

    binding_frames = [f for f in frames if f['type'] == 'subagent_spawn']
    assert len(binding_frames) == 1
    assert binding_frames[0]['spawn_id'] == 'ab12cd34ef56ab12'
    assert binding_frames[0]['source'] == 'MatMaster:direct'
    assert binding_frames[0]['content']['parent_call_id'] == 'call_x'
```

- [ ] **Step 6.2: 运行测试确认通过**

Run: `uv run pytest tests/test_chat_stream_subscribe_replay.py -q`
Expected: PASS（若 FAIL，说明回放链路对未知类型有隐藏过滤，须查 `_should_emit_event_to_sse` 与 `_normalize_replayed_event` 后修复再过）

- [ ] **Step 6.3: Commit**

```bash
git add tests/test_chat_stream_subscribe_replay.py
git commit -m "test(replay): guard subagent_spawn against replay discard"
```

---

### Task 7: 全量回归 + 人工验证

- [ ] **Step 7.1: 全量测试**

Run: `uv run pytest tests/ -q`
Expected: 全部 PASS。重点关注既有受影响面：`tests/matmaster/core/`、`tests/matmaster/tools/builtin/`、`tests/matmaster/integration/`、`tests/matmaster/services/test_agent_run_stream.py`、`tests/test_chat_stream_*.py`。

- [ ] **Step 7.2: 人工验证（spec §3.6，需要人操作，结果记录在 PR 描述）**

1. `mm-devshell` 进入调试终端，发起一个会触发并行双 spawn 的任务（提示词中明确要求同时委派两个 subagent）。
2. 检查事件日志序列：每个 spawn 的 `subagent_spawn` 先于该 spawn_id 的任何子事件出现；两个 spawn 的 `parent_call_id` 各自对应不同的父 `tool_call.call_id`。
3. 每条子流以 `run_result`（带相同 spawn_id）收口。
4. 父 `tool_result` 的 payload 中 `spawn_id` 与对应绑定事件一致。
5. （如有完整后端环境）检查 `evo_chat_events` 表：`subagent_spawn` 行存在且 `spawn_id` 列正确；前端订阅回放包含绑定事件。

- [ ] **Step 7.3: 收尾**

确认工作树只含计划内代码改动（`git status`；`docs/` 下的 spec 与本计划不入库），然后走 finishing-a-development-branch 流程（merge / PR 由用户决定，PR 目标分支 `test`）。

---

## 自检记录（写计划时已核对）

- spec §3.3 改动清单七行全部映射到 Task 1-6；§3.4 四项"自动成立"已逐一在代码核实（黑名单过滤、exclude_spawn_events、tool_call_id 注入、hook 保留），无需任务。
- spec §5 "明确不做"逐项核对：未新增 stop 事件（forward_terminal 转发的是既有 run_result 实例）；未动 DB schema；未动 kernel 与 tool_runner；未在子事件上附加 parent_call_id。
- 类型一致性：`spawn_fn(exp_name, task, cancel_token, *, parent_call_id, task_summary)` 在 orchestrator 闭包、AgentTool 调用、全部测试替身间一致；`DrainResult.spawn_id`、`forward_terminal` 命名全计划一致。
- 已知连带影响均有显式步骤：test_hook_wiring 四个 drain 替身、三个 forwarded 计数断言、agent_tool 三个 fake_spawn 替身。
