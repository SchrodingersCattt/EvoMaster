# MATTER Evaluation v5+ (Weighted)

MATTER 是 `playground/mat_master/evaluation/` 下的独立评测模块，当前仅维护 **v5+** 题库与运行链路。

v5+ 引入了 **显式权重机制** 和 **运行时解耦**，使评测更灵活、更可移植。

## 当前题库结构

- `question_bank/manifest.yaml`: v5+ 题库注册表。
- `question_bank/<capability>/*.yaml`: 实际题库文件。
- `question_bank/data/<question_id>/`: 每道带本地输入的题目对应一个数据目录，目录名使用当前 v5 题号。

当前 capability 列表：

- `batch_processing`
- `data_diagnosis`
- `property_prediction`
- `structure_construction`
- `workflow_orchestration`
- `safety_refusal`

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
evaluation/
├── evidence.py         （通用 EvidenceBundle 定义，默认不依赖任何 runtime mapping）
├── evidence_mapping.yaml  （当前 EvoMaster 兼容映射，由 runner 显式注入）
├── evaluator.py        （二元评分核心，不依赖工具名）
├── aggregator.py       （加权聚合）
└── ...
```

**特点**：
- 核心评测逻辑（evaluator/aggregator）不再硬编码工具名
- `EvidenceExtractor` 默认不加载任何 tool-name mapping
- 当前 EvoMaster 兼容映射由 `runner.py` 显式注入 `evidence_mapping.yaml`
- LLM judge context 包含工具描述、参数、观察，而非仅工具名

### 配置自定义映射

如需使用不同的 evidence 映射（如自定义运行时）：
```python
from playground.mat_master.evaluation.evidence import EvidenceExtractor

extractor = EvidenceExtractor(
    mapping_path="/path/to/custom/evidence_mapping.yaml"
)
bundle = extractor.extract(trajectory_json_path)
```

## 运行入口

CLI:

```bash
uv run python -m playground.mat_master.evaluation.cli \
  --eval-config playground/mat_master/evaluation/config.yaml \
  --capabilities batch_processing workflow_orchestration \
  --questions DF_mech_001 WO_mech_001
```

常用参数：

- `--capabilities`: 按 capability 过滤题目。
- `--questions`: 按 v5 question id 过滤题目。
- `--modes`: 选择 `direct` / `planner`。
- `--k`: 每题重复次数。

## 约定

- 仅支持 `version: "v5"` 的题库 YAML。
- `data_files.path` 必须指向 `question_bank/data/<v5_question_id>/...`。
- 顶层旧版 `level*.yaml` / `safety_refusal.yaml` 已移除，不再提供兼容加载。
- 评分标准统一使用 `axis` + `llm_binary_judge` / 确定性校验，不再保留旧版 `dimension` / `llm_judge_*` 写法。

### Batch Processing 题库说明

`batch_processing` capability 用于考察 **严格变量控制** 能力，而非简单的批量执行。当前共 5 道题，覆盖以下场景：

- **BP_struct_001**: 同一 MCP 工具的批量调用，仅一个几何参数（真空厚度）变化，其他参数（Miller 指标、层数等）冻结。
- **BP_struct_002**: 多结构批量输入生成，统一 k-point 密度（50 points/Ų）和电子学参数（ISMEAR、SIGMA），尽管结构尺度不同导致 k 点网格数目不同。
- **BP_struct_003**: 收敛测试，ENCUT 参数扫描，其他参数（k-mesh、ISMEAR、SIGMA）完全冻结，验证系统参数扫描能力。
- **BP_struct_004**: 批量后处理一致性，3 种材料使用相同分析参数（k-path、能量窗口、费米面参考点），输出格式统一。
- **BP_struct_005**: 批量失败恢复，5 个结构因几何问题失败后，仅修复几何参数，计算设置（k-mesh、ISMEAR、SIGMA、ENCUT）全程冻结。

**关键特点**：
- 不是考察「能否执行多个任务」，而是「能否在批量操作中精确控制哪个参数变、哪些参数不变」。
- 新增 verifier 类型：`batch_single_variable_sweep`、`batch_tool_args_constant`、`batch_consistent_calls`，支持精细的参数一致性检查。
- 每题都包含「控制变量合同」（sweep_variable vs locked_variables），agent 输出应明确表达这些信息。

## 主要模块

- `runner.py`: 加载题库、下发任务、汇总结果。
- `simulator.py`: 从题目生成模拟用户任务。
- `evaluator.py`: 逐题执行二元判分 + 加权计算。
- `aggregator.py`: 聚合 pass/fail 统计 + 加权统计。
- `reporter.py`: 生成 `raw_runs.jsonl`、汇总 JSON 和 Markdown 报告。
- `evidence.py`: 通用 EvidenceBundle 定义，适配器无关。
- `evidence_mapping.yaml`: 当前 EvoMaster 兼容映射文件，仅由 runner 注入，不是 core 默认依赖。

## 更多文档

- [devshell_claude_code_eval.md](./devshell_claude_code_eval.md) - DevShell 批量跑题 + Claude Code 人工判分（`run_devshell_eval.py`、产物路径、百分制话术）
- [WEIGHTED_PORTABLE_EVAL_MIGRATION.md](../../docs/mat_master/WEIGHTED_PORTABLE_EVAL_MIGRATION.md) - 迁移指南与 FAQ
- [WEIGHTED_EVAL_IMPLEMENTATION.md](../../docs/mat_master/WEIGHTED_EVAL_IMPLEMENTATION.md) - 实现细节
