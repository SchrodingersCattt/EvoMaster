# Phase 28: src 反向依赖反转与 Consumer 迁移 - Context

**Gathered:** 2026-04-01
**Status:** Ready for planning

<domain>
## Phase Boundary

消除 matmaster 对 src 的全部反向依赖（bohrium_setup.py 的 5 个函数 + script_env.py 的常量），同时迁移 src 消费者到 matmaster 原生数据结构（chat_history 消息类型）与 session 抽象（agent_run_bohrium SSHSession）。附带收回 Phase 27 遗留的 evomaster.env.bohrium 3 个函数到 matmaster 侧。

不包含 agent_run_bohrium.py 对 playground.mat_master.core.workspace_resolver 的依赖（留后续 phase）。

</domain>

<decisions>
## Implementation Decisions

### bohrium_setup.py 解耦 (INVR-01)
- **D-01:** BohriumSetupService 改为回调注入模式。构造函数接受 `load_credentials_fn`、`apply_credentials_fn`、`setup_fn`、`cleanup_fn` 四个 callable 参数，由 src/services/agent_run_service.py 在构造时注入实际函数。逻辑不搬迁，只反转依赖方向
- **D-02:** BohriumSetupResult 类型（NamedTuple）在 matmaster 侧定义副本，消除 TYPE_CHECKING 下对 src 的导入。src 侧保留原版本或改为从 matmaster 重新导入

### script_env.py 常量注入 (INVR-02)
- **D-03:** matmaster 侧定义 `BOHRIUM_OPENAPI_HOST = os.getenv('BOHRIUM_BASE_URL', 'https://open.bohrium.com').rstrip('/')`，不依赖 src 的 URL_PART 环境感知逻辑。运维通过环境变量控制

### evomaster.env.bohrium 函数收归
- **D-04:** `get_bohrium_credentials`、`get_bohrium_storage_config`、`inject_bohrium_executor` 三个纯函数直接搬入 matmaster 侧（与 BOHRIUM_OPENAPI_HOST 常量放在同一模块）。它们只读环境变量和构造 dict，无外部依赖
- **D-05:** path_adaptor.py 和 job_service.py 中的 3 个 lazy import 改为从 matmaster 侧模块导入

### chat_history 消息类型迁移 (CONS-03)
- **D-06:** chat_history.py 顶部的 `from evomaster.utils.types import AssistantMessage, ToolCall, ToolMessage, UserMessage` 全量切换到 matmaster.types.messages 中的对应类型
- **D-07:** events_to_dialog_messages 内部的 model_validate/model_dump 调用适配 matmaster 消息类型的字段和方法签名（Pydantic v2 兼容，需研究阶段确认字段映射）

### agent_run_bohrium session 迁移 (CONS-04)
- **D-08:** `from evomaster.agent.session.ssh import SSHSession, SSHSessionConfig` 切换到 `from matmaster.sessions.ssh import SSHSession, SSHSessionConfig`。Phase 25 已建立 matmaster 原生 SSHSession
- **D-09:** L155 的 `isinstance(ssh_session, SSHSession)` 改用 matmaster SSHSession
- **D-10:** agent_run_bohrium.py 对 playground.mat_master.core.workspace_resolver 的依赖不在本 phase 处理

### Claude's Discretion
- 搬入的 bohrium 函数在 matmaster/ 内的具体模块位置（`matmaster/integration/` 或 `matmaster/adaptors/calculation/` 均可）
- matmaster 消息类型与 evomaster 类型的具体字段映射细节（ToolCall vs ToolCallData 等）
- BohriumSetupResult 副本的具体字段定义（需从 src 侧读取）

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### matmaster 侧（需要修改的文件）
- `matmaster/integration/bohrium_setup.py` — BohriumSetupService，5 处 src lazy import 需改为回调注入
- `matmaster/tools/script_env.py` §57-63 — BOHRIUM_OPENAPI_HOST lazy import 需替换为 matmaster 侧常量
- `matmaster/adaptors/calculation/path_adaptor.py` §521,638 — evomaster.env.bohrium 的 2 处 lazy import
- `matmaster/adaptors/calculation/job_service.py` §63 — evomaster.env.bohrium 的 1 处 lazy import

### src 消费者（需要迁移的文件）
- `src/services/chat_history.py` §7-12 — evomaster.utils.types 消息类型导入，全文使用这些类型
- `src/services/agent_run_bohrium.py` §10 — evomaster SSHSession/SSHSessionConfig 导入，§155 isinstance 检查，§587-593 SSHSession 构造

### 迁移源（参考）
- `evomaster/env/bohrium.py` — get_bohrium_credentials, get_bohrium_storage_config, inject_bohrium_executor 三个函数源码
- `src/services/agent_run_bohrium.py` — BohriumSetupResult NamedTuple 定义（bohrium_setup 回调注入的类型参考）
- `evomaster/utils/types.py` — AssistantMessage/ToolCall/ToolMessage/UserMessage 定义（chat_history 切换的参考）

### matmaster 消息类型（切换目标）
- `matmaster/types/messages.py` — AssistantMessage, ToolCallData, ToolMessage, UserMessage（chat_history 切换目标）
- `matmaster/sessions/ssh.py` — SSHSession, SSHSessionConfig（agent_run_bohrium 切换目标）
- `matmaster/types/session.py` — Session Protocol 定义

### 先前 phase 参考
- `.planning/phases/25-session-playground/25-CONTEXT.md` — Session Protocol 设计、SSHSession 原生化决策
- `.planning/phases/27-mcp-calculation/27-CONTEXT.md` — D-08 明确 bohrium 函数迁移延后到 Phase 28

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `matmaster/sessions/ssh.py` — Phase 25 建立的原生 SSHSession，可直接供 agent_run_bohrium 使用
- `matmaster/types/messages.py` — 完整的 matmaster 消息类型体系，chat_history 切换目标
- `matmaster/types/session.py` — Session Protocol，提供类型标注
- `matmaster/integration/bohrium_setup.py` — 已有 BohriumSetupService 框架，只需改造构造函数

### Established Patterns
- 回调注入：matmaster 其他模块已使用 callable 参数注入（如 EventRouter 的 handler 注册）
- 环境变量读取：matmaster/config/ 中已有 os.getenv 模式
- lazy import：bohrium_setup 和 script_env 当前用 function-level import，改造后回调注入可消除这些

### Integration Points
- `src/services/agent_run_service.py` — BohriumSetupService 的构造方，需要传入回调函数
- chat_history 的调用方（src 内部）— 不需要改，只是内部实现换类型

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches

</specifics>

<deferred>
## Deferred Ideas

- agent_run_bohrium.py 对 `playground.mat_master.core.workspace_resolver` 的依赖（get_remote_session_workspace_root, load_workspace_config_dict）— 留后续 phase 处理
- matmaster 内其他 evomaster 残留（bash_tool.py:135 的 evomaster LocalSession、monitor_job/_llm.py 的 ConfigManager/create_llm）— 不在 Phase 28 范围

</deferred>

---

*Phase: 28-src-consumer*
*Context gathered: 2026-04-01*
