# Phase 17: AgentKernel 异步化 - Context

**Gathered:** 2026-03-28
**Status:** Ready for planning

<domain>
## Phase Boundary

将 AgentKernel 执行循环从 sync 全面改为 async def，收敛 Phase 13-16 所有叶节点异步依赖。run() / _run_loop() / _call_llm() / _do_stream_llm() 全部变 async，移除 _sync_call_async / _sync_iterate_async 桥接代码，time.sleep 替换为 asyncio.sleep。Bridge 从 agent.py 移入 Exp.run() 作为临时过渡（Phase 18 移除）。

本阶段不改 Exp 生命周期（Phase 18）、service 层桥接（Phase 19）、DevShell。

</domain>

<decisions>
## Implementation Decisions

### run() 返回模型
- **D-01:** `async def run() -> KernelRunResult`，不改为 async generator。事件继续走 MessageBus 传输路径。KERN-01 需修订：将"async generator (AsyncGenerator[AgentEvent, None])"改为"async def"。理由：Phase 16 已完成 MessageBus async 化，async generator 会推翻 push 事件架构变为 pull，改动量大且与已完成工作冲突。

### Exp→Kernel 过渡桥接
- **D-02:** Bridge 模式从 agent.py 移入 Exp.run()。Phase 17 完成后，sync Exp.run() 内创建临时 bridge loop (`asyncio.new_event_loop()` + `run_until_complete(kernel.run(...))`) 调用 async Kernel。与当前 agent.py 的 bridge 模式一致，只是位置上移一层。Phase 18 Exp async 化时删除。
- **D-03:** spawn 子 agent 路径（exp.py 的 spawn_fn 闭包内 `child_runtime.kernel.run()`）同理使用 bridge loop 包装。

### Bridge 函数处理
- **D-04:** agent.py 中 `_sync_call_async()` / `_sync_iterate_async()` / module-level `_bridge_loop` 全部删除。agent.py 变为纯 async 模块，零 bridge 残留。
- **D-05:** 不创建 matmaster/utils/async_bridge.py 共享模块。Exp.run() 内联 5 行桥接模式即可（Phase 18 前唯一消费者），不值得抽象。

### Claude's Discretion
- ConfirmationHook loop 注入方式：当前 run() 创建 bridge loop 并 set_loop()。async run() 内改用 asyncio.get_running_loop()，注入逻辑保持不变。
- Provider 生命周期管理：当前 run() 手动 __aenter__/__aexit__。async run() 改用 `async with spec.llm_provider:` 语法。summary_provider 同理。
- 测试迁移策略：随实现同步迁移为 async def test，pytest-asyncio auto mode 自动识别。
- GuardPipeline 保持同步调用（Phase 12 决策，纯计算无 I/O）。
- stop_event 保持 threading.Event（Phase 12 决策，is_set() 同步检查不变）。

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements & Roadmap
- `.planning/REQUIREMENTS.md` -- KERN-01~06, TEST-02, TEST-03 requirements 定义（注意 KERN-01 需修订）
- `.planning/ROADMAP.md` -- Phase 17 目标、依赖、成功标准
- `.planning/PROJECT.md` -- Protocol hard cut, Guard sync, stop_event threading.Event, DevShell 延后

### 前置阶段 Context（迁移模式参考）
- `.planning/phases/13-llm-provider/13-CONTEXT.md` -- _sync_call_async 桥接模式原始设计、_bridge_loop 机制、provider async context manager
- `.planning/phases/14-tool/14-CONTEXT.md` -- asyncio.to_thread 包装策略、ToolRegistry async execute
- `.planning/phases/15-hook/15-CONTEXT.md` -- Hook async 化、ConfirmationHook Future + set_loop 注入、run_* helpers async
- `.planning/phases/16-messagebus-eventrouter/16-CONTEXT.md` -- MessageBus async (asyncio.Queue)、EventRouter asyncio.Task、emit 调用者已迁移、service 层桥接模式

### Kernel（核心改造目标）
- `matmaster/core/agent.py` -- AgentKernel 当前实现（550 行），47 处 bridge 引用，2 处 time.sleep。run() / _run_loop() / _call_llm() / _do_stream_llm() 全部需 async 化

### Kernel 依赖（已 async，Phase 17 直接 await）
- `matmaster/core/hooks.py` -- run_* helpers（已 async，Phase 15）
- `matmaster/tools/registry.py` -- ToolRegistry.execute()（已 async，Phase 14）
- `matmaster/providers/openai_provider.py` -- OpenAIProvider chat/chat_stream（已 async，Phase 13）
- `matmaster/core/context_compactor.py` -- compact_if_needed()（已 async，Phase 13）
- `matmaster/core/bus.py` -- MessageBus.emit()（已 async，Phase 16）

### Exp（Phase 17 需加临时桥接）
- `matmaster/core/exp.py` -- Exp.run() (:277-294) 和 spawn_fn 闭包 (:115-120)，两处 kernel.run() 调用点需加 bridge

### 测试文件
- `tests/matmaster/core/test_agent.py` -- Kernel 单元测试（核心迁移目标）
- `tests/matmaster/integration/` -- E2E 和集成测试
- `tests/matmaster/devshell/` -- DevShell 测试（可能需要适配 async Kernel mock）
- `tests/conftest.py` -- Phase 12 建立的 async mock factories

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- pytest-asyncio auto mode（Phase 12）：async def test_* 自动识别，无需手动标记
- async mock factories（tests/conftest.py）：Phase 12 建立的 async mock 创建工具
- validate_async_protocol() helper：可验证 Kernel 方法的 async 签名

### Established Patterns
- Provider 用 async context manager 管理生命周期（Phase 13）
- ToolRegistry.execute() 已是 async def，await 即可（Phase 14）
- run_* helpers 已是 async def，await 即可（Phase 15）
- MessageBus.emit() 已是 async def，await 即可（Phase 16）
- ConfirmationHook 通过 set_loop() 注入 event loop 引用（Phase 15）

### Integration Points
- Exp.run() 调用 kernel.run()（Phase 17 后需 bridge，Phase 18 改 async）
- spawn_fn 闭包调用 child kernel.run()（同上）
- agent_run_service.py 通过 Exp.run() 间接调用 Kernel（Phase 19 桥接）
- DevShell 通过 Exp.run() 间接调用 Kernel（DevShell 不在 v2.0 范围）

</code_context>

<specifics>
## Specific Ideas

- agent.py 的改造本质是机械性的：47 处 `_sync_call_async(coro, _bridge_loop)` → `await coro`，`_sync_iterate_async(async_iter, loop)` → `async for item in async_iter`，`time.sleep(backoff)` → `await asyncio.sleep(backoff)`。
- run() 当前手动管理 provider 生命周期（3 层 try/finally）。async 后简化为 `async with spec.llm_provider:` + 可能的 `async with summary_provider:`。
- ConfirmationHook 的 set_loop() 注入：async run() 中用 `asyncio.get_running_loop()` 获取当前 loop，替代 `asyncio.new_event_loop()` 创建。语义不变。
- Exp.run() 的 bridge 模式：创建 event loop → run_until_complete(kernel.run()) → close loop。与当前 agent.py run() 结构一致。

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope

</deferred>

---

*Phase: 17-agentkernel*
*Context gathered: 2026-03-28*
