# Phase 18: Exp 生命周期异步化 - Context

**Gathered:** 2026-03-29
**Status:** Ready for planning

<domain>
## Phase Boundary

Exp 三阶段生命周期 (assemble/build_runtime/run) 全部改为 async def。SubAgent spawn 完整 async 链路打通（async spawn_fn → await child Exp.run() → await child kernel.run()）。移除 Phase 17 在 Exp.run() 和 spawn_fn 中留下的 bridge loop。cleanup callback 机制升级为支持 async callback。agent_run_service.py 加临时桥接适配 async build_runtime()。

本阶段不改 service 层整体重构（Phase 19）、DevShell、并行 Tool Dispatch。

</domain>

<decisions>
## Implementation Decisions

### assemble/build_runtime async 策略
- **D-01:** assemble() 和 build_runtime() 全部改为 async def，按 EXPL-01/EXPL-02 要求。当前虽无真正 async I/O，但统一接口，面向未来 MCP 网络初始化可直接 await。与 Protocol hard cut 决策一致（不维护 sync/async 双接口）。

### cleanup callback async 支持
- **D-02:** _run_cleanup_callbacks() 改为 async def。内部检测 callback 是否是 coroutine function（inspect.iscoroutinefunction），是则 await，否则直接调用。兼容现有 sync callback（tracker.clear、connector.cleanup），也为未来 MCP connector 的 async close 预留。_cleanup_callbacks 类型注解更新为 `list[Callable[[], None] | Callable[[], Coroutine]]`。

### service 层过渡桥接
- **D-03:** Phase 18 在 agent_run_service.py 加临时桥接。将已有的 bridge loop（`asyncio.new_event_loop()` + `run_until_complete`）扩展为同时覆盖 `build_runtime()` 调用。保证 Phase 18 完成后全部测试通过，service 层不中断。Phase 19 再整体重构 service 层。

### spawn_fn async 链路
- **D-04:** spawn_fn 闭包改为 async def。_make_spawn_fn 返回 async callable，内部 await child_exp.build_runtime() → await child_runtime.kernel.run()。移除 bridge loop。
- **D-05:** SpawnTool 直接覆写 async execute()，跳过 BuiltinTool 的 _execute + to_thread 模式，直接 await async spawn_fn()。SpawnTool 是唯一需要原生 async 执行的 BuiltinTool（spawn 本身就是 async 操作，不需要线程隔离）。_execute() 保留但标记为未使用（或删除），避免误调用。

### run() async 化
- **D-06:** Exp.run() 改为 async def。移除 Phase 17 留下的 `asyncio.new_event_loop()` + `run_until_complete(kernel.run(...))` bridge。直接 `await runtime.kernel.run(...)`。cleanup 在 async finally 中 `await self._run_cleanup_callbacks()`。

### Claude's Discretion
- _init_builtin_tools / _init_skill_tools / _init_mcp_tools 内部 helper 是否也改为 async def：当前都是 sync 操作，但 build_runtime() 变 async 后可以按需改造
- _resolve_compaction_llm 是否改 async：当前纯同步查表
- 测试迁移范围：test_exp.py 中 run/build_runtime/assemble 测试改 async def
- SpawnTool 覆写 execute() 后 _execute() 的处理方式（保留空实现 or 删除）

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements & Roadmap
- `.planning/REQUIREMENTS.md` -- EXPL-01~04, TOOL-05, BRDG-01 requirements 定义
- `.planning/ROADMAP.md` -- Phase 18 目标、依赖、成功标准
- `.planning/PROJECT.md` -- Protocol hard cut, Guard sync, stop_event threading.Event, DevShell 延后

### 前置阶段 Context（迁移模式参考）
- `.planning/phases/17-agentkernel/17-CONTEXT.md` -- Kernel async 化完成、bridge 从 agent.py 移入 Exp.run()、run() 返回 KernelRunResult（非 async generator）
- `.planning/phases/16-messagebus-eventrouter/16-CONTEXT.md` -- MessageBus async、EventRouter asyncio.Task、service 层最小桥接模式
- `.planning/phases/14-tool/14-CONTEXT.md` -- BuiltinTool execute() async via to_thread、ToolRegistry async execute
- `.planning/phases/15-hook/15-CONTEXT.md` -- Hook async 化、ConfirmationHook set_loop 注入

### Exp（核心改造目标）
- `matmaster/core/exp.py` -- Exp 当前实现（497 行）。assemble(:137-145)、build_runtime(:149-250)、run(:274-305)、_make_spawn_fn(:89-133)、_run_cleanup_callbacks(:71-85)
- `matmaster/tools/builtin/spawn_tool.py` -- SpawnTool（141 行）。_execute(:122-140) 调用 sync spawn_fn

### Exp 的 BuiltinTool 基类
- `matmaster/tools/builtin/base.py` -- BuiltinTool ABC（70 行）。execute() async via to_thread(_execute)。SpawnTool 需覆写此模式

### 外部调用者（需加桥接）
- `src/services/agent_run_service.py` -- build_runtime() 同步调用点（~Line 430 区域）、已有 bridge loop 用于 kernel.run()（:470-481）
- `matmaster/devshell/runner.py` -- DevShell 使用 Exp，不在 v2.0 范围，暂不改
- `matmaster/devshell/repl.py` -- 同上

### 测试文件
- `tests/matmaster/core/test_exp.py` -- Exp 单元测试（run/build_runtime/assemble/spawn/cleanup）
- `tests/matmaster/integration/` -- E2E 和集成测试
- `tests/conftest.py` -- Phase 12 建立的 async mock factories

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- pytest-asyncio auto mode（Phase 12）：async def test_* 自动识别
- async mock factories（tests/conftest.py）：Phase 12 建立的 async mock 创建工具
- BuiltinTool execute() async 模式（Phase 14）：to_thread 包装参考
- Phase 17 bridge 模式：Exp.run() 中的 `asyncio.new_event_loop()` + `run_until_complete()`，Phase 18 移除

### Established Patterns
- Provider 用 async context manager 管理生命周期（Phase 13）
- ToolRegistry.execute() 已是 async def（Phase 14）
- MessageBus.emit() 已是 async def（Phase 16）
- AgentKernel.run() 已是 async def 返回 KernelRunResult（Phase 17）
- agent_run_service 用独立 bridge loop 调用 async kernel（Phase 17）

### Integration Points
- agent_run_service.py 调用 exp.build_runtime()（Phase 18 后需 bridge，Phase 19 整体重构）
- DevShell 调用 exp.build_runtime()（不在 v2.0 范围）
- SpawnTool 通过 spawn_fn 闭包调用子 Exp 完整生命周期
- _cleanup_callbacks 被 tracker.clear 和 connector.cleanup 使用

</code_context>

<specifics>
## Specific Ideas

- Exp.run() 的改造核心是移除 5 行 bridge 代码（new_event_loop/run_until_complete/close），替换为直接 await kernel.run()。非常机械。
- _make_spawn_fn 的改造同理：移除 bridge loop，async def 闭包直接 await build_runtime + kernel.run。
- SpawnTool 覆写 execute() 时需要保留错误处理（spawn_fn is None 校验、参数校验），逻辑与当前 _execute() 一致，只是最后一步从 `self._spawn_fn(...)` 变为 `await self._spawn_fn(...)`。
- agent_run_service 的桥接：将 `runtime = exp.build_runtime(ctx, ...)` 改为 `runtime = _loop.run_until_complete(exp.build_runtime(ctx, ...))`，复用已有的 `_loop = asyncio.new_event_loop()`。但注意 _loop 创建位置可能需要上移（当前在 kernel.run 前才创建）。
- _run_cleanup_callbacks 的 async 检测用 `inspect.iscoroutinefunction(cb)`，与 Phase 12 的 validate_async_protocol 模式一致。

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope

</deferred>

---

*Phase: 18-exp*
*Context gathered: 2026-03-29*
