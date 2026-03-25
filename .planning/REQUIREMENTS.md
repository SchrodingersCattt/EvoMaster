# Requirements: MatMaster v1.1

**Defined:** 2026-03-24
**Core Value:** 三层抽象（playground->exp->agent）必须具有清晰、稳定、可测试的职责边界

## v1.1 Requirements

Requirements for Agent 外围能力构建。Each maps to roadmap phases.

### Builtin Tools

- [x] **TOOL-01**: Agent 可以通过 Read tool 读取远程文件内容（支持行范围指定）
- [x] **TOOL-02**: Agent 可以通过 Write tool 创建或覆盖远程文件
- [x] **TOOL-03**: Agent 可以通过 Edit tool 对远程文件进行精确字符串替换
- [x] **TOOL-04**: Agent 可以通过 Bash tool 在远程环境执行 shell 命令
- [ ] **TOOL-05**: Agent 可以通过 Glob tool 按模式搜索远程文件路径
- [ ] **TOOL-06**: Agent 可以通过 Grep tool 按正则搜索远程文件内容
- [x] **TOOL-07**: Agent 可以通过 ListDir tool 列出远程目录结构
- [x] **TOOL-08**: Write/Edit tool 执行前强制要求先 Read 目标文件（Read-Before-Modify 协议）
- [x] **TOOL-09**: Agent 可以通过 Task 套件创建、更新、查询任务状态用于工作追踪

### SubAgent

- [ ] **SUBA-01**: Agent 可以通过 SubAgent tool 调用 spawn 子 agent 执行特定任务，结果作为 tool_call result 返回
- [ ] **SUBA-02**: 子 agent 通过 ExpConfig 配置独立的 tool 集和 system prompt
- [ ] **SUBA-03**: 子 agent 共享父 agent 的 PlaygroundContext（workspace/session）
- [ ] **SUBA-04**: 子 agent 禁止再次 spawn 子 agent（递归深度保护）
- [ ] **SUBA-05**: 父 agent 取消时 stop_event 级联传播到子 agent
- [ ] **SUBA-06**: 子 agent 的事件通过父 agent 的 MessageBus 路由到前端

### Prompt/Description

- [ ] **PRMT-01**: 每个 builtin tool 具有精细化的 description 和 json_schema，优化 LLM 调用准确率
- [ ] **PRMT-02**: Exp system prompt（developer_instructions）针对 direct 模式设计完整的 agent 行为指导
- [ ] **PRMT-03**: SubAgent 的 exp 定义包含针对子任务场景的专用 system prompt

## Future Requirements

### Deferred Tools

- **TOOL-D01**: Think tool（agent 内部推理，不执行动作）
- **TOOL-D02**: WebFetch/WebSearch（远程环境可能无外网）
- **TOOL-D03**: NotebookRead/NotebookEdit（当前场景非核心）
- **TOOL-D04**: MultiEdit（批量编辑，LLM 调用出错率高）

### Deferred Infrastructure

- **INFR-D01**: Prompt 模板加载器基础设施（当前直接在 TOML/代码中管理）
- **INFR-D02**: 消除 evomaster session 依赖（v1.1 维持现状）

## Out of Scope

| Feature | Reason |
|---------|--------|
| evomaster session 依赖消除 | v1.1 维持现状，session-dependent tool 仍通过 BaseSession |
| 多 agent 编排（非 spawn） | 先完成 spawn 机制，编排后续设计 |
| src/ Web Service 层重构 | 保持现状，不在本次范围 |
| 前端 UI 改动 | 本次只涉及后端框架层 |
| SubAgent 递归 spawn | 明确禁止，防止无限嵌套 |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| TOOL-01 | Phase 9 | Complete |
| TOOL-02 | Phase 9 | Complete |
| TOOL-03 | Phase 9 | Complete |
| TOOL-04 | Phase 8 | Complete |
| TOOL-05 | Phase 9 | Pending |
| TOOL-06 | Phase 9 | Pending |
| TOOL-07 | Phase 8 | Complete |
| TOOL-08 | Phase 9 | Complete |
| TOOL-09 | Phase 8 | Complete |
| SUBA-01 | Phase 11 | Pending |
| SUBA-02 | Phase 11 | Pending |
| SUBA-03 | Phase 11 | Pending |
| SUBA-04 | Phase 11 | Pending |
| SUBA-05 | Phase 11 | Pending |
| SUBA-06 | Phase 11 | Pending |
| PRMT-01 | Phase 10 | Pending |
| PRMT-02 | Phase 10 | Pending |
| PRMT-03 | Phase 11 | Pending |

**Coverage:**
- v1.1 requirements: 18 total
- Mapped to phases: 18
- Unmapped: 0

---
*Requirements defined: 2026-03-24*
*Last updated: 2026-03-24 after roadmap creation*
