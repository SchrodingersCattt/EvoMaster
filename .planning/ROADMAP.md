# Roadmap: MatMaster Framework Refactoring (v2)

## Milestones

- ✅ **v1 MatMaster Framework Refactoring** -- Phases 1-7 (shipped 2026-03-22)
- ✅ **v1.1 Agent 外围能力构建** -- Phases 8-11 (shipped 2026-03-25)
- 🚧 **v2.0 matmaster 协程改造** -- Phases 12-19 (in progress)

## Phases

<details>
<summary>✅ v1 MatMaster Framework Refactoring (Phases 1-7) -- SHIPPED 2026-03-22</summary>

- [x] Phase 1: Foundation Contracts (2/2 plans) -- completed 2026-03-21
- [x] Phase 2: Agent Kernel (3/3 plans) -- completed 2026-03-22
- [x] Phase 3: Exp Assembly Layer (4/4 plans) -- completed 2026-03-22
- [x] Phase 4: Playground Layer (3/3 plans) -- completed 2026-03-22
- [x] Phase 5: Integration and Quality (5/5 plans) -- completed 2026-03-22
- [x] Phase 6: Service Layer Wiring (2/2 plans) -- completed 2026-03-22
- [x] Phase 7: Cleanup and Traceability (2/2 plans) -- completed 2026-03-22

Full details: milestones/v1-ROADMAP.md

</details>

<details>
<summary>✅ v1.1 Agent 外围能力构建 (Phases 8-11) -- SHIPPED 2026-03-25</summary>

- [x] Phase 8: BuiltinTool 基础设施与核心 Tools (3/3 plans) -- completed 2026-03-24
- [x] Phase 9: 文件操作 Tools (3/3 plans) -- completed 2026-03-25
- [x] Phase 10: Tool Description 与 System Prompt 设计 (2/2 plans) -- completed 2026-03-25
- [x] Phase 11: SubAgent Spawn 机制 (3/3 plans) -- completed 2026-03-25

</details>

### 🚧 v2.0 matmaster 协程改造 (In Progress)

**Milestone Goal:** 将 matmaster 框架从同步架构全链路改造为 async/await，为多 agent 编排做准备。自底向上分层迁移：叶节点 I/O (Provider/Tool) -> 中间层 (Hook/Bus/Router) -> 核心引擎 (Kernel/Exp) -> 服务层桥接。

- [x] **Phase 12: Protocol 层 + 测试基础设施** - 将所有 Protocol 签名改为 async，建立 pytest-asyncio 基础设施和 async validation helper (completed 2026-03-26)
- [x] **Phase 13: LLM Provider 异步实现** - OpenAIProvider 改用 AsyncOpenAI，chat/chat_stream 实现为 async (completed 2026-03-27)
- [x] **Phase 14: Tool 系统异步化** - 12 个 BuiltinTool + ToolRegistry 全部 async，session 调用用 asyncio.to_thread 桥接 (completed 2026-03-27)
- [x] **Phase 15: Hook 系统异步化** - 5 个 Hook 实现和 run_* helper 全部 async，ConfirmationHook reply queue 适配 (completed 2026-03-27)
- [ ] **Phase 16: MessageBus + EventRouter 异步化** - MessageBus 改用 asyncio.Queue，EventRouter 改为 asyncio.Task
- [ ] **Phase 17: AgentKernel 异步化** - Kernel.run() 改为 async，收敛所有异步依赖，ContextCompactor async 化
- [ ] **Phase 18: Exp 生命周期异步化** - assemble/build_runtime/run 全部 async，SubAgent spawn 完整 async 链路
- [ ] **Phase 19: 服务层桥接 + 并行 Tool Dispatch** - src/ 层 asyncio.run() 桥接 + 多 tool_call 并行执行优化

## Phase Details

### Phase 12: Protocol 层 + 测试基础设施
**Goal**: 所有 async Protocol 合约明确定义，pytest-asyncio 基础设施就绪，async mock 可用于后续阶段测试
**Depends on**: Phase 11 (v1.1 completed)
**Requirements**: PROT-01, PROT-02, PROT-03, PROT-04, PROT-05, TEST-01
**Success Criteria** (what must be TRUE):
  1. LLMProvider Protocol 的 chat/chat_stream 签名为 async def，chat_with_retry 已从 Protocol 中移除
  2. Tool Protocol 的 execute 和 BuiltinTool ABC 的 _execute 签名为 async def
  3. Hook Protocol 全部 7 个方法签名为 async def，Guard Protocol 的 evaluate 保持 sync 不变
  4. 运行 `pytest --co` 可成功收集测试，pytest-asyncio auto mode 配置生效，async def test_* 自动识别
  5. async validation helper (inspect.iscoroutinefunction) 可检测出 sync 实现误匹配 async Protocol
**Plans**: 2/2 plans complete

Plans:
- [x] 12-01-PLAN.md -- LLMProvider/Tool/BuiltinTool/Hook/EventHandler/ReplyQueueLike Protocol async 签名改造 + chat_with_retry 删除
- [x] 12-02-PLAN.md -- validate_async_protocol helper + async mock factories + pytest-asyncio 配置 + chat_with_retry 测试清理

### Phase 13: LLM Provider 异步实现
**Goal**: LLM 调用全链路非阻塞，OpenAIProvider 使用 AsyncOpenAI，streaming 通过 AsyncIterator 消费
**Depends on**: Phase 12
**Requirements**: LLMP-01, LLMP-02, LLMP-03
**Success Criteria** (what must be TRUE):
  1. OpenAIProvider.chat() 通过 AsyncOpenAI client 发起请求，await 返回 LLMResponse
  2. OpenAIProvider.chat_stream() 返回 AsyncIterator[StreamChunk]，可通过 async for 消费
  3. provider 支持 async context manager 生命周期管理（进入时创建 client，退出时关闭连接）
  4. 所有 provider 测试通过 pytest-asyncio 运行，mock 使用 async def
**Plans**: 2/2 plans complete

Plans:
- [x] 13-01-PLAN.md -- LLMProvider Protocol __aenter__/__aexit__ + OpenAIProvider async 实现 + provider 测试迁移
- [x] 13-02-PLAN.md -- ContextCompactor async + Kernel 共享 bridge loop + test_agent/devshell 全面适配

### Phase 14: Tool 系统异步化
**Goal**: 所有 tool 的 execute 方法为 async，session-dependent tool 通过 asyncio.to_thread 桥接同步 evomaster API
**Depends on**: Phase 12
**Requirements**: TOOL-01, TOOL-02, TOOL-03, TOOL-04, TOOL-05
**Success Criteria** (what must be TRUE):
  1. 12 个 BuiltinTool 的 execute() 均可通过 await 调用，返回正确结果
  2. BashTool 使用 asyncio.create_subprocess_exec（session-free 模式）或 asyncio.to_thread (session-dependent 模式)执行命令
  3. 文件操作类 Tool (Read/Write/Edit/Glob/Grep) 的 session 调用通过 asyncio.to_thread 包装，不阻塞 event loop
  4. SubAgentTool 的 spawn_fn 参数类型为 async callable，_execute 通过 await spawn_fn(...) 调用
  5. 所有 tool 测试通过 pytest-asyncio 运行
**Plans**: 2/2 plans complete

Plans:
- [x] 14-01-PLAN.md -- BuiltinTool ABC async execute + sync _execute rollback + ToolRegistry async execute + 核心测试迁移
- [x] 14-02-PLAN.md -- LazyMCPTool/SkillTool/EvoToolAdapter async + Kernel 桥接 + 全量 tool 测试 async 迁移

### Phase 15: Hook 系统异步化
**Goal**: 所有 Hook 实现的 7 个方法为 async，EventEmitterHook 可 await 异步 bus.emit()，ConfirmationHook 的 reply queue 支持 async 等待
**Depends on**: Phase 12
**Requirements**: HOOK-01, HOOK-02, HOOK-03
**Success Criteria** (what must be TRUE):
  1. 5 个具体 Hook (OutputProcessor/EventEmitter/Confirmation/History/Direct) 的所有方法均可 await
  2. ConfirmationHook 的 reply 等待机制从 queue.Queue.get() 改为 asyncio 兼容方案，跨线程 reply 推送正常工作
  3. EventEmitterHook 内部调用 await bus.emit(event) 而非同步 bus.emit()
  4. run_pre_tool_call / run_post_tool_call 等 helper 全部为 async def，内部 await 每个 hook
**Plans**: 2 plans

Plans:
- [x] 15-01-PLAN.md -- run_* helpers async + 5 Hook async (EventEmitter/OutputProcessor/AssistantState/SkillHit/DevStreamHook) + Kernel 桥接 + test_agent.py sync Hook 修复 + 测试迁移
- [x] 15-02-PLAN.md -- ConfirmationHook Future 重构 + Kernel loop 注入 + src/ 层完整 confirmation 通路适配 (ConfirmationHookAdapter) + ReplyQueueLike deprecated

### Phase 16: MessageBus + EventRouter 异步化
**Goal**: 事件传输链路全面 async：MessageBus 使用 asyncio.Queue，EventRouter 作为 asyncio.Task 消费事件
**Depends on**: Phase 15
**Requirements**: INFR-01, INFR-02, INFR-03
**Success Criteria** (what must be TRUE):
  1. MessageBus.emit() 和 MessageBus.get() 为 async 方法，底层使用 asyncio.Queue
  2. EventRouter 使用 asyncio.create_task 启动消费循环（替代 threading.Thread），支持 graceful stop + drain
  3. SSEHandler 和 PersistenceHandler 的 handle() 方法为 async def，可在 EventRouter 的 async 消费循环中 await
  4. Bus + Router 在同一个 event loop 中协作，事件从 emit 到 handler.handle 全链路无阻塞
**Plans**: TBD

### Phase 17: AgentKernel 异步化
**Goal**: Kernel 执行循环全面 async，收敛所有叶节点异步依赖，LLM/Tool/Hook 调用全部 await，ContextCompactor 内部 LLM 调用 async
**Depends on**: Phase 13, Phase 14, Phase 15, Phase 16
**Requirements**: KERN-01, KERN-02, KERN-03, KERN-04, KERN-05, KERN-06, TEST-02, TEST-03
**Success Criteria** (what must be TRUE):
  1. AgentKernel.run() 签名为 async def，内部 LLM 调用和 tool dispatch 通过 await 执行
  2. _do_stream_llm 使用 async for 消费 provider.chat_stream() 返回的 AsyncIterator
  3. ContextCompactor.compact_if_needed() 为 async，内部 LLM 摘要调用通过 await 执行
  4. stop_event 保持 threading.Event 类型，is_set() 同步检查不变（跨线程安全）
  5. retry backoff 使用 asyncio.sleep 替代 time.sleep，不阻塞 event loop
  6. 现有测试全部迁移为 async 并通过，无回归（976+ tests pass）
**Plans**: TBD

### Phase 18: Exp 生命周期异步化
**Goal**: Exp 三阶段生命周期 (assemble/build_runtime/run) 全部 async，SubAgent spawn 完整 async 链路打通
**Depends on**: Phase 17
**Requirements**: EXPL-01, EXPL-02, EXPL-03, EXPL-04
**Success Criteria** (what must be TRUE):
  1. Exp.assemble() 为 async def，未来 MCP 网络初始化可直接 await
  2. Exp.build_runtime() 为 async def，内部组装的 provider/tool/hook 均为 async 组件
  3. Exp.run() 为 async def，内部 await kernel.run() 并处理 async cleanup callback
  4. SubAgent spawn 完整 async 链路：async spawn_fn -> await child Exp.run() -> await child kernel.run()，父 agent 在 spawn 期间不阻塞 event loop
**Plans**: TBD

### Phase 19: 服务层桥接 + 并行 Tool Dispatch
**Goal**: src/ 服务层通过 asyncio.run() 桥接 async matmaster，多 tool_call 场景支持并行执行
**Depends on**: Phase 18
**Requirements**: BRDG-01, BRDG-02, TOOL-06
**Success Criteria** (what must be TRUE):
  1. agent_run_service.run_agent_sync() 通过 asyncio.new_event_loop().run_until_complete() 调用 async matmaster，无 RuntimeError
  2. 外部取消信号（stop API / Redis 轮询）能跨线程传播到 async kernel 的 stop_event，agent 正确终止
  3. 同一轮 LLM 返回多个 tool_call 时，tool 通过 asyncio.gather 并行执行，总耗时接近最慢单 tool 耗时（而非串行累加）
  4. DevShell 可通过 asyncio.run() 临时包装调用 async matmaster 进行开发验证
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 12 -> 13 -> 14 -> 15 -> 16 -> 17 -> 18 -> 19

Note: Phase 13 和 Phase 14 依赖关系上可以并行（都只依赖 Phase 12），但建议按顺序执行以控制变更范围。Phase 15 同理。Phase 17 是收敛点，必须等 13-16 全部完成。

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Foundation Contracts | v1 | 2/2 | Complete | 2026-03-21 |
| 2. Agent Kernel | v1 | 3/3 | Complete | 2026-03-22 |
| 3. Exp Assembly Layer | v1 | 4/4 | Complete | 2026-03-22 |
| 4. Playground Layer | v1 | 3/3 | Complete | 2026-03-22 |
| 5. Integration and Quality | v1 | 5/5 | Complete | 2026-03-22 |
| 6. Service Layer Wiring | v1 | 2/2 | Complete | 2026-03-22 |
| 7. Cleanup and Traceability | v1 | 2/2 | Complete | 2026-03-22 |
| 8. BuiltinTool 基础设施与核心 Tools | v1.1 | 3/3 | Complete | 2026-03-24 |
| 9. 文件操作 Tools | v1.1 | 3/3 | Complete | 2026-03-25 |
| 10. Tool Description 与 System Prompt 设计 | v1.1 | 2/2 | Complete | 2026-03-25 |
| 11. SubAgent Spawn 机制 | v1.1 | 3/3 | Complete | 2026-03-25 |
| 12. Protocol 层 + 测试基础设施 | v2.0 | 2/2 | Complete | 2026-03-26 |
| 13. LLM Provider 异步实现 | v2.0 | 2/2 | Complete | 2026-03-27 |
| 14. Tool 系统异步化 | v2.0 | 2/2 | Complete | 2026-03-27 |
| 15. Hook 系统异步化 | v2.0 | 2/2 | Complete   | 2026-03-27 |
| 16. MessageBus + EventRouter 异步化 | v2.0 | 0/0 | Not started | - |
| 17. AgentKernel 异步化 | v2.0 | 0/0 | Not started | - |
| 18. Exp 生命周期异步化 | v2.0 | 0/0 | Not started | - |
| 19. 服务层桥接 + 并行 Tool Dispatch | v2.0 | 0/0 | Not started | - |
