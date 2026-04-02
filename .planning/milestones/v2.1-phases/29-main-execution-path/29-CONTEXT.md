# Phase 29: 主执行路径切换 - Context

**Gathered:** 2026-04-01
**Status:** Ready for planning

<domain>
## Phase Boundary

丢弃所有 matmaster 不使用的遗留功能（playground/ 整个目录、run.py CLI、evaluation/ 目录），归档 playground 技能到临时位置，清理 matmaster 对 evomaster 的最后 2 处 runtime import（bash_tool + monitor_job），迁移 workspace_resolver 到 matmaster 侧。最终 matmaster 和 src 对 playground 零依赖，matmaster 对 evomaster 零 runtime import。

不包含 evomaster/ 目录的删除（留 Phase 30）。

</domain>

<decisions>
## Implementation Decisions

### 遗留代码丢弃
- **D-01:** 物理删除 `playground/` 整个目录、`run.py`、`evaluation/` 目录。git 历史可追溯，不需要保留死代码
- **D-02:** 一并删除对应测试：`tests/playground/`、`tests/evaluation/`、以及引用 playground 的 5 个测试文件（test_chat_history_reasoning_state.py、test_streaming_thought_protocol.py、test_ask_human_helpers.py、test_dialog_history_helpers.py、test_chat_event_source.py）
- **D-03:** `tests/test_workspace_resolver.py` 更新为从 matmaster 导入（不删除，因 workspace_resolver 迁移到 matmaster）

### CONS-02 处置
- **D-04:** CONS-02（本地 Web 调试后端走 matmaster 入口）标记为不适用（N/A）。本地调试以 DevShell 为准，不维护两套本地后端

### workspace_resolver 迁移
- **D-05:** `playground/mat_master/core/workspace_resolver.py` 的 `get_remote_session_workspace_root` 和 `load_workspace_config_dict` 搬入 matmaster 侧。`src/services/agent_run_bohrium.py` 改为从 matmaster 导入

### matmaster → evomaster 残余清理
- **D-06:** 清理 `matmaster/tools/builtin/bash_tool.py:135` 的 evomaster LocalSession isinstance 分支，只保留 matmaster LocalSession 检查
- **D-07:** 清理 `matmaster/tools/builtin/monitor_job/_llm.py:67-68` 的 evomaster ConfigManager/create_llm 依赖，改用 matmaster 原生 llm_factory

### evomaster 目录
- **D-08:** evomaster/ 目录不在本 phase 删除，留 Phase 30 审计后处理

### 技能归档
- **D-09:** `playground/mat_master/skills/`（19 个技能目录 + _common）移到 `.archive/playground-skills/`，不迁移到 matmaster。项目完成后由用户手动合并或删除
- **D-10:** `evomaster/skills/`（5 个技能）保持不动，`skills_root` 配置保持 `evomaster/skills`（evomaster/ 本 phase 不删）

### Claude's Discretion
- workspace_resolver 在 matmaster 侧的具体模块位置（`matmaster/integration/` 或新建 `matmaster/workspace/` 均可）
- monitor_job/_llm.py 替换为 matmaster llm_factory 的具体适配方式
- `.archive/` 是否加入 .gitignore

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### 删除目标
- `playground/` — 整个目录，包括 mat_master/core/、mat_master/service/、mat_master/skills/、mat_master/tools/ 等
- `run.py` — evomaster 统一 CLI 入口
- `evaluation/` — 评估路径

### matmaster 侧需修改的文件
- `matmaster/tools/builtin/bash_tool.py` §135 — evomaster LocalSession isinstance 分支，需删除
- `matmaster/tools/builtin/monitor_job/_llm.py` §64-79 — evomaster ConfigManager/create_llm，需替换为 matmaster llm_factory
- `configs/mat_master/config.yaml` §383 — `session.local.working_dir` 当前指向 `./playground/mat_master/workspace`，删除 playground 后需更新

### 迁移目标
- `playground/mat_master/core/workspace_resolver.py` — 整个文件搬入 matmaster 侧
- `playground/mat_master/skills/` — 19 个技能目录移到 `.archive/playground-skills/`

### src 侧需修改的文件
- `src/services/agent_run_bohrium.py` §12-15 — workspace_resolver import 需改为从 matmaster 导入

### 测试文件
- `tests/test_workspace_resolver.py` — 更新 import 路径
- `tests/playground/` — 整个目录删除
- `tests/evaluation/` — 整个目录删除
- `tests/test_chat_history_reasoning_state.py` — 删除
- `tests/test_streaming_thought_protocol.py` — 删除
- `tests/test_ask_human_helpers.py` — 删除
- `tests/test_dialog_history_helpers.py` — 删除
- `tests/test_chat_event_source.py` — 删除

### 先前 phase 参考
- `.planning/phases/25-session-playground/25-CONTEXT.md` — Session/Playground 原生化决策
- `.planning/phases/28-src-consumer/28-CONTEXT.md` — src 反向依赖反转，D-10 提到 workspace_resolver 延后

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `matmaster/sessions/local.py` — matmaster 原生 LocalSession，bash_tool 清理后唯一的 LocalSession 检查目标
- `matmaster/providers/llm_factory.py` — matmaster 原生 LLM 工厂，monitor_job 可复用
- `matmaster/config/loader.py` — matmaster 配置加载，monitor_job 可复用替代 evomaster ConfigManager

### Established Patterns
- 依赖反转：Phase 28 已建立回调注入模式（BohriumSetupService）
- 环境变量读取：matmaster 侧已有 os.getenv 标准模式

### Integration Points
- `src/services/agent_run_service.py` — 已完全使用 matmaster PlaygroundManager，零 evomaster import（CONS-01 基本满足）
- `src/worker/agent_worker.py` — 通过 agent_run_service 间接使用 matmaster
- `matmaster/devshell/` — 已是纯 matmaster 本地调试方案，无 playground 依赖

</code_context>

<specifics>
## Specific Ideas

- 用户明确要求丢弃所有 matmaster 未使用的功能，不是迁移而是删除
- playground 技能归档而非删除，留待项目完成后用户手动处理
- evomaster/ 不在本 phase 删除，是有意的分步策略

</specifics>

<deferred>
## Deferred Ideas

- evomaster/ 目录删除 — Phase 30 审计后处理
- evomaster/skills/ 的 5 个技能迁移到 matmaster/skills/ — 随 evomaster/ 删除一起处理
- `.archive/playground-skills/` 技能的正式合并到 matmaster — 项目完成后用户手动

</deferred>

---

*Phase: 29-main-execution-path*
*Context gathered: 2026-04-01*
