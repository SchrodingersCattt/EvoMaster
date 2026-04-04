# AGENTS_evaluation.md — 评测模块约定

本文件是 `evaluation/` 目录的 AI 助手约定文档，补充 [根目录 AGENTS.md](../AGENTS.md) 中的通用约定。
**若本文件规则有变更，必须同步修改本文件；若通用约定有变更，必须同步修改 `AGENTS.md`。**

---

## 评测框架概述（MATTER v5）

评测题库位于 `evaluation/question_bank/`，采用 **MATTER v5** 格式。

### 目录结构

```
evaluation/question_bank/
├── manifest.yaml                         # 题库注册表（工具声明 + bank 文件索引）
├── batch_processing/bp_struct.yaml
├── data_diagnosis/dd_general.yaml
├── data_fitting/df_mech.yaml
├── data_fitting/df_elec.yaml
├── structure_construction/sc_struct.yaml
├── workflow_orchestration/wo_*.yaml
├── safety_refusal/sr_general.yaml
└── data/                                 # 题目输入数据文件（按题目 ID 子目录）
    ├── README.md
    ├── BP_struct_001/
    └── ...
```

### 三层评分模型

- **每条 checklist item** → 二元判定 (pass/fail)，可带 `weight`（默认 1.0）
- **轴得分** = `Σ(pass_i × weight_i) / Σ(weight_i)`（同 axis 内聚合）
- **总分** = `Σ(axis_weight × axis_score) / Σ(active_axis_weight)`

三个评分轴（`axis`）：

| 轴 | 含义 |
|---|---|
| `correctness` | 答案是否正确 |
| `grounding` | 是否使用了正确的工具/数据源，而非凭空编造 |
| `efficiency` | 过程是否高效（无冗余调用、耗时/token 合理） |

---

## 字段必填 / 选填规则

### Bank 文件顶层（`QuestionBank`）

| 字段 | 必填？ | 说明 |
|------|--------|------|
| `version` | 可选（默认 `"v5"`） | 固定写 `v5`；Runner 遇到非 v5 会报错 |
| `capability` | 可选 | 顶层 hint，不影响运行 |
| `domain` | 可选 | 顶层 hint，不影响运行 |
| `questions` | **必填** | 至少 1 条题目 |

### 每道题（`QuestionItem`）

| 字段 | 必填？ | 默认值 | 说明 |
|------|--------|--------|------|
| `id` | **必填** | — | 唯一 ID；改语义时必须换 ID（见下方约定） |
| `capability` | **必填** | — | 枚举（见下方），用于过滤、safety 路由、聚合报告 |
| `domain` | **必填** | — | 枚举（见下方），用于聚合报告 |
| `intent` | **必填** | — | 英文一句话描述题目意图；LLM 裁判上下文 + prompt 改写时使用 |
| `human_prompt_seed` | **必填** | — | 直接发给 Agent 的用户 prompt（中英文皆可） |
| `tags` | 可选 | `[]` | 标签，目前未被代码消费（开发者归类用） |
| `mode_scope` | 可选 | `["direct", "planner"]` | 决定该题跑哪些 mode；不能为空 |
| `data_files` | 可选 | `[]` | 输入数据文件引用；Runner 会复制到 Agent workspace |
| `reference_answers` | **条件必填** | `[]` | 非 `safety_refusal` 题必须至少 1 条；`safety_refusal` 可为空 |
| `scoring_checklist` | **必填** | — | 至少 1 条评分项 |

#### capability 枚举

`knowledge_recall` / `structure_construction` / `property_prediction` / `workflow_orchestration` / `data_diagnosis` / `batch_processing` / `safety_refusal`

#### domain 枚举

`struct` / `elec` / `mech` / `thermo` / `kinetic` / `optical` / `general`

### `data_files` 每条（`DataFileRef`）

| 字段 | 必填？ | 默认值 | 说明 |
|------|--------|--------|------|
| `key` | **必填** | — | 引用标识 |
| `path` | **必填** | — | 相对 `question_bank/` 的路径，不能为空字符串 |
| `oss_url` | 可选 | `""` | OSS 地址 |
| `description` | 可选 | `""` | 描述 |

### `reference_answers` 每条（`ReferenceAnswer`）

| 字段 | 必填？ | 默认值 | 说明 |
|------|--------|--------|------|
| `key` | **必填** | — | 与 `scoring_checklist.id` 配对 |
| `value` | **必填** | — | 标准答案（数值 / 字符串 / 列表 / 字典均可） |
| `tolerance` | 可选 | `None`（≥0） | 数值容差；`numerical_range` 时建议填 |
| `unit` | 可选 | `""` | 单位标注（目前仅文档用途） |
| `tool_name` | 可选 | `None` | 关联工具名 |
| `tool_arg` | 可选 | `None` | 关联工具参数名 |

### `scoring_checklist` 每条（`ScoringCheckItem`）

| 字段 | 必填？ | 默认值 | 说明 |
|------|--------|--------|------|
| `id` | **必填** | — | 唯一标识；某些 `verify` 类型要求 `reference_answers` 中有 `key == id` 的条目 |
| `criterion` | **必填** | — | 检查标准描述；`llm_binary_judge` 时 LLM 裁判直接读此字段判分 |
| `axis` | 可选 | `"correctness"` | 评分轴：`correctness` / `grounding` / `efficiency` |
| `verify` | **必填** | — | 校验方法枚举（见下方） |
| `weight` | 可选 | `1.0`（≥0） | 同一 axis 内的加权比重 |

---

## `verify` 校验方法与 `reference_answers` 对应关系

**核心规则**：若 `scoring_checklist` 某条的 `verify` 属于"需要 ref"列表，则 `reference_answers` 中**必须**有 `key == 该条 id` 的条目，否则加载时报错。

### 需要对应 `reference_answers` 条目（否则加载报错）

| verify 类型 | `reference_answers.value` 格式 | 说明 |
|---|---|---|
| `exact_match` | 任意 | 精确匹配 |
| `numerical_range` | `float` / `int` | 检查 `value ± tolerance`（tolerance 建议填） |
| `contains_all` | `list[str]` | 检查 answer 包含列表中所有项 |
| `tool_called` | `list[str]`（工具名列表） | 检查至少调用了其中一个工具 |
| `tool_args_match` | `dict` | 需配合 `tool_name` + `tool_arg` 字段 |
| `batch_single_variable_sweep` | `{"tool_name": str, "sweep_param": str, "expected_values": list}` | 批量单变量扫描 |
| `batch_tool_args_constant` | `{"tool_name": str, "param_names": str, "expected_constant": any}` | 批量参数恒定 |
| `batch_consistent_calls` | `dict` | 批量调用一致性 |
| `duration_budget` | `{"max": int}`（毫秒） | 运行时间预算 |
| `turn_budget` | `{"max": int}`（轮次数） | Agent 轮次（step）预算；`total_steps <= max` 则 pass |
| `molcrys_slab_molecular_integrity` | `{"unit_cell_atoms": int, "slab_atoms": int, "layers": int}` | 分子晶体 slab 完整性 |
| `sc005_disorder_formulas` | `dict` | 无序结构化学式 |
| `struct_file_atom_count` | `{"filename": str, "expected": int, "tolerance": float}` | 用 pymatgen 读结构文件验证总原子数 |
| `struct_file_formula` | `{"filename": str, "formula": str}` | 用 pymatgen 读结构文件验证化学式（reduced composition 比较） |
| `struct_file_bond_count` | `{"filename": str, "element_a": str, "element_b": str, "cutoff_A": float, "expected_count": int, "tolerance": float}` | 统计元素对间短于 cutoff 的键数 |
| `struct_file_bond_length` | `{"filename": str, "element_a": str, "element_b": str, "cutoff_A": float, "expected": float, "tolerance": float}` | 计算元素对间键长均值并校验 |
| `struct_file_bond_angle` | `{"filename": str, "triplet": [A, B, C], "expected_deg": float, "tolerance_deg": float, "cutoff_A": float}` | 计算 A-B-C 键角均值（B 为顶点）并校验 |
| `struct_file_cell_param` | `{"filename": str, "param": "a"\|"b"\|"c"\|"alpha"\|"beta"\|"gamma", "expected": float, "tolerance": float}` | 读晶格参数并校验 |
| `struct_file_stoichiometry_ratio` | `{"filename": str, "element_a": str, "element_b": str, "expected_ratio": float, "tolerance": float}` | 验证 count(A)/count(B) 比值 |
| `struct_file_coordination` | `{"filename": str, "center_element": str, "expected": int, "tolerance": float, "cutoff_A": float}` | 统计中心元素的配位数均值并校验 |
| `struct_file_layer_count` | `{"filename": str, "expected": int, "tolerance": float, "axis": str, "layer_tol_A": float}` | 沿指定轴在笛卡尔坐标下统计**不同原子平面**数：排序后，与当前平面锚点距离超过 `layer_tol_A`（Å）则开始新平面；默认 `layer_tol_A` 为 `0.25`。旧字段 `gap_threshold_A` 仍可读，但语义为平面合并容差（与现实现一致），新题请写 `layer_tol_A` |
| `struct_file_count` | `{"pattern": str, "expected": int, "tolerance": int}` | 统计 workspace 中匹配 glob 的文件数（无需 pymatgen） |
| `struct_file_surface_termination` | `{"filename": str, "element": str, "axis": "x"\|"y"\|"z", "side": "top"\|"bottom"\|"both", "layer_tol_A": float}` | 检查 slab 最外层（top/bottom/both）是否由指定元素构成；用于验证 O-terminated 或其他特定终止面（如 CeO2(111) 的 O 终止）|

### 不需要对应 `reference_answers` 条目

| verify 类型 | 说明 |
|---|---|
| `llm_binary_judge` | LLM 读 `criterion` 文本自行判断 pass/fail |
| `no_retries` | 直接分析 evidence 中的 tool_calls 行为 |
| `artifact_exists` | 检查文件是否存在 |
| `event_type_called` | 检查事件类型是否被触发 |
| `source_type_used` | 检查数据源类型 |
| `call_count_range` | 分析工具调用次数（建议也配上 ref） |
| `token_budget` | 用 **最后一轮 LLM** 的原始 ``total_tokens``（**不**扣 cache）：``EvidenceBundle.token_usage_last_turn.total_tokens``（轨迹取 max ``step_id`` 的 ``meta.usage``；无 ``total_tokens`` 时用 prompt+completion 推导）。对 **external baseline** 这类只有整轮汇总、没有 ``usage_vendor_by_turn`` 的摘要，允许用 ``summary.usage.total_tokens / num_turns`` 近似最后一轮。ingest 顶层 ``item["tokens"]`` / ``extra["tokens_last_turn"]`` 与 :func:`evaluation.eval_ingest_client.extract_ingest_tokens` 对齐：有 ``usage_vendor_by_turn`` 时取最后一项的 ``total_tokens``；baseline 若启用近似口径则按 ``summary.usage.total_tokens / num_turns``；其余情况回退到 ``summary.usage.total_tokens``（整表累加标量）。建议配上 ref，格式同 `duration_budget`） |

---

## 字段被谁消费（数据流简表）

| 字段 | 发给 Agent？ | 评测基础设施用途 |
|------|:---:|------|
| `human_prompt_seed` | ✅ **直接发送** | Simulator 直接作为 prompt |
| `data_files` 的文件 | ✅ **间接**（复制到 workspace） | Runner 复制文件并追加提示 |
| `intent` | ⚠️ 仅 prompt 改写模式 | LLM 裁判上下文；prompt 改写上下文 |
| `id` | ❌ | task_id 标识；`--questions` 过滤 |
| `capability` | ❌ | `--capabilities` 过滤；safety 路由；聚合 + 报告 |
| `domain` | ❌ | 聚合 + 报告 |
| `mode_scope` | ❌ | 决定跑哪些 mode |
| `tags` | ❌ | 目前未被代码消费（预留） |
| `reference_answers` | ❌ | Evaluator 的标准答案查找表 |
| `scoring_checklist` | ❌ | Evaluator 逐条执行判分 |

---

## 题库编写约定

### 1. ID 变更规则

修改 `evaluation/question_bank/**/*.yaml` 中任一题目的题干、期望答案、`reference_answers`、`scoring_checklist` 或其他会影响评测语义的内容时，**必须同时更新该题的顶层 `id`**。新 `id` 可用时间戳或其他唯一后缀；若只是纯格式化、注释、空白或不影响语义的整理，可不改 `id`。

**程序化判分口径变更**：若修改 `evaluation/` 下某 `verify` 对应的 validator 实现，导致**同一 `reference_answers` 配置下的 pass/fail 含义发生变化**（例如层数由“粗间隙分块”改为“原子平面计数”），应视为评测语义变更：在题库中显式更新该题的 `human_prompt_seed` / `scoring_checklist` / `reference_answers` 等对判分的描述或参数，并**按上条规则更新该题顶层 `id`**（除非能证明全仓库无任何题目依赖旧语义且无需对齐题干——一般应对齐题库）。

### 2. 保持 YAML 原结构

编辑题库 YAML 时，尽量不要无关地改动 `key/value` 组织、字段层级、字段命名、字段顺序、锚点引用关系或列表结构；除非该结构调整本身就是本次修改所必需。纯空白、缩进、换行、引号风格等表面格式也应尽量少动，但优先级低于保持语义结构稳定。

### 3. manifest.yaml 同步

新增题目或 bank 文件后，需同步更新 `evaluation/question_bank/manifest.yaml` 中对应 bank 的 `questions` 计数。

### 4. data_files 放置规则

`data_files[].path` 必须指向 `question_bank/data/<v5_question_id>/...`。目录名使用当前 v5 题号。

### 5. 常用模板片段

几乎每道非 safety 题都建议包含的效率类通用项：

```yaml
reference_answers:
  - key: turn_budget
    value: {max: 12}         # 按题目复杂度调整；粗拍后根据执行情况收紧
  - key: duration_budget
    value: {max: 7200000}    # 2 小时，按需调整
  - key: token_budget_total
    value: {max: 8000}       # 最后一轮输入 token；复杂任务约 8k，批处理可放宽至 10k~12k

scoring_checklist:
  - id: no_retries
    criterion: "No repeated identical tool calls with the same parameter set."
    axis: efficiency
    verify: no_retries
  - id: efficiency_judge
    criterion: "Task completes without unnecessary exploratory calls."
    axis: efficiency
    verify: llm_binary_judge
  - id: turn_budget
    criterion: "Agent completes the task within the turn (step) budget."
    axis: efficiency
    verify: turn_budget
  - id: duration_budget
    criterion: "Wall-clock run duration does not exceed benchmark ceiling."
    axis: efficiency
    verify: duration_budget
  - id: token_budget_total
    criterion: "Total token usage stays within benchmark ceiling."
    axis: efficiency
    verify: token_budget
```

### 6. 评分与工具名称解耦

`human_prompt_seed`（直接发给 Agent 的 prompt）**禁止包含内部工具名称**（如 `mat_sg_build_surface_slab`、`mat_dpa_submit_optimize_structure` 等 MCP tool identifier）。Agent 应自行根据任务描述选择合适的工具，而不是被 prompt 直接提示。

- `reference_answers` 中的 `tool_name` / `tool_arg` 字段、`scoring_checklist.criterion` 中引用工具名均为**内部评分逻辑**，不发给 Agent，允许使用工具名。
- `intent` 中可以使用通用术语（如"表面构建工具"、"结构优化器"），但同样不应包含具体 MCP tool identifier。
- 新增或修改题目时，需检查 `human_prompt_seed` 中是否意外泄漏了工具名；审查方式：在所有 YAML 的 `human_prompt_seed` 文本中搜索 `mat_sg_`、`mat_dpa_`、`mat_struct_db_` 等前缀。

---

## DevShell 与 Claude Agent SDK 外层编排

- DevShell / IDE 流程：`evaluation/docs/devshell/devshell_claude_code_eval.md`（`run_devshell_eval.py` + `score_devshell_tasks.py` 自动评分）。
- **程序化**多轮「跑题 → 判分 → 改提示词/工具」：`evaluation/docs/devshell/devshell_agent_sdk_loop.md`；入口 `evaluation/scripts/devshell/run_devshell_agent_loop.py`，可选依赖 `uv sync --extra eval-agent`（`pyproject.toml` 中 `[project.optional-dependencies] eval-agent`）。自迭代时模型侧约定「每处修改单独 commit、无效则 revert」；编排层可用 `--no-git-reset-on-regression` 关闭「较上一轮退步则 reset 到本轮起点」的保险。默认在 **`--eval-ingest-pending-only`** 下每轮结束后自动 `score_devshell_tasks.py --submit` 上报 ingest（见该文档）；`--no-eval-ingest-submit-each-iteration` 可关。**双 Agent**：主 Agent 不得改 `evaluation/question_bank/`；题库/checklist 调整经 `escalate_checklist_revision` 由专责会话处理（见该文档）。

---

## 运行入口

```bash
# 指定 capability 或题目 ID 运行
uv run python -m evaluation.cli \
  --eval-config evaluation/config.yaml \
  --capabilities batch_processing workflow_orchestration \
  --questions DF_mech_001 WO_mech_001

# 后台运行（Linux）
evaluation/scripts/matter_cli/run_mat_master_eval_bg.sh start

# 后台运行（Windows）
evaluation/scripts/matter_cli/run_matmaster_evaluation_bg.ps1
```

详细说明见 [`evaluation/README_CN.md`](README_CN.md)。
