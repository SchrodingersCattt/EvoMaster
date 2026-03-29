# Claude Code Baseline 测评（与 DevShell 对照）

目标：用 **Claude Code** 直接完成题库任务（不跑 `mm-devshell`），产物与 `evaluation/scripts/run_devshell_eval.py` / `evaluation/scripts/finalize_cc_baseline_ingest.py` 对齐，便于和 MatMaster kernel 对比。

**术语（全文统一）**

- **仓库根**：本 Git 仓库根目录；MATTER 评测代码与题库在 `evaluation/`（跑题脚本在 `evaluation/scripts/`）。在终端可用 `cd "$(git rev-parse --show-toplevel)"` 进入。
- **RUN_DIR**：本次测评产物根目录（其下有 `workspaces/` 等）。**推荐不手工填写**：默认 `prepare` 会清空 `results/`，可在仓库根用下面「RUN_DIR 自动解析」中的一行命令得到绝对路径；若曾用 `--no-clean-results` 导致同前缀目录多个并存，再以 stderr 里 **`Run directory: `** 冒号后的路径为准。

推荐把对话 **人为拆成两阶段**（两个独立的 Claude Code 会话）：

| 阶段 | 会话 | 职责 |
|------|------|------|
| **阶段一** | 会话 A | **只做任务**：按 `_devshell_prompt.txt` 完成交付物 + 写 `_devshell_summary.json`。**禁止**判分、禁止上报、禁止讨论 checklist。 |
| **之间** | 终端 | 阶段一全部 workspace 完成后，在仓库根执行 `finalize_cc_baseline_ingest.py --eval-ingest-pending-only`，生成 `pending_ingest/*.json` 与 `raw_runs.jsonl`。 |
| **阶段二** | 会话 B（新开） | **只做阅卷与上报**：按题库 `scoring_checklist` 逐条打分，算百分制，对 **pending_ingest 目录下每一个 `.json` 文件各执行一次** `eval_ingest_submit_pending.py`，**每次调用都必须**包含 `--score`、`--score-reason`、`--suggestion`。 |

---

## 阶段之间：终端命令（结构生成类）

**1）搭工作区（pending，避免即时入库代理分）**

在终端执行（整段复制；**不要**加 `--no-clean-results`，以便默认清空 `results/` 后再创建本次 run）：

```bash
cd "$(git rev-parse --show-toplevel)"
uv run python evaluation/scripts/run_devshell_eval.py --prepare-cc-baseline --run-label baseline_cc_struct \
  --modes direct --capabilities structure_construction --eval-ingest-pending-only
```

- 仅跑前 N 条任务时：在上面的 `uv run` 命令中、`--eval-ingest-pending-only` **之前**插入 `--limit N`（N 为正整数）。
- 命令结束后，**RUN_DIR** 可取 stderr 里 **`Run directory: `** 冒号后的路径；若未保留输出，在仓库根执行下面「RUN_DIR 自动解析」命令即可（默认仅一个 `baseline_cc_struct_*` 目录时与 stderr 一致）。
- 默认行为会删除仓库根下 **`results/` 内全部文件与子目录**（保留 `results` 空壳）。只有需要与历史 run 并存时，再在命令末尾追加 `--no-clean-results`。

**RUN_DIR 自动解析（不必从 stderr 复制）**

在仓库根执行；`--run-label` 与 prepare 一致时（默认 `baseline_cc_struct`），取 `results/` 下该前缀目录按名字排序的**最后一个**（时间戳后缀即最新一次 prepare）：

```bash
ROOT="$(git rev-parse --show-toplevel)"
export RUN_DIR="$(find "$ROOT/results" -maxdepth 1 -type d -name 'baseline_cc_struct_*' | sort | tail -1)"
echo "$RUN_DIR"
```

若改了 `--run-label`，把上面 `name` 里的 `baseline_cc_struct_*` 换成你的前缀加 `_*`。若 `echo` 为空或存在多个候选且无法确定，请用 stderr 的 **`Run directory: `** 行。

**2）阶段一全部 workspace 完成后（仍在仓库根）**

无需手写路径时，可与上一步同一 shell 先 `export RUN_DIR=...`（见下「RUN_DIR 自动解析」），再：

```bash
cd "$(git rev-parse --show-toplevel)"
uv run python evaluation/scripts/finalize_cc_baseline_ingest.py --run-dir "$RUN_DIR" --eval-ingest-pending-only
```

成功时 **RUN_DIR** 下应出现目录 `pending_ingest/`（内含若干 `*.json`）和文件 `raw_runs.jsonl`。

**3）阶段二**：新开 Claude Code 会话，粘贴下文「一键话术 · 阶段二」；**RUN_DIR** 由执行者在仓库根用上文「RUN_DIR 自动解析」得到，无需你在对话里事先提供路径。

---

## 阶段一：做题（`_devshell_summary.json` 字段）

每个任务的根目录路径为：**RUN_DIR/workspaces/任务目录名/**。任务目录名与 `pending_ingest` 里 JSON 文件名（不含 `.json`）一致，例如 `SC_struct_007_direct_r0`。

- 读取该目录下的 `_devshell_prompt.txt`，完成全部交付要求。
- 在同一目录写入 **`_devshell_summary.json`**：**整文件仅一行** JSON，UTF-8，字段与 mm-devshell `--json-out` 一致：
  - `model`、`profile_key`、`route_key`、`status`、`reason`（任务已尽力完成时填 `"natural"`）、`final_content`、`num_turns`
  - `usage`：填对象，键为 `prompt_tokens`、`completion_tokens`、`total_tokens`（整数）；无法统计时填 `{}` 并在 `final_content` 首行写「tokens 未统计」
  - `duration_ms`：整数，从开始处理本题到写完 `_devshell_summary.json` 的 wall-clock 毫秒数
- 禁止修改、禁止删除 `_eval_task_meta.json`。
- 可选：将过程记录写入 **RUN_DIR/logs/任务目录名/devshell_console.log**。

最小示例（写入文件时压缩为**一行**）：

```json
{"model":"claude-sonnet-4-20250514","profile_key":"claude_code","route_key":null,"status":"completed","reason":"natural","final_content":"（摘要）","num_turns":12,"usage":{"prompt_tokens":8000,"completion_tokens":4000,"total_tokens":12000},"duration_ms":180000}
```

字段含义与 devshell 一致，见 `matmaster/devshell/cli.py` 中构造 `summary` 的代码。

---

## 阶段二：阅卷与上报（百分制 + 原因 + 建议）

- 先阅读 `evaluation/devshell_claude_code_eval.md` **第 3 节**，按其中公式用 `scoring_checklist` 的 `weight` 计算 **0–100 的整数**。
- 题目定义在 `evaluation/question_bank/` 下，按 `item.question_id`（与 YAML 中 `id` 一致）找到对应 YAML，读取 `scoring_checklist`。
- 证据来源：**RUN_DIR/raw_runs.jsonl** 中对应 `task_id` 的行、**RUN_DIR/workspaces/任务目录名/** 内文件；若 pending JSON 的 `item` 中含 `result_oss_url`，可下载该 zip 辅助核对。

对每个 **RUN_DIR/pending_ingest/*.json** 执行一次（在仓库根），将 `PENDING_FILE` 换成该文件的**绝对路径**，将 `SCORE_INT` 换成你算出的整数，将字符串参数换成你的判词：

```bash
cd "$(git rev-parse --show-toplevel)"
uv run python evaluation/scripts/eval_ingest_submit_pending.py \
  --pending "PENDING_FILE" \
  --score SCORE_INT \
  --score-reason "逐条写出 checklist 条目、weight、通过或部分通过或未通过、证据（仓库内相对路径或摘录）" \
  --suggestion "至少一条可执行改进；确实没有则写：无——并一句话说明理由"
```

- **PENDING_FILE** 示例：`$RUN_DIR/pending_ingest/SC_struct_007_direct_r0.json`（先按上文解析 `RUN_DIR`，再拼真实文件名）。
- 成功时终端最后一行附近出现 **`ingest ok`**。
- **`--suggestion` 禁止省略**：无改进内容时字面写 `无——` 加简短理由即可。

---

## 为何不要在一个会话里又做题又打分？

即时 POST 或未走 pending 时，入库 `score` 可能是 **100/0 代理分**（见 `matmaster.eval_ingest_client.score_for_eval_ingest`）。两阶段 + pending + `eval_ingest_submit_pending.py` 才能把 **真实百分制 + 原因 + 建议** 写入库。

---

## 库里如何认出 CC Baseline

`finalize_cc_baseline_ingest.py` 会在入库项上设置 `item.model` 后缀 `| cc_baseline`，以及 `item.extra.matter_eval_source` / `eval_runner` 为 `claude_code_baseline` 等字段；细节见脚本源码。

---

## 一键话术 · 阶段一（只做任务；**无需**向对话粘贴 RUN_DIR）

将下面整段复制到 **Claude Code 会话 A**。执行者自行在终端解析 **RUN_DIR**（见上文「RUN_DIR 自动解析」或 prepare 的 stderr），**不要求用户在对话里提供路径**。

> **【CC Baseline · 阶段一 · 执行者】**
> 你的职责只有：**做题**，不写 checklist 判分、不执行 `eval_ingest_submit_pending.py`、不讨论百分制。
> **RUN_DIR**：**不要**要求用户在对话中粘贴路径。在仓库根执行 prepare 后，用仓库文档 `evaluation/baseline_cc_eval.md` 中「RUN_DIR 自动解析」的 `ROOT` + `find` + `export RUN_DIR=...` 得到绝对路径；若 `prepare` 使用了与用户默认不同的 `--run-label`，把 `find` 的 `-name` 改成对应前缀。若 `RUN_DIR` 仍为空或存在多个候选，再读 stderr 的 **`Run directory: `** 行。
> **若尚未 prepare**：在终端进入仓库根（含 `scripts/` 的 Git 根），执行：
> `cd "$(git rev-parse --show-toplevel)"`
> `uv run python evaluation/scripts/run_devshell_eval.py --prepare-cc-baseline --run-label baseline_cc_struct --modes direct --capabilities structure_construction --eval-ingest-pending-only`
> 命令**不得**包含 `--no-clean-results`（除非用户明确要求保留历史 `results/`）。完成后按上一段解析 **RUN_DIR**。
> 仅跑部分题时：在上述第二行 `uv run` 命令中、在 `--eval-ingest-pending-only` **之前**插入 `--limit` 和正整数。
> **任务列表**：列出目录 `RUN_DIR/workspaces/` 下的**每一个一级子目录**名称；对每个名称 `TASK_DIR`（即 task_id），按顺序完成：
> 1. 将当前工作目录设为 `RUN_DIR/workspaces/TASK_DIR/`。
> 2. 阅读 `_devshell_prompt.txt`，完成其中全部交付物。
> 3. 在同一目录创建或覆盖 `_devshell_summary.json`：**文件内容为单行合法 JSON**，字段要求见仓库文件 `evaluation/baseline_cc_eval.md` 中「阶段一：做题」小节（必须含 `duration_ms`；`usage` 尽量填 token 数字）。
> 4. 不得修改 `_eval_task_meta.json`。
> **收尾**：所有 `TASK_DIR` 处理完后，在仓库根执行（使用你已解析的 **RUN_DIR**，勿让用户粘贴）：
> `cd "$(git rev-parse --show-toplevel)"`
> `uv run python evaluation/scripts/finalize_cc_baseline_ingest.py --run-dir "$RUN_DIR" --eval-ingest-pending-only`
> 确认 **RUN_DIR** 下存在 `pending_ingest` 目录（内有 `.json`）和文件 `raw_runs.jsonl`。然后停止本会话中的测评工作；**阅卷与上报在另一个新开的 Claude Code 会话中完成**，使用同文档「一键话术 · 阶段二」。

---

## 一键话术 · 阶段二（只阅卷上报；新开会话；**无需**粘贴 RUN_DIR）

将下面整段复制到 **新开的 Claude Code 会话 B**。执行者在仓库根用 `baseline_cc_eval.md` 中「RUN_DIR 自动解析」得到 **RUN_DIR**（与阶段一 `finalize` 所用目录一致；若 `results` 下仅有一个匹配目录，即为该次测评）。

> **【CC Baseline · 阶段二 · 阅卷者】**
> 你的职责只有：**阅卷**与**调用上报命令**，不修改 **RUN_DIR/workspaces/** 下已有交付物（除非用户明确要求修复明显损坏并说明）。
> **RUN_DIR**：**不要**要求用户粘贴路径；在仓库根按 `evaluation/baseline_cc_eval.md`「RUN_DIR 自动解析」导出 `RUN_DIR`。若存在多个 `*_` 目录无法唯一确定，请用户确认本次应对应哪一次 run（或提供 stderr / `manifest.json` 所在路径）。
> 前置检查：**RUN_DIR/pending_ingest/** 下至少有一个 `.json` 文件；**RUN_DIR/raw_runs.jsonl** 存在。若不满足，先让用户在仓库根补跑 finalize，不要编造路径。
> 先阅读文件 `evaluation/devshell_claude_code_eval.md` 的**第 3 节**，严格用其中 **weight 与 0.5×weight** 规则计算每题 **0–100 整数**。
> **客观性（必读）**：每条 checklist 的通过/部分通过/未通过，**主证据**须来自 **题目 YAML、该任务 `workspaces/<task_id>/_devshell_prompt.txt` 要求的交付物、以及 workspace 内真实文件内容**（结构类须实际打开 POSCAR/CIF 等核对格式与题设，不能只看存在性）。**`raw_runs.jsonl` 中该行 JSON 里的 `devshell_summary` / `final_content`、以及 `_devshell_summary.json`，仅作过程与状态参考**；**禁止**仅凭执行者自述或摘要文字给 checklist 判「通过」。若自述与文件或题设矛盾，**以文件与题设为准**，并在 `score-reason` 中写明矛盾点。
> 对 **RUN_DIR/pending_ingest/** 下**每一个**扩展名为 `.json` 的文件 `F`（含完整文件名，例如 `SC_struct_007_direct_r0.json`）：
> 1. 打开 `F`，读取 `item.question_id`，在 `evaluation/question_bank/` 中找到对应 YAML，读取 `scoring_checklist`；并阅读 **RUN_DIR/workspaces/（task_id）/_devshell_prompt.txt**，列出须交付的文件与约束。
> 2. **先**逐项核对 **RUN_DIR/workspaces/（同上 task_id）/** 下实际产物是否满足上一步与 YAML；**再**对照 **RUN_DIR/raw_runs.jsonl** 同行中的 `devshell_exit_code`、摘要等（摘要不能替代对产物的核对）。若 `F` 内 `item` 含 `result_oss_url`，可按需辅助取证。
> 3. 构造本条任务的 **PENDING_ABS**：`"$RUN_DIR/pending_ingest/$F文件名"`（`RUN_DIR` 无尾斜杠；`F` 含 `.json`）。示例：RUN_DIR=`/a/b/results/baseline_cc_struct_20260328_120000`，F=`SC_struct_007_direct_r0.json` → PENDING_ABS=`/a/b/results/baseline_cc_struct_20260328_120000/pending_ingest/SC_struct_007_direct_r0.json`。
> 4. 在仓库根执行**一次**上报。`--pending` 的引号内填**第 3 步整条 PENDING_ABS**；`--score` 后填本题算出的 **0–100 整数**（下面用 `73` 仅作格式示例，每题替换为真实分数）；`--score-reason` 与 `--suggestion` 各用一对双引号包住判词全文（判词内尽量避免未转义的双引号）。
> `cd "$(git rev-parse --show-toplevel)"`
> `uv run python evaluation/scripts/eval_ingest_submit_pending.py --pending "第3步得到的完整绝对路径" --score 73 --score-reason "逐条 checklist：条目、weight、判定、证据路径或摘录" --suggestion "可执行建议；若无写：无——加一句理由"`
> 5. 本条命令终端输出须含 **`ingest ok`**；然后再对下一个 `F` 重复步骤 1–5。
> 全部文件处理完后，在回复中输出表格：列包括 **question_id**、**task_id（文件名）**、**score**、**一句话结论**；若有多题，最后一行给出 **宏平均（整数，四舍五入）**。

---

## 附录：不限结构生成类时

将 prepare 命令中的 `--capabilities structure_construction` 删除，或改为 `schemas.py` 中 `CapabilityLiteral` 允许的其他能力名；阶段一、二话术里凡写「结构生成」处按你的筛选条件改写即可。

Cursor / Claude Code 可 **@** 本文件与 `devshell_claude_code_eval.md`。
