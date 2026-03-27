# Requirements: MatMaster v2.0 协程改造

**Defined:** 2026-03-26
**Core Value:** 三层抽象（playground->exp->agent）必须具有清晰、稳定、可测试的职责边界

## v2.0 Requirements

将 matmaster 框架从同步架构全链路改造为 async/await，为多 agent 编排做准备。

### Protocol 层

- [x] **PROT-01**: LLMProvider Protocol 的 chat() 和 chat_stream() 方法改为 async def，移除 chat_with_retry()（重试逻辑已在 Kernel._call_llm() 中）
- [x] **PROT-02**: Tool Protocol 的 run() 方法改为 async def，BuiltinTool ABC 的 execute() 改为 async def
- [x] **PROT-03**: Hook Protocol 全部 7 个方法改为 async def（on_agent_start, on_turn_start, on_tool_start, on_tool_end, on_turn_end, on_agent_end, on_guard_blocked）
- [x] **PROT-04**: Guard Protocol 的 evaluate() 保持同步（明确决策：纯计算无 I/O，async 增加开销无收益）
- [x] **PROT-05**: 为 async Protocol 添加 runtime validation helper，解决 runtime_checkable 不区分 sync/async 签名的问题

### LLM Provider

- [x] **LLMP-01**: OpenAIProvider 使用 AsyncOpenAI client，chat() 和 chat_stream() 实现为 async
- [x] **LLMP-02**: chat_stream() 使用 AsyncStream 作为 async iterator，正确处理 async context manager 生命周期
- [x] **LLMP-03**: provider 实例的创建和清理支持 async（__aenter__/__aexit__ 或显式 close）

### Tool 系统

- [ ] **TOOL-01**: 12 个 BuiltinTool 的 execute() 全部改为 async def
- [ ] **TOOL-02**: BashTool 使用 asyncio.create_subprocess_exec 替代 subprocess.run
- [ ] **TOOL-03**: 文件操作类 Tool（Read/Write/Edit/Glob/Grep）使用 asyncio.to_thread 包装同步文件 I/O
- [ ] **TOOL-04**: session-dependent tool 的 evomaster session 调用使用 asyncio.to_thread 桥接
- [ ] **TOOL-05**: SubAgentTool 的 spawn_fn 改为 async callable
- [ ] **TOOL-06**: 并行 Tool Dispatch — 同一轮多个 tool_call 使用 asyncio.gather 并行执行

### Hook 系统

- [ ] **HOOK-01**: 5 个具体 Hook 实现（OutputProcessorHook, EventEmitterHook, ConfirmationHook, HistoryHook, DirectHook）全部改为 async
- [ ] **HOOK-02**: ConfirmationHook 的 reply queue 机制适配 async（queue.Queue → asyncio 兼容方案）
- [ ] **HOOK-03**: EventEmitterHook 适配 async MessageBus

### 核心引擎

- [ ] **KERN-01**: AgentKernel.run() 改为 async generator（async def run() -> AsyncGenerator[AgentEvent, None]）
- [ ] **KERN-02**: Kernel 内部 LLM 调用改为 await（_call_llm, _call_llm_stream）
- [ ] **KERN-03**: Kernel 内部 tool dispatch 改为 await，支持并行执行
- [x] **KERN-04**: ContextCompactor 内部 LLM 调用改为 async
- [ ] **KERN-05**: stop_event 保留 threading.Event（跨线程安全，is_set() 同步检查不变）
- [ ] **KERN-06**: time.sleep 替换为 asyncio.sleep（retry backoff 等场景）

### 基础设施

- [ ] **INFR-01**: MessageBus 内部队列从 queue.Queue 改为 asyncio.Queue
- [ ] **INFR-02**: EventRouter 适配 async MessageBus（drain 逻辑改为 async）
- [ ] **INFR-03**: SSEHandler 和 PersistenceHandler 适配 async 事件消费

### Exp 生命周期

- [ ] **EXPL-01**: Exp.assemble() 改为 async def（MCP 初始化未来可能涉及网络 I/O）
- [ ] **EXPL-02**: Exp.build_runtime() 改为 async def
- [ ] **EXPL-03**: Exp.run() 改为 async def，内部 await kernel.run() 的 async generator
- [ ] **EXPL-04**: SubAgent spawn 完整 async 链路（async spawn_fn → async Exp.run() → async kernel）

### 服务层桥接

- [ ] **BRDG-01**: src/ 服务层（agent_run_service）通过 asyncio.run() 或 new_event_loop 桥接 async matmaster
- [ ] **BRDG-02**: stop_event 跨线程传播机制适配（service 线程 → matmaster event loop）

### 测试基础设施

- [x] **TEST-01**: 配置 pytest-asyncio（asyncio_mode="auto"），建立 async 测试基础设施
- [ ] **TEST-02**: 现有测试随实现阶段同步迁移为 async（不设独立测试迁移阶段）
- [ ] **TEST-03**: 迁移后全部测试通过，无回归

## v2.1 Requirements

延后到下一个里程碑。已记录但不在当前路线图中。

### 多 Agent 编排

- **ORCH-01**: 编排器层设计（多 agent 并发调度）
- **ORCH-02**: Agent 间通信机制（async 消息传递）
- **ORCH-03**: janus 双面队列替代 asyncio.Queue（跨线程 async/sync 桥接）

### DevShell

- **DSHL-01**: DevShell 改为 async REPL
- **DSHL-02**: DevShell async 入口与 matmaster async 核心直接集成

### evomaster Session

- **EVOS-01**: evomaster session 创建 async wrapper 消除 asyncio.to_thread 调用

## Out of Scope

| Feature | Reason |
|---------|--------|
| 多 agent 编排层 | v2.0 只做 async 基础设施，编排层 v2.1 设计 |
| src/ Web Service 层重构 | 保持现状，仅做最小桥接 |
| DevShell async 化 | 延后，asyncio.run() 包装即可 |
| 前端 UI 改动 | 本次只涉及后端框架层 |
| evomaster session async 化 | 上游依赖，用 to_thread 桥接 |
| Trio/anyio 引入 | 坚持 asyncio 标准库 |
| 双 Protocol（sync+async 并存） | 研究结论：anti-pattern，维护成本高 |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| PROT-01 | Phase 12 | Complete |
| PROT-02 | Phase 12 | Complete |
| PROT-03 | Phase 12 | Complete |
| PROT-04 | Phase 12 | Complete |
| PROT-05 | Phase 12 | Complete |
| LLMP-01 | Phase 13 | Complete |
| LLMP-02 | Phase 13 | Complete |
| LLMP-03 | Phase 13 | Complete |
| TOOL-01 | Phase 14 | Pending |
| TOOL-02 | Phase 14 | Pending |
| TOOL-03 | Phase 14 | Pending |
| TOOL-04 | Phase 14 | Pending |
| TOOL-05 | Phase 14 | Pending |
| TOOL-06 | Phase 19 | Pending |
| HOOK-01 | Phase 15 | Pending |
| HOOK-02 | Phase 15 | Pending |
| HOOK-03 | Phase 15 | Pending |
| KERN-01 | Phase 17 | Pending |
| KERN-02 | Phase 17 | Pending |
| KERN-03 | Phase 17 | Pending |
| KERN-04 | Phase 13 | Complete |
| KERN-05 | Phase 17 | Pending |
| KERN-06 | Phase 17 | Pending |
| INFR-01 | Phase 16 | Pending |
| INFR-02 | Phase 16 | Pending |
| INFR-03 | Phase 16 | Pending |
| EXPL-01 | Phase 18 | Pending |
| EXPL-02 | Phase 18 | Pending |
| EXPL-03 | Phase 18 | Pending |
| EXPL-04 | Phase 18 | Pending |
| BRDG-01 | Phase 19 | Pending |
| BRDG-02 | Phase 19 | Pending |
| TEST-01 | Phase 12 | Complete |
| TEST-02 | Phase 17 | Pending |
| TEST-03 | Phase 17 | Pending |

**Coverage:**
- v2.0 requirements: 35 total
- Mapped to phases: 35
- Unmapped: 0
- Complete: 10 (PROT-01~05, LLMP-01~03, KERN-04, TEST-01)

---
*Requirements defined: 2026-03-26*
*Last updated: 2026-03-27 after Phase 13 complete (restored from merge revert)*
