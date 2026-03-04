# EvoMaster v0.0.1 → v0.0.2 迁移方案

本文档将推荐的迁移方案固化为可执行步骤，便于从当前基于 EvoMaster v0.0.1 的代码逐步对齐上游 v0.0.2。迁移采用**分阶段、带兼容层**的方式，每阶段均可独立验证，降低冲突与回滚成本。

上游迁移指南：[EvoMaster MIGRATION_GUIDE v0.0.1 to v0.0.2](https://github.com/sjtu-sai-agents/EvoMaster/blob/main/docs/migration/MIGRATION_GUIDE_v0.0.1_to_v0.0.2.md)

### 迁移状态

| 步骤 | 状态 |
|------|------|
| 阶段 1.1 配置模型 | **已完成** |
| 阶段 1.2 配置兼容（YAML / 加载层） | **已完成** |
| 阶段 1.3 验收 | **已完成** |
| 阶段 1.4 现有 YAML 迁移到 v0.0.2 写法 | **已完成** |
| 阶段 2 | 未完成 |
| 阶段 3 | 未完成 |
| 阶段 4 | 未完成 |
| 与上游完全对齐（收尾） | 未完成 |

---

## 阶段 1：配置与兼容层

**目标**：在配置模型中支持 v0.0.2 的 `agents`、`ToolConfig`、per-agent 的 tools/skills，同时兼容现有 YAML（`enable_tools` 等）。

### 1.1 配置模型（`evomaster/config.py`）**[已完成]**

- 在 `EvoMasterConfig` 中增加 `agents: dict[str, Any]` 字段（与现有 `agent` 并存；加载时若 YAML 有 `agents` 则优先使用）。
- 新增 `ToolConfig` 类（如 `builtin: list[str]`、`mcp: str`），并在 per-agent 配置中支持 `tools` 字段。
- 新增 per-agent 读取方法：
  - `get_agent_config(name: str)`：按名称返回单个 agent 配置；
  - `get_agents_config()`：返回全部 `agents` 配置；
  - `get_agent_llm_config(name)`、`get_agent_tools_config(name)`、`get_agent_skills_config(name)`：按名称返回对应子配置。
- 保留现有 `get_agent_config()` 无参版本行为（如返回默认/单 agent 的字典），供旧调用方使用。

**兼容性说明（阶段 1.1）**：以下行为为兼容旧代码而保留，与上游 v0.0.2 不一致，将在「与上游完全对齐」收尾步骤中移除或改为与上游一致：
- 顶层保留 `agent`（单数）字段，与 `agents` 并存（上游仅保留 `agents`）。
- `get_agent_config(name=None)` 支持无参调用，且无参时返回 `config.agent` 或「第一个」agent（上游应为 `get_agent_config(name: str)`，必须传 name）。
- `get_agent_tools_config` / `get_agent_skills_config` 缺失时返回默认 dict/[] 及简写处理（上游类型或默认约定可能不同，收尾时以官方实现为准）。

### 1.2 配置兼容（YAML / 加载层）**[已完成]**

- 在配置加载或 getter 中增加一层转换：若某 agent 配置中仅有 `enable_tools: true/false`，则自动转换为 `tools: { builtin: ["*"] }` 或 `tools: { builtin: [] }`，这样现有所有使用 `enable_tools` 的 YAML 无需一次性修改。

### 1.3 验收 **[已完成]**

- 现有 configs 下的 YAML 无需改动即可通过新加载逻辑得到与当前行为一致的配置。
- 新写的 YAML 可使用 `tools:`、per-agent `skills:` 等 v0.0.2 格式。
- 验收方式：运行 `pytest tests/test_evomaster_config_migration.py -v`（需 Python ≥3.10）。

### 1.4 现有 YAML 迁移到 v0.0.2 写法 **[已完成]**

- 将 `configs/` 下所有仍使用 `enable_tools: true/false` 的 agent 配置改为 `tools: { builtin: ["*"] }` 或 `tools: { builtin: [] }` 等 v0.0.2 写法。
- 若有 per-agent 的 skills 需求，在对应 agent 下增加 `skills: ["*"]` 或 `skills: ["rag", "pdf"]` 等；若暂无需求可省略或写空列表。
- 迁移完成后，确认仓库内无 YAML 再依赖 `enable_tools`（可全文搜索 `enable_tools` 校验）。
- **目的**：完成此步后，在「与上游完全对齐（收尾）」中即可安全移除 `_normalize_agent_config_tools` 及 getter 里对 `enable_tools` 的兼容逻辑，与上游完全一致。

---

## 阶段 2：Playground 与 Agent 创建 **[未完成]**

**目标**：引入 AgentSlots、`_setup_agents()`，统一多 Agent 存储；工具注册支持按名称控制；Agent 支持 `enabled_tool_names`。

### 2.1 AgentSlots 与 self.agents（`evomaster/core/playground.py`）

- 引入 `AgentSlots` 容器类，支持 `dict` 式访问与属性访问（如 `self.agents.planning_agent`）。
- 在 `BasePlayground` 中：用 `self.agents` 存储多个 agent；在 `_setup_agents()` 中遍历 `agents` 配置，为每个 agent 创建实例并注册到 `self.agents`。
- 保留向后兼容：例如 `self.agent = self.agents.get_random_agent()` 或取第一个/默认 agent，确保只依赖单 agent 的调用方（如部分 Exp、`_create_exp()`）仍可用。

### 2.2 _create_agent 新旧签名并存

- **新签名**：`_create_agent(name, agent_config=None, llm_config=None, tool_config=None, skill_config=None)`；内部用 `tool_config` 推导 `enabled_tool_names`，并传给 Agent。
- **旧签名**：保留 `enable_tools`、`llm_config_dict`、`skill_registry` 参数；在实现中将其转换为 `tool_config` / `llm_config` / `skill_config` 后调用新逻辑，便于 MatMaster 等子类逐步迁移。

### 2.3 工具注册与 Agent 工具可见性

- 在 `evomaster/agent/tools/base.py` 中实现 `create_registry(builtin_names: list[str], skill_registry=None)`；当 `builtin_names=["*"]` 时行为与当前 `create_default_registry(skill_registry)` 一致。
- Agent 增加 `enabled_tool_names` 参数（或从 `tool_config` 推导）：仅将列表中的工具暴露给 LLM，与「代码中可调用的工具」解耦；所有工具仍注册到 registry。

### 2.4 验收

- BasePlayground 多 agent 模式下，每个 agent 均正确存入 `self.agents`，不再出现「只保留最后一个」的情况。
- 仍使用旧 `_create_agent(..., enable_tools=..., llm_config_dict=...)` 的子类无需修改即可通过兼容层工作。
- 新 Playground 可使用 `tool_config`、`enabled_tool_names` 精确控制工具。

---

## 阶段 3：Skills 统一 **[未完成]**

**目标**：将 KnowledgeSkill / OperatorSkill 统一为 Skill，SkillRegistry 支持按名称过滤与 create_subset；与 v0.0.2 的 Skill 模型对齐。

### 3.1 类型统一（`evomaster/skills/base.py`）

- 将 `OperatorSkill` 重命名为 `Skill`（或保留 `OperatorSkill` 作为 `Skill` 的别名以兼容现有 import）。
- `KnowledgeSkill`：要么合并为 `Skill` 的一种（用属性区分），要么保留为 `Skill` 子类；确保对外 API 逐步收敛到「仅使用 Skill」。
- `SkillMetaInfo`：移除或弃用 `skill_type` 在配置/序列化中的依赖，与 v0.0.2 行为一致；若内部仍需要区分，可用可选字段或子类。

### 3.2 SkillRegistry

- 使用单一存储（如统一 `_skills: dict[str, Skill]`），支持构造时 `skills: list[str] | None` 按名称过滤加载；新增 `create_subset(names: list[str])` 方法。

### 3.3 引用替换

- `evomaster/agent/tools/skill.py`：将所有 `OperatorSkill` / `KnowledgeSkill` 类型注解与分支改为 `Skill`（或兼容别名）。
- `playground/mat_master/core/registry.py`：加载逻辑改为使用统一 `Skill` 及 Registry 的按名过滤/子集接口。
- `evomaster/skills/__init__.py`：导出 `Skill`；可视情况保留 `OperatorSkill`/`KnowledgeSkill` 为别名或弃用。

### 3.4 验收

- 所有现有 Skill 相关单测与集成路径通过。
- 新代码仅依赖 `Skill` 与 SkillRegistry 的 per-agent 子集能力。

---

## 阶段 4：业务 Playground 与服务 **[未完成]**

**目标**：MatMaster、minimal_multi_agent、minimal_kaggle 等改用 `self.agents` 与新区配置；agent_run_service / server 中克隆 agent 时使用 `enabled_tool_names` 或 `tool_config`。

### 4.1 MatMaster（`playground/mat_master/`）

- 在保留自定义 MCP、SSH、MatMasterSkillRegistry 的前提下，改为从 `self.agents` 获取 agent（或通过 `agents.declare(...)` 后使用）。
- 创建 agent 时逐步改为传入 `tool_config` / `llm_config` / `skill_config`；若短期仍传 `enable_tools`，通过兼容层转换为 `tool_config`。

### 4.2 minimal_multi_agent、minimal_kaggle

- 从「子类自己维护多个 agent 属性（如 planning_agent、coding_agent）」改为使用 `self.agents` 的槽位；或短期在 BasePlayground 的 `_setup_agents()` 中同时填充 `self.agents` 与子类期望的 `self.planning_agent` 等，做过渡兼容。
- Exp 中改为从 `self.agents.planning_agent`、`self.agents.coding_agent` 等取值，或保持现有属性名但由 base 在 setup 时统一赋值。

### 4.3 agent_run_service 与 server

- 克隆或重建 agent 时，不再依赖 `base.enable_tools`，改为使用 `enabled_tool_names` 或从 `tool_config` 推导，保证克隆出的 agent 与配置一致。

### 4.4 验收

- 所有 Playground 与线上/测试流程行为与迁移前一致或符合预期。
- 配置可完全采用 v0.0.2 的 `tools:`、per-agent `skills:` 等形式。

---

## 与上游完全对齐（收尾）**[未完成]**

在阶段 1～4 **及阶段 1.4（现有 YAML 已迁移到 v0.0.2 写法）** 完成后，若需与 EvoMaster 上游 v0.0.2 完全一致，执行以下收尾步骤。

### 配置与 ConfigManager

- **enable_tools 兼容逻辑**：在阶段 1.4 已将所有 YAML 改为 `tools:` 的前提下，移除 `_normalize_agent_config_tools()`；在 `get_agent_config`、`get_agents_config` 中不再做 enable_tools → tools 的转换，直接返回原始配置。
- **顶层 `agent`**：从 `EvoMasterConfig` 中移除 `agent` 字段，或标记为弃用（deprecated）；所有 YAML 与代码仅使用 `agents`。迁移前需将仍使用 `agent:` 的配置改为 `agents: { default: ... }` 等形式。
- **get_agent_config(name)**：改为仅支持 `get_agent_config(name: str)`，移除无参重载；移除「无参时返回 config.agent 或第一个 agent」的逻辑。所有调用方改为显式传入 agent 名称。
- **get_agent_tools_config / get_agent_skills_config**：若上游返回 `ToolConfig` 等强类型，将本仓库的返回类型与默认值改为与上游一致；移除仅为兼容旧配置的简写（若上游无对应简写）。

### 其他兼容层

- 阶段 2 中保留的 `self.agent`、无参兼容等，在业务全部迁到 `self.agents` 后移除。
- 阶段 2 中 `_create_agent` 的旧签名（`enable_tools`、`llm_config_dict`、`skill_registry`）在调用方全部迁移后移除。

### 验收

- 配置模型、ConfigManager API 与上游 [EvoMaster config](https://github.com/sjtu-sai-agents/EvoMaster) 一致；无本仓库独有的兼容分支。

---

## 可选：后续可跟进的上游能力

- **并行实验**：ResourceAllocator、`setup_exp_workspace`、`execute_parallel_tasks()` 等，在需要时再引入。
- **多模态**：TaskInstance.images、`encode_image_to_base64`、`build_multimodal_content` 等，可按需单独合入。

---

## 与 AGENTS.md 的对应关系

- 本迁移方案与 [AGENTS.md](../AGENTS.md) 中「EvoMaster 上游仓库与本项目的关系」一节一致：在同步/升级上游时，以本方案分阶段实施，并参考上游 [v0.0.1 → v0.0.2 迁移指南](https://github.com/sjtu-sai-agents/EvoMaster/blob/main/docs/migration/MIGRATION_GUIDE_v0.0.1_to_v0.0.2.md)。
- 目标为与上游**完全一致**时，在阶段 1～4 完成后执行本文档中的「与上游完全对齐（收尾）」步骤。
