# Phase 34: Exp/Service 接入 + Hook 退役 - Research

**Researched:** 2026-04-02
**Domain:** Python asyncio generator event pipeline, Hook retirement, service layer integration
**Confidence:** HIGH

## Summary

Phase 34 是 v2.2 里程碑中集成风险最高的阶段。核心目标是将 Kernel generator 事件流贯穿到 Exp 和 Service 层，使 FullToolRunner 成为默认执行路径，并退役 4 个 Hook（EventEmitterHook / AssistantStateHook / SkillHitHook / OutputProcessorHook）及 ContextCompactor 的 Bus 依赖。

通过深入分析现有代码，Phase 32/33 已经完成了全部基础设施：`_run_items()` 已 yield ToolCallEvent/ToolResultEvent（KGEN-06），`run_stream()` 已消费 `_run_items()` 并 yield BusEvent，FullToolRunner 七步执行链已完整实现，ToolCatalog overlay/version 机制已就位。Phase 34 的工作是把这些已验证的组件激活到生产路径上。

关键风险在于 `_do_stream_llm()` 改造为 `_stream_llm_items()` sub-generator（130+ 行状态机），以及 Service 层 `run_agent_stream()` 需要保持与现有 EventRouter/SSEHandler/PersistenceHandler 的完全兼容。研究确认所有改造目标文件、事件语义契约、cleanup 模式均有清晰的参照基准。

**Primary recommendation:** 严格遵循 CONTEXT.md 的三 Plan 拆分策略（Plan 1 内核改造 -> Plan 2 Service 切流 -> Plan 3 Hook 退役），每个 Plan 独立可验证。

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Phase 34 分为 3 个 plan：Plan 1 内核改造，Plan 2 Service 层切流，Plan 3 Hook 退役
- **D-02:** FullToolRunner 直接成为默认执行路径，不使用灰度开关
- **D-03:** 丢弃 ESIN-08（system prompt 工具枚举段处理），从 REQUIREMENTS.md 中移除
- **D-04:** _do_stream_llm() 拆为 _stream_llm_items() sub-generator + 聚合器
- **D-05:** run_agent_stream() 将 generator 事件送入现有 bus/router，暂不移除 EventRouter
- **D-06:** 逐个渐进退役 Hook：EventEmitterHook -> AssistantStateHook -> SkillHitHook -> OutputProcessorHook -> ContextCompactor bus 依赖 -> Hook->Bus 路径移除
- **D-07:** ContextCompactor 改为 event_sink: Callable[[BusEvent], Awaitable[None]] 回调 + deque 暂存
- **D-08:** on_skill_hit 改走 ToolCatalog.register_overlay() + ToolCompiler 编译
- **D-09:** FullToolRunner 不调用 pre_hook/post_hook
- **D-10:** execute_batch 逐工具返回结果，不整批失败

### Claude's Discretion
- _stream_llm_items() 内部 yield 点的精确位置（在保持 segment-complete 语义等价的约束下）
- run_agent_stream() 中 bus.emit 与 generator 消费的具体桥接实现
- Hook 退役过程中测试文件的具体组织方式
- ConfirmationHook 不在退役范围内（FUTR-02），Phase 34 保持其在 hooks 列表中继续运行

### Deferred Ideas (OUT OF SCOPE)
- ESIN-08（system prompt 工具枚举段处理）-- 用户决定丢弃此需求
- ConfirmationHook 双向流在 generator 模型中的替代方案 -- FUTR-02（v2.3+）
- 去总线化（MessageBus + EventRouter 移除）-- Phase 36

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| KGEN-06 | _run_items() 在工具执行前 yield ToolCallEvent、执行后 yield ToolResultEvent | 已在 Phase 33 完成（commit 6f534fa6），agent.py L416-456 |
| ESIN-01 | Exp 新增 run_stream()，透传 Kernel generator | Exp.run() cleanup 模式（L285-306）为参照基准，需 try/finally + async generator |
| ESIN-02 | AgentRunService 新增 run_agent_stream()，消费 generator 事件 | run_agent() L203-441 为参照，bus.emit 桥接 generator 事件到 EventRouter |
| ESIN-03 | _do_stream_llm() 改造为子 generator _stream_llm_items() | _do_stream_llm() L626-787 是改造基础，130+ 行状态机 |
| ESIN-04 | Exp.build_runtime() 构造 FullToolRunner + ToolCatalog + RuntimeTopology 并注入 AgentRuntimeSpec | build_runtime() L149-244 注入点在 L227-234 的 model_copy |
| ESIN-05 | on_skill_hit 路径改为 ToolCatalog.register_overlay() | _init_skill_tools() L411-516 中 on_skill_hit 闭包需重写 |
| ESIN-06 | run_stream() 事件 source 归一化 | _normalize_public_source() 在 event_payloads.py L15-23 |
| ESIN-07 | ToolResult.payload/meta 到 SSE/持久化前端契约兼容映射 | event_payloads.py L107-116 的 tool_result 映射已存在 |
| HRET-01 | _run_items() 产出等价 ThoughtEvent/ResponseEvent 替代 EventEmitterHook | EventEmitterHook L231-282 是事件等价基准 |
| HRET-02 | _run_items() yield AssistantStateEvent 替代 AssistantStateHook | AssistantStateHook L35-51 逻辑简单可直接内联 |
| HRET-03 | _run_items() 产出等价 SkillHitEvent 替代 SkillHitHook | SkillHitHook L34-48 逻辑简单 |
| HRET-04 | OutputProcessorHook 功能迁移 | 当前 _build_service_hooks 初始化时 patterns=[] 实际无效 |
| HRET-05 | ContextCompactor bus 依赖改为 event_sink | context_compactor.py L206-217 和 L252-263 共两处 bus.emit |
| HRET-06 | 全部 Hook 退役后移除 Hook->Bus 间接事件路径 | EventEmitterHook 在 exp.py L184 创建，_build_service_hooks L143-147 三个 observer hooks |
| REGR-02 | Exp.run() 和 AgentRunService.run_agent() 行为不变 | 现有 23 kernel 测试 + 1455 总测试覆盖 |

</phase_requirements>

## Architecture Patterns

### 现有事件流路径（Hook 路径 -- 将被退役）

```
AgentKernel._run_loop()
  -> Hook.on_stream_chunk()
    -> EventEmitterHook.on_stream_chunk()  # ThoughtEvent/ResponseEvent
      -> MessageBus.emit()
        -> EventRouter._consume_loop()
          -> SSEHandler.handle()
          -> PersistenceHandler.handle()

AgentKernel._run_loop()
  -> Hook.pre_tool_call()
    -> EventEmitterHook.pre_tool_call()    # ToolCallEvent
  -> Hook.post_tool_call()
    -> EventEmitterHook.post_tool_call()   # ToolResultEvent
    -> SkillHitHook.post_tool_call()       # SkillHitEvent (use_skill only)
    -> OutputProcessorHook.post_tool_call()# extra ToolResultEvent (auto_save)

AgentKernel._run_loop()
  -> Hook.pre_llm_call()
    -> AssistantStateHook.pre_llm_call()   # AssistantStateEvent
```

### 目标事件流路径（Generator 路径）

```
AgentKernel._run_items()       <- 私有 AsyncGenerator
  yield _KernelItem(event=ThoughtEvent/ResponseEvent)   # _stream_llm_items()
  yield _KernelItem(event=ToolCallEvent)                 # 工具执行前 (KGEN-06 done)
  yield _KernelItem(event=ToolResultEvent)               # 工具执行后 (KGEN-06 done)
  yield _KernelItem(event=AssistantStateEvent)           # tool_calls 轮次
  yield _KernelItem(event=SkillHitEvent)                 # use_skill 命中
  yield _KernelItem(event=ContextCompactionEvent)        # deque 暂存后 yield
  |
  v
kernel.run_stream()            <- 公开 AsyncGenerator，yield BusEvent
  |
  v
Exp.run_stream()               <- try/finally cleanup 保证
  |
  v
AgentRunService.run_agent_stream()
  -> bus.emit(event)           <- 桥接到现有 EventRouter
    -> SSEHandler.handle()
    -> PersistenceHandler.handle()
```

### 关键改造点 1: _stream_llm_items() Sub-Generator

当前 `_do_stream_llm()` 是一个 coroutine（返回 LLMResponse），在流式过程中通过 Hook 回调产出事件。改造为 AsyncGenerator 需要：

```python
# 伪代码结构
async def _stream_llm_items(
    self, spec, api_messages, tool_defs, *, timeout, stop_event, event_sink
) -> AsyncIterator[_KernelItem]:
    """Sub-generator: yield streaming events, return LLMResponse via sentinel."""
    content_parts, reasoning_parts, tool_calls_acc = [], [], {}
    producing_reasoning = producing_content = False

    # yield start event
    yield _KernelItem(event=...)  # stream start

    async for chunk in spec.llm_provider.chat_stream(...):
        # chunk 处理（保留现有聚合逻辑）
        if chunk.reasoning_content:
            yield _KernelItem(event=ThoughtEvent(
                content=chunk.reasoning_content,
                stream_state="streaming",
            ))
        if chunk.content:
            # segment-complete: reasoning -> content 切换时
            if producing_reasoning:
                yield _KernelItem(event=ThoughtEvent(
                    content=''.join(reasoning_parts),
                    stream_state="complete",
                ))
                producing_reasoning = False
            yield _KernelItem(event=ResponseEvent(
                content=chunk.content,
                stream_state="streaming",
            ))
        # tool_call_deltas 处理...

    # finally: segment complete 残余
    # yield end event

    # 最后 yield 携带 LLMResponse 的 sentinel item
    yield _KernelItem(
        llm_response=LLMResponse(...),  # 需要扩展 _KernelItem
    )
```

**设计要点：**
- _KernelItem 需要新增 `llm_response` 字段或使用独立 sentinel
- _run_items() 中调用 _stream_llm_items() 并收集 LLMResponse
- retry/backoff 逻辑留在 _call_llm() 层（不进入 sub-generator）
- ContextCompactor 的 event_sink 注入暂存事件到 deque，LLM 调用后 yield

### 关键改造点 2: Exp.build_runtime() 注入

当前 build_runtime() 在 L227-234 组装最终 spec：

```python
spec = spec.model_copy(
    update={
        'tool_registry': registry,
        'system_prompt': system_prompt,
        'hooks': hooks,
        'compactor': compactor,
    }
)
```

需要扩展为：

```python
from matmaster.core.capability_policy import CapabilityPolicy
from matmaster.core.structural_validation import StructuralValidation
from matmaster.core.tool_runner import FullToolRunner
from matmaster.core.tool_scheduler import ToolScheduler
from matmaster.tools.tool_catalog import ToolCatalog
from matmaster.tools.tool_compiler import ToolCompiler
from matmaster.types.topology import RuntimeTopology

# 构造 RuntimeTopology
topology = RuntimeTopology(
    session_kind=ctx.session_type or "local",
    control_root=str(ctx.workdir),
    workspace_root=str(ctx.execution_workdir),
)

# ToolCatalog 包装 registry
compiler = ToolCompiler()
catalog = ToolCatalog(registry, compiler=compiler, topology=topology)

# 三层约束
structural_validation = StructuralValidation()
guard_pipeline = GuardPipeline(spec.guards)
capability_policy = CapabilityPolicy()
scheduler = ToolScheduler()

# FullToolRunner
full_runner = FullToolRunner(
    catalog=catalog,
    structural_validation=structural_validation,
    guard_pipeline=guard_pipeline,
    capability_policy=capability_policy,
    scheduler=scheduler,
    topology=topology,
)

spec = spec.model_copy(
    update={
        'tool_registry': registry,
        'tool_catalog': catalog,
        'tool_runner': full_runner,
        'runtime_topology': topology,
        'capability_policy': capability_policy,
        'structural_validation': structural_validation,
        'system_prompt': system_prompt,
        'hooks': hooks,
        'compactor': compactor,
    }
)
```

### 关键改造点 3: run_agent_stream() 桥接

run_agent_stream() 需要将 Exp.run_stream() 的 generator 事件桥接到现有 bus/router：

```python
async def run_agent_stream(self, ...):
    bus = MessageBus()
    router = EventRouter(bus=bus, handlers=[SSEHandler(...), PersistenceHandler(...)])
    await router.start()

    try:
        async for event in exp.run_stream(pg_ctx, task, ...):
            # source 归一化
            if hasattr(event, 'source'):
                event = _normalize_event_source(event, exp_name)
            bus.emit_nowait(event)

        # terminal 事件后补发 stream_closed
        bus.emit_nowait(StreamClosedEvent(...))
    finally:
        await router.stop()
        # cleanup...
```

### Anti-Patterns to Avoid

- **在 generator 内部直接 bus.emit()：** generator 是事件的单一来源，不应绕过 generator 直接写 bus。这会导致事件顺序不一致。
- **在 _stream_llm_items() 中处理 retry/backoff：** retry 逻辑留在 _call_llm() 层，sub-generator 只负责单次流式调用的事件产出。
- **一次性退役所有 Hook：** 必须逐个退役并验证，每退一个 Hook 都要确认事件等价性。
- **忽略 ConfirmationHook：** Phase 34 不退役 ConfirmationHook，它必须继续在 hooks 列表中运行。

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| 事件 source 归一化 | 新的 source mapping 逻辑 | 复用 event_payloads._normalize_public_source() | 已有稳定实现 |
| ToolResult->SSE 映射 | 新的序列化层 | 复用 event_payloads._public_content_for_event() | 前端契约已锁定 |
| AsyncGenerator cleanup | 手写 finally/close 逻辑 | Python 原生 async generator finalization + try/finally | Python 3.10+ 已有完善语义 |
| Tool schema 编译 | 新的 spec->binding 逻辑 | 复用 ToolCompiler.compile() | Phase 33 已实现和测试 |

## Common Pitfalls

### Pitfall 1: _stream_llm_items() Sub-Generator 返回值传递

**What goes wrong:** AsyncGenerator 不能通过 `return` 传递值给调用者。`_do_stream_llm()` 当前直接返回 `LLMResponse`，改为 generator 后无法直接返回。
**Why it happens:** Python AsyncGenerator 的 `return` 值不可被 `async for` 消费。
**How to avoid:** 使用 sentinel _KernelItem（携带 llm_response 字段）作为最后一个 yield，调用者在 `async for` 中检测此 sentinel 并提取 LLMResponse。或者在 _KernelItem 中新增 `llm_response: LLMResponse | None = None` 字段。
**Warning signs:** 如果看到 `StopAsyncIteration` 异常被 swallow 或 LLMResponse 数据丢失。

### Pitfall 2: Generator Cleanup 在异常路径下的行为

**What goes wrong:** Exp.run_stream() 是 async generator，如果调用方提前 break 或异常退出，generator 的 finally 块可能不执行 cleanup。
**Why it happens:** Python async generator 在 GC 时不保证调用 aclose()。
**How to avoid:** 调用方必须使用 `async with contextlib.aclosing(exp.run_stream(...)) as stream:` 或在 try/finally 中显式调用 `aclose()`。在 run_agent_stream() 中必须保证这一点。
**Warning signs:** MCP connector 或 session 资源泄漏。

### Pitfall 3: Hook 退役顺序导致重复事件

**What goes wrong:** 如果 generator 已经 yield 了某类事件（如 ToolCallEvent），但对应 Hook 尚未移除，同一事件会被产出两次（generator 一次 + Hook->Bus 一次）。
**Why it happens:** InlineToolRunner 仍调用 pre_hook/post_hook，而 FullToolRunner 不调用。当 FullToolRunner 成为默认后，Hook 路径自然断开。
**How to avoid:** Plan 1 中 FullToolRunner 成为默认后，InlineToolRunner 的 hook 调用路径不再触发，EventEmitterHook 产出的事件自然消失。Plan 3 是清理代码（删除已 dead 的 Hook 实现），而非行为切换。关键是确认 FullToolRunner 激活后事件等价性。
**Warning signs:** SSE 流中出现重复的 tool_call / tool_result 事件。

### Pitfall 4: ContextCompactor 事件暂存时机

**What goes wrong:** ContextCompactor 在 LLM 调用过程中触发（compact_if_needed 在 _run_items 的每轮开头），此时控制流已在 _stream_llm_items() 之前。但如果 compact 产生的 ContextCompactionEvent 通过 deque 暂存，暂存点和 yield 点的时序需要精确对齐。
**Why it happens:** compact_if_needed() 修改 messages in-place 并发出事件，当前在 _run_items L343-347 调用。
**How to avoid:** 按 D-07 方案：compactor 接受 event_sink 回调，sink 暂存到 deque。compact_if_needed 调用后，_run_items 检查 deque 并 yield 暂存事件。时间点在 LLM 调用之前（当前位置不变），yield 在 compact 调用之后立即执行。
**Warning signs:** ContextCompactionEvent 丢失或顺序错误。

### Pitfall 5: OutputProcessorHook 实际无效但退役需保持语义

**What goes wrong:** 当前 `_build_service_hooks` 中 `OutputProcessorHook(bus)` 初始化时 auto_save_patterns 和 summarize_patterns 均为空列表默认值，因此 `_matches()` 永远返回 False，这个 Hook 实际不产出任何事件。退役时如果试图把 auto_save/summarize 逻辑迁移到 ToolResult.payload，可能引入之前不存在的行为。
**Why it happens:** 服务层初始化时没有传入 patterns 参数。
**How to avoid:** 退役 OutputProcessorHook 时，确认当前生产环境中它确实无效。不需要在 ToolResult.payload 中实际注入 auto_save/summarize 标记（除非有其他调用方传入了 patterns）。直接删除即可。
**Warning signs:** 如果在其他代码路径中存在带 patterns 的 OutputProcessorHook 构造。

### Pitfall 6: ChatHistoryConverter source 过滤

**What goes wrong:** ChatHistoryConverter 的 events_to_dialog_messages() 内部使用 `_is_matmaster_source(source)` 过滤事件。如果 generator 事件的 source 不是 `MatMaster` 或 `MatMaster:*` 格式，历史回放会丢失事件。
**Why it happens:** Kernel 内部事件 source 默认为 "agent"，EventEmitterHook 将其转换为 exp_name（如 "direct"），再由 _normalize_public_source() 归一化为 "MatMaster"。
**How to avoid:** 在 run_agent_stream() 桥接时，对每个 generator 事件的 source 进行归一化。或者在 Exp.run_stream() 层注入正确的 source。确保最终到达 bus 的事件 source 符合 `MatMaster` / `MatMaster:<exp>` 格式。
**Warning signs:** 多轮对话历史恢复时丢失 assistant 消息或 tool 消息。

## Code Examples

### Pattern 1: Generator 事件桥接到 Bus

```python
# Source: matmaster/core/agent.py L236-277 (现有 run_stream() 模式)
async def run_stream(self, spec, task, history=None, stop_event=None):
    async with spec.llm_provider:
        async for item in self._run_items(spec, task, history, stop_event):
            if item.event is not None:
                yield item.event
            if item.terminal is not None:
                result = KernelResult(...)
                yield result.to_run_result_event(source="agent")
```

### Pattern 2: _KernelItem 事件产出（KGEN-06 已实现）

```python
# Source: matmaster/core/agent.py L416-456
# 工具执行前 yield ToolCallEvent
for tc in response.tool_calls:
    yield _KernelItem(event=ToolCallEvent(
        source="agent",
        call_id=tc.id,
        tool_name=tc.name,
        arguments=tc.arguments,
    ))

# 工具执行后 yield ToolResultEvent
for tc, tr in results:
    yield _KernelItem(event=ToolResultEvent(
        source="agent",
        call_id=tc.id,
        tool_name=tc.name,
        result=tr.content,
        status=tr.status,
        payload=tr.payload,
    ))
```

### Pattern 3: Exp.run() Cleanup 保证

```python
# Source: matmaster/core/exp.py L285-306
async def run(self, ctx, task, *, bus=None, ...):
    try:
        runtime = await self.build_runtime(ctx, bus=bus, ...)
        result = await runtime.kernel.run(runtime.spec, task, ...)
        return result.result
    finally:
        await self._run_cleanup_callbacks()
```

### Pattern 4: EventEmitterHook on_segment_complete 语义（退役参照基准）

```python
# Source: matmaster/core/hooks.py L255-282
async def on_segment_complete(self, segment_type, content, stream_id):
    if segment_type == "thought":
        await self._bus.emit(ThoughtEvent(
            source=self._source,
            spawn_id=self._spawn_id,
            content=content,
            stream_state="complete",
            stream_id=stream_id,
            reasoning_content=content,
        ))
    elif segment_type == "response":
        await self._bus.emit(ResponseEvent(
            source=self._source,
            spawn_id=self._spawn_id,
            content=content,
            stream_state="complete",
            stream_id=stream_id,
        ))
```

### Pattern 5: ToolCatalog.register_overlay() 已实现

```python
# Source: matmaster/tools/tool_catalog.py L63-74
def register_overlay(self, tool, *, source="mcp"):
    self._registry.register(tool, source=source)
    self._compiled_tools[tool.name] = self._compiler.compile(
        tool, self._topology, source=source,
    )
    self._version += 1
```

### Pattern 6: event_payloads.py tool_result 映射（前端契约）

```python
# Source: matmaster/integration/event_payloads.py L107-116
if event_type == 'tool_result':
    call_id = payload.get('call_id')
    return {
        'id': call_id,
        'call_id': call_id,
        'name': payload.get('tool_name'),
        'result': payload.get('result'),
        'status': payload.get('status', 'success'),
        'info': payload.get('payload') or {},  # payload -> info 兼容映射
    }
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Hook.on_stream_chunk -> Bus | _stream_llm_items() yield event | Phase 34 (本阶段) | 事件产出从间接回调变为直接 yield |
| EventEmitterHook 桥接 | Generator 直接产出 BusEvent | Phase 34 (本阶段) | 移除中间层，事件来源单一化 |
| InlineToolRunner (hook chain) | FullToolRunner (7-step chain) | Phase 33->34 | 工具执行从 hook 串联变为 pipeline |
| ToolRegistry 直接注册 | ToolCatalog.register_overlay() | Phase 32->34 | Skill MCP 工具走 overlay + version bump |
| kernel.run() -> Service | kernel.run_stream() -> Exp.run_stream() -> Service | Phase 34 | 事件流贯穿全链路 |

## Open Questions

1. **_KernelItem 如何携带 LLMResponse 回传给 _run_items()**
   - What we know: AsyncGenerator 不能 return 值，需要 sentinel 或新字段
   - What's unclear: 最简洁的实现方式（新增字段 vs _KernelItem 子类 vs 独立 sentinel dataclass）
   - Recommendation: 在 _KernelItem 新增 `llm_response: LLMResponse | None = None` 字段最简单，与现有 event/messages_delta/terminal 并列

2. **run_agent_stream() 中 playground/Bohrium/history 框架的复用边界**
   - What we know: run_agent() Stage 1-5 的 playground/Bohrium/history 逻辑需要完整复用
   - What's unclear: 是否抽出公共的 _setup_pipeline() 辅助方法，还是直接在 run_agent_stream() 中重复
   - Recommendation: 抽出公共 setup 逻辑到 _prepare_run_context() 避免代码重复，但这是 Claude's discretion 范围

3. **Exp.run_stream() 的 spawn_id 和 source 注入层级**
   - What we know: EventEmitterHook 当前接收 source 和 spawn_id 参数（exp.py L184）
   - What's unclear: generator 路径中 source 和 spawn_id 应该在哪一层注入（Kernel / Exp / Service）
   - Recommendation: Exp.run_stream() 接收 source_override 和 spawn_id 参数，在 yield 事件时注入。这保持与 EventEmitterHook 相同的注入层级

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio |
| Config file | pyproject.toml |
| Quick run command | `uv run pytest tests/matmaster/core/test_agent_kernel_stream.py -q` |
| Full suite command | `uv run pytest tests/matmaster/ -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ESIN-01 | Exp.run_stream() 透传 + cleanup | integration | `uv run pytest tests/matmaster/core/test_exp_runtime_v2.py -x` | Wave 0 |
| ESIN-02 | run_agent_stream() 消费 + SSE/persist | integration | `uv run pytest tests/test_chat_stream_direct.py -x` | Partial (现有 503 测试) |
| ESIN-03 | _stream_llm_items() segment-complete 等价 | unit | `uv run pytest tests/matmaster/core/test_agent_kernel_stream.py -x` | Partial (现有 stream 测试) |
| ESIN-04 | build_runtime() 注入 FullToolRunner | unit | `uv run pytest tests/matmaster/core/test_exp_runtime_v2.py -x` | Wave 0 |
| ESIN-05 | skill overlay -> catalog.register_overlay() | integration | `uv run pytest tests/matmaster/core/test_exp_skills.py -x` | Partial |
| ESIN-06 | source 归一化兼容 | unit | `uv run pytest tests/matmaster/integration/test_event_payloads.py -x` | Existing |
| ESIN-07 | ToolResult.payload -> info 映射 | unit | `uv run pytest tests/matmaster/integration/test_event_payloads.py -x` | Existing |
| HRET-01 | generator ThoughtEvent/ResponseEvent 等价 | unit | `uv run pytest tests/matmaster/core/test_agent_kernel_stream.py -x` | Partial |
| HRET-02 | AssistantStateEvent 直接 yield | unit | `uv run pytest tests/matmaster/core/test_agent_kernel_stream.py -x` | Wave 0 |
| HRET-03 | SkillHitEvent 直接 yield | unit | `uv run pytest tests/matmaster/core/test_agent_kernel_stream.py -x` | Wave 0 |
| HRET-04 | OutputProcessorHook 退役 | unit | `uv run pytest tests/matmaster/hooks/test_output_processor.py -x` | Existing (验证删除后不影响) |
| HRET-05 | ContextCompactor event_sink | unit | `uv run pytest tests/matmaster/core/test_context_compactor.py -x` | Existing (需扩展) |
| HRET-06 | Hook->Bus 路径移除 | integration | `uv run pytest tests/matmaster/integration/ -x` | Existing |
| REGR-02 | run()/run_agent() 行为不变 | regression | `uv run pytest tests/matmaster/core/test_agent_kernel.py tests/matmaster/core/test_agent_kernel_extended.py -x` | Existing |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/matmaster/core/test_agent_kernel.py tests/matmaster/core/test_agent_kernel_stream.py -q`
- **Per wave merge:** `uv run pytest tests/matmaster/ -q`
- **Phase gate:** Full suite green before /gsd:verify-work

### Wave 0 Gaps
- [ ] `tests/matmaster/core/test_exp_runtime_v2.py` -- covers ESIN-01, ESIN-04
- [ ] `tests/matmaster/core/test_agent_kernel_stream.py` 扩展 -- covers ESIN-03, HRET-01, HRET-02, HRET-03
- [ ] `tests/matmaster/core/test_context_compactor.py` 扩展 -- covers HRET-05 (event_sink 模式)
- [ ] `tests/matmaster/integration/test_event_payloads.py` 扩展 -- covers ESIN-07 (payload->info 显式)

## Project Constraints (from CLAUDE.md)

- 始终使用 `uv run` 或 `.venv`，不用系统 Python
- Import 按 标准库 -> 第三方 -> 本地 分组，全部放文件顶部
- 单文件超过 1000 行必须重构
- DAO 层不吞异常；service 层按需降级
- 新增工具必须实现 Tool Protocol 并返回 ToolResult
- frozen Pydantic model 层间契约
- @runtime_checkable Protocol 接口
- TYPE_CHECKING + lazy import 解决循环导入
- event_payloads.py 作为唯一公共 SSE/持久化映射层

## Sources

### Primary (HIGH confidence)
- `matmaster/core/agent.py` -- AgentKernel _run_items()/run_stream()/_do_stream_llm() 完整实现
- `matmaster/core/exp.py` -- Exp build_runtime()/run() cleanup 模式
- `src/services/agent_run_service.py` -- run_agent() 全流程 + _build_service_hooks()
- `matmaster/core/hooks.py` -- EventEmitterHook 完整实现（退役参照基准）
- `matmaster/hooks/assistant_state.py` -- AssistantStateHook 实现
- `matmaster/hooks/skill_hit.py` -- SkillHitHook 实现
- `matmaster/hooks/output_processor.py` -- OutputProcessorHook 实现
- `matmaster/core/context_compactor.py` -- ContextCompactor bus 使用点
- `matmaster/integration/event_payloads.py` -- SSE/持久化前端契约映射
- `matmaster/tools/tool_catalog.py` -- register_overlay() + version 机制
- `matmaster/core/tool_runner.py` -- FullToolRunner 七步执行链
- `matmaster/tools/skill_tool.py` -- on_skill_hit 闭包
- `docs/plans/2026-04-02-v2.2-phase2-advancement.md` -- Task 2/3 步骤设计
- `.planning/phases/34-exp-service-hook/34-CONTEXT.md` -- 用户锁定决策

### Secondary (MEDIUM confidence)
- `docs/specs/2026-04-02-kernel-generator-first.md` -- generator-first 架构 spec
- `docs/specs/2026-04-02-tool-runtime-v2.md` -- Tool Runtime v2 架构 spec
- `src/services/chat_history.py` -- ChatHistoryConverter source 过滤逻辑

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- 纯内部改造，不引入新依赖
- Architecture: HIGH -- 所有改造目标文件已逐行阅读，事件语义契约清晰
- Pitfalls: HIGH -- 基于代码分析发现 6 个具体风险点，均有明确规避策略

**Research date:** 2026-04-02
**Valid until:** 2026-04-30 (内部架构改造，不受外部依赖版本变化影响)
