# Phase 25: Session 与 Playground 原生化 - Context

**Gathered:** 2026-04-01
**Status:** Ready for planning

<domain>
## Phase Boundary

切断 `matmaster/core/playground.py` 对 evomaster 的全部运行时依赖（7 处 import），建立 matmaster 自有的 Session Protocol、LocalSession/SSHSession 原生实现、参数化 Playground 构造，使 Playground 层不再 import evomaster。

废弃 DockerSession，不再提供 Docker session 支持。

</domain>

<decisions>
## Implementation Decisions

### Session 抽象设计
- **D-01:** Session 抽象使用 `@runtime_checkable Protocol`（与 matmaster 其他抽象一致：Tool/Hook/Guard/LLMProvider 全部是 Protocol）
- **D-02:** Protocol 包含核心 5 方法 + 生命周期：`exec_bash`, `read_file`, `write_file`, `path_exists`, `is_file`, `open`, `close`, `is_open`
- **D-03:** Protocol 定义放在 `matmaster/types/session.py`（与 context.py/runtime.py/messages.py 一致），实现放在 `matmaster/sessions/`
- **D-04:** SessionConfig 用精简版 Pydantic model，只保留 `timeout` + `workspace_path` + `working_dir`。LocalSessionConfig 继承加 `encoding`。不复制 gpu_devices/cpu_devices/symlinks 等未使用字段

### Config 加载策略
- **D-05:** Playground 不再读 config.yaml，改为参数化构造（接受 session_type、archival 等参数）。与 DevRunner 已有的干净模式一致
- **D-06:** YAML 解析逻辑放在 PlaygroundManager 内部，读 config.yaml 后拆分参数传给 Playground 构造函数
- **D-07:** `playground.config.agents` 和 `playground.config_path.parent`（被 agent_run_service 借用来定位 LLM config）迁移到 service 层自行处理，不再通过 Playground 代理

### Docker/SSH Session 范围
- **D-08:** Phase 25 同时原生化 LocalSession + SSHSession，两者一步到位
- **D-09:** DockerSession 废弃，playground.py 中删除 docker 分支，不迁移到 matmaster
- **D-10:** SSHSession 原生实现放在 `matmaster/sessions/ssh.py`，复用 paramiko（evomaster 已有的依赖），接口匹配 Session Protocol

### Mixin 消除
- **D-11:** PlaygroundSessionMixin 的 `attach_session`/`attach_ssh_session` 内联到 Playground 类，删除 Mixin 继承关系
- **D-12:** 内联后使用 matmaster 原生 SSHSession 替代 evomaster 的

### Claude's Discretion
- SSHSession 原生实现的内部结构（连接池、重连策略等）由 Claude 根据 evomaster 现有实现判断复制范围
- PlaygroundManager 内部 YAML 解析的具体字段提取方式

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 当前耦合点
- `matmaster/core/playground.py` — Playground 类，7 处 evomaster import，Phase 25 主改造目标
- `matmaster/sessions/local.py` — 已有 matmaster 原生 LocalSession（5 方法），需升级匹配 Protocol
- `matmaster/types/context.py` — PlaygroundContext frozen model，session 字段类型为 Any

### evomaster 参考实现（迁移源）
- `evomaster/agent/session/base.py` — BaseSession ABC + SessionConfig，Session Protocol 的参考
- `evomaster/agent/session/local.py` — LocalSession + LocalSessionConfig，matmaster LocalSession 的对照
- `evomaster/agent/session/ssh.py` — SSHSession + SSHSessionConfig，需迁移到 matmaster
- `evomaster/core/playground_session.py` — PlaygroundSessionMixin，attach_session/attach_ssh_session 逻辑
- `evomaster/config.py` — ConfigManager + EvoMasterConfig，需从 Playground 中消除

### 调用方（需适配）
- `src/services/agent_run_service.py` — 通过 PlaygroundManager 使用 Playground，读取 config.agents 和 config_path
- `matmaster/integration/bohrium_setup.py` — 调用 attach_ssh_session
- `matmaster/devshell/runner.py` — 已经是干净模式（直接构造 PlaygroundContext），可作为参考

### 配置文件
- `matmaster_config/config.yaml` — 主配置，session/playground/workspace 段被 Playground 使用

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `matmaster/sessions/local.py`: 已有 LocalSession 实现（5 方法 + open/close no-op），需升级：添加 `is_open` property、`config` 属性以匹配 Protocol
- `matmaster/types/`: 已有 Protocol 定义模式（context.py/runtime.py），Session Protocol 可复用相同风格
- `matmaster/config/`: 已有 ExpConfig/LLMConfig 的 Pydantic 加载模式，SessionConfig 可复用相同风格
- `matmaster/devshell/runner.py:82-89`: DevRunner._create_session() 已展示了 matmaster 原生 LocalSession 的使用方式

### Established Patterns
- Protocol 定义：`@runtime_checkable Protocol`，所有方法标注完整签名
- Config model：Pydantic BaseModel + `model_config = ConfigDict(frozen=True)`
- 类型文件组织：抽象/契约在 `types/`，实现在对应功能目录

### Integration Points
- `PlaygroundContext.session` 字段（当前 `Any`）可在本 Phase 后改为 `Session` Protocol 类型
- `agent_run_service.py` 中 `playground.config.agents` 和 `playground.config_path` 的使用需迁移到 service 层自行读取
- `BohriumSetupService` 中 `attach_ssh_session` 调用需切换到 Playground 内联版本

</code_context>

<specifics>
## Specific Ideas

- 用户明确要求废弃 DockerSession，只保留 local + SSH 两种 session 类型
- 用户强调基于 matmaster 实际使用情况精简，不需要复制 evomaster 的全部功能面
- 用户指出 Playground 当前承担了不该有的 config 代理角色，应该剥离

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 25-session-playground*
*Context gathered: 2026-04-01*
