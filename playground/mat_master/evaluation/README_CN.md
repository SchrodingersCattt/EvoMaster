# MATTER Evaluation v5

MATTER 是 `playground/mat_master/evaluation/` 下的独立评测模块，当前仅维护 **v5** 题库与运行链路。

## 当前题库结构

- `question_bank/manifest.yaml`: v5 题库注册表。
- `question_bank/<capability>/*.yaml`: 实际题库文件。
- `question_bank/data/<question_id>/`: 每道带本地输入的题目对应一个数据目录，目录名使用当前 v5 题号。

当前 capability 列表：

- `batch_processing`
- `data_diagnosis`
- `property_prediction`
- `structure_construction`
- `workflow_orchestration`
- `safety_refusal`

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
- `evaluator.py`: 逐题执行二元判分。
- `aggregator.py`: 聚合 pass/fail 统计。
- `reporter.py`: 生成 `raw_runs.jsonl`、汇总 JSON 和 Markdown 报告。
