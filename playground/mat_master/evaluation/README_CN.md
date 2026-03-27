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

## 主要模块

- `runner.py`: 加载题库、下发任务、汇总结果。
- `simulator.py`: 从题目生成模拟用户任务。
- `evaluator.py`: 逐题执行二元判分。
- `aggregator.py`: 聚合 pass/fail 统计。
- `reporter.py`: 生成 `raw_runs.jsonl`、汇总 JSON 和 Markdown 报告。
