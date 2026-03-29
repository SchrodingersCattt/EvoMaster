# DevShell 单测：Claude Code 自跑自评

本文档与 **MATTER 评测**（`evaluation/`）同属一条链路：批量跑题用 `scripts/run_devshell_eval.py`，**判读**由对话里的 Agent 读题库与产物完成。

目标：**由你在对话里（Claude Code）执行终端命令**，跑通一条评测，再**根据题库与产物自行判断**是否完成任务；未通过时给出可操作的改进建议（含本仓库代码 vs 任务工作区脚本）。

这与「写 Python 脚本替你自动打分」不同：判读与结论由 **Claude Code 本轮对话**完成。

**本文档约定：默认只跑 `direct` 模式，默认并行数为 `2`。** 所有命令都显式带 `--modes direct --jobs 2`；若某个历史 run 里同时混有 `planner` 任务，判分时只看 `task_id` 形如 `*_direct_r*` 的记录。

## 1. 跑一条测例（在仓库根目录、uv 环境）

```bash
cd <repo-root>
# 需要判分后再写入 matmaster-tools-server 时，加 --eval-ingest-pending-only（推荐）
uv run python scripts/run_devshell_eval.py --modes direct --jobs 2 --questions <QUESTION_ID> --limit 1 --eval-ingest-pending-only
```

单题**较快**示例：`--modes direct --jobs 2 --questions SC_struct_007 --limit 1`（`structure_construction`，比批量结构题等更省时间）。其他题号按需替换。

**按能力筛「结构生成 / 结构构建」整类题：** 题库 YAML 里 `capability: structure_construction` 的题目会全部进入计划；用 `--capabilities structure_construction` 过滤，**不必**再手写题号列表。可与 `--limit` 联用做冒烟（只跑展开后的前 N 条 **direct** 任务）。

```bash
# 结构生成类：先跑前 3 条 direct（示例）
uv run python scripts/run_devshell_eval.py --modes direct --jobs 2 \
  --capabilities structure_construction --limit 3 --eval-ingest-pending-only

# 该类全部 direct（不传 --limit）
uv run python scripts/run_devshell_eval.py --modes direct --jobs 2 \
  --capabilities structure_construction --eval-ingest-pending-only
```

说明：`--capabilities` 按题目字段筛选；与 `--questions` 可同时使用（交集）。其它能力名与 `evaluation/schemas.py` 中 `CapabilityLiteral` 一致（如 `batch_processing`、`workflow_orchestration` 等）。

可选：`--model <route>`；需要题库原文进 md 时加 `--export-review-with-questions`。

**跑前清空 `results/`（默认）：** 不加额外参数时，脚本在创建本次 run 目录之前会删除仓库根下 **`results/` 目录内的全部文件与子目录**（不删 `results` 文件夹本身）。若要**保留**历史产物、与旧 run 并列存放，请加 **`--no-clean-results`**。

**与 tools-server 入库：** 使用 `--eval-ingest-pending-only` 时，跑题阶段**不会** POST；会在 `pending_ingest/<task_id>.json` 里写好除 `score`（及人工判词字段）外的字段。你在对话里按第 3 节判分后，直接用命令行把 `score`、`score_reason`、`suggestion` 传给第 4 节的 `eval_ingest_submit_pending.py` 即可。若不加该 flag 且配置了 `MATMASTER_TOOLS_SERVER`，脚本会在每题结束时即时入库（此时 `score` 仍是代理分，不是 checklist 百分制）。

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
| `logs/<task_id>/devshell_console.log` | 当使用 `--jobs > 1` 并行跑时，每个任务自己的终端输出 |
| `pending_ingest/<task_id>.json` | 仅在使用 `--eval-ingest-pending-only` 时生成；判分后用于上报（见第 4 节） |

题库定义（判分标准通常在这里）：

- `evaluation/question_bank/**/*.yaml` 中对应 `question_id` 的条目：`scoring_checklist`、`reference_answers`、`human_prompt_seed`。
- 若 run 目录里同时存在 `direct` / `planner` 记录，**只读取 `mode == "direct"` 或 `task_id` 后缀为 `_direct_r*` 的条目**。

## 3. 建议的判分步骤（由 Claude Code 执行）

**证据优先级**：百分制 checklist 的通过/部分通过/未通过，**主证据**须来自题目 YAML 与 **`workspaces/<task_id>/` 内实际文件**（须按需打开关键交付物核对内容与格式；结构题应读 POSCAR/CIF 等）。**`devshell_summary` / `final_content` 不得单独作为某条 checklist「已通过」的依据**，仅作过程与状态参考；若自述与文件或题设矛盾，**以文件与题设为准**。

1. 用 `read_file` 打开该题的 YAML，列出 checklist 条目与参考意图。
2. 打开对应 **`workspaces/<task_id>/`**，对照题目输出要求**逐项核对**实际产物（文件名、格式、**内容**）。
3. 再对照 `raw_runs.jsonl` 中该条的 `devshell_exit_code` 与 `devshell_summary`（辅助；不可替代第 2 步对文件的核对）。
4. 给出结论：**通过 / 部分通过 / 未通过**，逐条 checklist 说明证据（引用路径或摘录）。
5. **百分制得分（必答）**：在结论末尾给出**一个具体分数**，与 MATTER 题库口径对齐，便于对比与记录。
   - 对单题：读取该题 `scoring_checklist` 中每条目的 `weight`（未写则按 **1.0**）。对每条判定 **通过 / 部分通过 / 未通过**（部分通过计 **0.5 × 该条 weight 的满分贡献**；仅当证据显示「明显朝目标推进但未完全满足」时使用，并一句话说明理由）。
   - 公式：**得分 = 100 × (Σ 本条贡献) / (Σ weight)**，其中「通过」的贡献 = `weight`，「部分通过」= `0.5 × weight`，「未通过」= `0`。
   - 得分按上式算出后**四舍五入为 0–100 的整数**；输出格式示例（放在判读最后一行，便于复制）：`**百分制得分：73/100**`；若用户一次跑多题，可写每题分数并给 **宏平均**：`**宏平均：68/100**`。
   - 若本次跑题使用了 `--eval-ingest-pending-only`：在给出百分制整数后，**紧接着按第 4 节执行上报命令**（每个 `task_id` 一次），不要把「判分完成」当成流程结束。
6. 若未通过：区分
   - **环境/路径类**（如误用 `/share`、工作区理解错误）
   - **实现类**（脚本逻辑、参数、依赖）
   并给出**下一步修改建议**（可指向具体文件路径）。

## 4. 判分后上报 matmaster-tools-server（延迟入库）

适用：第 1 节使用了 `--eval-ingest-pending-only`，且环境已配置 `MATMASTER_TOOLS_SERVER`（及 OSS 相关变量，以便 `result_oss_url` 等字段完整）。

1. 确认 `Run directory`（即 `results/devshell_eval_*`）。每个任务的 pending 文件路径为：
   `pending_ingest/<task_id>.json`（`task_id` 与 `raw_runs.jsonl` 中该行的 `task_id` 相同，例如 `SC_struct_007_direct_r0`）。也可从 `raw_runs.jsonl` 里读 `eval_ingest_pending_path`（若存在）。
2. 准备以下评分字段（与 `matmaster-tools-server` 的 `EvalItemIn` 一致）：
   - **`score`**（必填）：第 3 节的百分制分数，数字类型（如 `73`）。
   - **`score_reason`**（建议填写）：打分原因 / 评分说明（对照 checklist 的简要依据与结论）。
   - **`suggestion`**（可选）：改进建议；无则省略该键或写空字符串。
3. 在**仓库根目录**执行（每题一条）：

```bash
uv run python scripts/eval_ingest_submit_pending.py \
  --pending <Run directory>/pending_ingest/<task_id>.json \
  --score <0-100> \
  --score-reason "<对照 checklist 的判分依据>" \
  --suggestion "<改进建议，可省略整个参数>"
```

多题则对每个 `pending_ingest/*.json` 各提交一次。成功时终端会打印 `ingest ok`。单字段长度超过 16384 时客户端会截断后再 POST。

**注意：** 未使用 `--eval-ingest-pending-only` 时，一般无需执行本节（除非你要手动补 POST）；若希望完全不上报，跑题时使用 `--no-eval-ingest`。

## 5. 不要假设的事项

- 不要假设已运行 MATTER `BinaryEvaluator` 或 Playground `run_mat_task`；devshell 批量脚本默认**不**跑线上同一套自动判分。
- 本地 **LocalSession** 的 cwd 是任务 workspace，不是 Bohrium 的 `/share`。

## 6. 用户一句话触发示例

用户可说：「按 `devshell-claude-code-eval` 工作流，对 question `<ID>` 跑一轮并判分。」
你应先**执行第 1 节命令**（若需 tools-server 真实分数，使用 `--eval-ingest-pending-only`），再按第 3 节输出结构化结论；若使用了 pending-only，**必须再执行第 4 节上报**。

用户可说：「按该文档跑 5 / 10 / 20 题或全部。」→ 话术与命令见**第 7 节**「批量：5 / 10 / 20 / 全部」。

若未自动带入上下文，用户可在句首加：**请先阅读本文件并按其中步骤执行。**

在 Cursor / Claude Code 里可直接 **@** 本文件路径：`evaluation/devshell_claude_code_eval.md`。

## 7. 可复制话术（在 Claude Code 里驱动本流程）

以下可直接粘贴；默认快例题为 **`SC_struct_007`**；将 `<route>`、题号列表等按需替换。文档路径（便于 @）：`evaluation/devshell_claude_code_eval.md`。

**`--limit` 与「全部」：** 本文档统一显式使用 `--modes direct --jobs 2`。在此前提下，`run_devshell_eval.py` 会先筛出 **direct** 任务列表，再以 **2 路并行** 调度，并应用 `--limit N`；因此 `--limit 5` 表示只跑前 **5 条 direct 任务**。**不传 `--limit`** 则跑当前筛选条件下的**全部 direct** 题目。批量命令同样可加 `--model <route>`、`--export-review-with-questions` 等，与下文单题话术一致。

**`--no-clean-results`：** 见第 1 节；需要与历史 `devshell_eval_*` 并存时再追加。

**批量：5 题（判分 + 延迟入库 + 宏平均）**

> 按 `evaluation/devshell_claude_code_eval.md`：在仓库根执行
> `uv run python scripts/run_devshell_eval.py --modes direct --jobs 2 --limit 5 --eval-ingest-pending-only`
> 记下 `Run directory`，按第 2、3 节**逐题**判分并输出每题 **`百分制得分：XX/100`**，**每题按第 4 节** `eval_ingest_submit_pending.py` 上报；最后给 **`宏平均：XX/100`** 与共性改进点。

**批量：10 题（同上）**

> 按 `evaluation/devshell_claude_code_eval.md`：在仓库根执行
> `uv run python scripts/run_devshell_eval.py --modes direct --jobs 2 --limit 10 --eval-ingest-pending-only`
> 记下 `Run directory`，按第 2、3 节**逐题**判分并输出每题 **`百分制得分：XX/100`**，**每题按第 4 节** `eval_ingest_submit_pending.py` 上报；最后给 **`宏平均：XX/100`** 与共性改进点。

**批量：20 题（同上）**

> 按 `evaluation/devshell_claude_code_eval.md`：在仓库根执行
> `uv run python scripts/run_devshell_eval.py --modes direct --jobs 2 --limit 20 --eval-ingest-pending-only`
> 记下 `Run directory`，按第 2、3 节**逐题**判分并输出每题 **`百分制得分：XX/100`**，**每题按第 4 节** `eval_ingest_submit_pending.py` 上报；最后给 **`宏平均：XX/100`** 与共性改进点。

**批量：全部题目（同上，不加 `--limit`）**

> 同上，命令改为：
> `uv run python scripts/run_devshell_eval.py --modes direct --jobs 2 --eval-ingest-pending-only`
> （不传 `--limit` 即跑当前筛选条件下的全部 **direct** 题目；判分与上报仍按第 2–4 节。）

**批量：仅本地、不落库（5 / 10 / 20 / 全部）**

> 与上文批量命令相同，把 `--eval-ingest-pending-only` 换成 `--no-eval-ingest`（或不配置 tools-server）（示例：`--modes direct --jobs 2 --limit 5 --no-eval-ingest`）；无需第 4 节。

**最短（单题 + 判分 + 改进 + 延迟入库上报）**

> 按 `evaluation/devshell_claude_code_eval.md` 执行：在仓库根用 `uv run python scripts/run_devshell_eval.py --modes direct --jobs 2 --questions SC_struct_007 --limit 1 --eval-ingest-pending-only` 跑一轮，记下 `Run directory`，再按第 2、3 节判分，**最后一行输出 `百分制得分：XX/100`**；然后按**第 4 节**用 `eval_ingest_submit_pending.py --pending ... --score ...` 上报，并把判分依据放进 **`--score-reason`**、可操作改进放进 **`--suggestion`**。

**最短（单题 + 判分 + 改进，仅本地不落库）**

> 同上，但跑题时用 `--no-eval-ingest`（或不要配置 tools-server），只做判分与改进建议，无需第 4 节。

**带模型路由**

> 按 `devshell-claude-code-eval`（见 `evaluation/devshell_claude_code_eval.md`）：先 `uv run python scripts/run_devshell_eval.py --modes direct --jobs 2 --questions SC_struct_007 --limit 1 --model <route> --eval-ingest-pending-only`，再完整判分（含百分制与改进建议），并按第 4 节上报。

**需要 `claude_review.md` 里带上题库原文**

> 按 `evaluation/devshell_claude_code_eval.md`：跑 `uv run python scripts/run_devshell_eval.py --modes direct --jobs 2 --questions SC_struct_007 --limit 1 --export-review-with-questions --eval-ingest-pending-only`，然后按文档判分、给 **`百分制得分：XX/100`** 和改进建议，并按**第 4 节**上报。

**多题 + 宏平均**

> 按 `evaluation/devshell_claude_code_eval.md`：依次跑 `... --modes direct --jobs 2 --questions SC_struct_007 SC_struct_008 --limit 2 --eval-ingest-pending-only`（题号按需改），对每个 task 单独判 checklist 并给 **每题百分制**；**每题按第 4 节** `eval_ingest_submit_pending.py` 上报对应分数；最后给 **`宏平均：XX/100`**，并汇总共性改进点。

**结构生成类（`structure_construction`）：批量前 N 题 + 判分 + 延迟入库**

> 按 `evaluation/devshell_claude_code_eval.md`：在仓库根执行
> `uv run python scripts/run_devshell_eval.py --modes direct --jobs 2 --capabilities structure_construction --limit 5 --eval-ingest-pending-only`
> （把 `5` 改成需要的条数；不传 `--limit` 则跑**全部**结构生成类 direct 任务。）记下 `Run directory`，按第 2、3 节**逐题**判分、`百分制得分` 与第 4 节上报，最后可给 **宏平均** 与共性改进点。

**结构生成类：仅本地不落库（前 N 题）**

> 同上，将 `--eval-ingest-pending-only` 换成 `--no-eval-ingest`；`--limit` 按需调整。

**按 capability 冒烟一条（固定题号，与「整类筛选」二选一即可）**

> 按 `devshell-claude-code-eval` 规则：用 `uv run python scripts/run_devshell_eval.py --modes direct --jobs 2 --capabilities structure_construction --questions SC_struct_007 --limit 1 --eval-ingest-pending-only` 跑一条（`--capabilities` 在此可省略，因题号已唯一确定），再按该 run 目录和对应 YAML 判分、输出 **百分制** 与建议，并按**第 4 节**上报。

**与 checklist / 百分制对齐的完整版**

> 按 `evaluation/devshell_claude_code_eval.md`：**先**在仓库根跑 `SC_struct_007` 单题（`--modes direct --jobs 2 --limit 1 --eval-ingest-pending-only`），**再**根据题库 `scoring_checklist` 对照产物判断是否完成；输出 **通过/部分通过/未通过** 的定性结论、**逐条证据**、**`百分制得分：XX/100`**（按文档里的 weight 公式）；然后**第 4 节上报**；若未达标，给**环境类 vs 实现类**的改进建议。
