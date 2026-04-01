# Requirements: MatMaster v2.1 解耦里程碑

**Defined:** 2026-04-01
**Core Value:** 三层抽象（playground->exp->agent）必须具有清晰、稳定、可测试的职责边界

## v2.1 Requirements

将 `matmaster/` 从 `evomaster/` 运行时依赖中抽离出来，使其成为可独立运行、测试与迁移的内核，同时保持当前 API/worker、本地 Web 调试和 Bohrium/calculation 链路可用。

### Playground 与 Session

- [ ] **PLAY-01**: 开发者可以在不安装 `evomaster` 的环境中创建并使用 `matmaster.sessions.local.LocalSession`，供 builtin tools 执行本地命令与文件操作
- [ ] **PLAY-02**: 开发者可以通过 `matmaster` 原生 session factory 创建 local / docker / ssh session，而 `matmaster.core.playground.Playground` 不再直接 import `evomaster.agent.session.*`
- [ ] **PLAY-03**: 开发者可以通过 `matmaster.core.playground.Playground` 加载主配置、准备 workspace、logging 和 session，而不依赖 `evomaster.config.ConfigManager` 或 `PlaygroundSessionMixin`

### Tool / MCP / Calculation

- [ ] **TOOL-07**: 开发者可以在 `matmaster.tools` 中注册并执行遗留 builtin 能力，而不需要 `EvoToolAdapter`
- [ ] **TOOL-08**: 开发者可以在 `matmaster.tools.builtin` 中使用原生 bash safety 与 edit helper，不再导入 `evomaster.agent.tools.builtin.*`
- [ ] **MCP-01**: 开发者可以通过 `matmaster.tools.lazy_mcp` 连接 MCP server、缓存 schema 并执行 tool，而不依赖 `evomaster.agent.tools.mcp.*`
- [ ] **CALC-01**: 开发者可以在 `matmaster` 侧解析 calculation runtime config、path adaptor 与 schema cache，而不直接导入 `evomaster.adaptors.calculation.*`
- [ ] **CALC-02**: 解耦后 Bohrium / calculation tool 的 executor、storage、OSS 上传与远端路径适配行为保持与当前协议兼容

### Consumers 与 Messages

- [ ] **CONS-01**: API/worker 主执行路径可以通过 matmaster 原生入口初始化 playground / exp / agent，而不是 `evomaster.core.get_playground_class`
- [ ] **CONS-02**: 本地 Web 调试后端可以通过 matmaster 原生入口初始化 playground，并保持当前启动、会话恢复与流式输出行为
- [ ] **CONS-03**: `src/services/chat_history.py` 等对话历史构建链路可以消费 matmaster 原生 message / tool_call 数据结构，不依赖 `evomaster.utils.types`
- [ ] **CONS-04**: `src/services/agent_run_bohrium.py` 等 session-sensitive 服务路径可以切换到 matmaster session abstraction 或显式 compat layer，避免直接依赖 evomaster session class

### Quality 与 Migration

- [ ] **QUAL-06**: 仓库提供 import audit 或等价测试，验证 `matmaster/` 运行时模块不再直接 import `evomaster`
- [ ] **QUAL-07**: 在不安装 `evomaster` 的受控测试环境中，`tests/matmaster/` 的核心测试集可以通过，证明 matmaster 可独立运行
- [ ] **QUAL-08**: 仓库提供一份解耦迁移文档，明确保留 compat layer、剩余遗留路径与后续清理顺序

## v2.2 Requirements

延后到后续里程碑。已记录但不在当前路线图中。

### 历史路径清理

- **LEGY-01**: `playground/mat_master/core/` 历史 solver / tool / registry 路径全部迁移到 matmaster 原生 API
- **LEGY-02**: 仓库非主执行路径中的剩余 `evomaster` import 清理完毕，命名与目录边界统一

### 发布与治理

- **PKG-01**: `matmaster` 具备独立打包与安装方案，`evomaster` 成为可选兼容依赖或独立仓库
- **HIST-01**: `.planning/MILESTONES.md` 与 milestones archive 完整补齐 v1.1 / v2.0 历史记录

## Out of Scope

| Feature | Reason |
|---------|--------|
| 新产品能力或前端交互改版 | 本里程碑聚焦架构解耦，不扩展用户可见功能 |
| `playground/mat_master/core/` 历史 solver 体系全面重写 | 仅处理阻塞 matmaster 独立运行的依赖点 |
| `bohr-agent-sdk` 服务端协议调整 | 必须保持现有 executor / storage / OSS 契约兼容 |
| 仓库所有历史 `evomaster` 引用一次性清零 | 优先收敛 `matmaster/` 与主执行路径，其他路径后续清理 |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| To be filled during roadmap creation | — | Pending |

**Coverage:**
- v2.1 requirements: 15 total
- Mapped to phases: 0
- Unmapped: 15 ⚠️

---
*Requirements defined: 2026-04-01*
*Last updated: 2026-04-01 after initial v2.1 definition*
