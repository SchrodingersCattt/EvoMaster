# Roadmap: MatMaster Framework Refactoring (v2)

## Milestones

- ✅ **v1 MatMaster Framework Refactoring** -- Phases 1-7 (shipped 2026-03-22)
- 🚧 **v1.1 Agent 外围能力构建** -- Phases 8-11 (in progress)

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

### 🚧 v1.1 Agent 外围能力构建 (In Progress)

**Milestone Goal:** 构建 matmaster 原生内置 tool 套件、SubAgent spawn 机制和配套 prompt/description 体系，使 agent 具备完整的独立执行能力。

- [ ] **Phase 8: BuiltinTool 基础设施与核心 Tools** - 建立 BuiltinTool 基类、session 注入模式，交付 BashTool/ListDirTool/TaskTool
- [ ] **Phase 9: 文件操作 Tools** - 交付 Read/Write/Edit/Glob/Grep 六个文件操作工具及 Read-Before-Modify 协议
- [ ] **Phase 10: Tool Description 与 System Prompt 设计** - 为所有 builtin tool 编写精细化 description/schema，设计 direct 模式 system prompt
- [ ] **Phase 11: SubAgent Spawn 机制** - 实现子 agent spawn/执行/取消/事件路由完整生命周期

## Phase Details

### Phase 8: BuiltinTool 基础设施与核心 Tools
**Goal**: Agent 可以通过原生 BuiltinTool 体系执行 shell 命令、浏览目录和追踪任务
**Depends on**: Phase 7 (v1 completed)
**Requirements**: TOOL-04, TOOL-07, TOOL-09
**Success Criteria** (what must be TRUE):
  1. Agent 可以通过 BashTool 在远程环境执行 shell 命令并获取输出
  2. Agent 可以通过 ListDirTool 列出远程目录结构
  3. Agent 可以通过 TaskTool 创建、更新、查询任务状态
  4. BuiltinTool 基类区分 session-dependent 和 session-free 两种模式，session 通过构造注入
  5. Exp 装配层能够根据 ExpConfig 自动注册 builtin tools（不走 EvoToolAdapter）
**Plans:** 3 plans

Plans:
- [x] 08-01-PLAN.md -- BuiltinTool ABC 基类 + BashTool + ListDirTool 实现与测试
- [x] 08-02-PLAN.md -- TaskStore + 5 个 TaskTool 实现与测试
- [ ] 08-03-PLAN.md -- Exp._init_builtin_tools 双源注册改造与集成测试

### Phase 9: 文件操作 Tools
**Goal**: Agent 具备完整的文件读写搜索能力，并通过 Read-Before-Modify 协议防止盲写
**Depends on**: Phase 8
**Requirements**: TOOL-01, TOOL-02, TOOL-03, TOOL-05, TOOL-06, TOOL-08
**Success Criteria** (what must be TRUE):
  1. Agent 可以通过 Read tool 读取远程文件内容（支持行范围指定）
  2. Agent 可以通过 Write tool 创建或覆盖文件、通过 Edit tool 精确字符串替换
  3. Write/Edit 执行前强制要求先 Read 目标文件，未 Read 时返回错误提示
  4. Agent 可以通过 Glob tool 按模式搜索文件路径、通过 Grep tool 按正则搜索文件内容
**Plans**: TBD

### Phase 10: Tool Description 与 System Prompt 设计
**Goal**: 每个 builtin tool 具有精细化的 description/schema 以优化 LLM 调用准确率，direct 模式具有完整的行为指导 prompt
**Depends on**: Phase 9
**Requirements**: PRMT-01, PRMT-02
**Success Criteria** (what must be TRUE):
  1. 每个 builtin tool 的 json_schema 包含精确的参数约束和使用示例，description 控制在 100 token 以内
  2. direct 模式的 system prompt（developer_instructions）包含完整的 agent 行为指导（工具使用规范、输出格式、错误处理策略）
  3. DevShell 中使用完整 tool 集和 system prompt 进行多轮对话，LLM 能正确调用工具完成文件操作任务
**Plans**: TBD

### Phase 11: SubAgent Spawn 机制
**Goal**: Agent 可以通过 tool_call 触发子 agent 执行特定任务，子 agent 有独立配置但共享父环境，支持取消传播和事件路由
**Depends on**: Phase 10
**Requirements**: SUBA-01, SUBA-02, SUBA-03, SUBA-04, SUBA-05, SUBA-06, PRMT-03
**Success Criteria** (what must be TRUE):
  1. Agent 调用 SubAgent tool 后，系统通过 ExpConfig 创建子 agent 并执行，结果作为 tool_call result 返回给父 agent
  2. 子 agent 拥有独立的 tool 集和 system prompt（通过子 exp TOML 定义），同时共享父 agent 的 workspace 和 session
  3. 子 agent 禁止再次 spawn 子 agent（递归深度 = 1），违反时返回错误
  4. 父 agent 取消时 stop_event 级联传播到正在运行的子 agent，子 agent 立即终止
  5. 子 agent 的流式事件通过父 agent 的 MessageBus 路由，前端可实时观察子 agent 执行过程
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 8 -> 9 -> 10 -> 11

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Foundation Contracts | v1 | 2/2 | Complete | 2026-03-21 |
| 2. Agent Kernel | v1 | 3/3 | Complete | 2026-03-22 |
| 3. Exp Assembly Layer | v1 | 4/4 | Complete | 2026-03-22 |
| 4. Playground Layer | v1 | 3/3 | Complete | 2026-03-22 |
| 5. Integration and Quality | v1 | 5/5 | Complete | 2026-03-22 |
| 6. Service Layer Wiring | v1 | 2/2 | Complete | 2026-03-22 |
| 7. Cleanup and Traceability | v1 | 2/2 | Complete | 2026-03-22 |
| 8. BuiltinTool 基础设施与核心 Tools | v1.1 | 0/3 | Planning | - |
| 9. 文件操作 Tools | v1.1 | 0/? | Not started | - |
| 10. Tool Description 与 System Prompt 设计 | v1.1 | 0/? | Not started | - |
| 11. SubAgent Spawn 机制 | v1.1 | 0/? | Not started | - |
