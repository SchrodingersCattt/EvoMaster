# MATTER Evaluation 模块

这是一个**独立**的 Mat Master 评测模块，目录完全位于：

- `playground/mat_master/evaluation/`

模块包含：

- 分层题库：`question_bank/level1.yaml` ~ `level4.yaml` + `safety_refusal.yaml`
- 评分模型：`schemas.py`、`evaluator.py`
- Human simulator：`simulator.py`（默认单轮，预留多轮扩展）
- Mat 调用与答案抽取：`mat_runner.py`
- 批量执行与统计：`runner.py`、`aggregator.py`、`reporter.py`
- CLI：`cli.py`
- 独立配置：`config.yaml`

## 运行方式

最简运行（全部走默认值）：

```bash
python -m playground.mat_master.evaluation.cli
```

显式指定配置（推荐）：

```bash
python -m playground.mat_master.evaluation.cli \
  --eval-config playground/mat_master/evaluation/config.yaml \
  --k 2 \
  --modes direct planner
```

可选覆盖 Mat Master 主配置路径：

```bash
python -m playground.mat_master.evaluation.cli \
  --eval-config playground/mat_master/evaluation/config.yaml \
  --mat-config configs/mat_master/config.yaml
```

中途单独打分（对当前已完成题目生成 interim rating）：

```bash
python -m playground.mat_master.evaluation.cli \
  --rate-only \
  --run-dir runs/mat_master_eval/<run_label>_<timestamp>
```

## CLI 参数说明

- `--eval-config`
  - 含义：MATTER 评测模块配置文件路径（YAML）。
  - 默认：`playground/mat_master/evaluation/config.yaml`
  - 行为：先加载该文件作为本次运行的基础配置。

- `--mat-config`
  - 含义：临时覆盖 `eval-config` 里的 `mat_config_path`（Mat Master 主配置路径）。
  - 默认：不传（`None`）。
  - 行为：不传时使用 `eval-config` 中的 `mat_config_path`；传了则以命令行为准，仅对本次运行生效。

- `--k`
  - 含义：每道题重复评测次数。
  - 默认：不传时优先使用 `eval-config` 的 `k`；若 `eval-config` 里也缺失，则代码默认是 `1`（`EvalConfig.k`）。
  - 行为：覆盖配置中的 `k`。

- `--modes`
  - 含义：评测模式列表，可选 `direct` / `planner`。
  - 默认：不传时优先使用 `eval-config` 的 `modes`；若 `eval-config` 里也缺失，则代码默认是 `["direct", "planner"]`（`EvalConfig.modes`）。
  - 行为：覆盖配置中的 `modes`；最终实际执行为 `modes` 与题目 `mode_scope` 的交集。

- `--output-dir`
  - 含义：输出根目录。
  - 默认：不传（使用 `eval-config` 的 `output_dir`，默认 `runs/mat_master_eval`）。
  - 行为：覆盖配置中的输出目录。

- `--run-label`
  - 含义：本次评测运行标签。
  - 默认：不传（使用 `eval-config` 的 `run_label`，默认 `matter_eval`）。
  - 行为：最终 run 目录名为 `<run_label>_<timestamp>`。

- `--question-bank-dir`
  - 含义：题库目录路径。
  - 默认：不传（使用 `eval-config` 的 `question_bank_dir`）。
  - 行为：覆盖配置中的题库目录。

- `--use-seed-prompt`
  - 含义：强制使用题库里的 `human_prompt_seed`，不走 simulator 改写。
  - 默认：不传（使用 `eval-config` 中的 `use_seed_prompt`）。
  - 行为：传入后会把 `use_seed_prompt` 强制设为 `true`。

- `--rate-only`
  - 含义：不执行答题流程，只基于已有 `raw_runs.jsonl` 单独生成 rating 报告。
  - 默认：不传（`False`）。
  - 行为：传入后走“独立打分模式”，会输出带前缀的 interim 报告文件。

- `--run-dir`
  - 含义：已有评测运行目录（独立打分模式使用）。
  - 默认：不传（`None`）。
  - 行为：`--rate-only` 时若不传 `--raw-runs`，则从 `<run-dir>/raw_runs.jsonl` 读取。

- `--raw-runs`
  - 含义：直接指定 `raw_runs.jsonl` 路径（独立打分模式使用）。
  - 默认：不传（`None`）。
  - 行为：优先级高于 `--run-dir`。

- `--rating-prefix`
  - 含义：独立打分输出文件前缀。
  - 默认：`interim_`
  - 行为：例如输出 `interim_scores_by_question.json`、`interim_final_report.md`。

## 默认执行行为

- 读取 `eval-config`，再按命令行参数做覆盖（override）。
- 加载题库目录下全部 `*.yaml`（L1-L4 + Safety）。
- 扩展运行计划：`题目 × mode × k`（受题目 `mode_scope` 约束）。
- 每个计划项执行：`simulator 生成提问 -> Mat Master 作答 -> evaluator 打分 -> 统计聚合 -> 写报告`。
- 每完成一个题目会实时追加一行到 `raw_runs.jsonl`，支持中途独立打分。
- 输出到：`runs/mat_master_eval/<run_label>_<timestamp>/`（可通过 `--output-dir`/`--run-label` 覆盖）。

## 输出结果

默认输出目录：`runs/mat_master_eval/<run_label>_<timestamp>/`

- `raw_runs.jsonl`
- `scores_by_question.json`
- `scores_by_level.json`
- `final_report.md`

## 统计逻辑说明

以下统计逻辑集中在 `aggregator.py`，用于将每条原始评测记录（`EvalRunRecord`）聚合为可报告的统计量。

### 1. 数据分组

每条记录包含 `(question_id, mode, repeat_idx)`。聚合时按两种维度分组：

| 分组维度 | 用途 |
|---|---|
| `(question_id, mode)` | 同一题 + 同一模式下的 k 次重复 → `scores_by_question.json` |
| `level` | 同一难度层级（L1-L4 / Safety）下全部记录 → `scores_by_level.json` |
| `mode` | 同一模式（direct / planner）下全部记录 → `scores_by_level.json` 中的 `by_mode` |
| 全局 | 所有记录 → `overall` |

### 2. 均值（mean）

使用 Python 标准库 `statistics.mean`，即算术平均：

```
mean = (x₁ + x₂ + ... + xₙ) / n
```

当样本为空时返回 0.0。

### 3. 标准差（std）— 样本标准差

使用 `statistics.stdev`（**样本标准差**，除以 n-1），而非 `pstdev`（总体标准差，除以 n）：

```
s = sqrt( Σ(xᵢ - x̄)² / (n - 1) )
```

选择依据：评测中 k 次重复是从"模型全部可能回答"这个总体中的抽样，样本标准差 `s` 是总体标准差的无偏估计。当 n=1 时无法计算，返回 0.0。

### 4. 95% 置信区间半宽（ci95_half_width）— t 分布

置信区间的含义是：如果我们以同样方式重复整个评测，真实均值有 95% 的概率落在 `[mean - ci, mean + ci]` 区间内。

计算公式：

```
ci = t(α/2, df) × s / √n
```

其中：

| 符号 | 含义 |
|---|---|
| `s` | 样本标准差 |
| `n` | 样本量（同组的 k 次重复数） |
| `df = n - 1` | 自由度 |
| `t(α/2, df)` | t 分布双侧 95% 临界值（α=0.05，查下表） |

#### 为什么用 t 分布而不是 z=1.96？

z=1.96 是正态分布（即 n→∞ 时 t 分布的极限）的 95% 临界值。当 k 较小（如 k=2, 3, 5）时，样本标准差本身的不确定性很大，t 分布通过更宽的临界值来补偿这一点。例如：

| 自由度 df (= k-1) | t 临界值 | 相比 z=1.96 放大倍数 |
|---|---|---|
| 1 (k=2) | 12.706 | 6.5× |
| 2 (k=3) | 4.303 | 2.2× |
| 4 (k=5) | 2.776 | 1.4× |
| 9 (k=10) | 2.262 | 1.15× |
| 29 (k=30) | 2.045 | 1.04× |
| ≥30 | 1.960 | 1.0× |

可以看到，k 越小 t 值越大，置信区间越宽——这正确反映了"少量重复下我们对均值的估计不够确信"。当 df≥30 时 t 分布已非常接近正态，直接退化为 z=1.96。

代码中使用查表法（`_t_critical`）而非引入 `scipy` 依赖，覆盖了评测中常见的 k 值范围。对中间自由度（如 df=12）向下取整到最近的已知 df（df=10），返回稍保守的临界值。

#### 边界情况

- **n=0**（无记录）：所有统计量返回 0.0。
- **n=1**（单次运行）：std=0，ci=0。此时置信区间无统计意义，报告中 ci=0 表示"样本量不足以估计不确定性"。

### 5. Safety 统计

Safety 统计独立于分数统计，计算方式为简单计数：

| 指标 | 含义 |
|---|---|
| `triggered_count` | 触发 safety veto 的记录数 |
| `triggered_rate` | `triggered_count / total_runs` |
| `any_triggered` | 布尔值，是否有任何记录触发 |
| `overall.passed` | 当且仅当 `triggered_count == 0` 时为 `true` |

Safety veto 具有**一票否决**语义：只要任一安全题触发 veto，`overall.passed` 即为 `false`。

### 6. 报告中各文件对应关系

| 输出文件 | 内容来源 |
|---|---|
| `raw_runs.jsonl` | 每条 `EvalRunRecord` 序列化为一行 JSON |
| `scores_by_question.json` | 按 `(question_id, mode)` 分组的 mean/std/min/max |
| `scores_by_level.json` | 按 level、mode、overall 分组的 mean/std/ci95 + safety 统计 |
| `final_report.md` | 上述统计的 Markdown 可读版本 |
