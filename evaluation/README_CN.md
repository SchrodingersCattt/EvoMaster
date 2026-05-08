# MATTER Evaluation v5+ (Weighted)

MATTER 是 `evaluation/` 下的独立评测模块，当前仅维护 **v5+** 题库与运行链路。

**目录约定（仓库根下 `evaluation/`）**

| 路径 | 内容 |
|------|------|
| `core/` | Python 实现：题库模型、runner、判分、报告等 |
| `scripts/devshell/` | DevShell / mm-devshell 批量跑题、`score_devshell_tasks`、`export_devshell_review_bundle` |
| `scripts/baseline/` | 外部 baseline（CC/Cursor/Codex 等）：`finalize_external_baseline_ingest`、`run_claude_cli_baseline_tasks`（仅 `claude` CLI） |
| `scripts/matter_cli/` | **Core 评测**（`evaluation.core` + Playground `run_mat_task`）：后台跑 `python -m evaluation`、Windows 启动脚本、run 目录监控 |
| `scripts/eval_ingest_submit_pending.py` | 共用：pending 入库（baseline / devshell 判分后上报） |
| `docs/baseline/` | 外部 baseline 流程说明（含 Claude Code / Cursor / Codex 两阶段话术） |
| `docs/devshell/` | DevShell 批量 + `score_devshell_tasks.py` 自动评分话术 |
| `question_bank/` | v5+ 题库与 `data/` 输入文件 |
| `config.yaml` | 默认评测配置 |
| `cli.py` / `__main__.py` | 同上：命令行入口，转发到 `core.cli`（与 `matter_cli` 里后台命令是同一套评测） |

v5+ 引入了 **显式权重机制** 和 **运行时解耦**，使评测更灵活、更可移植。

## 覆盖率口径

提示词 / 工具 / Skill 覆盖率脚本位于 `evaluation/scripts/coverage/`。
`extract_and_match.py` 输出的 `coverage_report.json` 同时包含：

- **Raw coverage**：所有抽取规则都计入分母。
- **Actionable coverage**：仅统计 `actionability: testable` 的规则，用于安排补题优先级。

规则分类可在 `evaluation/scripts/coverage/rule_scope_overrides.yaml` 调整。默认分类包括
`testable`、`policy_only`、`tool_schema`、`runtime_dependent`、`out_of_scope`。
补题时应优先看 `summary.actionable_pct` 与 actionable critical gaps，避免用关键词匹配代替真实 checklist 覆盖。

## 当前题库结构

- `question_bank/manifest.yaml`: v5+ 题库注册表。
- `question_bank/<capability>/<xx>_<domain>.yaml`: 实际题库文件（**每个文件对应唯一的 `(capability, domain)`**；`<xx>` 为两字母 capability 简写，定义见 `tests/evaluation/capability_abbrev.py`）。
- `question_bank/data/<question_id>/`: 每道带本地输入的题目对应一个数据目录，目录名使用当前 v5 题号。

当前 capability 列表：

- `batch_processing`
- `data_diagnosis`
- `execution_contract`（执行与交付约定：spec 与正文冲突以文件为准、根目录交付、归档解压、精确文件名等，对应 `matmaster/exps/` 与 `execution_contract/ec_agnostic.yaml`）
- `input_generation`（输入生成任务；VASP/ABACUS 等软件后端放在题目 tags 或 bank 语境中表达）
- `scientific_analysis`
- `structure_construction`
- `structure_retrieval`
- `workflow_orchestration`
- `safety_refusal`

说明：

- CO₂RR 等专题题与其它同 `(capability, domain)` 题**合并为同一 YAML**（例如 `structure_construction/sc_catalysis.yaml`），专题属性由 `tags` 表达。
- `workflow_orchestration` 现仅用于**明确多步流程组织**、工具链串联、阶段 gate 或上一步输出驱动下一步的任务；基于给定 bundle / 文献做比较、筛选、综述、推荐、机理解释的题目应优先归到 `scientific_analysis`。

当前 `domain` 语义：

- `domain` 仅表示业务线 / 应用场景，不再表示物理子域或方法轴。
- `domain` 枚举含五条业务线，以及无法归入时的 `agnostic`（见 `evaluation/AGENTS_evaluation.md`）。
- 材料对象、方法、软件、专题线统一放入 `tags`。

## 加权评分 (v5+ 新增)

### 核心概念

- **每条 checklist 项**：LLM/确定性校验仍产出二分判定 (pass/fail)
- **每条项可选权重**：默认 1.0，可在题库中单独指定
- **轴得分**：`axis_score = Σ(pass_i × weight_i) / Σ(weight_i)`
- **总分**：`overall = Σ(axis_w × axis_score) / Σ(active_axis_w)`

### 调整权重

#### 全局轴权重 (推荐)

编辑 `config.yaml`:
```yaml
axis_weights:
  correctness: 2.0      # 正确性 2 倍重要
  grounding: 1.0        # 工具/方法合理性
  efficiency: 0.5       # 效率 0.5 倍重要
```

**效果**: 一个慢但正确的方案总分会更低（因为 efficiency 权重小）。

#### 单条标准权重 (按需)

在题库 YAML 中，可给单条 checklist 加权：
```yaml
scoring_checklist:
  - id: "critical"
    criterion: "..."
    axis: "correctness"
    verify: "..."
    weight: 2.0  # 这条在 correctness 轴内计重

  - id: "minor"
    criterion: "..."
    axis: "correctness"
    verify: "..."
    # weight 默认 1.0
```

### 输出

评分结果同时包含：
- **raw 统计**：`pass_rate = passed_count / total_count`（向后兼容）
- **加权统计**：`weighted_pass_rate = avg(overall_weighted_score)`

## 运行时解耦 (v5+ 新增)

### 运行时兼容注入

```
evaluation/core/
├── evidence.py            （通用 EvidenceBundle 定义，默认不依赖任何 runtime mapping）
├── evidence_mapping.yaml  （当前 EvoMaster 兼容映射，由 runner 显式注入）
├── evaluator.py           （二元评分核心，不依赖工具名）
├── aggregator.py          （加权聚合）
└── ...
```

**特点**：
- 核心评测逻辑（evaluator/aggregator）不再硬编码工具名
- `EvidenceExtractor` 默认不加载任何 tool-name mapping
- 当前 EvoMaster 兼容映射由 `core/runner.py` 显式注入 `evidence_mapping.yaml`
- LLM judge context 包含工具描述、参数、观察，而非仅工具名

### 配置自定义映射

如需使用不同的 evidence 映射（如自定义运行时）：
```python
from evaluation.core.evidence import EvidenceExtractor

extractor = EvidenceExtractor(
    mapping_path="/path/to/custom/evidence_mapping.yaml"
)
bundle = extractor.extract(trajectory_json_path)
```

## 运行入口

CLI:

```bash
uv run python -m evaluation.cli \
  --eval-config evaluation/config.yaml \
  --slices 'batch_processing workflow_orchestration[polymer]' \
  --questions DF_mech_001 WO_mech_001
```

常用参数：

- `--slices`: OR-of-slices，语法 `cap`、`cap[dom]`、`cap[d1,d2]`、`cap@tag`、`cap[dom]@t1,t2`（**每个 slice 只有一个 `@`**；**括号外**空格分隔 slice，`[]` 与 `@` 后列表内禁止空格，逗号分隔；`@` 后多 tag 为 AND；与 `evaluation/config.yaml` 中 `include_slices` 一致）。
- `--questions`: 按 v5 question id 过滤题目。
- `--k`: 每题重复次数。

评测 Runner 固定以 **direct** 任务模式执行（不再提供 `--modes` 或题内 `mode_scope`）。

与 DevShell / baseline **不同**：该路径会跑 **BinaryEvaluator** 与 Playground **`run_mat_task`**（见 `core/runner.py` + `core/mat_runner.py`）。长时间或无人值守时可选用：

```bash
evaluation/scripts/matter_cli/run_mat_master_eval_bg.sh start
# Windows：evaluation/scripts/matter_cli/run_matmaster_evaluation_bg.ps1
# 看产物：evaluation/scripts/matter_cli/monitor_matmaster_evaluation.ps1 -RunDir <run_dir>
```

## 约定

- 仅支持 `version: "v5"` 的题库 YAML。
- `data_files.path` 必须指向 `question_bank/data/<v5_question_id>/...`。
- 顶层旧版 `level*.yaml` / `safety_refusal.yaml` 已移除，不再提供兼容加载。
- 评分标准统一使用 `axis` + `llm_binary_judge` / 确定性校验，不再保留旧版 `dimension` / `llm_judge_*` 写法。

### Batch Processing 题库说明

`batch_processing` capability 用于考察 **严格变量控制** 能力，而非简单的批量执行。当前共 3 道题，覆盖以下场景：

- **BP_struct_003**: 收敛测试，ENCUT 参数扫描，其他参数（k-mesh、ISMEAR、SIGMA）完全冻结，验证系统参数扫描能力。
- **BP_struct_004**: 批量后处理一致性，3 种材料使用相同分析参数（k-path、能量窗口、费米面参考点），输出格式统一。
- **BP_struct_005**: 批量失败恢复，5 个结构因几何问题失败后，仅修复几何参数，计算设置（k-mesh、ISMEAR、SIGMA、ENCUT）全程冻结。

**关键特点**：
- 不是考察「能否执行多个任务」，而是「能否在批量操作中精确控制哪个参数变、哪些参数不变」。
- 新增 verifier 类型：`batch_single_variable_sweep`、`batch_tool_args_constant`、`batch_consistent_calls`，支持精细的参数一致性检查。
- 每题都包含「控制变量合同」（sweep_variable vs locked_variables），agent 输出应明确表达这些信息。

## 主要模块（均在 `core/`）

- `runner.py`: 加载题库、下发任务、汇总结果。
- `simulator.py`: 从题目生成模拟用户任务。
- `evaluator.py`: 逐题执行二元判分 + 加权计算。
- `aggregator.py`: 聚合 pass/fail 统计 + 加权统计。
- `reporter.py`: 生成 `raw_runs.jsonl`、汇总 JSON 和 Markdown 报告。
- `evidence.py`: 通用 EvidenceBundle 定义，适配器无关。
- `evidence_mapping.yaml`: 当前 EvoMaster 兼容映射文件，仅由 runner 注入。

## 更多文档

- [baseline_cc_eval.md](./docs/baseline/baseline_cc_eval.md) - Claude Code baseline（与 DevShell 产物对齐）
- [devshell_claude_code_eval.md](./docs/devshell/devshell_claude_code_eval.md) - DevShell 批量跑题 + `score_devshell_tasks.py` 自动评分（`evaluation/scripts/devshell/run_devshell_eval.py`、产物路径、百分制话术）
- [WEIGHTED_PORTABLE_EVAL_MIGRATION.md](../../docs/mat_master/WEIGHTED_PORTABLE_EVAL_MIGRATION.md) - 迁移指南与 FAQ
- [WEIGHTED_EVAL_IMPLEMENTATION.md](../../docs/mat_master/WEIGHTED_EVAL_IMPLEMENTATION.md) - 实现细节
