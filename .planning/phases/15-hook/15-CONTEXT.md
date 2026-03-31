# Phase 15: Hook 系统异步化 - Context

**Gathered:** 2026-03-27
**Status:** Ready for planning

<domain>
## Phase Boundary

将 5 个具体 Hook 实现（EventEmitter、Confirmation、OutputProcessor、AssistantState、SkillHit）的所有方法改为真正的 async def 实现。6 个 run_* helper 函数改为 async def，内部 await 每个 hook 方法。ConfirmationHook 从 queue.Queue 阻塞等待改为 asyncio.Future 异步等待，废弃 ReplyQueueLike Protocol。src/ 层调用点最小化适配。

本阶段不改 AgentKernel（Phase 17）、MessageBus（Phase 16）、Exp 生命周期（Phase 18）。Kernel 通过已有 _sync_call_async 桥接调用 async hook helpers。

</domain>

<decisions>
## Implementation Decisions

### ConfirmationHook 异步等待机制
- **D-01:** ConfirmationHook.pre_tool_call() 每次创建 asyncio.Future，通过 `await asyncio.wait_for(future, timeout)` 挂起等待用户回复。超时时 asyncio.TimeoutError → 返回 HookAction.SKIP（映射当前 queue.Empty 行为）。
- **D-02:** 外部线程（src/ 服务层、Redis worker）通过 `loop.call_soon_threadsafe(future.set_result, reply)` 跨线程推送回复。取消时 `loop.call_soon_threadsafe(future.set_result, None)`。
- **D-03:** event loop 引用通过构造函数注入：`ConfirmationHook.__init__(loop: asyncio.AbstractEventLoop, ...)`。与 Kernel 的 _bridge_loop 一致。

### ReplyQueueLike 废弃
- **D-04:** 废弃 ReplyQueueLike Protocol（hooks/ 版和 src/ 版都废弃）。ConfirmationHook 不再接收 reply_queue 参数，改为暴露 `resolve(reply: str)` 和 `cancel()` 方法。这两个方法内部使用 `loop.call_soon_threadsafe(future.set_result, ...)` 推送。
- **D-05:** src/ 层 agent_run_service.py 的调用点在 Phase 15 一并改动：`reply_queue.put_content(x)` → `hook.resolve(x)`，`reply_queue.put_cancel()` → `hook.cancel()`。改动量极小，不算服务层重构。

### run_* helpers 异步化
- **D-06:** 6 个 run_* helper 函数（run_pre_tool_call, run_should_continue, run_pre_llm_call, run_post_tool_call, run_on_stream_chunk, run_on_segment_complete）全部改为 async def，内部 await 每个 hook 的对应方法。run_guard_blocked 同理。
- **D-07:** Kernel 调用 run_* helpers 的位置改为 `_sync_call_async(run_pre_tool_call(hooks, tc), _bridge_loop)`，复用 Phase 13/14 建立的桥接模式。Phase 17 Kernel async 化时直接 await，去掉桥接。

### bus.emit() 过渡策略
- **D-08:** EventEmitterHook、OutputProcessorHook、AssistantStateHook、SkillHitHook 方法签名改 async def，但内部 `self._bus.emit(event)` 保持 sync 调用。async def 内调 sync 函数完全合法，且 bus.emit() 只是往 queue.Queue put 一个事件，几乎不阻塞。Phase 16 改 MessageBus 为 async 时再统一改 await。

### 简单 Hook 改造策略
- **D-09:** OutputProcessorHook、AssistantStateHook、SkillHitHook 三个 hook 内部只做简单计算和 bus.emit()，直接改 async def 签名即可，不需要 to_thread 包装（无阻塞 I/O）。EventEmitterHook 同理。

### Claude's Discretion
- ConfirmationHook.resolve()/cancel() 的具体实现细节（pending future 引用管理、多次调用防护）
- asyncio.TimeoutError 到 HookAction.SKIP 的错误消息格式
- run_* helpers 中对 HookAction/bool 返回值的短路逻辑在 async 下的实现方式
- 测试迁移范围和 async mock 策略
- EventEmitterHook 中 on_segment_complete 的向后兼容签名处理
- BaseHook 默认实现是否需要调整（当前已是 async def，应保持不变）

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements & Roadmap
- `.planning/REQUIREMENTS.md` — HOOK-01, HOOK-02, HOOK-03 requirements 定义
- `.planning/ROADMAP.md` — Phase 15 目标、依赖、成功标准
- `.planning/PROJECT.md` — 核心决策（Protocol hard cut, 自底向上迁移, Guard sync）

### 前置阶段 Context
- `.planning/phases/12-protocol/12-CONTEXT.md` — Hook Protocol async 签名决策（D-02, D-05）
- `.planning/phases/13-llm-provider/13-CONTEXT.md` — Kernel _sync_call_async 桥接模式（D-04）
- `.planning/phases/14-tool/14-CONTEXT.md` — ToolRegistry async + Kernel 桥接扩展（D-09）

### Hook Protocol + BaseHook + run_* helpers（核心改造目标）
- `matmaster/core/hooks.py` — Hook Protocol（7 async 方法）+ BaseHook 默认实现 + EventEmitterHook + 6 个 run_* helpers

### 5 个具体 Hook 实现（改造目标）
- `matmaster/hooks/confirmation.py` — ConfirmationHook + ReplyQueueLike Protocol（废弃目标）
- `matmaster/hooks/output_processor.py` — OutputProcessorHook
- `matmaster/hooks/assistant_state.py` — AssistantStateHook
- `matmaster/hooks/skill_hit.py` — SkillHitHook

### Kernel 桥接（扩展调用点）
- `matmaster/core/agent.py` — AgentKernel._run_loop() 中 6 个 run_* 调用点 + _sync_call_async 桥接函数

### src/ 层调用点（最小化适配）
- `src/services/agent_run_service.py` — ReplyQueueLike Protocol 定义（废弃）+ reply_queue.put_content/put_cancel 调用点

### 测试文件
- `tests/matmaster/hooks/` — Hook 单元测试（随实现迁移为 async）
- `tests/matmaster/core/test_hooks.py` — run_* helpers 测试
- `tests/conftest.py` — Phase 12 建立的 async mock factories

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `_sync_call_async` / `_sync_iterate_async` 桥接函数已在 agent.py 中建立（Phase 13）
- Phase 12 建立的 pytest-asyncio 基础设施 + async mock factories（tests/conftest.py）
- validate_async_protocol() helper 可验证改造后的 Hook 实现
- BaseHook 默认实现已是 async def（Phase 12 改的），无需修改

### Established Patterns
- Hook Protocol 使用 @runtime_checkable 装饰器 + 7 个 async 方法
- BaseHook 提供默认实现（pre_tool_call → CONTINUE, should_continue → True, 其他 → no-op）
- run_* helpers 封装遍历逻辑 + 短路语义（pre_tool_call: SKIP 短路, should_continue: False 短路）
- ConfirmationHook 通过 bus.emit(ConfirmationRequestEvent) 通知前端，然后等待回复
- Kernel 通过 _bridge_loop + _sync_call_async 调用 async 组件（Phase 13/14 模式）

### Integration Points
- run_* helpers 被 AgentKernel._run_loop() 在 6 个位置调用（pre_llm_call, should_continue, on_stream_chunk, on_segment_complete, pre_tool_call, post_tool_call, on_guard_blocked）
- ConfirmationHook 被 Exp.assemble() 组装，reply_queue 从 src/ 层注入
- EventEmitterHook 被 Exp.assemble() 组装，bus 从 Exp 层传入
- src/ agent_run_service.py 持有 reply_queue 引用，通过 HTTP 确认 API 调用 put_content/put_cancel

</code_context>

<specifics>
## Specific Ideas

- ConfirmationHook 改为 Future 模式后，每次 pre_tool_call 创建新 Future，resolve/cancel 操作的是"当前挂起的 Future"。需要 `_pending_future: asyncio.Future | None` 状态管理。
- run_* helpers 当前 broken（sync 调 async 不 await），Phase 15 修复这个问题的同时完成 async 化。
- ReplyQueueLike 废弃后，ConfirmationHook 的 __init__ 签名从 `(reply_queue, bus, timeout, ...)` 变为 `(loop, bus, timeout, ...)`。Exp.assemble() 组装逻辑需要相应调整。
- src/ 层的 ReplyQueueLike Protocol 定义和 _MockReplyQueue 测试辅助类一并清理。

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 15-hook*
*Context gathered: 2026-03-27*
