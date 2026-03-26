# Phase 4: Playground Layer - Context

**Gathered:** 2026-03-22
**Status:** Ready for planning

<domain>
## Phase Boundary

重构 playground 为纯物理环境准备层（workspace + session + logging），只输出 PlaygroundContext。Playground 不负责 MCP、skill、tool、guard、LLM 等能力初始化——这些由 Exp 层和 Service 层编排处理。同时修正 Phase 3 中 PlaygroundContext 的字段归属（移除 mcp_manager/skill_registry），并新增 workspace 归档配置（WKSP-04）。

</domain>

<decisions>
## Implementation Decisions

### Playground 职责边界（核心修正）
- Playground 只负责**物理工作环境**：workspace 目录创建、Session（Docker/SSH/Local）管理、logging 配置
- MCP 初始化、Skill Registry 加载、LLM Provider 创建、Tool Registry 构建 **全部不属于 Playground**——由 Exp 层在 assemble() 中完成
- Config YAML 由 **Service 层统一读取**，物理环境 config 传给 Playground，能力 config（MCP/skill/LLM）传给 Exp 构造函数
- Service 层是薄编排层：读 config → 分发 → Playground.prepare() → Exp.assemble() → Kernel.run()

### Playground = Workspace 等价
- 当前项目实际是 1 Session : 1 Playground : 1 Workspace 的 1:1:1 关系（session_id = task_id，每次 run 结束后 playground 销毁）
- 因此 Playground 等价于 Workspace：每个 Playground 实例就是一个具体的工作环境
- 不需要"Playground 管理多个 workspace"的抽象

### 生命周期：prepare() + cleanup() 两段式
- `prepare(run_meta) -> PlaygroundContext`：创建 Session + 创建 workspace 目录 + 配置 logging，返回 PlaygroundContext 快照
- `cleanup()`：关闭 Session + 触发 workspace 归档
- 不需要单独的 setup()——1:1:1 关系下 prepare 一次性完成所有初始化

### 统一 Playground 类
- 只写一个 Playground 类，mat_master 和 minimal 通过不同的 config YAML 区分
- 重构后 Playground 层面无代码差异——session 类型、workspace root、logging 级别等全部 config 驱动
- Bohrium SSH 的特殊逻辑：Service 层加载凭证，Playground 用凭证创建 SSH session

### 构造参数
- Playground(config_path) 接收 config 文件路径，内部读取物理环境相关配置
- 与现有模式一致，子类通过覆写私有方法自定义行为（虽然当前不需要子类）

### PlaygroundContext 字段修正
- **移除** `mcp_manager: Any` 和 `skill_registry: Any`——不属于物理环境
- **新增** `archival: WorkspaceArchivalConfig | None`——workspace 归档配置（嵌套 Pydantic model，含 enabled/oss_bucket/oss_prefix/credential_ref）
- **保留** workdir / session_type / cache_area / env_vars / run_meta

### Exp 层 MCP/Skill 初始化
- Exp 构造函数接收 `mcp_config` 和 `skill_config`（结构化数据，由 Service 层从 YAML 提取）
- `assemble(ctx)` 中初始化 MCP manager（用 ctx.workdir 替换 workspace 路径占位符）和 Skill registry
- `run()` 内 try/finally 清理 MCP（Exp 自管能力资源生命周期）

### 资源释放：混合模式
- **Kernel**：无状态，无需 cleanup
- **Exp**：run() 内 try/finally 自管 MCP 等能力资源（谁创建谁清理）
- **Playground**：由 Service 层调用 cleanup()（Playground 不控制 run 流程）
- **Service**：编排整体顺序——Exp 先结束 → Playground cleanup → 持久化/Redis/配额

### Phase 4 对 Phase 3 代码的修改
- 在 Phase 4 的 plan 中一并处理，不单独出 gap closure plan
- 修改范围小：PlaygroundContext 移除 2 字段 + DirectExp 调整传参路径 + ContextBuilder 参数来源变更

### 新旧共存策略
- 新代码写在 `matmaster/playground/`，旧代码 `playground/mat_master/` 保留不动
- Phase 4 期间新代码独立存在，有单元测试验证
- Phase 5 时 Service 层切换到新 Playground（与 Phase 1-3 的策略一致）

### Session 复用
- 新 Playground 直接 import 使用现有 evomaster 的 LocalSession/DockerSession/SSHSession
- Session 不属于本次重构范围，是稳定的基础设施
- 后续可考虑 Session Protocol 抽象（非 Phase 4 范围）

### Claude's Discretion
- WorkspaceArchivalConfig 的具体字段设计（在包含 enabled/oss_bucket/oss_prefix/credential_ref 的基础上）
- Playground 内部私有方法的拆分方式（_create_session / _create_workspace / _setup_logging）
- PlaygroundContext 中 config 相关字段的具体表达方式
- 新 Playground 的模块组织结构（matmaster/playground/ 下的文件划分）

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 项目定义
- `.planning/PROJECT.md` — 项目愿景、核心价值、三层职责划分
- `.planning/REQUIREMENTS.md` — Phase 4 需求：WKSP-01 ~ WKSP-04
- `.planning/ROADMAP.md` — Phase 4 目标、成功标准、依赖关系

### Phase 1-3 交付物（Phase 4 直接依赖和修改）
- `matmaster/types/context.py` — PlaygroundContext 当前定义（需移除 mcp_manager/skill_registry，新增 archival）
- `matmaster/types/runtime.py` — AgentRuntimeSpec 定义（Exp 输出，不变）
- `matmaster/assembly/exp.py` — Exp base class（assemble/run 接口，run 中 try/finally 需增加 MCP cleanup）
- `matmaster/assembly/direct_exp.py` — DirectExp（构造函数需新增 mcp_config/skill_config，assemble 需改为自行初始化 MCP/Skill）
- `matmaster/assembly/context_builder.py` — ContextBuilder.build()（skill_registry 参数来源需调整）
- `matmaster/assembly/tool_registry.py` — ToolRegistry（MCP 工具注册逻辑将在 Exp 中完成）

### Phase 1-3 上下文
- `.planning/phases/01-foundation-contracts/01-CONTEXT.md` — Phase 1 决策（matmaster/ 目录、事件设计、MessageBus）
- `.planning/phases/02-agent-kernel/02-CONTEXT.md` — Phase 2 决策（循环终止、Hook 扩展、LLMProvider）
- `.planning/phases/03-exp-assembly-layer/03-CONTEXT.md` — Phase 3 决策（ToolRegistry、ContextBuilder、DirectExp、WorkerRegistry Protocol）

### 代码库分析
- `.planning/codebase/ARCHITECTURE.md` — 现有架构、数据流、Service 层编排模式
- `.planning/codebase/CONVENTIONS.md` — 命名规范、代码风格、Protocol 使用模式
- `.planning/codebase/STRUCTURE.md` — 目录结构、新代码放置位置

### 现有实现（需理解以设计新 Playground）
- `evomaster/core/playground.py` — 现有 BasePlayground（setup/run/cleanup 生命周期、Session 创建、MCP 初始化、workspace 管理）
- `playground/mat_master/core/playground.py` — 现有 MatMasterPlayground（Bohrium SSH、workspace root 自定义、MCP tool allowlist、skill sync to remote）
- `playground/minimal/core/playground.py` — 现有 MinimalPlayground（空壳，只设 config_dir）
- `evomaster/agent/session/` — 现有 LocalSession/DockerSession/SSHSession（新 Playground 直接复用）
- `src/services/agent_run_service.py` — Service 层编排（_get_or_create_playground、run_agent_sync、workspace OSS 上传、Bohrium 凭证加载）

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `evomaster/agent/session/` LocalSession/DockerSession/SSHSession — 新 Playground 直接 import 复用，不需要重新实现
- `matmaster/types/context.py` PlaygroundContext — 需修改字段但保留 frozen Pydantic model 模式
- `matmaster/assembly/direct_exp.py` DirectExp — 需调整构造参数和 assemble 中的 MCP/Skill 初始化路径
- `src/dao/oss_io.py` upload_dir_to_oss — workspace 归档的 OSS 上传实现，可从 Service 层复用

### Established Patterns
- Pydantic frozen model 用于不可变契约（PlaygroundContext、AgentRuntimeSpec）
- `@runtime_checkable` Protocol 用于接口定义（Guard、Hook、LLMProvider）
- 同步 threading 模型 — agent 运行在 ThreadPoolExecutor 中
- 新代码放 `matmaster/` 命名空间下

### Integration Points
- `matmaster/playground/` — Phase 4 新代码位置（Playground base class、WorkspaceArchivalConfig）
- PlaygroundContext — Playground 输出、Exp.assemble() 输入。Phase 4 修正字段归属
- Service 层 — Phase 5 切换到新 Playground 调用路径

</code_context>

<specifics>
## Specific Ideas

- Playground = Workspace 的等价关系是基于项目当前 1:1:1（session:playground:workspace）的实际模式确定的。如果未来需要 1 Session 多 Workspace 的场景，可以引入 Session 池或外部注入模式，但当前不需要
- MCP 的 workspace 路径依赖通过 assemble(ctx) 中的 ctx.workdir 自然解决——Exp 在 assemble 时同时拥有 mcp_config（构造函数）和 workdir（PlaygroundContext）
- Service 层"统一读 config"不意味着 Service 层理解 MCP 内部——它只提取 config 字典传给 Exp，实际初始化由 Exp 完成

</specifics>

<deferred>
## Deferred Ideas

- Session Protocol 抽象（在 matmaster/ 下定义 Session Protocol 替代直接 import evomaster Session 类）— 后续优化
- 1 Session 多 Workspace 支持（Session 池/外部注入模式）— 未来需求时再引入
- Playground 子类扩展点（如果出现纯 config 无法驱动的特化需求）— 未来评估

</deferred>

---

*Phase: 04-playground-layer*
*Context gathered: 2026-03-22*
