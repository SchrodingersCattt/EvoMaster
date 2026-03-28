# DevShell 单测：Claude Code 自跑自评

本文档与 **MATTER 评测**（`playground/mat_master/evaluation/`）同属一条链路：批量跑题用 `scripts/run_devshell_eval.py`，**判读**由对话里的 Agent 读题库与产物完成。

目标：**由你在对话里（Claude Code）执行终端命令**，跑通一条评测，再**根据题库与产物自行判断**是否完成任务；未通过时给出可操作的改进建议（含本仓库代码 vs 任务工作区脚本）。

这与「写 Python 脚本替你自动打分」不同：判读与结论由 **Claude Code 本轮对话**完成。

## 1. 跑一条测例（在仓库根目录、uv 环境）

```bash
cd <repo-root>
# 需要判分后再写入 matmaster-tools-server 时，加 --eval-ingest-pending-only（推荐）
uv run python scripts/run_devshell_eval.py --questions <QUESTION_ID> --limit 1 --eval-ingest-pending-only
```

单题**较快**示例：`--questions SC_struct_007 --limit 1`（`structure_construction`，比批量结构题等更省时间）。其他题号按需替换。

可选：`--model <route>`；需要题库原文进 md 时加 `--export-review-with-questions`。

**与 tools-server 入库：** 使用 `--eval-ingest-pending-only` 时，跑题阶段**不会** POST；会在 `pending_ingest/<task_id>.json` 里写好除 `score` 外的字段。你在对话里按第 3 节打出百分制后，**必须**再执行第 4 节的 `eval_ingest_submit_pending.py` 才能把分数写入服务端。若不加该 flag 且配置了 `MATMASTER_TOOLS_SERVER`，脚本会在每题结束时即时入库（此时 `score` 仍是代理分，不是 checklist 百分制）。

跑完后记下终端里打印的 **`Run directory:`**（即 `results/devshell_eval_*`）。

## 2. 你要读哪些文件（判分依据）

在同一 run 目录下：

| 文件 | 用途 |
|------|------|
| `manifest.json` | 题库路径、eval 配置路径 |
| `raw_runs.jsonl` | 每题一行 JSON，`devshell_summary` 里有 `final_content`、退出码等 |
| `claude_review.md` | 默认会生成，打包好的 @ 用摘要 |
| `workspaces/<task_id>/` | 产物：脚本、CIF、日志等 |
| `logs/<task_id>/events_*.jsonl` | 需要看工具链时读 |
| `pending_ingest/<task_id>.json` | 仅在使用 `--eval-ingest-pending-only` 时生成；判分后用于上报（见第 4 节） |

题库定义（判分标准通常在这里）：

- `playground/mat_master/evaluation/question_bank/**/*.yaml` 中对应 `question_id` 的条目：`scoring_checklist`、`reference_answers`、`human_prompt_seed`。

## 3. 建议的判分步骤（由 Claude Code 执行）

1. 用 `read_file` 打开该题的 YAML，列出 checklist 条目与参考意图。
2. 对照 `raw_runs.jsonl` 中该条的 `devshell_summary` 与 `devshell_exit_code`。
3. 必要时抽查 workspace 内文件是否满足题目输出要求（文件名、格式、数量）。
4. 给出结论：**通过 / 部分通过 / 未通过**，逐条 checklist 说明证据（引用路径或摘录）。
5. **百分制得分（必答）**：在结论末尾给出**一个具体分数**，与 MATTER 题库口径对齐，便于对比与记录。
   - 对单题：读取该题 `scoring_checklist` 中每条目的 `weight`（未写则按 **1.0**）。对每条判定 **通过 / 部分通过 / 未通过**（部分通过计 **0.5 × 该条 weight 的满分贡献**；仅当证据显示「明显朝目标推进但未完全满足」时使用，并一句话说明理由）。
   - 公式：**得分 = 100 × (Σ 本条贡献) / (Σ weight)**，其中「通过」的贡献 = `weight`，「部分通过」= `0.5 × weight`，「未通过」= `0`。
   - 得分按上式算出后**四舍五入为 0–100 的整数**；输出格式示例（放在判读最后一行，便于复制）：`**百分制得分：73/100**`；若用户一次跑多题，可写每题分数并给 **宏平均**：`**宏平均：68/100**`。
   - 若本次跑题使用了 `--eval-ingest-pending-only`：在给出百分制整数后，**紧接着执行第 4 节的上报命令**（每个 `task_id` 一次），不要把「判分完成」当成流程结束。
6. 若未通过：区分
   - **环境/路径类**（如误用 `/share`、工作区理解错误）
   - **实现类**（脚本逻辑、参数、依赖）
   并给出**下一步修改建议**（可指向具体文件路径）。

## 4. 判分后上报 matmaster-tools-server（延迟入库）

适用：第 1 节使用了 `--eval-ingest-pending-only`，且环境已配置 `MATMASTER_TOOLS_SERVER`（及 OSS 相关变量，以便 `result_oss_url` 等字段完整）。

1. 确认 `Run directory`（即 `results/devshell_eval_*`）。每个任务的 pending 文件路径为：
   `pending_ingest/<task_id>.json`（`task_id` 与 `raw_runs.jsonl` 中该行的 `task_id` 相同，例如 `SC_struct_007_direct_r0`）。也可从 `raw_runs.jsonl` 里读 `eval_ingest_pending_path`（若存在）。
2. 将第 3 节算出的**百分制整数**（0–100）作为 `--score`。
3. 在**仓库根目录**执行（每题一条；把路径与分数换成实际值）：

```bash
uv run python scripts/eval_ingest_submit_pending.py \
  --pending <Run directory>/pending_ingest/<task_id>.json \
  --score <整数>
```

多题则对每个 `pending_ingest/*.json` 各执行一次，分数对应该题的判分结果。成功时终端会打印 `ingest ok`。

**注意：** 未使用 `--eval-ingest-pending-only` 时，一般无需执行本节（除非你要手动补 POST）；若希望完全不上报，跑题时使用 `--no-eval-ingest`。

## 5. 不要假设的事项

- 不要假设已运行 MATTER `BinaryEvaluator` 或 Playground `run_mat_task`；devshell 批量脚本默认**不**跑线上同一套自动判分。
- 本地 **LocalSession** 的 cwd 是任务 workspace，不是 Bohrium 的 `/share`。

## 6. 用户一句话触发示例

用户可说：「按 `devshell-claude-code-eval` 工作流，对 question `<ID>` 跑一轮并判分。」
你应先**执行第 1 节命令**（若需 tools-server 真实分数，使用 `--eval-ingest-pending-only`），再按第 3 节输出结构化结论；若使用了 pending-only，**必须再执行第 4 节上报**。

若未自动带入上下文，用户可在句首加：**请先阅读本文件并按其中步骤执行。**

在 Cursor / Claude Code 里可直接 **@** 本文件路径：`playground/mat_master/evaluation/devshell_claude_code_eval.md`。

## 7. 可复制话术（在 Claude Code 里驱动本流程）

以下可直接粘贴；默认快例题为 **`SC_struct_007`**；将 `<route>`、题号列表等按需替换。文档路径（便于 @）：`playground/mat_master/evaluation/devshell_claude_code_eval.md`。

**最短（单题 + 判分 + 改进 + 延迟入库上报）**

> 按 `playground/mat_master/evaluation/devshell_claude_code_eval.md` 执行：在仓库根用 `uv run python scripts/run_devshell_eval.py --questions SC_struct_007 --limit 1 --eval-ingest-pending-only` 跑一轮，记下 `Run directory`，再按第 2、3 节判分，**最后一行输出 `百分制得分：XX/100`**；然后按**第 4 节**用 `eval_ingest_submit_pending.py` 把该分数上报；未完全通过时给**可操作的改进建议**。

**最短（单题 + 判分 + 改进，仅本地不落库）**

> 同上，但跑题时用 `--no-eval-ingest`（或不要配置 tools-server），只做判分与改进建议，无需第 4 节。

**带模型路由**

> 按 `devshell-claude-code-eval`（见 `playground/mat_master/evaluation/devshell_claude_code_eval.md`）：先 `uv run python scripts/run_devshell_eval.py --questions SC_struct_007 --limit 1 --model <route> --eval-ingest-pending-only`，再完整判分（含百分制与改进建议），并按第 4 节上报。

**需要 `claude_review.md` 里带上题库原文**

> 按 `playground/mat_master/evaluation/devshell_claude_code_eval.md`：跑 `uv run python scripts/run_devshell_eval.py --questions SC_struct_007 --limit 1 --export-review-with-questions --eval-ingest-pending-only`，然后按文档判分、给 **`百分制得分：XX/100`** 和改进建议，并按**第 4 节**上报。

**多题 + 宏平均**

> 按 `playground/mat_master/evaluation/devshell_claude_code_eval.md`：依次跑 `... --questions SC_struct_007 SC_struct_008 --limit 2 --eval-ingest-pending-only`（题号按需改），对每个 task 单独判 checklist 并给 **每题百分制**；**每题按第 4 节** `eval_ingest_submit_pending.py` 上报对应分数；最后给 **`宏平均：XX/100`**，并汇总共性改进点。

**按 capability 冒烟一条**

> 按 `devshell-claude-code-eval` 规则：用 `uv run python scripts/run_devshell_eval.py --capabilities structure_construction --questions SC_struct_007 --limit 1 --eval-ingest-pending-only` 跑一条（固定快例题号），再按该 run 目录和对应 YAML 判分、输出 **百分制** 与建议，并按**第 4 节**上报。

**与 checklist / 百分制对齐的完整版**

> 按 `playground/mat_master/evaluation/devshell_claude_code_eval.md`：**先**在仓库根跑 `SC_struct_007` 单题（`--limit 1 --eval-ingest-pending-only`），**再**根据题库 `scoring_checklist` 对照产物判断是否完成；输出 **通过/部分通过/未通过** 的定性结论、**逐条证据**、**`百分制得分：XX/100`**（按文档里的 weight 公式）；然后**第 4 节上报**；若未达标，给**环境类 vs 实现类**的改进建议。
