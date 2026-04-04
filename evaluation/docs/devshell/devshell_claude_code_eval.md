# DevShell 单测：Claude Code 自跑自评

本文档用于 **DevShell / mm-devshell** 跑题后的标准评测流程。当前流程已对齐 baseline：

1. **阶段一：跑题**
   用 `evaluation/scripts/devshell/run_devshell_eval.py` 生成 `workspaces/`、`logs/`、`raw_runs.jsonl`，并在需要延迟入库时写出 `pending_ingest/*.json`。
2. **阶段二：自动评分**
   用 `evaluation/scripts/devshell/score_devshell_tasks.py` 基于同一套 **BinaryEvaluator** 自动计算 0–100 分与 `score_reason`，可选一键提交到 ingest API。

与旧版不同：**不再推荐人工逐条 checklist 手算百分制并手写 `score_reason`**。若要抽查证据，可在自动评分后再打开低分任务的 `workspace` / `events_*.jsonl` 做复核。

若需要 **Claude Agent SDK** 外层循环（多轮跑题 → 自动评分 → 改仓库 → 再跑），见同目录 [devshell_agent_sdk_loop.md](devshell_agent_sdk_loop.md)。

## 0. 核心原则

- **默认只跑 `direct` 模式，默认并行数为 `4`。**
- 若要获得真实分数并延迟入库，跑题时使用 **`--eval-ingest-pending-only`**。
- **不要**再直接调用 `eval_ingest_submit_pending.py` 手工赋分；应优先使用 `score_devshell_tasks.py`。
- `score_devshell_tasks.py` 会读取：
  - `raw_runs.jsonl`
  - `workspaces/<task_id>/_devshell_summary.json`
  - `logs/<task_id>/events_*.jsonl`
  - `pending_ingest/<task_id>.json`
- 评分口径与 MATTER 保持一致：
  - `struct_file_*` / `artifact_exists` 等走确定性校验
  - `llm_binary_judge` 走 `evaluation/config.yaml` 中的 `evaluator_llm`
  - grounding / efficiency 的 tool/event 检查来自 DevShell 事件日志，而不是人工阅读总结文本

## 1. 阶段一：跑一轮 DevShell

在仓库根目录、uv 环境下执行：

```bash
cd <repo-root>
uv run python evaluation/scripts/devshell/run_devshell_eval.py \
  --modes direct \
  --jobs 4 \
  --questions <QUESTION_ID> \
  --limit 1 \
  --eval-ingest-pending-only
```

常见变体：

```bash
# 单题快跑
uv run python evaluation/scripts/devshell/run_devshell_eval.py \
  --modes direct --jobs 4 --questions SC_struct_007 --limit 1 \
  --eval-ingest-pending-only

# 按 capability 冒烟前 3 题
uv run python evaluation/scripts/devshell/run_devshell_eval.py \
  --modes direct --jobs 4 --capabilities structure_construction --limit 3 \
  --eval-ingest-pending-only

# 同类题全量
uv run python evaluation/scripts/devshell/run_devshell_eval.py \
  --modes direct --jobs 4 --capabilities structure_construction \
  --eval-ingest-pending-only
```

说明：

- 默认会在创建本次 run 前清空仓库根 `results/` 下的旧产物；若要保留历史结果，请加 `--no-clean-results`。
- `Run directory:` 会打印到终端，例如 `results/devshell_eval_20260404_...`。
- `--eval-ingest-pending-only` 打开后，每个任务完成时会生成：
  - `raw_runs.jsonl` 中该题的结果行
  - `pending_ingest/<task_id>.json`
- 若不加该 flag 且配置了 `MATMASTER_TOOLS_SERVER`，脚本会即时 POST 代理分（通常 100/0），**不代表 BinaryEvaluator 真实分数**。

## 2. 阶段二：自动评分

### 2.1 Dry-run 看分数

```bash
uv run python evaluation/scripts/devshell/score_devshell_tasks.py \
  --run-dir <Run directory> \
  --dry-run
```

或按 run label 自动找最新目录：

```bash
uv run python evaluation/scripts/devshell/score_devshell_tasks.py \
  --run-label devshell_eval \
  --dry-run
```

输出会包含：

- 每个 `task_id` 的 `score/100`
- 自动生成的 Markdown `score_reason`
- 末尾宏平均 `Average score: XX/100`

若只想重评分某一题：

```bash
uv run python evaluation/scripts/devshell/score_devshell_tasks.py \
  --run-dir <Run directory> \
  --tasks SC_struct_007_direct_r0 \
  --dry-run
```

### 2.2 正式写分并提交

```bash
uv run python evaluation/scripts/devshell/score_devshell_tasks.py \
  --run-dir <Run directory> \
  --submit
```

行为说明：

- 若存在 `pending_ingest/<task_id>.json`，脚本会先把 `score` / `score_reason` 写回该文件，再自动 POST。
- 成功提交时会输出：`[ingest] <task_id> ok`
- 若只想写回 `pending_ingest`，先不提交：**省略 `--submit`**
- 若之后仍需补 `suggestion`，可再用：

```bash
uv run python evaluation/scripts/eval_ingest_submit_pending.py \
  --pending <Run directory>/pending_ingest/<task_id>.json \
  --score <已有分数> \
  --suggestion "<可执行改进建议>"
```

### 2.3 依赖说明

- `llm_binary_judge` 依赖 `evaluation/config.yaml` 中的 `evaluator_llm`
- `struct_file_*` 类校验依赖 `pymatgen`
- 如缺少结构校验依赖，先执行：

```bash
uv sync --extra calculation
```

## 3. 你该看哪些文件

自动评分通常不需要人工逐条阅卷，但排查低分任务时建议看：

| 文件 | 用途 |
|------|------|
| `manifest.json` | 本次 run 的配置 |
| `raw_runs.jsonl` | 每题的 `question_id`、`mode`、`duration_ms`、`devshell_summary` |
| `workspaces/<task_id>/` | 实际交付物（CIF、POSCAR、脚本、报告等） |
| `logs/<task_id>/events_*.jsonl` | DevShell 事件日志；自动评分据此恢复 tool calls / event types |
| `pending_ingest/<task_id>.json` | 待写回或待提交的 ingest payload |

## 4. 推荐话术

以下话术已按 **score 脚本** 流程收口。

**最短：单题跑题 + 自动评分 + 提交**

> 按 `evaluation/docs/devshell/devshell_claude_code_eval.md`：在仓库根执行
> `uv run python evaluation/scripts/devshell/run_devshell_eval.py --modes direct --jobs 4 --questions SC_struct_007 --limit 1 --eval-ingest-pending-only`
> 记下 `Run directory`，然后执行
> `uv run python evaluation/scripts/devshell/score_devshell_tasks.py --run-dir <Run directory> --submit`。
> 回复时给出该题 `score/100`、关键 pass/fail 条目，以及需要补充的改进建议。

**单题只看分，不提交**

> 按 `evaluation/docs/devshell/devshell_claude_code_eval.md`：先跑
> `uv run python evaluation/scripts/devshell/run_devshell_eval.py --modes direct --jobs 4 --questions SC_struct_007 --limit 1 --eval-ingest-pending-only`，
> 再执行
> `uv run python evaluation/scripts/devshell/score_devshell_tasks.py --run-dir <Run directory> --dry-run`。
> 输出最终 `score/100` 和 `score_reason` 摘要，但先不要提交。

**多题批量 + 宏平均**

> 按 `evaluation/docs/devshell/devshell_claude_code_eval.md`：先跑
> `uv run python evaluation/scripts/devshell/run_devshell_eval.py --modes direct --jobs 4 --questions SC_struct_007 SC_struct_008 --eval-ingest-pending-only`，
> 再执行
> `uv run python evaluation/scripts/devshell/score_devshell_tasks.py --run-dir <Run directory> --submit`。
> 回复中逐题列出 `question_id`、`task_id`、`score/100`、主要 pass/fail 条目，最后给宏平均。

**按 capability 批量**

> 按 `evaluation/docs/devshell/devshell_claude_code_eval.md`：先跑
> `uv run python evaluation/scripts/devshell/run_devshell_eval.py --modes direct --jobs 4 --capabilities structure_construction --eval-ingest-pending-only`，
> 再执行
> `uv run python evaluation/scripts/devshell/score_devshell_tasks.py --run-dir <Run directory> --submit`。
> 如有低分任务，再打开对应 `workspace` 与 `events_*.jsonl` 解释原因。

## 5. 不要假设的事项

- `run_devshell_eval.py` 本身**不**运行 BinaryEvaluator；真实评分发生在 `score_devshell_tasks.py`
- 不要把 `devshell_summary.final_content` 当作 checklist 通过的唯一依据
- 不要再走“人工算百分制 → 手填 `--score-reason`”作为默认路径
- 本地 `LocalSession` 的 cwd 是任务 workspace，不是 Bohrium 的 `/share`

## 6. 与 baseline 的对齐关系

- baseline：`score_baseline_tasks.py`
- devshell：`score_devshell_tasks.py`

两者都走同一套 `BinaryEvaluator`，差别只在证据来源：

- baseline 从 `_devshell_summary.json` + workspace 文件恢复证据
- devshell 额外读取 `events_*.jsonl`，因此 `tool_called`、`event_type_called`、`no_retries` 等 grounding / efficiency 项可以按真实轨迹判分
