# AGENTS_evaluation.md — 评测模块约定

本文件是 `evaluation/` 目录的 AI 助手约定文档，补充 [根目录 AGENTS.md](../AGENTS.md) 中的通用约定。
**若本文件规则有变更，必须同步修改本文件；若通用约定有变更，必须同步修改 `AGENTS.md`。**

---

## 评测框架概述（MATTER v5）

评测题库位于 `evaluation/question_bank/`，采用 **MATTER v5** 格式。

### 目录结构

```
evaluation/question_bank/
├── manifest.yaml              # 题库注册表（path = `<capability>/<xx>_<domain>.yaml` 或 `<capability>/<xx>_<domain>_<tag>.yaml`，xx=两字母简写）
├── batch_processing/          # 如 bp_catalysis.yaml、bp_agnostic.yaml
├── data_diagnosis/
├── execution_contract/
├── input_generation/
├── safety_refusal/
├── scientific_analysis/
├── structure_construction/
├── structure_retrieval/
├── workflow_orchestration/
└── data/                      # 题目输入数据（按题目 ID 子目录；路径与 YAML 中 data_files 一致）
    ├── README.md
    └── ...
```

**约定**：

- **manifest 中每条 `path` 对应一个 bank 文件**：文件名为 canonical ``<capability>/<xx>_<domain>.yaml``，或同一 `(capability, domain)` 下经批准的分裂名 ``<capability>/<xx>_<domain>_<tag>.yaml``（如按工具链拆分的 `ig_agnostic_vasp.yaml` / `ig_agnostic_abacus.yaml`）。**`<xx>` 为两字母 capability 简写**（见 `tests/evaluation/capability_abbrev.py` 中 `CAPABILITY_TO_TWO_LETTER`；canonical basename 公式见同文件 `bank_yaml_basename`）。文件名与 `(capability, domain)` 的一致性由 `tests/evaluation/test_question_bank_taxonomy.py` 中 `test_bank_yaml_filename_matches_capability_and_domain` 校验。同一文件内所有题目的 `domain` 与顶层 `domain` 一致。
- 专题（如 CO₂RR）等优先用题目级 **`tags`** 或 `id` 前缀区分；仅在必要时（例如输入生成按不同 DFT 工具链维护）将同一 domain 拆为多文件。

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

### `EvalConfig` 与 Agent 运行（`run_mat_task`）

- **`empty_completion_max_retries`**（默认 `1`）：当单次运行结果为 `status=completed`、无工具调用、且无可见答案（含内核 `reason=natural` 或旧版 playground 无 `reason` 字段）时，视为「可能因网关/流式偶发空流」，**整题重跑**最多额外次数；`0` 表示关闭。`mat_result` 会附带 `empty_completion_retry_count`（实际执行的重试次数），`duration_ms` 为**多次尝试之和**。

---

## 字段必填 / 选填规则

### Bank 文件顶层（`QuestionBank`）

| 字段 | 必填？ | 说明 |
|------|--------|------|
| `version` | 可选（默认 `"v5"`） | 建议写 `v5`；Runner 遇到非 v5 会报错 |
| `capability` | **必填** | bank 的固定能力切片；**必须与该 bank 下每道题的 `capability` 完全一致**，否则加载报错 |
| `domain` | **必填** | bank 的固定领域切片；**必须与该 bank 下每道题的 `domain` 完全一致**，否则加载报错 |
| `questions` | **必填** | 至少 1 条题目 |

**禁止 mixed bank**：一个 bank 文件只表示一个固定的 `(capability, domain)` 切片。若题目需要落在不同 capability 或 domain，必须拆成多个 bank 文件，而不是省略顶层字段规避校验。

### 每道题（`QuestionItem`）

| 字段 | 必填？ | 默认值 | 说明 |
|------|--------|--------|------|
| `id` | **必填** | — | 唯一 ID；改语义时必须换 ID（见下方约定） |
| `capability` | **必填** | — | 枚举（见下方），用于过滤、safety 路由、聚合报告 |
| `domain` | **必填** | — | 枚举（见下方），用于聚合报告 |
| `intent` | **必填** | — | 英文一句话描述题目意图；LLM 裁判上下文 + prompt 改写时使用 |
| `human_prompt_seed` | **必填** | — | 直接发给 Agent 的用户 prompt（中英文皆可） |
| `tags` | 可选 | `[]` | 粗粒度 **facet** 标签，取值限定为 `QuestionTag`（`evaluation/core/question_tags.py`）；**不要**把晶面、化学式、MP id 等写进 tags（应写在题干 / `intent`） |
| `priority` | 可选 | `None` | 门控优先级；`"P0"` = P0 回归门控（见下方 P0 Gate 章节） |
| `data_files` | 可选 | `[]` | 输入数据文件引用；Runner 会复制到 Agent workspace |
| `reference_answers` | **条件必填** | `[]` | 非 `safety_refusal` 题必须至少 1 条；`safety_refusal` 可为空 |
| `scoring_checklist` | **必填** | — | 至少 1 条评分项 |

#### capability / domain / tags 语义约定（须遵守）

字段名 **`capability` / `domain` / `tags` 为仓库与 `schemas.py` 中的正式列名**，**不计划**改为 `task` / `subject` / `facet`。规划讨论中的 **Task / Subject / Facet** 与三者**一一对应**，仅作口头与架构文档别名。

| 字段 | 别名（规划用语） | 单选 / 多选 | 必须回答的一句话 |
|------|------------------|-------------|------------------|
| **`capability`** | Task（任务类） | **单选**（枚举之一） | 这道题要测的是哪一类**任务能力**？（例如：建结构、库检索、批量扫描、日志诊断、写 VASP/ABACUS 输入、多步编排与交付、安全拒答、CO₂RR 专题等。） |
| **`domain`** | Subject（业务线 / 应用场景） | **单选**（枚举之一） | 这道题最终服务于哪条**业务线或应用场景**？五条业务线为 `battery` / `catalysis` / `polymer` / `alloy` / `semiconductor`；无法归入时任选 `agnostic`（见下方 domain 枚举）。 |
| **`tags`** | Facet（主题 / 工具 / 项目线） | **多选**（字符串列表，可空） | 除 capability/domain 外，还需要哪些**可并列**的标记？（如产品线、工具链、内部专题、提示词契约类 `direct_contract` 等。） |

**填写顺序（固定）**：先 **`capability`** → 再 **`domain`** → 最后按需补 **`tags`**。同一道题只有一个 capability、一个 domain；tags 可多个或没有。

**`tags` 没有（不写或 `tags: []`）怎么理解**：表示**不需要**额外 Facet 标记；**capability** + **domain** 已足够描述本题归类，题目合法且常见。只有当你还想打「第二条轴线」的信息（项目线、工具链、契约子类、内部分析用关键词）时才加字符串；**不要为了凑字段而填 tags**。

**`tags` 的负面约束**：
- **不要重复当前题自己的 `capability` 或 `domain`**；同一信息不应在三条轴上重复表达。
- **不要使用泛过程占位词**，如 `workflow`、`workflow_acceleration`、`workflow_closure`、`loop_oriented`、`plotting`、`structure_build`。这类词只说明“做事方式”或编写过程，不是稳定的主题/工具/方法 Facet。
- 优先写**主题 / 工具链 / 方法族**；若只是想表达“这是个多步流程题”，应由 `capability=workflow_orchestration` 表达，而不是再写 `workflow` tag。
- **受控词表 + 前缀语义**：合法 tag 为数十个粗粒度值，例如 `meta_userlog`、`wf_batch`、`abacus`、`vasp`、`phy_surface`、`chem_co2rr`、`mat_hea` 等（完整列表见 `question_tags.py`）。**不要在 tags 里堆材料实例名**（如单个化学式、Miller 指数），以免与 `capability`/`domain` 信息重复且难以维护。
- **命名风格**：仅使用词表内 `lower_snake_case` 字符串；材料名、化学式类 **legacy** 别名仍由 `schemas.CANONICAL_TAG_ALIASES` 拒绝并提示 canonical（归一化后进入上述词表）。

**与 `--slices` 的关系**：Runner 当前仅按 **`capability` + `domain`** 过滤；**需要稳定用 CLI 切分的维度**应落在二者之一（或专题 capability），不要**只**写在 `tags` 里（除非已实现 tags 筛选，见下文「运行筛选」）。

**正交性**：不要求数学意义上完全正交；若题干同时涉及多个对象、方法或软件，`domain` 仍只保留**最终业务线 / 应用场景**这一条主轴，其余信息进入 `tags`。

#### capability 枚举

与 `evaluation/core/schemas.py` 中 `CapabilityLiteral` **保持一致**（加载题库时按此校验）：

`structure_construction` / `structure_retrieval` / `scientific_analysis` / `workflow_orchestration` / `execution_contract` / `data_diagnosis` / `batch_processing` / `safety_refusal` / `input_generation`

**语义说明（撰写与筛选时）**

| 取值 | 含义 |
|------|------|
| `structure_construction` | 构建或修改结构（slab、界面、缺陷等），不强调「从数据库拉取」 |
| `structure_retrieval` | 从结构数据库检索、筛选、汇总元数据（如 `mat_struct_db` 路径） |
| `scientific_analysis` | 以科学数据、结构或计算结果为输入，产出数值、分类、拟合、精修、特征工程或后处理结果；不强调多步流程编排 |
| `workflow_orchestration` | 存在**明确多步流程组织**、工具/脚本/MCP 串联、或阶段性交付依赖的任务；若题目主要是在已有数据/文献上做比较、综述、筛选、机理解释或推荐，而不是考察流程编排本身，优先标 `scientific_analysis` |
| `execution_contract` | **执行与交付约定**（对应 `direct` 实验与 `matmaster/exps/`、系统提示中的硬性交付规则）：如 spec 与正文冲突以文件为准、归档解压到根目录、交付物精确命名等；**不是**领域科研 workflow，与 `workflow_orchestration` 区分 |
| `data_diagnosis` | 根据日志/输入/输出诊断问题并给修复建议 |
| `batch_processing` | 批量、扫描、多案例一致性与参数控制 |
| `safety_refusal` | 合规与安全拒答 |
| `input_generation` | 生成某类计算软件或工作流所需输入文件；软件后端（如 VASP、ABACUS）放在 `tags` 或 bank 语境中表达，而不再占用 capability |

#### domain 枚举

与 `evaluation/core/schemas.py` 中 `DomainLiteral` **保持一致**（共 **6** 个取值）：

`battery` / `catalysis` / `polymer` / `alloy` / `semiconductor` / `agnostic`

**语义说明**

- `domain` 仅表示**业务线 / 应用场景**（或明确的非业务线归类），用于 `--slices`、报告聚合、bank 分组与覆盖统计；与 `capability` 正交。
- `battery`：电池与储能业务线，如正负极、离子迁移、电解液、电池材料分析等。
- `catalysis`：催化与表界面反应业务线，如吸附、反应路径、CO2RR、HER 等。
- `polymer`：聚合物与软物质业务线。
- `alloy`：合金与金属材料业务线。
- `semiconductor`：半导体与电子材料业务线。
- `agnostic`：**无法稳定归入**上述五条业务线时的归类（如跨领域通用能力题、legacy 题库待复审题）；**active `question_bank/` 新题应优先**在五条业务线中选其一，仅在确实无单一业务主轴时使用本值。
- 材料对象、方法、软件、专题线不再进入五条业务线 `domain`；统一通过 `tags` 表达。
- 未纳入本轮业务线迁移的 bank 必须移出 `evaluation/question_bank/`，不能与 active business-line banks 混放。
- 对 `domain=agnostic` 的大卷，可在严格题级复审后拆出新的「仅五条业务线」bank；迁回时题必须同时满足单一 `capability` 与单一 **五条业务线之一**的 `domain`（未复审前可保留为 `agnostic`）。

#### 新题填写 checklist（capability / domain / tags）

新增或改写题目时建议按顺序自检：

1. **`capability`**：按**任务形态**选（建结构 / 查库 / 科学分析 / 批量 / 诊断 / 输入生成 / 编排 / **执行与交付约定** / 安全），与「测什么能力」一致；测 spec 优先级、归档解压、精确文件名等 **执行/交付契约** → `execution_contract`；不要仅因用了某软件就选 `workflow_orchestration`——若本质是输入生成，应用 `input_generation`。
2. **`domain`**：按题目的**最终业务目标 / 应用场景**选：五条业务线 **或** `agnostic`（见上）；不要再把物理轴、方法轴、软件轴写进 `domain`。
3. **粒度不够时**：可加 **`tags`**（如 `mlip`、`userlog`、`abacus`、`scxrd`）做人读与二次分析；**tags 不参与 `--slices`**。若该维度需要**命令行一切就切**，应先确认它是否真的是稳定业务线，否则不要硬塞进 `domain`。
4. **`workflow_orchestration` 只留真编排题**：必须存在明确的阶段流程、工具链串联、上一步输出作为下一步输入，或流程/交付 gate；若只是基于给定 bundle / 文献做比较、筛选、推荐、方法综述或机理分析，优先标 `scientific_analysis`。
5. **与 `evaluation/core/schemas.py` 一致**：`CapabilityLiteral` / `DomainLiteral` 未列出的取值会导致加载失败；需要新枚举时须同时改 **schemas + 本文档 + 相关 runner 假设**（若有）。

#### capability / domain / tags 如何区分、何时用哪个

| 维度 | 回答的问题 | 典型取值思路 | 谁消费 |
|------|------------|----------------|--------|
| **capability** | 这道题测**哪类任务能力**（建结构、查库、科学分析、批量、诊断、输入生成、编排、安全、执行契约等） | 与「任务形态」一致：能标 `input_generation` 或 `scientific_analysis` 就不要标成笼统的 `workflow_orchestration` | **`--slices`、聚合报告 `by_capability`**；加载时校验 |
| **domain** | 这道题最终属于**哪条业务线 / 应用场景**（五条业务线或 `agnostic`） | 与「切片想怎么分」对齐：稳定业务线优先；无法唯一归类时用 `agnostic` | **`--slices` 的 `cap[dom1,dom2]`**、按域聚合；加载时校验 |
| **tags** | **二级标签**：项目线、工具线、内部别名（如 `mlip`、`userlog`、`co2rr`） | 不承载「枚举完整性」；用于人读、检索、细粒度切片 | **`--slices` 中经 `@` 与 capability/domain 组合筛选**（见下节）；亦可用于自建报表 |

**选用顺序（简版）**：先定 **capability**（任务形态）→ 再定 **domain**（题材/主轴，且兼顾你以后想怎么写 `--slices`）→ 需要更细、但**不值得**加新 domain 时再加 **tags**。

#### 运行筛选：`--slices` / `include_slices`（capability / domain / tags）

实现见 `evaluation/core/runner.py` 的 `_question_matches_slice` 与 `evaluation/core/slice_parser.py`：每条 slice 匹配题目的 **`capability`**，可选 **`domain`** 列表，可选 **`tags`**（**AND**：题目需包含 slice 中列出的每一个 tag）。

- **CLI**：`--slices 'A B[a,b] C[d] WO@wf_batch'`。
  - **括号外空白**分隔多条 **slice**，多条之间为 **OR**（命中任一即保留该题）。
  - **`cap`** 单独出现：该 capability 下 **任意 domain**、**任意 tags** 均命中（仍受其它筛选约束）。
  - **`cap[a]`** 或 **`cap[a,b]`**：capability 相同 **且** `question.domain` 落在列表中（列表内为 **OR**）。
  - **`cap@t1`** 或 **`cap@t1,t2`** 或 **`cap[a,b]@t1,t2`**：每个 slice **至多一个** `@`；`@` 后为逗号分隔的 tag 列表，要求题目的 `tags` **同时包含**所列每一个（**AND**）。tag 名与题目中枚举一致（匹配时不区分大小写）；列表内禁止空白（与 `[]` 内域名相同）。
  - **`[]` 内禁止空白**，域名用逗号分隔，如 `[battery,catalysis]`，不能写成 `[battery, catalysis]`。
- **`evaluation/config.yaml`**：`include_slices: [{ capability: "…", domains: ["…"], tags: ["…"] }, …]`；省略 `domains` 表示该 capability **不限 domain**；省略 `tags` 表示**不限 tag**。

**其它按题 ID 子集**：仍可用 **`--questions` / `include_question_ids`** 或按 bank 文件加载。**需要频繁切的维度**仍建议优先落在 `domain` / `capability`；`tags` 适合二级维度或与 `@` 组合收窄。

#### 基于 `--slices` 的命名与调整建议

1. **先反推常用切片**：列出团队最常跑的命令，例如「只要电化学 workflow」「只要聚合物」「只要 ABACUS 输入」。把每条映射到 `capability` + `domain` 的组合是否**能一条 `--slices` 写出来**；若不能，优先考虑 **调整 domain**（或专题 capability），而不是加 tags。
2. **`workflow_orchestration` + 多主题**：该 capability 只应用于确实考察流程组织的题；保留在该类中的题仍应**务必用 `domain` 区分**（五条业务线之一，或确实无主轴时用 `agnostic`），否则只能 `workflow_orchestration` 全选或依赖题目 ID 列表。
3. **专题线不要直接占用 capability 或 domain**：如 `co2rr`、`userlog`、`mlip`、`scxrd`、`abacus` 等优先放入 `tags`；只有当它本身就是独立**任务形态**时，才应升级成 capability。
4. **tags 与 CLI**：可用 `--slices` 的 `@tag` 做 **capability/domain 之外的收窄**；仍以 capability/domain 为主轴，避免把本应属于 domain 的维度只写在 tags 里。
5. **新增枚举值**：若出现「必须同时按某维度筛，但该维度既不适合塞 domain 也不适合塞 capability」的重复需求，再评估 **新 domain** 或 **题库 tags 枚举扩展**，避免 capability 无限膨胀。

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
| `workspace_resolve` | 可选 | `None` | `None` 或省略 = `recursive`（默认）；`root` = `artifact_exists` / `text_file_*` 仅在 **workspace 根目录**按文件名解析（单段 basename，无路径分隔符），不递归子目录 |

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
| `struct_file_atom_count` | `{"filename": str, "expected": int, "tolerance": float, "element": str(可选)}` | 用 pymatgen 读结构文件验证原子数；未提供 `element` 时校验总原子数，提供时校验该元素计数 |
| `struct_file_formula` | `{"filename": str, "formula": str}` | 用 pymatgen 读结构文件验证化学式（reduced composition 比较） |
| `struct_file_bond_count` | `{"filename": str, "element_a": str, "element_b": str, "cutoff_A": float, "expected_count": int, "tolerance": float}` | 统计元素对间短于 cutoff 的键数 |
| `struct_file_bond_length` | `{"filename": str, "element_a": str, "element_b": str, "cutoff_A": float, "expected": float, "tolerance": float}` | 计算元素对间键长均值并校验 |
| `struct_file_bond_angle` | `{"filename": str, "triplet": [A, B, C], "expected_deg": float, "tolerance_deg": float, "cutoff_A": float}` | 计算 A-B-C 键角均值（B 为顶点）并校验 |
| `struct_file_cell_param` | `{"filename": str, "param": "a"\|"b"\|"c"\|"alpha"\|"beta"\|"gamma", "expected": float, "tolerance": float}` | 读晶格参数并校验 |
| `struct_file_stoichiometry_ratio` | `{"filename": str, "element_a": str, "element_b": str, "expected_ratio": float, "tolerance": float}` | 验证 count(A)/count(B) 比值 |
| `struct_file_coordination` | `{"filename": str, "center_element": str, "expected": int, "tolerance": float, "cutoff_A": float}` | 统计中心元素的配位数均值并校验 |
| `struct_file_layer_count` | `{"filename": str, "expected": int, "tolerance": float, "axis": str, "layer_tol_A": float, "element": str(可选)}` | 沿指定轴在笛卡尔坐标下统计**不同原子平面**数：排序后，与当前平面锚点距离超过 `layer_tol_A`（Å）则开始新平面；默认 `layer_tol_A` 为 `0.25`。提供 `element` 时仅统计该元素的分层（如仅统计 slab 金属层，不计溶剂/离子层）。旧字段 `gap_threshold_A` 仍可读，但语义为平面合并容差（与现实现一致），新题请写 `layer_tol_A` |
| `struct_file_count` | `{"pattern": str, "expected": int, "tolerance": int}` | 统计 workspace 中匹配 glob 的文件数（无需 pymatgen） |
| `struct_file_surface_termination` | `{"filename": str, "element": str, "axis": "x"\|"y"\|"z", "side": "top"\|"bottom"\|"both", "layer_tol_A": float}` | 检查 slab 最外层（top/bottom/both）是否由指定元素构成；用于验证 O-terminated 或其他特定终止面（如 CeO2(111) 的 O 终止）|
| `checkcif_no_a_alerts` | `{"filename": str, "max_a_alerts": int}` | 在 workspace 中找到匹配 `filename`（glob，默认 `*.cif`）的 CIF 文件，POST 到 IUCr checkCIF 服务（`https://checkcif.iucr.org/cgi-bin/checkcif_hkl.pl`），解析 HTML 响应中的 A/B/C/G 级别警告数，验证 A 级警告数 ≤ `max_a_alerts`（默认 0）。实现见 `evaluation/validators/checkcif.py`。|
| `text_file_contains_all` | `{"filename": str, "tokens": list[str], "flags": str, "case_sensitive": bool, "normalize_whitespace": bool}` | 读取 workspace 文本文件并检查 `tokens` 全部出现；可选 `flags: "i"`、大小写与空白归一化控制；若该条 ref 上设 `workspace_resolve: root`，则只读**根目录**下该文件名 |
| `text_file_regex` | `{"filename": str, "pattern": str, "flags": str}` | 读取 workspace 文本文件并做正则匹配（`flags` 支持 `i/m/s`）；若该条 ref 上设 `workspace_resolve: root`，则只读**根目录**下该文件名 |

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
| `capability` | ❌ | `--slices` 过滤（见下）；safety 路由；聚合 + 报告 |
| `domain` | ❌ | 聚合 + 报告 |
| `tags` | ❌ | 目前未被代码消费（预留） |
| `priority` | ❌ | `"P0"` 触发 P0 回归门控 |
| `reference_answers` | ❌ | Evaluator 的标准答案查找表 |
| `scoring_checklist` | ❌ | Evaluator 逐条执行判分 |

---

## 题库编写约定

### 1. ID 变更规则

修改 `evaluation/question_bank/**/*.yaml` 中任一题目的题干、期望答案、`reference_answers`、`scoring_checklist` 或其他会影响评测语义的内容时，**必须同时更新该题的顶层 `id`**。新 `id` 可用时间戳或其他唯一后缀；若只是纯格式化、注释、空白或不影响语义的整理，可不改 `id`。

仅调整 **`capability` / `domain` / `tags` 等分类元数据**、且**不改变**题干、期望产物、判分逻辑、`reference_answers`、`scoring_checklist` 或执行预算时，视为**切片/聚合口径整理**而非单题评测语义变更，**可不改 `id`**。若该分类改动会连带改变题目的筛选集合、对外题号约定或数据目录命名，再单独评估是否需要 bump。

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

- 自迭代时「产品侧」可写资产以 `config/`、`matmaster/exps/`、`matmaster/skills/`、`matmaster/tools/`、`matmaster/adaptors/calculation/`、`matmaster/devshell/` 等为准；`matmaster/core/` 仅在框架层缺陷明确时再动。`matmaster/cache/` 下 JSON 视为生成物，若改动影响 MCP schema / lazy tool 可见性，应执行 `uv run python -m matmaster.tools.cache_mcp_schemas --config-dir config` 再生成，而不是长期手改。默认不优先修改 `src/`、`app.py` 等 API / Worker 路径，除非失败与该链路明确相关。本仓库已移除历史 `playground/mat_master/` 目录树（与 EvoMaster 上游示例 `playground/` 不是同一概念）。
- **内置工具 `prompt()`（Claude Agent SDK 编排）**：DevShell **optimization 子 Agent** 的系统提示在 `evaluation/devshell_agent/loop_prompts.py`（`SYSTEM_PROMPT_OPTIMIZATION` 中 ``matmaster/tools/`` 小节）；主 Agent 在同文件 `SYSTEM_PROMPT_MAIN` 中亦有委派约束。原则：`matmaster/tools/builtin/.../tool.py` 的 `prompt()` 只保留流程说明与跨技能硬约束；各软件镜像/命令以 `matmaster/skills/<name>/SKILL.md` 为准，**禁止**为提分在工具 `prompt()` 贴与技能重复的默认表。引导行为优先改技能、题目 seed 或评测 fixture。
- DevShell / IDE 流程：`evaluation/docs/devshell/devshell_claude_code_eval.md`（`run_devshell_eval.py` + `score_devshell_tasks.py` 自动评分）。
- **程序化**多轮「跑题 → 判分 → 分流优化」：`evaluation/docs/devshell/devshell_agent_sdk_loop.md`；入口 `evaluation/scripts/devshell/run_devshell_agent_loop.py`，可选依赖 `uv sync --extra eval-agent`（`pyproject.toml` 中 `[project.optional-dependencies] eval-agent`）。该入口默认 **`--model bedrock-claude-opus`**（内层 `mm-devshell` 对应 `config/llm_config.yaml` 路由 `bedrock-claude-opus` → `opus_bedrock`）；若需走 LiteLLM 的 Opus，显式传 **`--model claude-opus-4-6`**。默认在 **`--eval-ingest-pending-only`** 下每轮结束后自动 `score_devshell_tasks.py --submit` 上报 ingest（见该文档）；`--no-eval-ingest-submit-each-iteration` 可关。**三 Agent**：主 Agent 只负责 Drive、读取脱敏摘要并显式委派，禁止编辑文件；**仅允许**通过 MCP `main_read_text` / `main_glob_paths` / `main_grep_text` 只读整棵 ``evaluation/devshell_agent_history/``（含各次 run 子目录与 ``index.jsonl``），**禁止**读取 `evaluation/**` 其余路径；Checklist Agent 可只读 `evaluation/question_bank/`、`evaluation/core/` 等，由 `escalate_checklist_revision` 触发，**写入仅限**会话目录下 `proposed_question_bank_changes.md`（proposal，不自动 git commit）；优化 Agent 仅处理产品侧目录，由 `delegate_optimization` 触发，禁止读取 `evaluation/**`（会话目录除外）。Checklist Agent 与优化 Agent 均应通过编排器提供的**受限 MCP 文件工具**读写，不再依赖内建 `Read/Edit/Write/Bash`。若 checklist follow-up 造成题目 `id` 集合变化，应立即停止外层循环。跨轮摘要持久化到 `evaluation/devshell_agent_history/`，不受 `results/` 清理影响。无人值守运行时默认 **`--permission-mode bypassPermissions`**（Claude Agent SDK），避免子会话中 Bash（如 `git`）因需人工批准而失败；交互式可改用 `acceptEdits`。

---

## P0 回归门控（P0 Gate）

### 概述

P0 题目是被标记为最高优先级的评测题。在 DevShell Agent 多轮迭代循环中，每轮评测会**先跑 P0 题目**，评分后与上一轮的 P0 分数做对比。若 P0 宏平均分下降，则：

1. **跳过**当前轮剩余的非 P0 题目（节省时间和费用）
2. 编排器在随后启动 **optimization 专责子回合**，由子 Agent 调用受控 MCP 工具 **git_revert_commits_after_base**，以 **上一轮迭代开局时的 ``HEAD``**（与 ``last_p0_scores`` 更新时所用代码快照一致）为 ``base_sha``，对 ``base_sha..HEAD`` 上的提交按从新到旧执行 **``git revert --no-edit``**（**不使用** ``git reset``），以撤销**上一轮** optimization auto-commit 等在基线之后累积的提交（**不是**「本轮迭代开局 HEAD」：若本轮评测前尚未产生新 commit，旧逻辑会出现无可 revert 的空区间）。
3. 在 `outcomes` 中标记 `p0_regression: true`，视为**优化失败**
4. 外层循环 **continue** 进入下一轮

### 标记方式

在题目 YAML 中设置 `priority: P0`：

```yaml
- id: WO_elec_001_20260404
  capability: workflow_orchestration
  domain: battery
  priority: P0        # ← P0 回归门控题目
  tags:
    - band_structure
```

评测基础设施在运行时从题库扫描所有 `priority == "P0"` 的题目（`collect_p0_question_ids`），无需在配置文件中维护 ID 列表。

**`execution_contract` 契约 P0 集**：`question_bank/execution_contract/ec_agnostic.yaml` 中三道题均标记为 `priority: P0`，用于 DevShell 等流程中**优先运行**并与历史分数比对，锁住 direct 交付契约（spec 与正文冲突以文件为准、归档解压到根目录、交付物精确文件名）。其中 **spec 冲突题**的得分仅来自确定性项（`artifact_exists`、`text_file_regex`），**不包含** `llm_binary_judge`，避免 P0 门控被裁判方差放大。

### 执行流程

1. `run_devshell_eval` MCP 工具通过 `collect_p0_question_ids` 扫描题库中 `priority == "P0"` 的题目，若存在则进入两阶段模式：
   - **Phase 1（P0 gate）**：仅跑 P0 题目 → 评分 → 与 `last_p0_scores` 对比
   - **Phase 2（remaining）**：仅跑非 P0 题目（`--exclude-question-ids`）→ 评分
2. 合并两阶段结果，返回包含 `p0_gate_passed` / `p0_gate_failed` 的摘要
3. `AgentLoopSharedState.last_p0_scores` 仅在 P0 gate 通过时更新
4. 两阶段共用同一 ingest `run_id`：编排器为整轮生成一个 UUID，经 `--eval-ingest-run-id` 传给 Phase 1 与 Phase 2 的两次 `run_devshell_eval.py`，使 `pending_ingest` 与 manifest 在写入时即一致，tools-server 按 `run_id` 聚合为**一轮**评测（不再出现「P0 一拨、remaining 一拨」两个 `run_id`）。

### CLI 新增参数

- `run_devshell_eval.py --exclude-question-ids ID1 ID2 ...` — 从 run plan 中排除指定题目
- `run_devshell_eval.py --eval-ingest-run-id <UUID>` — 固定本次 run 的 ingest `run_id`（默认每进程随机 UUID）；P0 gate 编排器自动注入，一般无需手写

### 与现有流程的兼容

- 题库中无 `priority: P0` 题目时，行为与之前完全一致（单阶段执行）
- 第一轮迭代（无历史 P0 分数）P0 gate 始终通过

---

## 运行入口

```bash
# 指定切片（OR）或题目 ID 运行；切片语法：cap cap[dom] cap[d1,d2]（括号外空格分隔）
uv run python -m evaluation.cli \
  --eval-config evaluation/config.yaml \
  --slices 'batch_processing workflow_orchestration[polymer]' \
  --questions DF_mech_001 WO_mech_001

# 后台运行（Linux）
evaluation/scripts/matter_cli/run_mat_master_eval_bg.sh start

# 后台运行（Windows）
evaluation/scripts/matter_cli/run_matmaster_evaluation_bg.ps1
```

详细说明见 [`evaluation/README_CN.md`](README_CN.md)。

---

## 附录：若未来重构 taxonomy，三者如何更清晰、`--slices` 如何演进（规划）

本节是**架构规划**，不是当前运行时的强制行为；落地时需同步改 `evaluation/core/schemas.py`、`slice_parser.py`、`runner._apply_filters`、CLI 与 DevShell 传参。

### 当前模型的典型痛点

- **`domain` 混用两种语义**：既有物理子域（`elec`、`mech`），也有方法/产物主轴（`incar`、`scxrd`），「正交」感弱。
- **`workflow_orchestration` 的边界仍需维护**：它现在应只覆盖真编排题；若后续又开始吸收综述、推荐、比较类题目，应优先把这些题归回 `scientific_analysis`，而不是继续放大 workflow 桶。
- **`tags` 不参与筛选**：与「常用维度」重叠时，作者会被迫把本该可切的维度塞进 `domain`，或只能枚举 `--questions`。

重构的目标应是：**每个字段只回答一类问题**，且 **runner 能按团队最常用的组合做过滤**，而不只靠题目 ID。

### 推荐的重构分层（可渐进）

下面三列是**目标语义**（可与现有字段名映射或替换，不必一次改名全库）。

| 目标轴 | 回答的唯一问题 | 与现状的对应思路 |
|--------|----------------|------------------|
| **任务类**（Task） | Agent 在做什么「类」的事：建结构、检索、批量扫描、诊断、写输入、多步编排、合规拒答、垂直专题等 | 对应并**收紧** `capability`；过宽的 `workflow_orchestration` 可拆成子类（如 `workflow_mcp`、`workflow_bash_pipeline`）或**强制**用第二轴表达 |
| **题材类**（Subject） | 材料/过程属于哪条科学线：结构/电子/力学/热/动力学/聚合物等 | 对应 **`domain` 中纯「物理/材料」部分**；`incar`/`scxrd` 若保留，建议**迁出**为第三轴，避免与 Subject 混枚举 |
| **主题/产品线**（Theme / Facet） | 项目线、工具链、内部专题：MLIP、CO₂RR、userlog、某客户包等 | 对应 **`tags` 或升级为结构化 `facets`**（如 `toolchain:mlip`）；**可用 `--slices` 的 `@tag` 参与过滤**（见上） |

可选 **第四类**（若 Subject 与「方法/数据形态」仍打架）：单独 **`method` 或 `artifact`**（`xrd` / `incar` / `md_trajectory`），与 Subject 正交；这样 `--slices` 可以 `task × subject × method` 组合而不膨胀单一枚举。

### 重构后 `--slices` 的调整方向

**原则**：切片语法表达的是 **AND/OR 组合过滤**，且默认与报表维度一致（避免「文档里叫 domain、CLI 里叫别的」）。

1. **最小演进（兼容优先）**
   - 保留现有 `cap` 与 `cap[dom1,dom2]` 语义不变。
   - **已实现**：同一 slice 内用 **`@tag1,tag2`**（**一个 `@`**）要求题目 **tags 全包含**（AND），与 capability/domain 组合；多条 slice 之间仍为 **OR**。若将来需要「全局 tags_any / tags_all 与 slices AND」可再增加独立 CLI 参数。
   - 题库可逐步补全 tags，无需立刻拆 domain。

2. **中等演进（表达式升级）**
   - 引入 **显式键名**，避免 `cap[dom]` 隐式二元：例如
     `--filter 'task:workflow_orchestration subject:elec' --filter 'task:input_generation'`
     多条 `--filter` 之间 **OR**；单条内多键 **AND**。
   - 或 **版本化**：`--slices-v2 '...'`，旧 `--slices` 长期别名到 v1。

3. **配置驱动（复杂组合）**
   - `eval_config.yaml` 中 `include_slices` 扩展为结构化对象，例如：
     `{ "all_of": [{ "capability": "...", "domains": [...] }, { "tags_any": ["mlip"] }] }`
   - 适合 CI/DevShell，CLI 只传配置文件路径。

**不推荐**：无版本、无迁移期的「直接改 `cap[dom]` 含义」——会破坏历史报告与 ingest 对比。

### 迁移与兼容（若动真格）

1. **双写期**：题目同时保留旧 `capability`/`domain` 与新的 `task`/`subject`（或映射表在加载时填充）。
2. **别名层**：`workflow_orchestration` → 映射到新的 task 枚举中的若干值，聚合报表可合并展示。
3. **切片别名**：旧字符串 `workflow_orchestration[elec]` 解析为新过滤器，保证既有脚本可跑。
4. **文档与 schema 同步**：任何新轴进入 `QuestionItem` 都必须进 `schemas.py` 与本文档。

### 小结

- **更清晰**：三轴各司其职——**任务类 / 题材类 / 主题或方法 facet**；混在一起的 `incar`、`scxrd` 宜迁到「方法/产物」轴或 facets。
- **`--slices` 调整**：优先 **加 tags 过滤或结构化 filter**（AND/OR 明确），而不是仅改字符串语法；大改时用 **v2 + 兼容层**。
- **落地顺序**：先 **tags 参与 runner 过滤**（收益/成本比最高）→ 再视报表需求拆 task/domain 枚举或加 `method` 轴。
