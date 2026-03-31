# Project Research Summary

**Project:** matmaster-evo v2.0 协程改造
**Domain:** Python agent framework sync-to-async migration
**Researched:** 2026-03-26
**Confidence:** HIGH

## Executive Summary

matmaster v2.0 的核心任务是将一套三层同步 Agent 框架（Playground/Exp/AgentKernel）改造为全链路 async/await 架构。研究结论明确：改造不需要引入新的运行时依赖，所有所需 async 原语均来自 Python 3.13 stdlib（asyncio.Queue、asyncio.Event、TaskGroup、create_subprocess_exec），OpenAI AsyncOpenAI 已在当前安装的 openai 2.20.0 中可用，唯一需新增的依赖是 dev-only 的 pytest-asyncio。与此同时，参考 Pydantic AI、OpenAI Agents SDK、Microsoft Agent Framework 等框架的实现方式，成熟的 async agent 架构模式清晰、落地成本可评估。

推荐的改造路径是自底向上的分层迁移：先将 LLM Provider 和 Tool 系统等叶节点改为 async，再向上依次改造 Hook/MessageBus/EventRouter，最后完成 AgentKernel 主循环和 Exp 生命周期的 async 化，最终以一个薄薄的 asyncio.run() 包装层桥接 src/ 服务层。Guard 系统（纯 CPU 计算）和 Playground.prepare()（文件 I/O，out of scope）保持同步不变。这种按依赖顺序分 Phase 推进的方式可以最小化在任何时刻处于"半 async"状态的组件数量。

关键风险集中在 async 传染性引发的链路断裂、runtime_checkable Protocol 无法检测 async/sync 签名不匹配、跨线程 stop_event 设计、以及异步 Generator 资源泄漏四个方向。每个风险都有明确的预防措施：在构建时加入 async 签名 validation helper、保留 threading.Event 用于跨线程取消信号（通过 loop.call_soon_threadsafe 桥接）、在 LLM streaming 中用 contextlib.aclosing 包裹资源、并以 PYTHONASYNCIODEBUG=1 在 CI 中检测阻塞调用。

## Key Findings

### Recommended Stack

无需新增运行时依赖，Python 3.13 stdlib 和现有依赖已覆盖全部 async 需求。唯一新增的是 dev 依赖 pytest-asyncio（asyncio_mode = "auto" 配置，自动识别所有 async def test_*）。明确不引入 anyio、trio、janus、aiofiles 等第三方库——在 v2.0 全链路 async 场景下均为过度依赖。

**Core technologies:**
- Python 3.13.2 asyncio: asyncio.Queue、asyncio.Event、asyncio.TaskGroup、create_subprocess_exec — 所有 async 原语的唯一来源
- openai 2.20.0 AsyncOpenAI: LLM 异步 API 客户端 — 已安装版本直接可用，无需升级
- httpx 0.28.1 AsyncClient: AsyncOpenAI 底层 HTTP 客户端 — 已安装版本直接可用
- pytest-asyncio >= 1.0.0: async 测试运行器 — 唯一新增 dev 依赖

**Async primitive mapping（sync -> async）:**
- queue.Queue -> asyncio.Queue（MessageBus）
- threading.Event -> asyncio.Event（内部 cancel），threading.Event 保留用于跨线程边界
- time.sleep() -> await asyncio.sleep()（retry backoff）
- Iterator[StreamChunk] -> AsyncIterator[StreamChunk]（LLM streaming）
- subprocess.run() -> asyncio.to_thread(session.exec_bash)（BashTool，因 evomaster session 是 sync C 扩展）

### Expected Features

**Must have（table stakes，v2.0 必须完成）:**
- Async kernel 执行循环（AgentKernel.run() -> async def）— 每个主流框架都以 async 为核心
- AsyncIterator[StreamChunk] LLM streaming — LLM streaming 是最长 I/O，必须非阻塞
- Async LLM provider protocol（chat/chat_stream -> async def）— 无此改造整个 async 链路无法启动
- Async tool execute()（12 个 builtin tool + adapter + registry）— tool 是 I/O 密集型，必须 async
- asyncio.Event 取消机制 — 替换 threading.Event 用于 event loop 内部取消
- asyncio.Queue MessageBus — 替换 queue.Queue，保证 async producer/consumer 安全
- Async hooks（全部 7 个 hook 方法）— EventEmitterHook 要 emit 到 async bus 必须 async
- await asyncio.sleep 替换 time.sleep — 否则 retry 阻塞整个 event loop
- Async ContextCompactor — 内部 LLM 调用必须 async
- Async Exp lifecycle（build_runtime/run）— 编排所有 async 子组件

**Should have（高价值优化，v2.0 Phase 2）:**
- 并行 tool dispatch（asyncio.gather 在多 tool_call 时并发执行）— 对 LLM 多工具调用场景 2-5x 提速
- Sequential tool mode（opt-out 标志）— 有副作用依赖的 tool 需要顺序执行
- Async subagent spawn（spawn_fn -> async def）— 父 agent 等待子 agent 期间不阻塞 event loop
- Per-tool timeout（asyncio.wait_for 包装）— 防止单个 tool 阻塞整个 loop

**Defer（v2.0 out of scope）:**
- Async generator kernel 返回类型（KernelRunResult -> AsyncGenerator[AgentEvent]）— 重大 API 变更，留 v3
- Background compaction（非阻塞后台压缩）— 增加复杂度
- 分布式 event bus（Redis Pub/Sub）— 独立关注点
- Actor model 并发（每 agent 独立 event loop）— 过度工程
- evomaster session 改为 async — out of scope，用 asyncio.to_thread 桥接

### Architecture Approach

改造遵循自底向上的依赖链原则：叶节点 I/O 组件（LLMProvider、BuiltinTool）先改 async，中间层（Hook、MessageBus、EventRouter）跟进，最后是顶层编排（AgentKernel、Exp）。Guard 系统（纯 CPU 计算）和 Playground 层（out of scope）明确保持同步。src/ 服务层只做最小改动：在 ThreadPoolExecutor worker 线程中用 asyncio.new_event_loop() + loop.run_until_complete() 包装，保留 FastAPI 事件循环不变。

**Major components and changes:**
1. LLMProvider Protocol — chat/chat_stream 签名改 async，移除 chat_with_retry（retry 归 kernel 负责）
2. Tool Protocol + BuiltinTool — execute/_execute 改 async，evomaster session 调用用 asyncio.to_thread() 桥接
3. Hook Protocol（7 方法）+ EventEmitterHook — 全部改 async
4. MessageBus — queue.Queue -> asyncio.Queue，emit/get 变 async
5. EventRouter — threading.Thread -> asyncio.Task（background consumer）
6. AgentKernel.run() — 核心循环改 async，await LLM/tool/hook，time.sleep -> asyncio.sleep
7. Exp.build_runtime()/run() — 编排层改 async，spawn_fn 返回 async callable
8. AgentRunService（src/）— 添加 asyncio.new_event_loop() + run_until_complete() wrapper，仅此一处改动

**Sync/async 边界（仅 3 处）:**
- Boundary 1：AgentRunService -> matmaster（asyncio.new_event_loop per run）
- Boundary 2：matmaster tools -> evomaster session（asyncio.to_thread）
- Boundary 3：DevShell -> matmaster（asyncio.run，延后至 v2.0 后）

### Critical Pitfalls

1. **runtime_checkable Protocol 不检查 async/sync 签名** — isinstance() 对实现了同名 sync 方法的旧类仍返回 True，await 时才 TypeError。防范：在 build_runtime 和 kernel 初始化处加 inspect.iscoroutinefunction() 运行时 validation helper；CI 中加专项测试验证所有 mock 满足 async Protocol。

2. **asyncio.run() 嵌套 RuntimeError** — 在已有 running event loop 的上下文中调用 asyncio.run() 直接崩溃。防范：asyncio.run() 只出现在唯一的 sync->async 边界（AgentRunService.run_agent_sync），kernel 内部的 spawn_fn 改为 async 后直接 await，不再需要 asyncio.run()。

3. **在 async 代码中调用阻塞 I/O 冻结 event loop** — async/await 是协作式并发，time.sleep/session.exec_bash/openai.OpenAI 等阻塞调用会挂起所有协程。防范：LLM 调用和 sleep 在第一阶段必须解决；BashTool 等用 asyncio.to_thread() 桥接；开发期用 PYTHONASYNCIODEBUG=1 自动检测超过 100ms 的阻塞。

4. **stop_event 跨线程问题（threading.Event vs asyncio.Event）** — asyncio.Event 不是线程安全的，跨线程 set() 行为未定义。防范：matmaster 内部的 cancel check 使用 threading.Event.is_set()（线程安全，非阻塞），跨线程信号通过 loop.call_soon_threadsafe(stop_event.set) 发送。不要在 async kernel 内部用 asyncio.Event 来接收跨线程取消信号。

5. **async Generator 资源泄漏（LLM streaming）** — async generator 如果被提前中断（exception/cancel），GC 的 aclose() 调度依赖 event loop 存活，finally 块可能不执行，导致 httpx 连接泄漏。防范：在 _do_stream_llm 中用 async with aclosing(provider.chat_stream(...)) as stream；OpenAIProvider.chat_stream 内部加 try/finally 关闭 SDK stream；pytest 中启用 filterwarnings = ["error::ResourceWarning"]。

## Implications for Roadmap

Based on research, the critical path of async dependencies determines the phase structure. No phase can proceed until its upstream leaf dependencies are async. The total code change is approximately 900 lines of production code plus test migration across 863 tests.

### Phase 1: 协议层 + 测试基础设施（Foundation）

**Rationale:** Protocol 定义是整个链路的合约，必须最先确定。测试基础设施必须在第一个实现阶段之前就绪。这个阶段会有意地破坏所有 863 个测试（Protocol 改变 -> 实现不匹配），这是可接受的代价——后续每个 Phase 会逐模块修复。

**Delivers:** 明确的 async 合约边界；pytest-asyncio 配置就绪；async mock fixtures 可用。

**Addresses:** table stakes 的 async LLM provider protocol、async tool protocol、async hook protocol（仅 Protocol 定义，不含实现）

**Avoids:** Pitfall C1（runtime_checkable 不检查 async 签名）——在此 Phase 加入 validation helper

**Files:** matmaster/types/llm_provider.py、tools/tool_registry.py、core/hooks.py、types/guards.py（NO CHANGE）；pyproject.toml 新增 pytest-asyncio；tests/conftest.py async 配置；async mock factories

### Phase 2: LLM Provider 实现（Leaf I/O — Highest Value）

**Rationale:** LLM 调用是所有 I/O 中耗时最长的操作，async 化价值最高。且所有后续 Phase 的组件（kernel、compactor）都依赖 async LLM provider。

**Delivers:** OpenAIProvider 使用 AsyncOpenAI；chat_stream 返回 AsyncIterator；retry backoff 从 time.sleep 改为 asyncio.sleep；LLM streaming 资源安全关闭。

**Addresses:** table stakes 的 async LLM provider 实现；async retry（asyncio.sleep）

**Avoids:** Pitfall C3（阻塞 I/O 冻结 event loop）中的 LLM 和 sleep 部分；Pitfall C5（async generator 资源泄漏）

**Uses:** openai.AsyncOpenAI（already available）、httpx.AsyncClient（already available）、contextlib.aclosing

### Phase 3: Tool 系统（Leaf I/O）

**Rationale:** Tool 执行是 kernel loop 的另一个 I/O 密集型节点，与 LLM Provider 并列为叶节点，可与 Phase 2 并行实施但依赖 Phase 1 的 async Tool Protocol。

**Delivers:** 全部 12 个 builtin tool + adapter + registry 完成 async execute；evomaster session 调用通过 asyncio.to_thread() 桥接；spawn_fn 改为 async callable（基础框架）。

**Addresses:** table stakes 的 async tool execute；async subagent spawn（Phase 1 of spawn）

**Avoids:** Pitfall C3（session.exec_bash 阻塞 event loop）；Pitfall M5（spawn_fn 链路断裂）

**Uses:** asyncio.to_thread（桥接 evomaster session）

### Phase 4: Hook 系统

**Rationale:** Hook 是 kernel loop 的中间层拦截点，所有 7 个方法需 async 才能在 pre/post_tool_call 中 emit 到 async MessageBus。Hook 改造相对独立，复杂度低。

**Delivers:** Hook Protocol 全部 7 方法 async 实现；EventEmitterHook 可 await async bus.emit()；run_* helpers 改为 async。

**Addresses:** table stakes 的 async hooks（全部 7 个方法）

**Avoids:** Pitfall M4（sync 消费 async generator 的 TypeError）间接关联

### Phase 5: MessageBus + EventRouter（Infrastructure）

**Rationale:** MessageBus 是 Hook 和 EventRouter 的桥梁，必须在 Hook 系统（Phase 4）完成后才能完整替换，确保 EventEmitterHook -> asyncio.Queue -> EventRouter 的全链路 async。

**Delivers:** MessageBus 从 queue.Queue 改为 asyncio.Queue；EventRouter 从 threading.Thread 改为 asyncio.Task；所有 EventHandler.handle() 改为 async。

**Addresses:** table stakes 的 asyncio.Queue MessageBus

**Avoids:** Pitfall M2（MessageBus 线程边界问题）

### Phase 6: AgentKernel（Core Loop — Convergence）

**Rationale:** Kernel 是所有叶节点的消费者（LLM、Tool、Hook、Bus），必须等 Phase 2-5 全部完成后才能改造。这是改动最大的单文件变更，也是 async 改造的核心收敛点。

**Delivers:** AgentKernel.run() 改为 async；_call_llm/_do_stream_llm 改为 async（async for 替换 for）；stop_event 设计最终确定（threading.Event 保留用于跨线程，asyncio 内部机制用 CancelledError）；ContextCompactor.compact_if_needed() 改为 async。

**Addresses:** table stakes 的 async kernel 执行循环；async context compaction

**Avoids:** Pitfall C3（LLM/sleep 阻塞 event loop，在此 Phase 彻底解决）；Pitfall C4（stop_event 线程安全设计）

### Phase 7: Exp 生命周期（Orchestration）

**Rationale:** Exp 编排 kernel + tool + hook + bus，依赖 Phase 6 kernel async 完成后才能改造。

**Delivers:** Exp.build_runtime()/run() 改为 async；async cleanup callbacks；完整的 async subagent spawn 链路。

**Addresses:** table stakes 的 async Exp lifecycle；should-have 的 async subagent spawn

**Avoids:** Pitfall M3（清理回调资源泄漏）；Pitfall M5（spawn_fn 链路断裂）

### Phase 8: 服务层桥接 + 并行 Tool 优化（Integration）

**Rationale:** src/ 服务层只需最小改动（asyncio.new_event_loop() wrapper）。并行 tool dispatch（asyncio.gather）作为高价值优化在 async 基础设施稳定后实施。

**Delivers:** AgentRunService.run_agent_sync() 用 asyncio.new_event_loop().run_until_complete() 包装；loop.call_soon_threadsafe 桥接 stop_event；并行 tool dispatch（多 tool_call 时并发）；sequential tool mode opt-out。

**Addresses:** should-have 的并行 tool dispatch；per-tool timeout

**Avoids:** Pitfall C2（asyncio.run() 嵌套 RuntimeError）——明确此处是唯一的 sync->async 边界

### Phase Ordering Rationale

- Protocol 先行（Phase 1）确保所有实现层的合约明确，即使这会短暂破坏所有测试
- 叶节点并行（Phase 2+3）可同步推进，互不依赖，均依赖 Phase 1 完成
- Hook/Bus/Router（Phase 4+5）在叶节点完成后依序推进，Phase 4 -> Phase 5 有依赖（EventEmitterHook 需要 async hook 先完成）
- Kernel（Phase 6）在所有 async 依赖就绪后一次性完成核心改造，是最大风险收敛点
- Exp/Service（Phase 7+8）作为最终收口，Phase 7 -> Phase 8 有依赖

### Research Flags

**需要在 Phase 执行时深入研究的领域:**
- Phase 4（Hook 系统）中的 ConfirmationHook：当前用 queue.Queue.get() 阻塞等待用户回复。改为 async 后，ConfirmationHook.pre_tool_call 需要 asyncio.Queue.get()，但 src/ 层向 queue 推送 confirmation reply 的机制（跨线程）需要仔细设计——两个不同 event loop 无法共享 asyncio.Queue
- Phase 5（EventRouter）的 drain 逻辑：Task 取消时如何保证残余事件被消费完毕，需要在实现时验证 drain_timeout 策略是否可靠

**标准模式，可直接实施（无需额外研究）:**
- Phase 2（OpenAIProvider 改为 AsyncOpenAI）— 已有完整代码示例，本地验证通过
- Phase 6（time.sleep -> asyncio.sleep）— 机械替换，无歧义
- Phase 8（asyncio.new_event_loop wrapper）— 标准 ThreadPoolExecutor + asyncio 桥接模式

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | 所有关键依赖均经本地环境验证（AsyncOpenAI、asyncio.Queue、pytest-asyncio 可用性）；版本兼容矩阵完整 |
| Features | HIGH | 参考 Pydantic AI、OpenAI Agents SDK、Microsoft Agent Framework 三个主流框架的实现方式；must-have 与 should-have 边界清晰 |
| Architecture | HIGH | 基于代码库实际源码分析（而非假设性分析）；迁移顺序基于实际依赖图；每个 sync/async 边界均有具体机制方案 |
| Pitfalls | HIGH | 5 个 Critical + 6 个 Moderate pitfall 均基于 Python 官方文档、PEP 544/525 和实际 codebase 结构；每个均有 Detection 方法 |

**Overall confidence:** HIGH

### Gaps to Address

- **ConfirmationHook 跨线程 reply 机制：** 当前 confirmation_reply API 端点向 queue.Queue push 回复，ConfirmationHook.pre_tool_call() blocking get()。async 化后 reply queue 需要变为 asyncio.Queue，但 API handler 在 FastAPI event loop 中 put，kernel 的 ConfirmationHook 在独立 event loop 中 get——两个不同 event loop 无法共享 asyncio.Queue。需要在 Phase 4 执行前设计具体的跨 loop 通信方案（建议：用 janus.Queue 或 asyncio.Queue + loop.call_soon_threadsafe 组合）。

- **DevShell async 入口（Phase 9，out of scope）：** DevShell 改造被明确推迟。但在 v2.0 开发期间，DevShell 需要用 asyncio.run(exp.run(...)) 临时包装，这个包装本身需要在 Phase 7 完成后立即添加，否则开发调试工具失效。建议在 Phase 7 完成时同步添加临时 wrapper，正式改造留 v2.1。

- **863 个测试的迁移节奏：** 研究确认了分层迁移策略（types -> core -> tools -> integration），但具体到每个 Phase 需要修复多少测试、是否允许 CI 中存在 expected-failing 测试，需要在 Phase 1 完成后根据实际破坏范围决定。

## Sources

### Primary (HIGH confidence)

- Python 3.13 官方文档 — asyncio.Queue、asyncio.Event、asyncio.TaskGroup、create_subprocess_exec、asyncio 调试模式
- PEP 544 (runtime_checkable Protocol) — 确认 isinstance 只检查方法名存在性，不检查 coroutine function 标记
- PEP 525 (Asynchronous Generators) — 确认 async generator finalization 依赖 event loop 存活机制
- openai 2.20.0 本地验证 — from openai import AsyncOpenAI 成功；AsyncStream 是 AsyncIterator + async context manager
- pytest-asyncio 1.3.0 官方文档 — auto mode 配置；asyncio_default_fixture_loop_scope；async fixture 标记规则

### Secondary (MEDIUM confidence)

- Pydantic AI 官方文档 — async-first 设计；sync tools 自动 asyncio.to_thread；parallel tool execution 为默认行为
- OpenAI Agents SDK 官方文档 — RunHooks 7 个 async 方法；Runner.run_sync() = loop.run_until_complete(run())；sync tools via asyncio.to_thread
- Microsoft Agent Framework 官方文档 — async CompactionStrategy Protocol；async middleware 模式
- janus 库文档（aio-libs）— 双面 queue 性能说明："for sync-only and async-only cases, use native queues"

### Tertiary (LOW confidence)

- gradual asyncio migration 社区博客 — Protocol hard cut vs gradual dual-protocol 的取舍；验证了 matmaster 选择 hard cut 的合理性（内部代码库，无外部消费者）

---
*Research completed: 2026-03-26*
*Ready for roadmap: yes*
