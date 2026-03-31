# Phase 19: 服务层桥接 + 并行 Tool Dispatch - Context

**Gathered:** 2026-03-29
**Status:** Ready for planning

<domain>
## Phase Boundary

src/ 服务层（agent_run_service.py）重构桥接架构：将当前双 event loop（router daemon thread + kernel run_until_complete）统一为单 event loop + daemon thread + run_coroutine_threadsafe 模式。Kernel 内 tool dispatch 从串行改为 asyncio.gather 并行执行。DevShell 桥接改为 asyncio.run()。

本阶段不重构 service 层业务逻辑、不改 MessageBus/EventRouter 内部实现、不改 DevShell async REPL（v2.1）。

</domain>

<decisions>
## Implementation Decisions

### Bridge 统一策略
- **D-01:** agent_run_service.py 统一为单 event loop。一个 daemon thread 运行 `_loop.run_forever()`，所有 async 调用（router.start、exp.build_runtime、kernel.run、exp cleanup、router.stop）通过 `asyncio.run_coroutine_threadsafe(coro, _loop).result()` 提交到该 loop。消除当前双 loop 架构（`_router_loop` + `_loop`）。
- **D-02:** Kernel 执行通过 `run_coroutine_threadsafe(...).result()` 阻塞等待，与当前 `run_until_complete()` 语义等价。ThreadPoolExecutor 线程阻塞等待是预期行为。
- **D-03:** ConfirmationHook 的 set_loop() 注入：kernel async context 中 `asyncio.get_running_loop()` 自然获取到统一 loop，无需特殊处理。

### 并行 Tool Dispatch
- **D-04:** 全部 tool 并行执行，不按类别区分（只读 vs 有副作用）。信任 LLM 不会在同一轮发出冲突的 tool_call 组合。与 Claude Code 等主流 Agent 做法一致。
- **D-05:** asyncio.gather(return_exceptions=True)，所有 tool 执行完毕后统一收集结果。失败的 tool 返回 exception 对象，转换为 ToolResult(status='error', content=str(exception))。LLM 看到错误后自行决定是否重试。
- **D-06:** Guard 评估和 pre_tool_call hook 保持串行（决策门控，可能拦截/跳过/暂停确认）。只有通过 guard + pre_hook 的 tool 进入并行执行。post_tool_call hook 在各 tool 完成后调用。

### DevShell Bridge
- **D-07:** DevShell runner.py 将现有 `new_event_loop() + run_until_complete()` 替换为 `asyncio.run()`。最小改动，不与 service 层统一模式（DevShell 是开发工具，无需 daemon thread + run_forever 的复杂度）。

### Claude's Discretion
- 并行 dispatch 后 ToolMessage 的追加顺序（保持 tool_calls 原序 vs 按完成顺序）
- stop_event 检查点是否需要在并行 dispatch 前/后增加
- 统一 loop 的生命周期管理细节（创建时机、cleanup 顺序、异常时的 loop 关闭）
- 并行 dispatch 是否需要 asyncio.Semaphore 限制并发数（当前 LLM 单轮 tool_call 数量有限，大概率不需要）

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements & Roadmap
- `.planning/REQUIREMENTS.md` -- BRDG-01, BRDG-02, TOOL-06 requirements 定义
- `.planning/ROADMAP.md` -- Phase 19 目标、依赖、成功标准（4 条）
- `.planning/PROJECT.md` -- Protocol hard cut, stop_event threading.Event, DevShell 延后

### 前置阶段 Context（桥接模式演进历程）
- `.planning/phases/18-exp/18-CONTEXT.md` -- Exp lifecycle async、service 层临时桥接（D-03）、spawn async 链路
- `.planning/phases/17-agentkernel/17-CONTEXT.md` -- Kernel async、bridge 从 agent.py 移入 Exp.run()（D-02）、不创建共享 bridge 模块（D-05）
- `.planning/phases/16-messagebus-eventrouter/16-CONTEXT.md` -- MessageBus asyncio.Queue、EventRouter asyncio.Task、service 层最小桥接（D-07）

### Service 层（核心改造目标）
- `src/services/agent_run_service.py` -- run_agent_sync() 当前实现。双 loop 架构：_router_loop（:305-311）、_loop（:430-487）。stop_event 传播（:456-461, :482）。quota 调用（:528-533）
- `src/services/stream_service.py` -- ConfirmationHookAdapter 桥接 legacy ReplyQueueLike

### Kernel Tool Dispatch（并行化目标）
- `matmaster/core/agent.py` -- AgentKernel._run_loop() 串行 tool dispatch（:176 `for tc in response.tool_calls:`）。Guard 评估（:177-192）、pre_tool_call hook（:194-206）、execute（:207-224）、post_tool_call hook（:227）

### DevShell（最小桥接）
- `matmaster/devshell/runner.py` -- DevRunner.run() 当前 bridge loop（:104 `new_event_loop()`）

### 测试文件
- `tests/matmaster/core/test_agent.py` -- Kernel 单元测试
- `tests/matmaster/integration/` -- E2E 和集成测试
- `tests/conftest.py` -- async mock factories

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- pytest-asyncio auto mode（Phase 12）：async def test_* 自动识别
- async mock factories（tests/conftest.py）：Phase 12 建立的 async mock 创建工具
- ToolResult(status, content, info) 统一结果模型：并行 dispatch 后的 exception 转换目标

### Established Patterns
- run_coroutine_threadsafe 模式：agent_run_service.py:312 已有 router.start() 使用此模式，Phase 19 统一推广
- EventRouter 的 asyncio.Task 消费循环（Phase 16）：在统一 loop 中自然协作
- AgentKernel.run() 已是 async def（Phase 17）：直接 await 即可
- ConfirmationHook set_loop() 注入（Phase 15）：async context 中 get_running_loop() 自动获取

### Integration Points
- agent_run_service.run_agent_sync() 在 ThreadPoolExecutor 线程中运行
- stop_event 是 threading.Event，从 service 层传入 kernel.run()（跨线程安全，is_set() 同步检查）
- EventRouter start/stop 需要在统一 loop 上执行
- quota 调用（:528-533）当前有 run_coroutine_threadsafe 和 asyncio.run 两个路径，需统一

</code_context>

<specifics>
## Specific Ideas

- agent_run_service.py 的核心改造：删除 `_router_loop` + `_router_loop_thread`，将 `_loop` 改为 daemon thread + run_forever，所有 async 调用统一通过 run_coroutine_threadsafe 提交。清理量大于新增量。
- Kernel 并行 dispatch 改造集中在 agent.py:176 的 `for tc in response.tool_calls:` 循环。拆分为：串行 guard+hook 门控 → 并行 asyncio.gather(tool executions) → 串行 post_hook + message append。
- DevShell 改造是一行替换：`_loop = asyncio.new_event_loop()` + `_loop.run_until_complete(...)` + `_loop.close()` → `asyncio.run(...)`。

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope

</deferred>

---

*Phase: 19-tool-dispatch*
*Context gathered: 2026-03-29*
