# AgentKernel Generator-First 改造设计

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 AgentKernel 主循环改造为 AsyncGenerator，统一事件生产路径，为 Tool Runtime v2 铺路

**Architecture:** 三层接口设计 — `_run_items()` 私有 generator 产出内核流项，`run_stream()` 映射为公开 BusEvent，`run()` 收集 messages 返回 KernelRunResult。同时抽出 ToolRunner Protocol + InlineToolRunner 过渡实现，为 Tool Runtime v2 的 ToolRunner/ToolScheduler 预留接口。

**Tech Stack:** Python 3.10+ / asyncio / Pydantic v2 / pytest + pytest-asyncio

**日期:** 2026-04-02

**状态:** 草案（基于 Claude + GPT 双重评审后的共识方案）

**前置文档:** `docs/specs/2026-04-02-tool-runtime-v2.md`（Tool Runtime v2 架构设计）

---

## 1. 设计目标

在保留 `Playground → Exp → AgentKernel` 三层架构的前提下：

- 让 AgentKernel 主循环具备 generator-first 事件流能力
- 保持现有 `run()` 接口完全兼容，不影响任何现有调用方和测试
- 抽出 ToolRunner Protocol，将工具执行链从 Kernel 分离
- 为 Tool Runtime v2 的 ToolCatalog/ToolScheduler/CapabilityPolicy 预留 AgentRuntimeSpec 字段
- 复用现有 `events.py` 事件类型，不引入平行事件层

---

## 2. 背景与问题

### 2.1 当前架构

AgentKernel 是纯 async 执行循环（`matmaster/core/agent.py`），内部已经是 async stream（LLM provider 的 `chat_stream()`），但事件传递依赖 Hook → MessageBus → EventRouter 间接链路：

```
Kernel._run_loop()
  → Hook.on_stream_chunk()           # 观察型 Hook
    → EventEmitterHook.on_stream_chunk()  # 翻译为 BusEvent
      → MessageBus.emit()                 # asyncio.Queue
        → EventRouter._consume_loop()     # asyncio.Task 消费
          → SSEHandler.handle()           # 前端推送
          → PersistenceHandler.handle()   # 数据库持久化
```

同时，Kernel 内部维护 `messages` 列表并最终返回 `KernelRunResult`（含完整 transcript）。事件广播和消息转录是两条并行轨道。

### 2.2 目标架构

Kernel 主循环变成 generator，直接产出事件。事件流成为主通路：

```
Kernel._run_items()          ← 私有 generator，产出 _KernelItem
  │
  ├→ run_stream()            ← 公开接口，yield BusEvent（复用 events.py）
  │    └→ Service/Exp 消费
  │
  └→ run()                   ← 兼容接口，收集 messages + terminal → KernelRunResult
       └→ 现有所有调用方不变
```

### 2.3 与 Tool Runtime v2 的协同

Tool Runtime v2（`docs/specs/2026-04-02-tool-runtime-v2.md`）将引入 ToolRunner 从 Kernel 抽出工具执行链。本次改造同步完成：

- 定义 `ToolRunner` Protocol
- 实现 `InlineToolRunner`（包装当前 agent.py L217-311 的 guard → execute → post-hook 逻辑）
- 在 `AgentRuntimeSpec` 预留 `tool_runner`、`tool_catalog`、`runtime_topology`、`capability_policy` 字段
- Kernel 通过 ToolRunner 执行工具，不再内联 guard/gather/append 逻辑

---

## 3. 非目标

- 不改动 `Exp.run()` 签名（Phase 2）
- 不改动 `AgentRunService.run_agent()`（Phase 2）
- 不移除 MessageBus / EventRouter / EventEmitterHook（Phase 3+）
- 不改动 Hook Protocol 接口
- 不新建事件类型（复用 `events.py` 现有类型）
- 不改动取消机制（继续使用 `threading.Event`）

---

## 4. 核心设计

### 4.1 三层接口

#### Layer 0: `_run_items()` — 内核私有 generator

产出 `_KernelItem`，承载三类信息：

```python
@dataclass
class _TerminalItem:
    """内核终止结果，仅 run() 消费。"""
    status: str          # completed / cancelled / failed
    reason: str          # natural / max_turns / cancelled / hook_stopped / invalid_finish
    final_content: str | None
    num_turns: int
    stop_reason: str | None
    usage: dict[str, int]

@dataclass
class _KernelItem:
    """内核私有流项。不公开。"""
    event: BusEvent | None = None             # 可映射为公开事件的项
    messages_delta: list[Message] | None = None   # messages 追加
    terminal: _TerminalItem | None = None     # 终止项（最后一个 yield）
```

设计原则：
- `event` 复用 `matmaster/types/events.py` 中已有的 `ThoughtEvent`、`ResponseEvent`、`ToolCallEvent`、`ToolResultEvent`、`RunResultEvent`
- `messages_delta` 让 `run()` 能增量追加 messages，不需要从事件反推 transcript
- `terminal` 携带内核私有状态（usage、stop_reason 等），不暴露到公开事件

#### Layer 1: `run_stream()` — 公开 generator 接口

```python
async def run_stream(
    self,
    spec: AgentRuntimeSpec,
    task: str,
    history: list[Message] | None = None,
    stop_event: threading.Event | None = None,
    *,
    source: str = "kernel",
    spawn_id: str | None = None,
) -> AsyncIterator[BusEvent]:
```

职责：
- 消费 `_run_items()`，过滤出 `item.event`，逐个 yield
- 在 terminal 时 yield 最后一个 `RunResultEvent`（不含 messages）
- 忽略 `messages_delta`（run_stream 消费者不需要 transcript）

yield 的事件类型（全部复用 `events.py`）：

| 事件类型 | Phase 1 产出时机 | Phase 2 产出时机 |
|----------|-----------------|-----------------|
| `ThoughtEvent` | LLM 调用返回后的 final completed snapshot | 流式 chunk + segment complete |
| `ResponseEvent` | LLM 调用返回后的 final completed snapshot | 流式 chunk + segment complete |
| `ToolCallEvent` | LLM 请求调用工具（ToolRunner 执行前） | 同 Phase 1 |
| `ToolResultEvent` | 工具执行完成（ToolRunner 返回后） | 同 Phase 1 |
| `RunResultEvent` | 终止（最后一个 yield） | 同 Phase 1 |

#### Layer 2: `run()` — 兼容接口

```python
async def run(
    self,
    spec: AgentRuntimeSpec,
    task: str,
    history: list[Message] | None = None,
    stop_event: threading.Event | None = None,
) -> KernelRunResult:
```

职责：
- 消费 `_run_items()`，收集 `messages_delta`，拿 `terminal`
- 构造 `KernelRunResult(result=KernelResult(...), messages=messages)`
- **行为与当前 `run()` 完全一致**，所有现有调用方和测试不受影响

关键：`run()` 和 `run_stream()` 消费同一个 `_run_items()`，不会出现行为分叉。`_run_items()` 是唯一的执行路径。

### 4.2 _run_items() 内部状态

`_run_items()` 使用一个局部 `_KernelState` 对象管理循环状态（不挂在 `self` 上，保持 Kernel 无状态/并发安全）：

```python
@dataclass
class _KernelState:
    """内核循环局部状态。每次 _run_items() 调用独立。"""
    messages: list[Message]
    turn: int = 0
    total_usage: dict[str, int] = field(default_factory=dict)
    last_stop_reason: str | None = None
    # Tool Runtime v2 预留
    last_catalog_version: int | None = None
    cached_tool_definitions: list[dict[str, Any]] | None = None
```

### 4.3 ToolRunner Protocol

从 `agent.py` L217-311 的 Phase 1/2/3 逻辑提取为独立接口：

```python
# matmaster/core/tool_runner.py

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

@dataclass(frozen=True)
class ToolExecutionContext:
    """Per-batch execution context passed explicitly to execute_batch().

    Avoids mutable side channels (like set_turn()) that would make
    ToolRunner non-reentrant. All state needed for guard evaluation,
    scheduling, and cancellation is captured here.

    Phase 2+: extend with runtime_topology, capability_policy,
    structural_validation as they become available.
    """
    turn: int
    max_turns: int
    stop_event: threading.Event | None = None  # Phase 1: InlineToolRunner 不使用；Phase 2: ToolRunner 可按 stop_mode 使用
    # Phase 2+ (None in Phase 1):
    # runtime_topology: RuntimeTopology | None = None
    # capability_policy: CapabilityPolicy | None = None


@runtime_checkable
class ToolRunner(Protocol):
    """工具执行链接口。

    Kernel 将一批 tool_calls 委托给 ToolRunner，
    ToolRunner 负责：guard → pre_hook → execute → post_hook。
    Kernel 只关心最终的 (ToolCallData, ToolResult) 列表。

    ToolRunner 是无状态/可重入的。所有 per-call 上下文通过
    ToolExecutionContext 显式传入，不依赖 set_turn() 等 side channel。
    """

    async def execute_batch(
        self,
        tool_calls: list[ToolCallData],
        ctx: ToolExecutionContext,
        *,
        on_result: Callable[[ToolCallData, ToolResult], Awaitable[None]] | None = None,
    ) -> list[tuple[ToolCallData, ToolResult]]:
        """执行一批 tool_calls。

        Args:
            tool_calls: LLM 返回的工具调用列表。
            ctx: 本次执行的上下文（turn、max_turns、stop_event 等）。
            on_result: 每个工具完成时的回调（用于 Kernel 即时 yield 事件）。

        Returns:
            按原始顺序排列的 (tc, result) 列表。
        """
        ...
```

接口设计要点：

- **显式上下文**：所有 per-call 状态通过 `ToolExecutionContext` 传入，ToolRunner 实例可安全并发/复用（spawn 嵌套场景）
- **批量入参**：接收整批 tool_calls，内部可按 ResourceClaim 调度（Phase 2+）
- **回调**：`on_result` 让 Kernel 在每个工具完成时立即 yield `ToolResultEvent`，不需等全批完成
- **按原始顺序返回**：保证 ToolMessage 追加顺序与 LLM 请求顺序一致
- **不暴露 Scheduler 内部**：acquire/release、fast path 对 Kernel 透明
- **Phase 2 扩展**：`ToolExecutionContext` 增加 `runtime_topology`、`capability_policy` 等字段，ToolRunner 签名不变

### 4.4 InlineToolRunner

Phase 1 过渡实现，逻辑来自当前 `agent.py` L217-311：

```python
class InlineToolRunner:
    """Phase 1 过渡实现：包装当前 Kernel 内联的 guard → execute → post_hook 逻辑。

    接口已对齐 ToolRunner Protocol，内部逻辑不变。
    无状态/可重入：所有 per-call 上下文通过 ToolExecutionContext 传入。
    Phase 2 替换为 ToolRunner（含 ToolScheduler + ResourceClaim + 三层约束）。
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        guard_pipeline: GuardPipeline,
        hooks: list[Hook],
    ) -> None:
        self._registry = tool_registry
        self._guard_pipeline = guard_pipeline
        self._hooks = hooks

    async def execute_batch(
        self,
        tool_calls: list[ToolCallData],
        ctx: ToolExecutionContext,
        *,
        on_result: Callable[[ToolCallData, ToolResult], Awaitable[None]] | None = None,
    ) -> list[tuple[ToolCallData, ToolResult]]:
        """Phase 1: guard → pre_hook → asyncio.gather → post_hook。

        逻辑等价于当前 agent.py L217-311 的 Phase 1/2/3，
        但接口已长成 ToolRunner Protocol 的样子。
        """
        # Phase 1: Serial guard + pre_hook gating
        # outcomes: (tc, result_or_None, was_executed)
        outcomes: list[tuple[ToolCallData, ToolResult | None, bool]] = []
        approved_indices: list[int] = []

        # 注意：stop_event 检查不在 InlineToolRunner 内部。
        # 取消检查是 Kernel _run_items() 的职责——当前 agent.py L222-229
        # 在进入工具执行前检查 stop_event 并直接 return cancelled 终止
        # 整个 run，不会为未执行的工具补 ToolResult/ToolMessage。
        # InlineToolRunner 只负责 guard → execute → post_hook。

        for tc in tool_calls:
            guard_result = self._guard_pipeline.evaluate(
                tc, ctx.turn, ctx.max_turns
            )
            if not guard_result.allowed:
                await run_guard_blocked(self._hooks, tc, guard_result)
                blocked_content = f'BLOCKED: {guard_result.reason}'
                if guard_result.guidance:
                    blocked_content += f'\n{guard_result.guidance}'
                result = ToolResult(status="error", content=blocked_content)
                outcomes.append((tc, result, False))
                if on_result:
                    await on_result(tc, result)
                continue

            action = await run_pre_tool_call(self._hooks, tc)
            if action == HookAction.SKIP:
                result = ToolResult(content='Tool call skipped by hook.')
                outcomes.append((tc, result, False))
                if on_result:
                    await on_result(tc, result)
                continue

            approved_indices.append(len(outcomes))
            outcomes.append((tc, None, True))

        # Phase 2: Parallel execution of approved tools
        if approved_indices:
            approved_tcs = [outcomes[idx][0] for idx in approved_indices]

            async def _execute(tc: ToolCallData) -> ToolResult:
                try:
                    return await self._registry.execute(tc.name, tc.arguments)
                except Exception as e:
                    logger.exception('Tool execution failed: %s', tc.name)
                    return ToolResult.from_error(tc.name, e)

            results = await asyncio.gather(
                *[_execute(tc) for tc in approved_tcs],
                return_exceptions=True,
            )

            for result_idx, outcome_idx in enumerate(approved_indices):
                tc = outcomes[outcome_idx][0]
                raw = results[result_idx]
                if isinstance(raw, BaseException):
                    tool_result = ToolResult.from_error(tc.name, raw)
                else:
                    tool_result = raw
                outcomes[outcome_idx] = (tc, tool_result, True)
                if on_result:
                    await on_result(tc, tool_result)

        # Phase 3: Post hooks in original order
        # 重要：与当前 agent.py L307-311 行为一致——
        # 只要工具真正执行过（was_executed=True），无论成功还是失败，
        # 都执行 post_tool_call hook。Guard deny 和 Hook skip 不执行。
        final: list[tuple[ToolCallData, ToolResult]] = []
        for tc, result, was_executed in outcomes:
            assert result is not None
            if was_executed:
                await run_post_tool_call(self._hooks, tc, result)
            final.append((tc, result))

        return final
```

**post_tool_call 语义说明**：当前 `agent.py` L307-311 中，只要工具经过了 guard + pre_hook 审核并实际执行（无论 ToolResult.status 是 success 还是 error），都会调用 `post_tool_call` hook。这是正确行为——post hook 承载审计、输出处理、事件发射等职责，工具执行失败也需要这些处理。仅 guard deny 和 hook skip 的工具不执行 post hook（因为它们根本没有执行）。InlineToolRunner 通过 `was_executed` 标记区分这两类。

### 4.5 AgentRuntimeSpec 扩展

在 `matmaster/types/runtime.py` 的 `AgentRuntimeSpec` 新增可选字段，Phase 1 全部为 `None`：

```python
class AgentRuntimeSpec(BaseModel):
    # ... 现有字段不变 ...

    # ── Tool Runtime v2 预留字段 ──
    # Phase 1: 全部 None，Kernel 回退到 InlineToolRunner + tool_registry
    # Phase 2: 由 Exp.build_runtime() 填充
    tool_runner: Any | None = None          # ToolRunner Protocol 实例
    tool_catalog: Any | None = None         # ToolCatalog 实例
    runtime_topology: Any | None = None     # RuntimeTopology
    capability_policy: Any | None = None    # CapabilityPolicy
    structural_validation: Any | None = None  # StructuralValidation
```

类型用 `Any` 是因为 Phase 1 这些类型尚未定义（在 Tool Runtime v2 中定义）。Phase 2 引入具体类型后替换为精确类型注解。

### 4.6 Tool definitions 解析抽象

当前 `agent.py` L337-344 直接从 `spec.tool_registry.get_tool_definitions()` 获取 tool schema 传给 provider。Phase 1 将此逻辑抽为独立 helper，使 Phase 2 的 ToolCatalog 能直接替换：

```python
# matmaster/core/agent.py 内部 helper

def _resolve_tool_definitions(
    spec: AgentRuntimeSpec,
    state: _KernelState,
) -> list[dict[str, Any]] | None:
    """解析当前应发给 provider 的 tool definitions。

    Phase 1: 回退到 spec.tool_registry.get_tool_definitions()。
    Phase 2: 优先使用 spec.tool_catalog，检查 version 变更时刷新缓存。
    """
    # Phase 2 路径（当 tool_catalog 非 None 时激活）
    if spec.tool_catalog is not None:
        current_version = spec.tool_catalog.version
        if current_version != state.last_catalog_version:
            state.cached_tool_definitions = spec.tool_catalog.build_definitions()
            state.last_catalog_version = current_version
        return state.cached_tool_definitions

    # Phase 1 回退
    if (
        spec.tool_registry
        and hasattr(spec.tool_registry, 'get_tool_definitions')
    ):
        return spec.tool_registry.get_tool_definitions()
    return None
```

`_run_items()` 在每轮 LLM 调用前调用此 helper，而不是直接访问 `tool_registry`。这样 Phase 2 注入 `tool_catalog` 后，Kernel 的 LLM 调用路径自动切换到 catalog-based definitions，**不需要第二次改动 Kernel**。

### 4.7 LLM 流式输出的事件 yield

**Phase 1 策略：仅 final completed snapshot，非 segment-complete parity**

当前 `_do_stream_llm()` 内部通过 Hook 回调在流式过程中实时发出 stream chunk 和 segment complete 事件（`agent.py` L502-606）。这些 segment 边界在 `_do_stream_llm()` 运行时消费，函数返回后只剩下拼接后的 `response.content` 和 `response.reasoning_content`。

因此 Phase 1 的 `_run_items()` **无法还原 segment-complete 语义**。它只能在 `_call_llm()` 返回后，基于最终合并结果发出一个 completed snapshot：

```python
# _run_items() 中，_call_llm() 返回后：
if response.content:
    yield _KernelItem(event=ResponseEvent(
        source=source, spawn_id=spawn_id,
        content=response.content,
        stream_state="complete",  # 注意：这是最终合并快照，不是 segment-complete
    ))
if response.reasoning_content:
    yield _KernelItem(event=ThoughtEvent(
        source=source, spawn_id=spawn_id,
        content=response.reasoning_content,
        stream_state="complete",
        reasoning_content=response.reasoning_content,
    ))
```

**与 EventEmitterHook 的语义差异**：EventEmitterHook 的 `on_segment_complete` 在流式过程中触发，可以区分多个 segment（如先 thought 再 response）。`_run_items()` Phase 1 只发最终合并结果，可能将多个 segment 合并为一条事件。Phase 1 这不是问题——没有 `run_stream()` 的消费者。

**Phase 2 计划**：将 `_do_stream_llm()` 改造为子 generator `_stream_llm_items()`，在流式过程中实时 yield chunk 和 segment-complete 事件，与 EventEmitterHook 达到完全一致的语义。届时移除 EventEmitterHook。

### 4.8 Hook 系统处理策略

Phase 1 **不改动任何 Hook**：

| Hook 类型 | Phase 1 处理 |
|-----------|-------------|
| 观察型（`on_stream_chunk`, `on_segment_complete`, `post_tool_call`） | 仍然调用，EventEmitterHook 仍然 emit 到 Bus。`_run_items()` 也 yield final snapshot 事件（见 4.7）。两条路径并存但语义粒度不同 |
| 拦截型（`should_continue`, `pre_tool_call` → SKIP） | 留在 Kernel / InlineToolRunner 内部，不能通过 yield 实现 |
| `pre_llm_call` | 留在 Kernel 内部 |

两条路径并存不会产生重复事件问题，因为 Phase 1 没有消费者使用 `run_stream()`。现有调用方仍然走 `run()` → `_run_items()` → Hook → Bus → EventRouter。

Phase 2 切换 service 层后，再逐步移除 Hook → Bus 路径。

### 4.9 取消机制

Phase 1 **不改动取消机制**：

- 继续使用 `threading.Event`（Redis 驱动的跨 worker 停止信号）
- `_run_items()` 内部在每轮开始、**工具批次执行前**、stream chunk 间隔、retry backoff 中检查 `stop_event`
- `InlineToolRunner` **不检查** `stop_event`——取消是 Kernel `_run_items()` 的职责，在调用 `execute_batch()` 前由 Kernel 检查并直接 yield terminal + return（与当前 `agent.py` L222-229 行为一致）
- 不使用 `generator.aclose()`（工具执行涉及 `asyncio.to_thread` 包装的 sync 逻辑，`aclose()` 无法中断）

Tool Runtime v2 的 `ToolBinding.stop_mode` 和 `SessionCapabilities.exec_cancel` 是更上层的取消语义，与 generator 的 `aclose()` 不在同一层面。

---

## 5. 与 Tool Runtime v2 的衔接协议

### 5.1 Phase 衔接时序

```
              Kernel Generator 改造              Tool Runtime v2
              ─────────────────────              ───────────────
Phase 1:      _run_items() / run_stream()        ToolCatalog facade
(本次)        run() 委托 _run_items()            ToolSpec / ToolBinding 定义
              InlineToolRunner 抽出              ToolResult 升级 (payload+meta)
              AgentRuntimeSpec 预留字段          SessionCapabilities

Phase 2:      Exp.run_stream()                   ToolRunner 接入真实实现
              Service 层消费 generator            ToolScheduler + ResourceClaim
              移除 Hook → Bus 路径              ToolCatalog.version 动态刷新
                                                 StructuralValidation 启用

Phase 3:      评估移除 Bus/Router                约束迁移
              (非必须)                            CapabilityPolicy 启用
```

### 5.2 Kernel 对 ToolRunner 的消费方式

Phase 1 Kernel 内 `_run_items()` 的工具执行段伪代码：

```python
# 构造 ToolRunner
if spec.tool_runner is not None:
    # Phase 2+: 使用注入的 ToolRunner
    tool_runner = spec.tool_runner
else:
    # Phase 1: 使用 InlineToolRunner
    tool_runner = InlineToolRunner(
        tool_registry=spec.tool_registry,
        guard_pipeline=guard_pipeline,
        hooks=spec.hooks,
    )

# ...每轮工具执行...
exec_ctx = ToolExecutionContext(
    turn=state.turn,
    max_turns=spec.max_turns,
    stop_event=stop_event,
)

async def _on_tool_result(tc, result):
    # 即时产出事件（通过闭包写入 items queue）
    nonlocal pending_items
    pending_items.append(_KernelItem(
        event=ToolResultEvent(
            source=source, spawn_id=spawn_id,
            call_id=tc.id, tool_name=tc.name,
            result=result.content, status=result.status,
            info=result.info,
        ),
    ))

results = await tool_runner.execute_batch(
    response.tool_calls,
    exec_ctx,
    on_result=_on_tool_result,
)

# 追加 ToolMessages
for tc, result in results:
    state.messages.append(ToolMessage(
        tool_call_id=tc.id, tool_name=tc.name, content=result.content,
    ))
yield _KernelItem(messages_delta=[...])  # 批量追加
```

Phase 2 只需 Exp 在 `build_runtime()` 时注入真实 ToolRunner，Kernel 代码零改动。

### 5.3 ToolCatalog.version 预留

`_KernelState` 已预留 `last_catalog_version` 和 `cached_tool_definitions` 字段。Phase 2 Kernel 在每轮 LLM 调用前检查：

```python
# Phase 2 激活（当 spec.tool_catalog 非 None 时）
if spec.tool_catalog is not None:
    current_version = spec.tool_catalog.version
    if current_version != state.last_catalog_version:
        state.cached_tool_definitions = spec.tool_catalog.build_definitions()
        state.last_catalog_version = current_version
        # 内部更新，Phase 1 不 yield 公开事件
```

Phase 1 此路径不激活（`spec.tool_catalog` 为 None）。

---

## 6. 与当前实现的关键差异

| 维度 | 当前 | Phase 1 完成态 |
|------|------|---------------|
| Kernel 主接口 | `run() -> KernelRunResult` | `run()` 不变 + 新增 `run_stream() -> AsyncIterator[BusEvent]` |
| 内部执行路径 | `_run_loop()` 直接返回 | `_run_items()` generator，`run()` 和 `run_stream()` 都消费它 |
| 工具执行 | Kernel 内联 guard → gather → append | `InlineToolRunner.execute_batch()` 委托 |
| 事件产出 | 仅通过 Hook → Bus | `_run_items()` yield + Hook → Bus 并存 |
| AgentRuntimeSpec | 无预留字段 | 新增 5 个可选 `Any` 字段 |
| 消息转录 | `_run_loop()` 返回 messages | `_run_items()` 通过 `messages_delta` 增量产出 |

---

## 7. 影响分析

### 7.1 不受影响的组件（Phase 1 不改动）

- `matmaster/core/exp.py` — Exp.run() 继续调用 kernel.run()，行为不变
- `matmaster/devshell/runner.py` — DevRunner 继续调用 kernel.run()，行为不变
- `src/services/agent_run_service.py` — 继续调用 kernel.run()，行为不变
- `matmaster/core/bus.py` — MessageBus 保留
- `matmaster/integration/event_router.py` — EventRouter 保留
- `matmaster/core/hooks.py` — Hook Protocol 不变，EventEmitterHook 保留
- `matmaster/hooks/` — 所有 service 层 Hook 保留
- `src/services/stream_service.py` — SSE 层不变

### 7.2 改动文件清单

| 文件 | 改动类型 | 内容 |
|------|----------|------|
| `matmaster/core/agent.py` | **重构** | `_run_loop` → `_run_items` generator；新增 `run_stream()`；`run()` 委托 `_run_items()`；工具执行委托 ToolRunner |
| `matmaster/core/tool_runner.py` | **新增** | ToolRunner Protocol + InlineToolRunner |
| `matmaster/types/runtime.py` | **扩展** | AgentRuntimeSpec 新增 5 个可选字段 |
| `tests/matmaster/core/test_tool_runner.py` | **新增** | InlineToolRunner 单元测试 |
| `tests/matmaster/core/test_agent_kernel_stream.py` | **新增** | run_stream() 集成测试 |

### 7.3 现有测试兼容性

现有 50+ 个 `kernel.run()` 调用的测试**全部不需要修改**。`run()` 的签名、参数、返回类型完全不变。内部行为等价：同样的 messages 构建、同样的 Hook 调用、同样的 guard 检查、同样的工具执行。

验证方式：改造完成后运行全量现有测试 `pytest tests/matmaster/core/test_agent_kernel.py tests/matmaster/core/test_agent_kernel_extended.py -v`，必须全绿。

---

## 8. 文件结构

### 8.1 新增文件

```
matmaster/
  core/
    tool_runner.py          # ToolRunner Protocol + InlineToolRunner（新增）

tests/
  matmaster/
    core/
      test_tool_runner.py           # InlineToolRunner 单元测试（新增）
      test_agent_kernel_stream.py   # run_stream() 集成测试（新增）
```

### 8.2 修改文件

```
matmaster/
  core/
    agent.py                # 重构：_run_items / run_stream / run 三层
  types/
    runtime.py              # AgentRuntimeSpec 扩展
```

---

## Chunk 1: ToolRunner Protocol + InlineToolRunner

### Task 1: ToolRunner Protocol 定义

**Files:**
- Create: `matmaster/core/tool_runner.py`
- Test: `tests/matmaster/core/test_tool_runner.py`

- [ ] **Step 1: Write the ToolRunner Protocol test**

```python
# tests/matmaster/core/test_tool_runner.py
"""ToolRunner Protocol and InlineToolRunner tests."""

import pytest

from matmaster.core.tool_runner import ToolRunner, InlineToolRunner


class TestToolRunnerProtocol:
    """ToolRunner Protocol is runtime_checkable."""

    def test_inline_tool_runner_satisfies_protocol(self):
        """InlineToolRunner is a valid ToolRunner implementation."""
        assert isinstance(InlineToolRunner.__new__(InlineToolRunner), ToolRunner)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/core/test_tool_runner.py::TestToolRunnerProtocol::test_inline_tool_runner_satisfies_protocol -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'matmaster.core.tool_runner'`

- [ ] **Step 3: Create ToolRunner Protocol and InlineToolRunner skeleton**

```python
# matmaster/core/tool_runner.py
"""ToolRunner Protocol and InlineToolRunner transition implementation.

ToolRunner defines the interface for tool execution chains.
InlineToolRunner wraps current agent.py guard → execute → post_hook logic
behind the ToolRunner interface, serving as Phase 1 bridge to Tool Runtime v2.

Phase 2: Replace InlineToolRunner with ToolRunner backed by
ToolScheduler + ResourceClaim + three-layer constraints.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from matmaster.core.guard_pipeline import GuardPipeline
from matmaster.core.hooks import (
    HookAction,
    run_guard_blocked,
    run_post_tool_call,
    run_pre_tool_call,
)
from matmaster.tools.tool_result import ToolResult

if TYPE_CHECKING:
    from matmaster.core.hooks import Hook
    from matmaster.tools.tool_registry import ToolRegistry
    from matmaster.types.messages import ToolCallData

logger = logging.getLogger(__name__)


@runtime_checkable
class ToolRunner(Protocol):
    """Tool execution chain interface.

    Kernel delegates a batch of tool_calls to ToolRunner.
    ToolRunner handles: guard → pre_hook → execute → post_hook.
    Kernel only cares about the final (ToolCallData, ToolResult) list.
    """

    async def execute_batch(
        self,
        tool_calls: list[ToolCallData],
        ctx: ToolExecutionContext,
        *,
        on_result: Callable[[ToolCallData, ToolResult], Awaitable[None]] | None = None,
    ) -> list[tuple[ToolCallData, ToolResult]]:
        """Execute a batch of tool_calls.

        Args:
            tool_calls: Tool calls from LLM response.
            ctx: Per-batch execution context (turn, max_turns, stop_event).
            on_result: Callback invoked as each tool completes
                       (enables Kernel to yield events immediately).

        Returns:
            List of (tc, result) in original order.
        """
        ...


class InlineToolRunner:
    """Phase 1 transition: wraps current Kernel-inline tool execution logic.

    Interface aligns with ToolRunner Protocol. Stateless/reentrant:
    all per-call context passed via ToolExecutionContext.

    Phase 2: replaced by ToolRunner backed by ToolScheduler + ResourceClaim.
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        guard_pipeline: GuardPipeline,
        hooks: list[Hook],
    ) -> None:
        self._registry = tool_registry
        self._guard_pipeline = guard_pipeline
        self._hooks = hooks

    async def execute_batch(
        self,
        tool_calls: list[ToolCallData],
        ctx: ToolExecutionContext,
        *,
        on_result: Callable[[ToolCallData, ToolResult], Awaitable[None]] | None = None,
    ) -> list[tuple[ToolCallData, ToolResult]]:
        # Phase 1: Serial guard + pre_hook gating
        outcomes: list[tuple[ToolCallData, ToolResult | None, bool]] = []
        approved_indices: list[int] = []

        # 注意：stop_event 检查不在 InlineToolRunner 内部。
        # 取消是 Kernel _run_items() 的职责——在进入 execute_batch 前
        # 由 Kernel 检查并直接 yield terminal + return。
        for tc in tool_calls:
            guard_result = self._guard_pipeline.evaluate(
                tc, ctx.turn, ctx.max_turns
            )
            if not guard_result.allowed:
                await run_guard_blocked(self._hooks, tc, guard_result)
                blocked_content = f'BLOCKED: {guard_result.reason}'
                if guard_result.guidance:
                    blocked_content += f'\n{guard_result.guidance}'
                result = ToolResult(status="error", content=blocked_content)
                outcomes.append((tc, result, False))
                if on_result:
                    await on_result(tc, result)
                continue

            action = await run_pre_tool_call(self._hooks, tc)
            if action == HookAction.SKIP:
                result = ToolResult(content='Tool call skipped by hook.')
                outcomes.append((tc, result, False))
                if on_result:
                    await on_result(tc, result)
                continue

            approved_indices.append(len(outcomes))
            outcomes.append((tc, None, True))

        # Phase 2: Parallel execution of approved tools
        if approved_indices:
            approved_tcs = [outcomes[idx][0] for idx in approved_indices]

            async def _execute_tool(tc: ToolCallData) -> ToolResult:
                try:
                    return await self._registry.execute(tc.name, tc.arguments)
                except Exception as e:
                    logger.exception('Tool execution failed: %s', tc.name)
                    return ToolResult.from_error(tc.name, e)

            results = await asyncio.gather(
                *[_execute_tool(tc) for tc in approved_tcs],
                return_exceptions=True,
            )

            for result_idx, outcome_idx in enumerate(approved_indices):
                tc = outcomes[outcome_idx][0]
                raw = results[result_idx]
                if isinstance(raw, BaseException):
                    tool_result = ToolResult.from_error(tc.name, raw)
                else:
                    tool_result = raw
                outcomes[outcome_idx] = (tc, tool_result, True)
                if on_result:
                    await on_result(tc, tool_result)

        # Phase 3: Post hooks in original order (only for executed tools)
        final: list[tuple[ToolCallData, ToolResult]] = []
        for tc, result, needs_post_hook in outcomes:
            assert result is not None
            if needs_post_hook:
                await run_post_tool_call(self._hooks, tc, result)
            final.append((tc, result))

        return final
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/core/test_tool_runner.py::TestToolRunnerProtocol::test_inline_tool_runner_satisfies_protocol -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/core/tool_runner.py tests/matmaster/core/test_tool_runner.py
git commit -m "feat: add ToolRunner Protocol and InlineToolRunner skeleton"
```

---

### Task 2: InlineToolRunner 单元测试 — guard deny

- [ ] **Step 1: Write the guard deny test**

```python
# tests/matmaster/core/test_tool_runner.py (append)

from unittest.mock import AsyncMock, MagicMock

from matmaster.core.guard_pipeline import GuardPipeline
from matmaster.core.hooks import BaseHook
from matmaster.core.tool_runner import ToolExecutionContext
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.types.guards import GuardResult
from matmaster.types.messages import ToolCallData


def _make_tc(name: str = "test_tool", args: dict | None = None) -> ToolCallData:
    return ToolCallData(id=f"call_{name}", name=name, arguments=args or {})


class TestInlineToolRunnerGuardDeny:
    """InlineToolRunner blocks tool calls when guard denies."""

    @pytest.mark.asyncio
    async def test_guard_deny_returns_blocked_result(self):
        registry = ToolRegistry()
        pipeline = GuardPipeline()
        pipeline.evaluate = MagicMock(
            return_value=GuardResult(allowed=False, reason="loop detected", guidance="try different args")
        )
        runner = InlineToolRunner(
            tool_registry=registry,
            guard_pipeline=pipeline,
            hooks=[BaseHook()],
        )
        tc = _make_tc()
        ctx = ToolExecutionContext(turn=1, max_turns=10)

        results = await runner.execute_batch([tc], ctx)

        assert len(results) == 1
        _, result = results[0]
        assert result.status == "error"
        assert "BLOCKED" in result.content
        assert "loop detected" in result.content
        assert "try different args" in result.content
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/core/test_tool_runner.py::TestInlineToolRunnerGuardDeny -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/matmaster/core/test_tool_runner.py
git commit -m "test: InlineToolRunner guard deny behavior"
```

---

### Task 3: InlineToolRunner 单元测试 — pre_hook skip

- [ ] **Step 1: Write the hook skip test**

```python
# tests/matmaster/core/test_tool_runner.py (append)

from matmaster.core.hooks import HookAction


class _SkipHook(BaseHook):
    async def pre_tool_call(self, tool_call: ToolCallData) -> HookAction:
        return HookAction.SKIP


class TestInlineToolRunnerHookSkip:
    """InlineToolRunner respects pre_tool_call SKIP."""

    @pytest.mark.asyncio
    async def test_hook_skip_returns_skipped_result(self):
        registry = ToolRegistry()
        runner = InlineToolRunner(
            tool_registry=registry,
            guard_pipeline=GuardPipeline(),
            hooks=[_SkipHook()],
        )
        tc = _make_tc()
        ctx = ToolExecutionContext(turn=1, max_turns=10)

        results = await runner.execute_batch([tc], ctx)

        assert len(results) == 1
        _, result = results[0]
        assert "skipped" in result.content.lower()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/core/test_tool_runner.py::TestInlineToolRunnerHookSkip -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/matmaster/core/test_tool_runner.py
git commit -m "test: InlineToolRunner hook skip behavior"
```

---

### Task 4: InlineToolRunner 单元测试 — successful execution + on_result callback

- [ ] **Step 1: Write the execution test**

```python
# tests/matmaster/core/test_tool_runner.py (append)

from matmaster.tools.tool_result import ToolResult as TR


class TestInlineToolRunnerExecution:
    """InlineToolRunner executes tools and fires on_result callback."""

    @pytest.mark.asyncio
    async def test_successful_execution(self):
        registry = ToolRegistry()
        mock_tool = MagicMock()
        mock_tool.name = "echo"
        mock_tool.description = "echo tool"
        mock_tool.json_schema = {"type": "object", "properties": {}}
        mock_tool.execute = AsyncMock(return_value=TR(content="hello"))
        registry.register(mock_tool, source="test")

        runner = InlineToolRunner(
            tool_registry=registry,
            guard_pipeline=GuardPipeline(),
            hooks=[BaseHook()],
        )
        ctx = ToolExecutionContext(turn=1, max_turns=10)

        callback_results: list[tuple[str, str]] = []

        async def on_result(tc, result):
            callback_results.append((tc.name, result.content))

        tc = _make_tc("echo")
        results = await runner.execute_batch([tc], ctx, on_result=on_result)

        assert len(results) == 1
        _, result = results[0]
        assert result.content == "hello"
        assert result.status == "success"

        # on_result callback was invoked
        assert len(callback_results) == 1
        assert callback_results[0] == ("echo", "hello")

    @pytest.mark.asyncio
    async def test_multiple_tools_preserve_order(self):
        """Results maintain original tool_calls order."""
        registry = ToolRegistry()
        for name in ("tool_a", "tool_b", "tool_c"):
            mock_tool = MagicMock()
            mock_tool.name = name
            mock_tool.description = name
            mock_tool.json_schema = {"type": "object", "properties": {}}
            mock_tool.execute = AsyncMock(return_value=TR(content=f"result_{name}"))
            registry.register(mock_tool, source="test")

        runner = InlineToolRunner(
            tool_registry=registry,
            guard_pipeline=GuardPipeline(),
            hooks=[BaseHook()],
        )
        ctx = ToolExecutionContext(turn=1, max_turns=10)

        tcs = [_make_tc("tool_a"), _make_tc("tool_b"), _make_tc("tool_c")]
        results = await runner.execute_batch(tcs, ctx)

        assert [tc.name for tc, _ in results] == ["tool_a", "tool_b", "tool_c"]
        assert [r.content for _, r in results] == [
            "result_tool_a", "result_tool_b", "result_tool_c"
        ]
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/core/test_tool_runner.py::TestInlineToolRunnerExecution -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/matmaster/core/test_tool_runner.py
git commit -m "test: InlineToolRunner execution and on_result callback"
```

---

### Task 5: InlineToolRunner 单元测试 — tool execution error

- [ ] **Step 1: Write the error handling test**

```python
# tests/matmaster/core/test_tool_runner.py (append)


class TestInlineToolRunnerErrors:
    """InlineToolRunner handles tool execution errors gracefully."""

    @pytest.mark.asyncio
    async def test_tool_exception_returns_error_result(self):
        registry = ToolRegistry()
        mock_tool = MagicMock()
        mock_tool.name = "fail_tool"
        mock_tool.description = "fails"
        mock_tool.json_schema = {"type": "object", "properties": {}}
        mock_tool.execute = AsyncMock(side_effect=RuntimeError("boom"))
        registry.register(mock_tool, source="test")

        runner = InlineToolRunner(
            tool_registry=registry,
            guard_pipeline=GuardPipeline(),
            hooks=[BaseHook()],
        )
        ctx = ToolExecutionContext(turn=1, max_turns=10)

        tc = _make_tc("fail_tool")
        results = await runner.execute_batch([tc], ctx)

        assert len(results) == 1
        _, result = results[0]
        assert result.status == "error"
        assert "boom" in result.content

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        registry = ToolRegistry()
        runner = InlineToolRunner(
            tool_registry=registry,
            guard_pipeline=GuardPipeline(),
            hooks=[BaseHook()],
        )
        ctx = ToolExecutionContext(turn=1, max_turns=10)

        tc = _make_tc("nonexistent")
        results = await runner.execute_batch([tc], ctx)

        assert len(results) == 1
        _, result = results[0]
        assert result.status == "error"
        assert "not found" in result.content.lower()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/core/test_tool_runner.py::TestInlineToolRunnerErrors -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/matmaster/core/test_tool_runner.py
git commit -m "test: InlineToolRunner error handling"
```

---

## Chunk 2: AgentRuntimeSpec 扩展 + Kernel 三层重构

### Task 6: AgentRuntimeSpec 预留字段

**Files:**
- Modify: `matmaster/types/runtime.py:40-74`
- Test: `tests/matmaster/types/test_runtime.py`

- [ ] **Step 1: Write the test for new optional fields**

```python
# tests/matmaster/types/test_runtime.py (append to existing file)

class TestAgentRuntimeSpecToolRunnerFields:
    """AgentRuntimeSpec has optional Tool Runtime v2 fields."""

    def test_default_none(self):
        spec = AgentRuntimeSpec()
        assert spec.tool_runner is None
        assert spec.tool_catalog is None
        assert spec.runtime_topology is None
        assert spec.capability_policy is None
        assert spec.structural_validation is None

    def test_can_set_tool_runner(self):
        sentinel = object()
        spec = AgentRuntimeSpec(tool_runner=sentinel)
        assert spec.tool_runner is sentinel
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/types/test_runtime.py::TestAgentRuntimeSpecToolRunnerFields -v`
Expected: FAIL with `pydantic validation error` (fields not defined)

- [ ] **Step 3: Add fields to AgentRuntimeSpec**

In `matmaster/types/runtime.py`, add after line 73 (`meta` field):

```python
    # ── Tool Runtime v2 预留字段 ──
    # Phase 1: 全部 None，Kernel 回退到 InlineToolRunner + tool_registry
    # Phase 2: 由 Exp.build_runtime() 填充
    tool_runner: Any | None = None
    tool_catalog: Any | None = None
    runtime_topology: Any | None = None
    capability_policy: Any | None = None
    structural_validation: Any | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/types/test_runtime.py::TestAgentRuntimeSpecToolRunnerFields -v`
Expected: PASS

- [ ] **Step 5: Run existing runtime tests to verify no regression**

Run: `uv run pytest tests/matmaster/types/test_runtime.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add matmaster/types/runtime.py tests/matmaster/types/test_runtime.py
git commit -m "feat: add Tool Runtime v2 placeholder fields to AgentRuntimeSpec"
```

---

### Task 7: _KernelItem / _KernelState / _TerminalItem 内部类型

**Files:**
- Modify: `matmaster/core/agent.py` (在文件顶部定义私有类型)

- [ ] **Step 1: Write the test for internal types**

```python
# tests/matmaster/core/test_agent_kernel_stream.py
"""Tests for AgentKernel generator-first interfaces (run_stream / _run_items)."""

import pytest

from matmaster.core.agent import _KernelItem, _KernelState, _TerminalItem
from matmaster.types.events import ResponseEvent
from matmaster.types.messages import SystemMessage


class TestKernelInternalTypes:
    """Private kernel types are correctly defined."""

    def test_kernel_item_event_only(self):
        event = ResponseEvent(source="test", content="hello")
        item = _KernelItem(event=event)
        assert item.event is event
        assert item.messages_delta is None
        assert item.terminal is None

    def test_kernel_item_messages_delta(self):
        msg = SystemMessage(content="sys")
        item = _KernelItem(messages_delta=[msg])
        assert item.event is None
        assert item.messages_delta == [msg]

    def test_terminal_item(self):
        term = _TerminalItem(
            status="completed", reason="natural",
            final_content="done", num_turns=3,
            stop_reason="stop", usage={"prompt_tokens": 100},
        )
        assert term.status == "completed"
        assert term.num_turns == 3

    def test_kernel_state_defaults(self):
        state = _KernelState(messages=[])
        assert state.turn == 0
        assert state.total_usage == {}
        assert state.last_catalog_version is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/core/test_agent_kernel_stream.py::TestKernelInternalTypes -v`
Expected: FAIL with `ImportError: cannot import name '_KernelItem'`

- [ ] **Step 3: Add internal types to agent.py**

In `matmaster/core/agent.py`, add after the `_ToolOutcome` class (line 70):

```python
@dataclass
class _TerminalItem:
    """Kernel terminal result. Only consumed by run()."""

    status: str
    reason: str
    final_content: str | None
    num_turns: int
    stop_reason: str | None
    usage: dict[str, int]


@dataclass
class _KernelItem:
    """Private kernel stream item. Not public API."""

    event: Any | None = None
    messages_delta: list[Message] | None = None
    terminal: _TerminalItem | None = None


@dataclass
class _KernelState:
    """Per-invocation kernel loop state. Local to _run_items(), not on self."""

    messages: list[Message]
    turn: int = 0
    total_usage: dict[str, int] = field(default_factory=dict)
    last_stop_reason: str | None = None
    # Tool Runtime v2 placeholders
    last_catalog_version: int | None = None
    cached_tool_definitions: list[dict[str, Any]] | None = None
```

Also add `from dataclasses import dataclass, field` to imports at line 20.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/core/test_agent_kernel_stream.py::TestKernelInternalTypes -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/core/agent.py tests/matmaster/core/test_agent_kernel_stream.py
git commit -m "feat: add private _KernelItem/_KernelState/_TerminalItem types"
```

---

### Task 8: `_run_items()` generator — 主循环重构

**Files:**
- Modify: `matmaster/core/agent.py`

这是核心改造。将 `_run_loop()` 改为 `_run_items()` generator。

- [ ] **Step 1: Write the integration test for _run_items**

```python
# tests/matmaster/core/test_agent_kernel_stream.py (append)

from unittest.mock import AsyncMock, MagicMock, patch

from matmaster.core.agent import AgentKernel, _KernelItem
from matmaster.core.hooks import BaseHook
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.tools.tool_result import ToolResult
from matmaster.types.events import ResponseEvent, RunResultEvent, ToolCallEvent, ToolResultEvent
from matmaster.types.messages import ToolCallData
from matmaster.types.runtime import AgentRuntimeSpec


def _make_spec(
    provider=None,
    tool_registry=None,
    hooks=None,
    max_turns=10,
) -> AgentRuntimeSpec:
    """Helper to build a minimal AgentRuntimeSpec for testing."""
    if provider is None:
        provider = MagicMock()
        provider.__aenter__ = AsyncMock(return_value=provider)
        provider.__aexit__ = AsyncMock(return_value=False)
        # Natural finish: no tool calls, just content
        provider.chat_stream = MagicMock(return_value=_async_iter([
            MagicMock(
                content="hello", reasoning_content=None,
                tool_call_deltas=None, finish_reason="stop", usage={"prompt_tokens": 10},
            ),
        ]))
    return AgentRuntimeSpec(
        llm_provider=provider,
        tool_registry=tool_registry or ToolRegistry(),
        hooks=hooks or [BaseHook()],
        max_turns=max_turns,
        system_prompt="You are a test agent.",
    )


async def _async_iter(items):
    for item in items:
        yield item


class TestRunItemsNaturalFinish:
    """_run_items yields events and terminal for natural finish."""

    @pytest.mark.asyncio
    async def test_natural_finish_yields_terminal(self):
        kernel = AgentKernel()
        spec = _make_spec()
        items = []
        async for item in kernel._run_items(
            spec, "hello", None, None, source="test", spawn_id=None
        ):
            items.append(item)

        # Last item must be terminal
        assert items[-1].terminal is not None
        assert items[-1].terminal.status == "completed"
        assert items[-1].terminal.reason == "natural"

        # Should have messages_delta items (SystemMessage + UserMessage + AssistantMessage)
        all_deltas = [
            msg
            for item in items
            if item.messages_delta
            for msg in item.messages_delta
        ]
        assert len(all_deltas) >= 3  # system + user + assistant
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/matmaster/core/test_agent_kernel_stream.py::TestRunItemsNaturalFinish -v`
Expected: FAIL with `AttributeError: 'AgentKernel' object has no attribute '_run_items'`

- [ ] **Step 3: Implement `_run_items()` generator**

Refactor `matmaster/core/agent.py`:

1. Rename `_run_loop()` to `_run_items()`
2. Change return type from `-> KernelRunResult` to `-> AsyncIterator[_KernelItem]`
3. Add `source` and `spawn_id` parameters
4. Replace `return self._finish(...)` with `yield _KernelItem(terminal=...)` + `return`
5. Replace `messages.append(...)` with `yield _KernelItem(messages_delta=[msg])` and also append locally
6. Construct InlineToolRunner from spec, delegate tool execution
7. Yield `_KernelItem(event=ToolCallEvent(...))` before each tool call
8. Yield `_KernelItem(event=ToolResultEvent(...))` after each tool result
9. Initial messages (system + history + user) yield as `messages_delta` at start
10. **抽出 `_resolve_tool_definitions()` helper**（见 4.6 节），替换 `_call_llm()` 中直接访问 `spec.tool_registry.get_tool_definitions()` 的代码。`_run_items()` 每轮 LLM 调用前通过此 helper 获取 tool_defs，传入 `_call_llm()`。Phase 1 回退到 `tool_registry`；Phase 2 自动切到 `tool_catalog`

Key: the full `_run_items()` implementation replaces the current `_run_loop()`. `_call_llm()` 的签名改为接收 `tool_defs` 参数（而非内部从 spec 获取），`_do_stream_llm()` 保持不变。这确保 Phase 2 注入 `tool_catalog` 时 Kernel 零改动。

The `_run_items()` method signature:

```python
async def _run_items(
    self,
    spec: AgentRuntimeSpec,
    task: str,
    history: list[Message] | None,
    stop_event: threading.Event | None,
    *,
    source: str = "kernel",
    spawn_id: str | None = None,
) -> AsyncIterator[_KernelItem]:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/matmaster/core/test_agent_kernel_stream.py::TestRunItemsNaturalFinish -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add matmaster/core/agent.py tests/matmaster/core/test_agent_kernel_stream.py
git commit -m "feat: implement _run_items() generator as kernel execution core"
```

---

### Task 9: `run()` 委托 `_run_items()` + `run_stream()` 公开接口

**Files:**
- Modify: `matmaster/core/agent.py`

- [ ] **Step 1: Write tests for run() delegation and run_stream()**

```python
# tests/matmaster/core/test_agent_kernel_stream.py (append)

from matmaster.types.runtime import KernelRunResult
from matmaster.types.events import BusEvent


class TestRunDelegation:
    """run() delegates to _run_items() and returns KernelRunResult."""

    @pytest.mark.asyncio
    async def test_run_returns_kernel_run_result(self):
        kernel = AgentKernel()
        spec = _make_spec()
        result = await kernel.run(spec, "hello")

        assert isinstance(result, KernelRunResult)
        assert result.result.status == "completed"
        assert result.result.reason == "natural"
        assert len(result.messages) >= 3  # system + user + assistant

    @pytest.mark.asyncio
    async def test_run_preserves_messages(self):
        kernel = AgentKernel()
        spec = _make_spec()
        result = await kernel.run(spec, "hello")

        msg_types = [type(m).__name__ for m in result.messages]
        assert "SystemMessage" in msg_types
        assert "UserMessage" in msg_types
        assert "AssistantMessage" in msg_types


class TestRunStream:
    """run_stream() yields BusEvent instances."""

    @pytest.mark.asyncio
    async def test_run_stream_yields_bus_events(self):
        kernel = AgentKernel()
        spec = _make_spec()
        events = []
        async for event in kernel.run_stream(spec, "hello"):
            events.append(event)

        # Last event must be RunResultEvent
        assert isinstance(events[-1], RunResultEvent)
        assert events[-1].status == "completed"

        # All events must be BusEvent-compatible (have 'type' field)
        for event in events:
            assert hasattr(event, "type")
            assert hasattr(event, "source")

    @pytest.mark.asyncio
    async def test_run_stream_no_messages_in_events(self):
        """Public events must not leak internal messages."""
        kernel = AgentKernel()
        spec = _make_spec()
        async for event in kernel.run_stream(spec, "hello"):
            assert not hasattr(event, "messages")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/matmaster/core/test_agent_kernel_stream.py::TestRunDelegation tests/matmaster/core/test_agent_kernel_stream.py::TestRunStream -v`
Expected: FAIL (run() and run_stream() not yet refactored to use _run_items)

- [ ] **Step 3: Implement run() and run_stream()**

In `matmaster/core/agent.py`:

```python
async def run(
    self,
    spec: AgentRuntimeSpec,
    task: str,
    history: list[Message] | None = None,
    stop_event: threading.Event | None = None,
) -> KernelRunResult:
    """Execute agent loop. Returns KernelRunResult with full transcript.

    Delegates to _run_items() -- same execution path as run_stream().
    """
    async with spec.llm_provider:
        _summary_provider = None
        if spec.compactor and hasattr(spec.compactor, '_summary_provider'):
            sp = spec.compactor._summary_provider
            if sp is not spec.llm_provider:
                _summary_provider = sp

        async def _consume():
            messages: list[Message] = []
            terminal: _TerminalItem | None = None
            async for item in self._run_items(
                spec, task, history, stop_event,
                source="kernel", spawn_id=None,
            ):
                if item.messages_delta:
                    messages.extend(item.messages_delta)
                if item.terminal:
                    terminal = item.terminal

            assert terminal is not None
            status = 'cancelled' if terminal.reason == 'cancelled' else (
                'failed' if terminal.reason == 'invalid_finish' else 'completed'
            )
            from matmaster.types.runtime import KernelResult, KernelRunResult
            result = KernelResult(
                status=status,
                reason=terminal.reason,
                final_content=terminal.final_content,
                num_turns=terminal.num_turns,
                stop_reason=terminal.stop_reason,
                usage=terminal.usage,
            )
            return KernelRunResult(result=result, messages=messages)

        if _summary_provider is not None:
            async with _summary_provider:
                return await _consume()
        else:
            return await _consume()

async def run_stream(
    self,
    spec: AgentRuntimeSpec,
    task: str,
    history: list[Message] | None = None,
    stop_event: threading.Event | None = None,
    *,
    source: str = "kernel",
    spawn_id: str | None = None,
) -> AsyncIterator[BusEvent]:
    """Generator-first interface. Yields BusEvent (reuses events.py types).

    Last yield is always RunResultEvent. Does not expose internal messages.
    """
    async with spec.llm_provider:
        _summary_provider = None
        if spec.compactor and hasattr(spec.compactor, '_summary_provider'):
            sp = spec.compactor._summary_provider
            if sp is not spec.llm_provider:
                _summary_provider = sp

        async def _stream():
            async for item in self._run_items(
                spec, task, history, stop_event,
                source=source, spawn_id=spawn_id,
            ):
                if item.event is not None:
                    yield item.event
                if item.terminal is not None:
                    status = 'cancelled' if item.terminal.reason == 'cancelled' else (
                        'failed' if item.terminal.reason == 'invalid_finish' else 'completed'
                    )
                    yield RunResultEvent(
                        source=source,
                        spawn_id=spawn_id,
                        status=status,
                        reason=item.terminal.reason,
                        final_content=item.terminal.final_content,
                    )

        if _summary_provider is not None:
            async with _summary_provider:
                async for event in _stream():
                    yield event
        else:
            async for event in _stream():
                yield event
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/matmaster/core/test_agent_kernel_stream.py::TestRunDelegation tests/matmaster/core/test_agent_kernel_stream.py::TestRunStream -v`
Expected: PASS

- [ ] **Step 5: Run ALL existing kernel tests to verify zero regression**

Run: `uv run pytest tests/matmaster/core/test_agent_kernel.py tests/matmaster/core/test_agent_kernel_extended.py -v`
Expected: All PASS — run() behavior is identical to before

- [ ] **Step 6: Commit**

```bash
git add matmaster/core/agent.py tests/matmaster/core/test_agent_kernel_stream.py
git commit -m "feat: implement run() delegation and run_stream() public generator"
```

---

### Task 10: LLM final completed snapshot 事件

**Files:**
- Modify: `matmaster/core/agent.py`

Phase 1 策略（与 4.7 节一致）：`_run_items()` 在 `_call_llm()` 返回后，基于最终合并结果发出 final completed snapshot。**不是** segment-complete parity，因为 `_do_stream_llm()` 返回时 segment 边界信息已消费。

具体行为：
- `_call_llm()` 返回 `LLMResponse` 后，如果有 `content`，yield 一个 `ResponseEvent(stream_state='complete', content=response.content)`
- 如果有 `reasoning_content`，yield 一个 `ThoughtEvent(stream_state='complete', content=response.reasoning_content)`
- 这些是最终合并快照，可能将流式阶段的多个 segment 合并为一条事件
- 逐 chunk 和逐 segment 的流式事件仍然由 EventEmitterHook → Bus 路径处理
- Phase 2 将 `_do_stream_llm()` 改造为子 generator 后才能达到完整的流式语义

- [ ] **Step 1: Write test for final snapshot events**

```python
# tests/matmaster/core/test_agent_kernel_stream.py (append)


class TestRunStreamLLMEvents:
    """run_stream() yields final completed snapshot LLM events (not segment-level)."""

    @pytest.mark.asyncio
    async def test_response_event_on_natural_finish(self):
        """Natural finish yields ResponseEvent with final merged content."""
        kernel = AgentKernel()
        spec = _make_spec()  # provider returns content="hello"
        events = []
        async for event in kernel.run_stream(spec, "hello"):
            events.append(event)

        response_events = [e for e in events if isinstance(e, ResponseEvent)]
        # At least one ResponseEvent with the content
        assert any(e.content == "hello" for e in response_events)
```

- [ ] **Step 2: Implement final completed snapshot event yield in _run_items()**

In `_run_items()`, after `_call_llm()` returns a response with content, yield:

```python
if response.content:
    yield _KernelItem(event=ResponseEvent(
        source=source, spawn_id=spawn_id,
        content=response.content,
        stream_state="complete",
    ))
if response.reasoning_content:
    yield _KernelItem(event=ThoughtEvent(
        source=source, spawn_id=spawn_id,
        content=response.reasoning_content,
        stream_state="complete",
        reasoning_content=response.reasoning_content,
    ))
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/matmaster/core/test_agent_kernel_stream.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add matmaster/core/agent.py tests/matmaster/core/test_agent_kernel_stream.py
git commit -m "feat: yield final completed snapshot LLM events from _run_items"
```

---

### Task 11: run_stream() 工具执行事件测试

**Files:**
- Test: `tests/matmaster/core/test_agent_kernel_stream.py`

- [ ] **Step 1: Write test for tool call/result events**

```python
# tests/matmaster/core/test_agent_kernel_stream.py (append)


def _make_tool_spec(provider=None, tool_registry=None):
    """Build a spec where LLM returns one tool call then natural finish."""
    if provider is None:
        provider = MagicMock()
        provider.__aenter__ = AsyncMock(return_value=provider)
        provider.__aexit__ = AsyncMock(return_value=False)

        call_count = 0

        async def chat_stream_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: tool call
                chunk = MagicMock(
                    content=None, reasoning_content=None,
                    finish_reason="tool_calls",
                    usage={"prompt_tokens": 10},
                    tool_call_deltas=[{
                        "index": 0, "id": "call_1",
                        "name": "echo", "arguments": '{"text": "hi"}',
                    }],
                )
                yield chunk
            else:
                # Second call: natural finish
                chunk = MagicMock(
                    content="done", reasoning_content=None,
                    tool_call_deltas=None, finish_reason="stop",
                    usage={"prompt_tokens": 20},
                )
                yield chunk

        provider.chat_stream = chat_stream_side_effect

    registry = tool_registry or ToolRegistry()
    mock_tool = MagicMock()
    mock_tool.name = "echo"
    mock_tool.description = "echo"
    mock_tool.json_schema = {"type": "object", "properties": {"text": {"type": "string"}}}
    mock_tool.execute = AsyncMock(return_value=ToolResult(content="echoed: hi"))
    registry.register(mock_tool, source="test")

    return _make_spec(provider=provider, tool_registry=registry)


class TestRunStreamToolEvents:
    """run_stream() yields ToolCallEvent and ToolResultEvent."""

    @pytest.mark.asyncio
    async def test_tool_call_and_result_events(self):
        kernel = AgentKernel()
        spec = _make_tool_spec()
        events = []
        async for event in kernel.run_stream(spec, "call echo"):
            events.append(event)

        event_types = [type(e).__name__ for e in events]
        assert "ToolCallEvent" in event_types
        assert "ToolResultEvent" in event_types
        assert "RunResultEvent" in event_types

        # ToolCallEvent before ToolResultEvent
        tc_idx = event_types.index("ToolCallEvent")
        tr_idx = event_types.index("ToolResultEvent")
        assert tc_idx < tr_idx

        # ToolCallEvent has correct fields
        tc_event = events[tc_idx]
        assert tc_event.tool_name == "echo"
        assert tc_event.call_id == "call_1"

        # ToolResultEvent has correct fields
        tr_event = events[tr_idx]
        assert tr_event.tool_name == "echo"
        assert "echoed: hi" in tr_event.result
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/matmaster/core/test_agent_kernel_stream.py::TestRunStreamToolEvents -v`
Expected: PASS (if _run_items correctly yields tool events via InlineToolRunner on_result callback)

- [ ] **Step 3: Commit**

```bash
git add tests/matmaster/core/test_agent_kernel_stream.py
git commit -m "test: run_stream tool call/result event ordering"
```

---

### Task 12: 全量回归测试

- [ ] **Step 1: Run all kernel tests**

Run: `uv run pytest tests/matmaster/core/test_agent_kernel.py tests/matmaster/core/test_agent_kernel_extended.py tests/matmaster/core/test_tool_runner.py tests/matmaster/core/test_agent_kernel_stream.py -v`
Expected: All PASS

- [ ] **Step 2: Run Exp tests**

Run: `uv run pytest tests/matmaster/core/test_exp.py -v`
Expected: All PASS (Exp.run calls kernel.run, which still returns KernelRunResult)

- [ ] **Step 3: Run integration tests**

Run: `uv run pytest tests/matmaster/integration/ -v -k "not real_api"`
Expected: All PASS

- [ ] **Step 4: Commit (if any test fixes needed)**

```bash
git commit -m "fix: address regression from kernel generator-first refactor"
```

---

## 9. 设计决策记录

### D-01: 为什么三层接口而不是两层

两层设计（`run_stream()` → `run()` 委托）无法干净地解决 messages 归属问题：`run()` 需要完整 transcript 构造 `KernelRunResult`，但 messages 不应出现在公开事件中。三层设计让 `_run_items()` 同时产出 events 和 messages_delta，各层按需取用，一条执行路径、零分叉。

### D-02: 为什么复用 events.py 而不是新建事件类型

当前 `events.py` 的 `ThoughtEvent`、`ResponseEvent`、`ToolCallEvent`、`ToolResultEvent`、`RunResultEvent` 已经被 SSEHandler、PersistenceHandler、所有集成测试消费。引入平行事件类型（如 `StreamChunkEvent`、`SegmentCompleteEvent`）会导致两套类型并存，增加迁移面而无收益。

### D-03: 为什么 Phase 1 只做 final completed snapshot 而非 segment-complete 或逐 chunk

`_do_stream_llm()` 在流式过程中通过 Hook 回调消费 segment 边界信息（`agent.py` L543、L589），函数返回时只剩拼接后的 `response.content` 和 `response.reasoning_content`。因此 `_run_items()` 无法还原 segment-complete 语义（比如区分"先 thought 后 response"两个独立 segment），只能发出最终合并快照。将 `_do_stream_llm()` 改造为子 generator 需要重构其内部的 content_parts/reasoning_parts/tool_calls_acc 累积逻辑，改动面大且 Phase 1 没有 `run_stream()` 的真实消费者。Phase 2 再做。

### D-04: 为什么同时抽出 InlineToolRunner

如果 Phase 1 只做 generator 而工具执行仍然内联在 `_run_items()` 中，等 Tool Runtime v2 的 ToolRunner 落地时 Kernel 需要第二次重构。InlineToolRunner 的接口已经对齐 ToolRunner Protocol，Phase 2 只需 Exp 注入真实实现，Kernel 代码零改动。

### D-05: 为什么 Hook 调用和 generator yield 并存

Phase 1 没有 `run_stream()` 的消费者。现有 service 层全部走 `run()` → Hook → Bus → EventRouter。如果 Phase 1 就移除 Hook 路径，所有事件传递会断掉。并存是过渡态，Phase 2 切换 service 层后再移除。

### D-06: 为什么不改动取消机制

当前取消是 Redis 驱动的跨 worker `threading.Event`。工具执行涉及 `asyncio.to_thread()` 包装的 sync 逻辑（`BuiltinTool.execute()`、`BashTool` subprocess）。`generator.aclose()` 无法中断这些操作。Tool Runtime v2 的 `ToolBinding.stop_mode` 和 `SessionCapabilities.exec_cancel` 是更上层的取消语义设计，与 generator 不在同一层面。

### D-07: 为什么 AgentRuntimeSpec 预留字段用 `Any`

`ToolCatalog`、`RuntimeTopology`、`CapabilityPolicy`、`StructuralValidation` 这些类型在 Tool Runtime v2 中定义，Phase 1 尚未创建。用 `Any` 避免循环依赖和提前耦合。Phase 2 引入具体类型后替换为精确类型注解。

---

## 10. 后续阶段概要

### Phase 2: Exp + Service 层接入

- `Exp` 新增 `run_stream()`，透传 Kernel generator
- `AgentRunService` 新增 `run_agent_stream()`，消费 generator，事件仍喂给 Bus
- 将 `_do_stream_llm()` 改造为子 generator `_stream_llm_items()`，支持逐 chunk yield（达到与 EventEmitterHook 完全一致的 segment-complete 语义）
- **移除 Hook → Bus 路径的前置条件**（每个 Hook 必须有等价替代方案后才能退役）：
  - `EventEmitterHook` → `_run_items()` 产出等价的 `ThoughtEvent`/`ResponseEvent`/`ToolCallEvent`/`ToolResultEvent`（Phase 2 将 `_do_stream_llm()` 改造为子 generator 后达成）
  - `AssistantStateHook` → `_run_items()` 在 tool_calls 轮次追加 AssistantMessage 时，同时 yield `AssistantStateEvent(state=msg.model_dump())`（`chat_history.py` 依赖此事件重建带 `tool_calls` 的 `AssistantMessage`）
  - `SkillHitHook` → `_run_items()` 产出等价的 `SkillHitEvent`
  - `OutputProcessorHook` → 当前功能是在 `ToolResultEvent.info` 中注入 `auto_save`/`summarize` 标志（`output_processor.py` L42-70），下游根据标志决定自动保存或生成摘要。替代方案：将 pattern matching 逻辑移入 ToolRunner 的 post-execute 阶段（在 `ToolResult.info` 中直接标注），或作为 `run_stream()` 消费侧的 event transformer。具体方案在 Phase 2 落地前确定
  - `ContextCompactor` → bus 依赖改为通过 Kernel yield `ContextCompactionEvent`
- 全部前置条件满足后，移除 Hook → Bus 路径（5 个 Hook 退役）

### Phase 3: 评估去总线化

- 是否移除 MessageBus + EventRouter
- 如果移除，需要在消费侧实现 async fanout + buffer 替代
- SSE 先发、持久化不阻塞 token 流、workspace upload 不拖慢主循环

---

## 11. 开放问题

### Q-01: run_stream() source 归一化

`run_stream()` 默认 `source="kernel"`，但现有 `chat_history.py` 只把 `MatMaster` 系来源当作主对话事件解析。Phase 2 service 层消费 generator 时，必须统一 source 归一化（在 service 层将 `source` 覆写为 `MatMaster` 或当前 exp_name），而不是直接沿用 `kernel`。Phase 1 不受影响（`run_stream()` 没有消费者）。

### Q-02: ToolResultEvent 载荷升级时机

Tool Runtime v2 将 `ToolResult` 升级为 `status + content + payload + meta`（取代当前的 `status + content + info`）。当前 `ToolResultEvent` 按现有总线协议压平成 `result=content, status=status, info=info`。Phase 1 保持现状。Phase 2 或 3 需要决定是否升级 `ToolResultEvent` 的载荷结构以承接 `payload + meta`，或者在 service 层做映射。此决策不阻塞 Phase 1。
