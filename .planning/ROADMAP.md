# Roadmap: MatMaster Framework Evolution

## Milestones

- ✅ **v1 MatMaster Framework Refactoring** - Phases 1-7 (shipped 2026-03-22)
- ✅ **v1.1 Agent 外围能力构建** - Phases 8-11 (shipped 2026-03-25)
- ✅ **v2.0 matmaster 协程改造** - Phases 12-24 (shipped 2026-03-30)
- 🚧 **v2.1 matmaster/ 完全独立化** - Phases 25-30 (active 2026-04-01)

## Phases

<details>
<summary>✅ v1 MatMaster Framework Refactoring (Phases 1-7) -- SHIPPED 2026-03-22</summary>

- [x] Phase 1: Foundation Contracts (2/2 plans) - completed 2026-03-21
- [x] Phase 2: Agent Kernel (3/3 plans) - completed 2026-03-22
- [x] Phase 3: Exp Assembly Layer (4/4 plans) - completed 2026-03-22
- [x] Phase 4: Playground Layer (3/3 plans) - completed 2026-03-22
- [x] Phase 5: Integration and Quality (5/5 plans) - completed 2026-03-22
- [x] Phase 6: Service Layer Wiring (2/2 plans) - completed 2026-03-22
- [x] Phase 7: Cleanup and Traceability (2/2 plans) - completed 2026-03-22

Full details: milestones/v1-ROADMAP.md

</details>

<details>
<summary>✅ v1.1 Agent 外围能力构建 (Phases 8-11) -- SHIPPED 2026-03-25</summary>

- [x] Phase 8: BuiltinTool 基础设施与核心 Tools (3/3 plans) - completed 2026-03-24
- [x] Phase 9: 文件操作 Tools (3/3 plans) - completed 2026-03-25
- [x] Phase 10: Tool Description 与 System Prompt 设计 (2/2 plans) - completed 2026-03-25
- [x] Phase 11: SubAgent Spawn 机制 (3/3 plans) - completed 2026-03-25

</details>

<details>
<summary>✅ v2.0 matmaster 协程改造 (Phases 12-24) -- SHIPPED 2026-03-30</summary>

- [x] Phase 12: Protocol 层 + 测试基础设施 - completed 2026-03-26
- [x] Phase 13: LLM Provider 异步实现 - completed 2026-03-27
- [x] Phase 14: Tool 系统异步化 - completed 2026-03-27
- [x] Phase 15: Hook 系统异步化 - completed 2026-03-27
- [x] Phase 16: MessageBus + EventRouter 异步化 - completed 2026-03-28
- [x] Phase 17: AgentKernel 异步化 - completed 2026-03-28
- [x] Phase 18: Exp 生命周期异步化 - completed 2026-03-29
- [x] Phase 19: 服务层桥接 + 并行 Tool Dispatch - completed 2026-03-29
- [x] Phase 20: Confirmation Flow Recovery - completed 2026-03-30
- [x] Phase 21: Async Leaf I/O Cleanup - completed 2026-03-29
- [x] Phase 22: Audit Metadata Backfill - completed 2026-03-29
- [x] Phase 23: Verification + Nyquist Closure - completed 2026-03-30
- [x] Phase 24: emit_nowait Tech Debt Cleanup - completed 2026-03-29

</details>

### 🚧 v2.1 matmaster/ 完全独立化 (Active -- Phases 25-30)

**Milestone Goal:** 让 `matmaster/` 运行时路径不再 import `evomaster/`、`playground/` 或 `src/`，成为可独立运行、测试与发布的核心包。三方向解耦：evomaster (~15 imports, 8 files)、playground (1 import)、src (6 imports, 2 files)。

- [ ] **Phase 25: Session 与 Playground 原生化** - 切断 session/config/playground 对 evomaster 的运行时硬依赖，建立 matmaster 自有环境准备入口
- [ ] **Phase 26: Tool 内化与遗留工具收归** - 移除 EvoToolAdapter、内化 builtin helper、收归 MonitorJobTool 与 web_search_tool，让全部 tool 在 matmaster 原生运行
- [ ] **Phase 27: MCP 与 Calculation 原生链路** - 将 lazy_mcp、schema cache 与 calculation path adaptor 收回 matmaster 侧，保持 Bohrium 协议兼容
- [ ] **Phase 28: src 反向依赖反转与 Consumer 迁移** - 消除 bohrium_setup/script_env 对 src 的反向依赖，同时迁移 src 消费者到 matmaster 原生数据结构
- [ ] **Phase 29: 主执行路径切换** - API/worker 与本地 Web 调试后端改走 matmaster 原生入口，保持主路径持续可运行
- [ ] **Phase 30: 解耦审计与独立性证明** - 用 import audit、隔离测试与迁移文档证明 matmaster 可脱离 evomaster/playground/src 独立运行

## Phase Details

<details>
<summary>✅ v2.0 Phase Details (Phases 12-24) -- collapsed for readability</summary>

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
  2. BashTool 使用 asyncio.create_subprocess_exec（session-free 模式）或 asyncio.to_thread (session-dependent 模式) 执行命令
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
**Plans**: 3/3 plans complete

Plans:
- [x] 15-01-PLAN.md -- run_* helpers async + 5 Hook async (EventEmitter/OutputProcessor/AssistantState/SkillHit/DevStreamHook) + Kernel 桥接 + test_agent.py sync Hook 修复 + 测试迁移
- [x] 15-02-PLAN.md -- ConfirmationHook Future 重构 + Kernel loop 注入 + src/ 层完整 confirmation 通路适配 (ConfirmationHookAdapter) + ReplyQueueLike deprecated
- [x] 15-03-PLAN.md -- Gap closure: _sync_call_async _bridge_loop 参数修复 + REQUIREMENTS.md 状态更新

### Phase 16: MessageBus + EventRouter 异步化
**Goal**: 事件传输链路全面 async：MessageBus 使用 asyncio.Queue，EventRouter 作为 asyncio.Task 消费事件
**Depends on**: Phase 15
**Requirements**: INFR-01, INFR-02, INFR-03
**Success Criteria** (what must be TRUE):
  1. MessageBus.emit() 和 MessageBus.get() 为 async 方法，底层使用 asyncio.Queue
  2. EventRouter 使用 asyncio.create_task 启动消费循环（替代 threading.Thread），支持 graceful stop + drain
  3. SSEHandler 和 PersistenceHandler 的 handle() 方法为 async def，可在 EventRouter 的 async 消费循环中 await
  4. Bus + Router 在同一个 event loop 中协作，事件从 emit 到 handler.handle 全链路无阻塞
**Plans**: 2/2 plans complete

Plans:
- [x] 16-01-PLAN.md -- MessageBus async (asyncio.Queue) + EventRouter async (asyncio.Task) + SSEHandler/PersistenceHandler/WorkspaceHandler async handle() + 测试迁移
- [x] 16-02-PLAN.md -- 13 个 emit 调用点 await 迁移 + service 层 emit_nowait/router 桥接/SSEHandler 构造函数适配

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
  6. 现有测试全部迁移为 async 并通过，无回归（1187+ tests pass）
**Plans**: 2 plans

Plans:
- [x] 17-01-PLAN.md -- AgentKernel async 化 (run/run_loop/call_llm/do_stream_llm) + bridge 删除 + test_agent.py 35 测试 async 迁移
- [x] 17-02-PLAN.md -- 全部 sync 入口 bridge 适配 (Exp.run + spawn_fn + DevRunner + agent_run_service) + test_subagent_spawn AsyncMock + 外部测试 async 迁移 + 全量回归

### Phase 18: Exp 生命周期异步化
**Goal**: Exp 三阶段生命周期 (assemble/build_runtime/run) 全部 async，SubAgent spawn 完整 async 链路打通
**Depends on**: Phase 17
**Requirements**: EXPL-01, EXPL-02, EXPL-03, EXPL-04
**Success Criteria** (what must be TRUE):
  1. Exp.assemble() 为 async def，未来 MCP 网络初始化可直接 await
  2. Exp.build_runtime() 为 async def，内部组装的 provider/tool/hook 均为 async 组件
  3. Exp.run() 为 async def，内部 await kernel.run() 并处理 async cleanup callback
  4. SubAgent spawn 完整 async 链路：async spawn_fn -> await child Exp.run() -> await child kernel.run()，父 agent 在 spawn 期间不阻塞 event loop
**Plans**: 2/2 plans complete

Plans:
- [x] 18-01-PLAN.md -- Exp 核心 4 方法 async 化 (assemble/build_runtime/run/cleanup) + AgentRuntime 类型更新 + service/DevShell 桥接 + test_exp.py 迁移
- [x] 18-02-PLAN.md -- SubAgent spawn async 链路 (_make_spawn_fn async 闭包 + SpawnTool execute() override) + spawn 测试 AsyncMock 迁移

### Phase 19: 服务层桥接 + 并行 Tool Dispatch
**Goal**: src/ 服务层通过统一 daemon thread event loop 桥接 async matmaster，多 tool_call 场景支持并行执行
**Depends on**: Phase 18
**Requirements**: BRDG-01, BRDG-02, TOOL-06
**Success Criteria** (what must be TRUE):
  1. agent_run_service.run_agent_sync() 通过 asyncio.new_event_loop().run_until_complete() 调用 async matmaster，无 RuntimeError
  2. 外部取消信号（stop API / Redis 轮询）能跨线程传播到 async kernel 的 stop_event，agent 正确终止
  3. 同一轮 LLM 返回多个 tool_call 时，tool 通过 asyncio.gather 并行执行，总耗时接近最慢单 tool 耗时（而非串行累加）
  4. DevShell 可通过 asyncio.run() 临时包装调用 async matmaster 进行开发验证
**Plans**: 2/2 plans complete

Plans:
- [x] 19-01-PLAN.md -- agent_run_service.py 双 loop 统一为单 daemon thread + run_coroutine_threadsafe + DevShell asyncio.run()
- [x] 19-02-PLAN.md -- AgentKernel 串行 tool dispatch 改为 asyncio.gather 并行 + TestParallelToolDispatch 测试

### Phase 20: Confirmation Flow Recovery
**Goal**: 恢复 ConfirmationHook 的 async 等待模型，修复 stream/service adapter 接口错配，并重新打通 confirmation flow
**Depends on**: Phase 19
**Requirements**: HOOK-02
**Gap Closure**: Closes audit requirement gap HOOK-02, ConfirmationHookAdapter interface mismatch, and broken Confirmation Flow
**Success Criteria** (what must be TRUE):
  1. ConfirmationHook 不再依赖 queue.Queue.get() 阻塞等待，而是恢复 asyncio 兼容的等待机制
  2. ConfirmationHook 对外暴露 stream_service 所需的 resolve()/cancel() 接口，adapter 不再触发 AttributeError
  3. agent_run_service 能在受控范围内重新启用 confirmation hook，confirmation reply 可真正影响 tool execution
  4. confirmation path 的 hook/service/integration 回归测试全部通过
**Plans**: 2/2 plans complete

Plans:
- [x] 20-01-PLAN.md -- 恢复 Future-based ConfirmationHook 与 hook/adapter 回归测试
- [x] 20-02-PLAN.md -- 受控重新启用 service/worker confirmation bridge，并关闭 HOOK-02 traceability gap

### Phase 21: Async Leaf I/O Cleanup
**Goal**: 完成叶子 I/O 层遗留 async 清理，落地 BashTool 原生 async subprocess 路径，并移除 provider 孤儿接口
**Depends on**: Phase 19
**Requirements**: TOOL-02
**Gap Closure**: Closes audit requirement gap TOOL-02 and OpenAIProvider orphaned integration gap
**Success Criteria** (what must be TRUE):
  1. BashTool 在 session-free 执行路径使用 asyncio.create_subprocess_exec，而不是同步 subprocess bridge
  2. session-dependent 执行路径的行为边界被明确保留或拆分，不再与 TOOL-02 的实现目标混淆
  3. OpenAIProvider 删除孤立的 chat_with_retry 接口，对外 API 与 LLMProvider Protocol 保持一致
  4. tool/provider 相关测试更新并通过
**Plans**: 1 plan

Plans:
- [x] 21-01-PLAN.md -- BashTool native async subprocess 双路径 + OpenAIProvider chat_with_retry 孤儿删除

### Phase 22: Audit Metadata Backfill
**Goal**: 回填 v2.0 planning artifacts 的 audit 元数据缺口，保证 re-audit 时 requirements 与 summary 可追踪
**Depends on**: Phase 20, Phase 21
**Requirements**: None (audit metadata closure)
**Gap Closure**: Closes remaining audit metadata/documentation gaps, especially missing requirements-completed frontmatter in historical summaries
**Success Criteria** (what must be TRUE):
  1. audit 标记缺失的 SUMMARY.md 均补齐 requirements-completed frontmatter
  2. v2.0 planning artifacts 的元数据与当前 ROADMAP/REQUIREMENTS 状态保持一致
  3. 重新运行 milestone audit 时，不再因 planning metadata 缺口报 documentation gap
**Plans**: 1 plan

Plans:
- [x] 22-01-PLAN.md -- Fix 3 SUMMARY.md frontmatter + PROJECT.md stale content + audit file commit with resolution addendum

### Phase 23: Verification + Nyquist Closure
**Goal**: 关闭 Phase 20 VERIFICATION.md 缺失导致的 HOOK-02 验证缺口，修复 Phase 20/21/22 的 Nyquist 合规状态
**Depends on**: Phase 22
**Requirements**: HOOK-02 (verification gap closure)
**Gap Closure**: Closes HOOK-02 verification gap and Nyquist compliance for Phases 20, 21, 22
**Success Criteria** (what must be TRUE):
  1. Phase 20 VERIFICATION.md 存在且验证 HOOK-02 为 SATISFIED
  2. Phase 20 VALIDATION.md 存在且 nyquist_compliant=true
  3. Phase 21 VALIDATION.md nyquist_compliant=true, wave_0_complete=true
  4. Phase 22 VALIDATION.md 存在且 nyquist_compliant=true
  5. Re-audit 时 HOOK-02 状态从 partial 变为 satisfied
**Plans**: 1 plan

Plans:
- [x] 23-01-PLAN.md -- Create/update VERIFICATION.md + VALIDATION.md for Phases 20/21/22, update milestone audit

### Phase 24: emit_nowait Tech Debt Cleanup
**Goal**: 将 `matmaster/` 包内全部 12 处 emit_nowait() 升级为 await bus.emit()，清理过期注释、更新 bus docstring 和 stop_event 类型标注
**Depends on**: Phase 23
**Requirements**: HOOK-03 (integration tech debt closure)
**Gap Closure**: Closes emit_nowait integration gap and minor tech debt items
**Success Criteria** (what must be TRUE):
  1. `matmaster/` 内全部 emit_nowait() 调用替换为 await bus.emit()
  2. 4 处引用 sync kernel context 的过期注释已删除
  3. `bus.py` docstring 更新为 emit() 是主路径
  4. `agent_run_service.py` 中 stop_event 类型标注从 Any 改为 threading.Event
  5. 全量测试通过，无回归
**Plans**: 1 plan

Plans:
- [x] 24-01-PLAN.md -- Migrate 12 emit_nowait to await bus.emit() + stale comment cleanup + bus docstring + stop_event type + test assertion updates

</details>

### Phase 25: Session 与 Playground 原生化
**Goal**: matmaster 具备自有的 session 抽象、config 加载与 playground 环境准备能力，切断对 evomaster session/config/mixin 的运行时依赖
**Depends on**: Phase 24
**Requirements**: PLAY-01, PLAY-02, PLAY-03
**Success Criteria** (what must be TRUE):
  1. 开发者在不安装 evomaster 的环境中可以直接创建并使用 matmaster.sessions.local.LocalSession，供 builtin tools 执行本地命令与文件操作
  2. matmaster 原生 session factory 可以创建 local、docker、ssh session，而 matmaster.core.playground.Playground 运行时不再导入 evomaster.agent.session 下的任何模块
  3. matmaster.core.playground.Playground 可以独立完成主配置加载、workspace 准备、logging 初始化与 session 装配，不再依赖 evomaster.config.ConfigManager 或 PlaygroundSessionMixin
  4. 现有依赖 session 的 builtin tools (BashTool, ReadTool 等) 可以通过 matmaster 原生 session 正常执行文件和命令操作
**Plans**: 3 plans

Plans:
- [x] 25-01-PLAN.md — Session Protocol + LocalSession 升级 + tmux 辅助模块
- [x] 25-02-PLAN.md — SSHSession 原生实现（内联 SSHEnv）
- [ ] 25-03-PLAN.md — Playground 参数化改造 + PlaygroundManager YAML 解析 + Mixin 内联

### Phase 26: Tool 内化与遗留工具收归
**Goal**: 全部 tool 能力在 matmaster.tools 原生运行，消除 EvoToolAdapter、evomaster builtin helper 依赖、MonitorJobTool 和 web_search_tool 的外部导入
**Depends on**: Phase 25
**Requirements**: TOOL-07, TOOL-08, TOOL-09, TOOL-10
**Success Criteria** (what must be TRUE):
  1. 开发者可以在 matmaster.tools 中直接注册并执行遗留 builtin 能力，EvoToolAdapter 从 tool 注册链路中移除
  2. matmaster.tools.builtin 中的 bash safety 检查与 edit helper 由 matmaster 原生实现提供，不再导入 evomaster.agent.tools.builtin 下的任何模块
  3. MonitorJobTool 通过 matmaster 原生注册或 skill 机制提供，exp.py 不再 lazy import evomaster.agent.tools.builtin.monitor_job
  4. web_search_tool 通过 matmaster 原生实现或 skill 注册提供，exp.py 不再 import playground.mat_master.tools.web_search（消除 matmaster -> playground 依赖）
  5. 在仅安装 matmaster 的环境中加载全部 builtin tools 和 exp 注册的 tools 时，不会触发任何 evomaster 或 playground 运行时导入
**Plans**: 3 plans

Plans:
- [x] 26-01-PLAN.md — Helper 内化（bash_safety + editor 内联）+ web_search 名称修正
- [x] 26-02-PLAN.md — MonitorJobTool 搬入 matmaster 并改继承 BuiltinTool
- [ ] 26-03-PLAN.md — EvoToolAdapter 清理 + exp.py 原生注册切换 + 回归测试

### Phase 27: MCP 与 Calculation 原生链路
**Goal**: MCP 连接、schema cache 与 calculation path adaptor 全部收回 matmaster 侧，同时维持 Bohrium executor/storage/OSS 协议兼容
**Depends on**: Phase 25, Phase 26
**Requirements**: MCP-01, CALC-01, CALC-02
**Success Criteria** (what must be TRUE):
  1. matmaster.tools.lazy_mcp 可以独立连接 MCP server、缓存 schema 并执行 tool，不依赖 evomaster.agent.tools.mcp 下的 manager 或 connector
  2. matmaster 侧可以原生解析 calculation runtime config、path adaptor 与 schema cache，正确识别 path 输入并构造 executor、storage 参数
  3. Bohrium / calculation tool 在 submit 或 run 场景下继续生成与当前协议兼容的 executor、storage、OSS 上传与远端路径适配行为，不引入协议破坏
  4. cache_mcp_schemas.py 和 eval_tooling_snapshot.py 不再 import evomaster MCP manager 或 calculation adaptor
**Plans**: 3 plans

Plans:
- [ ] 25-01-PLAN.md — Session Protocol + LocalSession 升级 + tmux 辅助模块
- [ ] 25-02-PLAN.md — SSHSession 原生实现（内联 SSHEnv）
- [ ] 25-03-PLAN.md — Playground 参数化改造 + PlaygroundManager YAML 解析 + Mixin 内联

### Phase 28: src 反向依赖反转与 Consumer 迁移
**Goal**: 消除 matmaster 对 src 的反向依赖（bohrium_setup + script_env），同时迁移 src 消费者到 matmaster 原生数据结构与 session 抽象
**Depends on**: Phase 25
**Requirements**: INVR-01, INVR-02, CONS-03, CONS-04
**Success Criteria** (what must be TRUE):
  1. matmaster/integration/bohrium_setup.py 不再 lazy import src.services.agent_run_bohrium 的 5 个函数，改为通过回调注入或将逻辑移入 matmaster 侧
  2. matmaster/tools/script_env.py 不再 lazy import src.utils.constant.BOHRIUM_OPENAPI_HOST，改为配置注入或 matmaster 侧常量
  3. src/services/chat_history.py 等对话历史构建链路可以直接消费 matmaster 原生 message / tool_call 数据结构，保持当前历史恢复行为
  4. src/services/agent_run_bohrium.py 等 session-sensitive 服务路径可以切换到 matmaster session abstraction 或显式 compat layer，避免直接依赖 evomaster session class
**Plans**: 3 plans

Plans:
- [ ] 25-01-PLAN.md — Session Protocol + LocalSession 升级 + tmux 辅助模块
- [ ] 25-02-PLAN.md — SSHSession 原生实现（内联 SSHEnv）
- [ ] 25-03-PLAN.md — Playground 参数化改造 + PlaygroundManager YAML 解析 + Mixin 内联

### Phase 29: 主执行路径切换
**Goal**: API/worker 与本地 Web 调试后端切换到 matmaster 原生入口，不再依赖 evomaster.core.get_playground_class，且主路径持续可运行
**Depends on**: Phase 27, Phase 28
**Requirements**: CONS-01, CONS-02
**Success Criteria** (what must be TRUE):
  1. API/worker 主执行路径通过 matmaster 原生入口初始化 playground、exp 与 agent，保持消息发送、run 执行与事件推送主流程可用
  2. 本地 Web 调试后端通过 matmaster 原生入口初始化 playground，保持启动、会话恢复与流式输出行为
  3. 主执行路径中不再出现 evomaster.core.get_playground_class 或等价的 evomaster 入口调用
**Plans**: 3 plans

Plans:
- [ ] 25-01-PLAN.md — Session Protocol + LocalSession 升级 + tmux 辅助模块
- [ ] 25-02-PLAN.md — SSHSession 原生实现（内联 SSHEnv）
- [ ] 25-03-PLAN.md — Playground 参数化改造 + PlaygroundManager YAML 解析 + Mixin 内联

### Phase 30: 解耦审计与独立性证明
**Goal**: 用 import audit、隔离测试和迁移文档证明 matmaster 可脱离 evomaster/playground/src 独立运行，并为 v2.2 清理留出清晰后手
**Depends on**: Phase 29
**Requirements**: QUAL-06, QUAL-07, QUAL-08
**Success Criteria** (what must be TRUE):
  1. 仓库提供 import audit 或等价测试，能够明确证明 matmaster/ 运行时模块不再直接 import evomaster、playground 或 src
  2. 在不安装 evomaster 的受控测试环境中，tests/matmaster/ 的核心测试集可以通过，证明 matmaster 可独立运行
  3. 仓库提供一份解耦迁移文档，明确记录保留的 compat layer、剩余遗留路径与后续清理顺序
  4. 全量测试通过，无回归（1195+ tests pass 作为基线）

**Plans**: 3 plans

Plans:
- [ ] 25-01-PLAN.md — Session Protocol + LocalSession 升级 + tmux 辅助模块
- [ ] 25-02-PLAN.md — SSHSession 原生实现（内联 SSHEnv）
- [ ] 25-03-PLAN.md — Playground 参数化改造 + PlaygroundManager YAML 解析 + Mixin 内联

## Progress

**Execution Order:**
历史 phases 已完成到 Phase 24。v2.1 执行顺序为 25 -> 26 -> 27 -> 28 -> 29 -> 30。

Phase 25 先切断环境准备层耦合，为后续所有迁移建立稳定底座。Phase 26 在此之上内化全部 tool 能力（含 playground/ 依赖的 web_search_tool），消除 matmaster -> evomaster 和 matmaster -> playground 的 tool 层依赖。Phase 27 收回 MCP/calculation 高风险链路，依赖 Phase 25 (session) 和 Phase 26 (tool 注册)。Phase 28 合并处理 src 反向依赖反转与 consumer 迁移，因为 bohrium_setup 同时涉及两个方向。Phase 29 在基础设施和消费者就绪后切换主入口。Phase 30 最后用 import audit、隔离测试与迁移文档做证据收口。

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
| 15. Hook 系统异步化 | v2.0 | 3/3 | Complete | 2026-03-27 |
| 16. MessageBus + EventRouter 异步化 | v2.0 | 2/2 | Complete | 2026-03-28 |
| 17. AgentKernel 异步化 | v2.0 | 2/2 | Complete | 2026-03-28 |
| 18. Exp 生命周期异步化 | v2.0 | 2/2 | Complete | 2026-03-29 |
| 19. 服务层桥接 + 并行 Tool Dispatch | v2.0 | 2/2 | Complete | 2026-03-29 |
| 20. Confirmation Flow Recovery | v2.0 | 2/2 | Complete | 2026-03-30 |
| 21. Async Leaf I/O Cleanup | v2.0 | 1/1 | Complete | 2026-03-29 |
| 22. Audit Metadata Backfill | v2.0 | 1/1 | Complete | 2026-03-29 |
| 23. Verification + Nyquist Closure | v2.0 | 1/1 | Complete | 2026-03-30 |
| 24. emit_nowait Tech Debt Cleanup | v2.0 | 1/1 | Complete | 2026-03-29 |
| 25. Session 与 Playground 原生化 | v2.1 | 2/3 | In Progress|  |
| 26. Tool 内化与遗留工具收归 | v2.1 | 2/3 | Complete    | 2026-04-01 |
| 27. MCP 与 Calculation 原生链路 | v2.1 | 0/TBD | Not started | - |
| 28. src 反向依赖反转与 Consumer 迁移 | v2.1 | 0/TBD | Not started | - |
| 29. 主执行路径切换 | v2.1 | 0/TBD | Not started | - |
| 30. 解耦审计与独立性证明 | v2.1 | 0/TBD | Not started | - |
