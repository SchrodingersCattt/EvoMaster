# EvoMaster v0.0.1 → v0.0.2 迁移方案

本文档将推荐的迁移方案固化为可执行步骤，便于从当前基于 EvoMaster v0.0.1 的代码逐步对齐上游 v0.0.2。迁移采用**分阶段、带兼容层**的方式，每阶段均可独立验证。

**上游迁移指南**：[EvoMaster MIGRATION_GUIDE v0.0.1 to v0.0.2](https://github.com/sjtu-sai-agents/EvoMaster/blob/main/docs/migration/MIGRATION_GUIDE_v0.0.1_to_v0.0.2.md)

---

## 文档结构

| 章节 | 说明 |
|------|------|
| [迁移状态总览](#迁移状态总览) | 各阶段完成情况与验收 |
| [阶段 1～4 摘要](#阶段-1～4-摘要) | 配置、Playground、Skills、业务迁移步骤（已完成） |
| [未对齐或未完全集成](#未对齐或未完全集成) | 与上游差异、本仓独有、待办与注意事项 |
| [已对齐清单](#已对齐清单) | 与上游一致的实现点 |
| [后续可跟进与可选](#后续可跟进与可选) | 已接入能力、可选对齐、与 AGENTS.md 对应 |

---

## 迁移状态总览

| 步骤 | 状态 |
|------|------|
| 阶段 1.1 配置模型 | ✅ 已完成 |
| 阶段 1.2 配置兼容（YAML / 加载层） | ✅ 已完成 |
| 阶段 1.3 验收 | ✅ 已完成 |
| 阶段 1.4 现有 YAML 迁移到 v0.0.2 写法 | ✅ 已完成 |
| 阶段 2.1 AgentSlots 与 self.agents | ✅ 已完成 |
| 阶段 2.2 _create_agent 新旧签名 | ✅ 已完成（仅保留新签名） |
| 阶段 2.3 工具注册与 Agent 工具可见性 | ✅ 已完成 |
| 阶段 2.4 验收 | ✅ 已完成 |
| 阶段 3.1～3.4 Skills 统一 | ✅ 已完成 |
| 阶段 4.1～4.4 业务 Playground 与服务 | ✅ 已完成 |
| 收尾（顶层 agent 移除、_require_dict、get_agent_tools_config 等） | ✅ 已完成 |

**验收**：在 Python ≥3.10 下执行 `pytest tests/test_evomaster_config_migration.py -v` 通过。

---

## 阶段 1～4 摘要

### 阶段 1：配置与兼容层

- **1.1** `EvoMasterConfig` 使用 `agents: dict`，无顶层 `agent`；新增 `ToolConfig`（顶层 `tools`）；per-agent 通过 `get_agent_config(name)`、`get_agent_tools_config(name)`、`get_agent_skills_config(name)` 等读取。
- **1.2** YAML 中不再使用 `enable_tools`；缺失 `tools` 时 `get_agent_tools_config` 返回默认 `{ builtin: ['*'], mcp: '' }`。
- **1.3 / 1.4** 现有 `configs/` 下 YAML 已全部改为 `tools: { builtin: [...], mcp: "..." }` 形式。

### 阶段 2：Playground 与 Agent 创建

- **2.1** `AgentSlots` 容器，`self.agents` 存多 agent；`self.agent = self.agents.get_random_agent()` 兼容单 agent 调用。
- **2.2** `_create_agent(name, agent_config=None, tool_config=None, llm_config=None, skill_config=None, skill_registry=None)`，无 `enable_tools`/`llm_config_dict` 参数；`enable_tools` 由 `tool_config.builtin` 推导。
- **2.3** `create_registry(builtin_names, skill_registry)`；Agent 使用 `enabled_tool_names` 控制暴露给 LLM 的工具；每 agent 通过 `_create_tools_for_agent(skill_registry, tool_config)` 获得独立 ToolRegistry，MCP 经 `register_tools_into(registry)` 按需注入。

### 阶段 3：Skills 统一

- **3.1** `evomaster/skills/__init__.py` 仅导出 `BaseSkill`、`Skill`、`SkillMetaInfo`、`SkillRegistry`（无 `OperatorSkill`/`KnowledgeSkill` 对外别名）。
- **3.2** `SkillRegistry` 单一存储、`create_subset(skill_names)`、按名称过滤加载；内部仍保留 `KnowledgeSkill` 类及 `knowledge/`、`operator/` 目录加载逻辑（见[未对齐](#未对齐或未完全集成)）。

### 阶段 4：业务 Playground 与服务

- **4.1** MatMaster 通过 `_setup_agents(skill_registry)` 填充 `self.agents`，`_create_agent` 使用 `tool_config`、`llm_config`、`skill_config`；保留 MCP、SSH、MatMasterSkillRegistry 等定制。
- **4.2** 上游 EvoMaster 中的多 Agent 示例 playground 等均使用 `_setup_agents` 与 `get_agent_tools_config`/`get_agent_skills_config`（本仓库仅保留 MatMaster 业务 playground，示例可参考 [EvoMaster 上游](https://github.com/sjtu-sai-agents/EvoMaster)）。
- **4.3** `agent_run_service`、`playground/mat_master/service/server.py` 克隆 agent 时传入 `enabled_tool_names`、`enable_tools`，与 base 一致。

---

## 未对齐或未完全集成

### 与上游差异（需保留或可选）

| 类别 | 项 | 说明 |
|------|----|------|
| **本仓独有（建议保留）** | `config_path` / `config_dir` | 与上游一致，保留。 |
| **本仓独有（建议保留）** | 顶层 `mcp` 配置 | 如 `mcp.config_file`、`mcp.enabled`、`mcp.calculation_servers`、`tool_include_only` 等，MatMaster 专用。 |
| **可选对齐** | `load_dotenv(override=True)` | 本仓已传，上游未传；影响小。 |
| **可选对齐** | `enabled_tool_names` 传值 | 本仓 `None` = 全部，上游可能传 `["*"]`；语义等价。 |

### 内部实现与上游不一致（对外 API 已统一）

| 项 | 说明 |
|----|------|
| **AgentSlots.declare** | 上游示例中有 `self.agents.declare("planning_agent", ...)`；本仓 `AgentSlots` 无 `declare`，直接通过 `_setup_agents` 的 `__setitem__` 注册。功能等价，可选补 `declare` 以兼容上游示例。 |

### 配置与使用注意事项（易踩坑）

| 项 | 说明 |
|----|------|
| **per-agent MCP 注入** | 仅当 `get_agent_tools_config(name)["mcp"]` 非空时，才会对该 agent 调用 `mcp_manager.register_tools_into(registry)`。YAML 中若未写 `tools.mcp` 或写为 `mcp: ""`，该 agent **不会**获得任何 MCP 工具。需要 MCP 时请显式配置，例如：`tools: { builtin: ["*"], mcp: "*" }`（或 `mcp: "mcp_config.json"`）。 |
| **YAML 中无 enable_tools 兼容** | `get_agent_tools_config` 不会根据 `enable_tools` 自动生成 `tools`；若 YAML 仍写 `enable_tools` 而未写 `tools`，将走默认 `{ builtin: ['*'], mcp: '' }`。当前所有 configs 已改为 `tools`，无需再加兼容逻辑。 |

### Skills 已与上游对齐（已完成）

- **配置层**：已移除 `SkillConfig`、`KnowledgeSkillConfig`、`OperatorSkillConfig`、`EvoMasterConfig.skill`、`get_skill_config()`；产品配置（如 `configs/mat_master/config.yaml`）已删除 `skill:` 段；`evomaster/__init__.py` 已去掉上述导出。
- **Skills 实现层**：已移除 `KnowledgeSkill` 类；`SkillRegistry._load_skills()` 仅从 `skills_root` 子目录加载统一类型 `Skill`；`get_meta_info_context()` 不再区分类型；已删除 `get_knowledge_skills()` / `get_operator_skills()`。
- **文档**：`docs/skills.md`、`docs/zh/skills.md` 已更新为仅描述 `Skill` 与统一加载方式。

---

## 已对齐清单

- 顶层仅 `agents`，无 `agent`；`get_agent_config(name)` 必须传 name。
- `get_agents_config()` 空时 `raise ValueError`；`_require_dict` 用于 llm/agent/session 等校验。
- 顶层 `tools: ToolConfig`（builtin/mcp 为 list）；per-agent 由 `get_agent_tools_config` 返回 dict（builtin list、mcp str）。
- `get_agent_tools_config` / `get_agent_skills_config` 解析规则与上游一致（含 `"default"`、`["*"]`、空值等）。
- `_create_agent` 仅新签名；BasePlayground、MatMaster 及测试均使用新签名。
- 每 agent 独立 ToolRegistry：`_create_tools_for_agent(skill_registry, tool_config)`，MCP 通过 `register_tools_into(registry)` 按需注入；子类可覆盖以增加 playground 级工具。
- Agent 运行时参数 `enable_tools`（由 tool_config 推导）、`enabled_tool_names`；克隆 agent 时传递 `enabled_tool_names`、`enable_tools`。
- **Skills**：仅 `Skill` 类型；无 `SkillConfig`/`get_skill_config`/顶层 `skill`；`SkillRegistry` 从 skills_root 子目录统一加载 `Skill`，无 `get_knowledge_skills`/`get_operator_skills`，`get_meta_info_context()` 不区分类型。

---

## 后续可跟进与可选

### 已接入能力

- **并行实验**：`BasePlayground.setup_exp_workspace(task_id)`、`execute_parallel_tasks(tasks, max_workers)`（当前可串行；进程级并行见 `run.run_tasks_parallel`）。
- **多模态**：TaskInstance.images、BaseExp.run(..., images=...)、`evomaster.utils.multimodal`、BaseMessage.content 支持 list[dict]、ContextManager 多模态 token 估算等。

### 可选对齐（影响小）

- 去掉 `load_dotenv(..., override=True)` 以与上游完全一致。
- 克隆/创建 agent 时传 `enabled_tool_names=["*"]` 替代 `None`（语义已等价）。

### 与 AGENTS.md 的对应

- 本迁移方案与 [AGENTS.md](../../AGENTS.md) 中「EvoMaster 上游仓库与本项目的关系」一致。
- 同步/升级上游时按本方案分阶段实施，并参考上游 [v0.0.1 → v0.0.2 迁移指南](https://github.com/sjtu-sai-agents/EvoMaster/blob/main/docs/migration/MIGRATION_GUIDE_v0.0.1_to_v0.0.2.md)。
- 当前**配置与 Playground 已按 v0.0.2 风格**（`agents`、`tools: { builtin, mcp }`、per-agent skills）；仅部分配置与 Skills 内部实现保留本仓独有或历史结构，见[未对齐](#未对齐或未完全集成)。
