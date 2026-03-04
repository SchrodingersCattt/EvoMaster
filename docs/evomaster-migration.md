# EvoMaster v0.0.1 → v0.0.2 迁移方案

本文档将推荐的迁移方案固化为可执行步骤，便于从当前基于 EvoMaster v0.0.1 的代码逐步对齐上游 v0.0.2。迁移采用**分阶段、带兼容层**的方式，每阶段均可独立验证。

**上游迁移指南**：[EvoMaster MIGRATION_GUIDE v0.0.1 to v0.0.2](https://github.com/sjtu-sai-agents/EvoMaster/blob/main/docs/migration/MIGRATION_GUIDE_v0.0.1_to_v0.0.2.md)

---

## 文档结构

| 章节 | 说明 |
|------|------|
| [迁移状态](#迁移状态) | 各阶段完成情况一览 |
| [阶段 1～4](#阶段-1配置与兼容层) | 配置、Playground、Skills、业务迁移步骤（已完成） |
| [收尾与对齐情况](#收尾与对齐情况) | 与上游完全对齐的收尾步骤及当前差异梳理 |
| [后续可跟进与可选对齐](#后续可跟进与可选对齐) | 已接入能力、未接入项、可选对齐、本仓独有 |
| [与 AGENTS.md 的对应](#与-agentsmd-的对应关系) | 与项目约定的对应关系 |

---

## 迁移状态

| 步骤 | 状态 |
|------|------|
| 阶段 1.1 配置模型 | 已完成 |
| 阶段 1.2 配置兼容（YAML / 加载层） | 已完成 |
| 阶段 1.3 验收 | 已完成 |
| 阶段 1.4 现有 YAML 迁移到 v0.0.2 写法 | 已完成 |
| 阶段 2.1 AgentSlots 与 self.agents | 已完成 |
| 阶段 2.2 _create_agent 新旧签名并存 | 已完成 |
| 阶段 2.3 工具注册与 Agent 工具可见性 | 已完成 |
| 阶段 2.4 验收 | 已完成 |
| 阶段 3.1～3.4 Skills 统一 | 已完成 |
| 阶段 4.1～4.4 业务 Playground 与服务 | 已完成 |
| 与上游完全对齐（收尾） | 已完成 |

---

## 阶段 1：配置与兼容层

**目标**：在配置模型中支持 v0.0.2 的 `agents`、`ToolConfig`、per-agent 的 tools/skills，并兼容现有 YAML。

### 1.1 配置模型（`evomaster/config.py`）

- `EvoMasterConfig` 增加 `agents: dict[str, Any]`；新增 `ToolConfig`（顶层：`builtin`/`mcp` 均为 `list[str]`），并增加顶层 `tools: ToolConfig`。
- Per-agent 读取：`get_agent_config(name)`、`get_agents_config()`、`get_agent_llm_config(name)`、`get_agent_tools_config(name)`、`get_agent_skills_config(name)`。
- 收尾时已移除顶层 `agent`；`get_agent_config` 仅支持带 `name`；tools/skills 解析与上游一致。

### 1.2 配置兼容（YAML / 加载层）

- 若 agent 配置中仅有 `enable_tools: true/false`，自动转换为 `tools: { builtin: ["*"] }` 或 `tools: { builtin: [] }`（阶段 1.4 后 YAML 已全部改为 v0.0.2 写法，兼容逻辑可不再依赖）。

### 1.3 验收

- 现有 configs 下 YAML 通过新加载逻辑行为一致；新 YAML 可使用 `tools:`、per-agent `skills:`。
- 验收：`pytest tests/test_evomaster_config_migration.py -v`（Python ≥3.10）。

### 1.4 现有 YAML 迁移到 v0.0.2 写法

- `configs/` 下已全部改为 `tools: { builtin: ["*"] }` 等，无 `enable_tools`；per-agent 需 skills 时使用 `skills: ["*"]` 或具体列表。

---

## 阶段 2：Playground 与 Agent 创建

**目标**：引入 AgentSlots、`_setup_agents()`，统一多 Agent 存储；工具按名称控制；Agent 支持 `enabled_tool_names`。

### 2.1 AgentSlots 与 self.agents

- `AgentSlots` 容器，`self.agents` 存多个 agent；`_setup_agents()` 遍历配置创建并注册。
- 兼容：`self.agent = self.agents.get_random_agent()` 等，单 agent 调用方仍可用。

### 2.2 _create_agent 签名

- 收尾后仅保留新签名：`_create_agent(name, agent_config=None, llm_config=None, tool_config=None, skill_config=None, skill_registry=None)`；内部从 `tool_config` 推导 `enabled_tool_names`。旧参数 `enable_tools`、`llm_config_dict` 已移除，调用方已全部迁至新签名。

### 2.3 工具注册与 Agent 工具可见性

- `create_registry(builtin_names, skill_registry)`；`ToolRegistry.get_tool_specs(names)` 支持按名称过滤；Agent 使用 `enabled_tool_names` 控制暴露给 LLM 的工具。

### 2.4 验收

- 多 agent 正确存入 `self.agents`；新 Playground 可用 `tool_config`、`enabled_tool_names` 控制工具。验收测试：`test_stage_2_4_multi_agent_stored_in_agents_slots`。

---

## 阶段 3：Skills 统一

**目标**：与上游一致，对外仅 Skill；SkillRegistry 支持按名称过滤与 create_subset。

### 3.1 类型统一

- 可执行技能类名为 **Skill**（无 OperatorSkill 别名）；`SkillMetaInfo` 仅 `name`、`description`、`license`；`evomaster/skills/__init__.py` 仅导出 BaseSkill、Skill、SkillMetaInfo、SkillRegistry。

### 3.2 SkillRegistry

- 单一存储 `_skills`；`get_knowledge_skills` / `get_operator_skills` 按类型过滤；`create_subset(skill_names)` 返回子集。

### 3.3 引用替换与验收

- 全仓统一使用 `Skill`；对外 API 与上游一致，集成路径（MatMasterSkillRegistry、SkillTool 等）正常。

---

## 阶段 4：业务 Playground 与服务

**目标**：MatMaster、minimal_multi_agent、minimal_kaggle 等改用 `self.agents` 与新区配置；克隆 agent 时使用 `enabled_tool_names` / `tool_config`。

### 4.1 MatMaster

- 多 agent 通过 `_setup_agents(skill_registry)` 填充 `self.agents` 并设 `self.agent`；`_create_agent` 使用 `tool_config`、`llm_config`、`skill_config`；per-agent skill 子集由 `get_agent_skills_config(name)` + `create_subset` 传入。保留自定义 MCP、SSH、MatMasterSkillRegistry。

### 4.2 minimal_multi_agent、minimal_kaggle

- 均改为 `_setup_agents`，从 `self.agents.get(...)` 赋回子类属性；minimal_kaggle 校验 agent 配置后传入 skill_registry。

### 4.3 agent_run_service 与 server

- 克隆 agent 时传入 `enabled_tool_names`，与 base 一致（`agent_run_service.py`、`playground/mat_master/service/server.py`）。

### 4.4 验收

- 各 Playground 通过 `_setup_agents` 与 `self.agents` 创建 agent；克隆逻辑已传 `enabled_tool_names`。建议 CI/手动跑一遍回归。

---

## 收尾与对齐情况

### 已完成的收尾步骤

- **配置**：移除顶层 `agent`；`get_agent_config(name)` 仅支持带 name；`get_agent_tools_config` / `get_agent_skills_config` 解析规则与上游一致；无 `enable_tools` 兼容依赖。
- **ConfigManager**：`get_agents_config()` 空时 `raise ValueError`；已实现 `_require_dict` 并在 get_llm_config、get_agent_config、get_agents_config、get_session_config 等使用。
- **顶层 tools**：`EvoMasterConfig` 已增加 `tools: ToolConfig`（`builtin`/`mcp` 均为 `list[str]`）；per-agent 仍由 `get_agent_tools_config` 返回 dict。
- **_create_agent**：旧签名（`enable_tools`、`llm_config_dict`）已移除；BasePlayground、MatMaster、x_master、minimal_skill_task 及测试均已使用新签名。

### 与上游当前差异（简要）

| 类别 | 说明 |
|------|------|
| **已对齐** | 顶层 `tools`(ToolConfig)、ToolConfig 类型、get_agents_config 空抛错、_require_dict、get_llm_config/get_agent_config 校验、get_agent_tools_config/get_agent_skills_config 规则、顶层 agent 移除、_create_agent 新签名、self.agent 兼容设值、**每 agent 独立 tools**（_create_tools_for_agent + register_tools_into）。 |
| **本仓独有** | `get_skill_config()`、`skill`/`mcp` 配置（业务扩展）；`config_path` 与上游一致，保留。 |
| **Tools 模型** | **已对齐**：每 agent 在 _create_agent 内通过 _create_tools_for_agent(skill_registry, tool_config) 获得独立 ToolRegistry；MCP 连接全局初始化一次，通过 register_tools_into(registry) 按需注入到各 agent 的 registry；子类（如 MatMasterPlayground）可覆盖 _create_tools_for_agent 增加 playground 级工具。 |
| **实现细节** | `enabled_tool_names`：本仓 `None`=全部，上游传 `["*"]`，Agent 侧等价；`load_dotenv(..., override=True)` 本仓已用，上游未传，影响小。 |

验收：在 Python ≥3.10 下 `pytest tests/test_evomaster_config_migration.py -v` 通过。

---

## 后续可跟进与可选对齐

### 已接入能力

- **并行实验**：`BasePlayground.setup_exp_workspace(task_id)`、`execute_parallel_tasks(tasks, max_workers)`（当前串行；进程级并行用 `run.run_tasks_parallel`）。
- **多模态**：TaskInstance 含 `images: list[str]`；BaseExp.run(..., images=...)、Playground.run(..., images=...)；`evomaster.utils.multimodal`（encode_image_to_base64、build_multimodal_content）；Agent 初始化时按 task.images 构建多模态 UserMessage；BaseMessage.content 支持 list[dict]；Dialog.get_messages_for_api 与 context 计费支持多模态。

### 已接入（与上游一致）

- **每 agent 独立 tools**：BasePlayground 不再使用全局 `self.tools`；在 _create_agent 内调用 _create_tools_for_agent(skill_registry, tool_config) 为每个 agent 创建独立 ToolRegistry（builtin + skill_tool + 按 tool_config.mcp 注入的 MCP 工具）；MCPToolManager 提供 register_tools_into(registry) 向指定 registry 注入工具；MatMasterPlayground 等子类通过覆盖 _create_tools_for_agent 增加 memory、peek_file 等。

### 可选对齐（影响小）

| 项 | 说明 |
|----|------|
| load_dotenv(override=True) | 本仓已传，上游未传；可去掉以完全一致。 |
| enabled_tool_names 传值 | 本仓 None=全部，上游 ["*"]；语义已等价。 |

### 本仓独有（建议保留）

- **skill / mcp / get_skill_config**：mat_master 等业务依赖。
- **config_path**：与上游一致，保留。

---

## 与 AGENTS.md 的对应关系

- 本迁移方案与 [AGENTS.md](../AGENTS.md) 中「EvoMaster 上游仓库与本项目的关系」一致：同步/升级上游时按本方案分阶段实施，并参考上游 [v0.0.1 → v0.0.2 迁移指南](https://github.com/sjtu-sai-agents/EvoMaster/blob/main/docs/migration/MIGRATION_GUIDE_v0.0.1_to_v0.0.2.md)。
- 目标为与上游完全一致时，在阶段 1～4 完成后执行本文档中的「收尾」步骤；当前收尾已完成，差异仅剩设计选择与可选细节。
