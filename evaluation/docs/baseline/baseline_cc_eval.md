# Claude Code Baseline 测评（与 DevShell 对照）

目标：用 **Claude Code**（或同布局的 **Cursor** 等）直接完成题库任务（不跑 `mm-devshell`），产物与 `evaluation/scripts/devshell/run_devshell_eval.py` / `evaluation/scripts/baseline/finalize_external_baseline_ingest.py` 对齐，便于和 MatMaster kernel 对比。入库时由 `manifest` / `--baseline-channel` 区分 `claude_code` 与 `cursor`。**不安装 Anthropic claude CLI、只在 Cursor 里做题**时：直接复制文末 **「一键话术 · 纯 Cursor 阶段一」**；分步说明见 **「纯 Cursor baseline（无 Anthropic claude CLI）」**。

**术语（全文统一）**

- **仓库根**：本 Git 仓库根目录；MATTER 评测代码与题库在 `evaluation/`（跑题脚本在 `evaluation/scripts/`）。在终端可用 `cd "$(git rev-parse --show-toplevel)"` 进入。
- **RUN_DIR**：本次测评产物根目录（其下有 `workspaces/` 等）。**推荐不手工填写**：默认 `prepare` 会清空 `results/`，可在仓库根用下面「RUN_DIR 自动解析」中的一行命令得到绝对路径；若曾用 `--no-clean-results` 导致同前缀目录多个并存，再以 stderr 里 **`Run directory: `** 冒号后的路径为准。

流程分两阶段：

| 阶段 | 执行方式 | 职责 |
|------|----------|------|
| **阶段一** | **有 claude CLI**：终端跑脚本，或将「一键话术 · 阶段一」交给 IDE 代跑（`run_claude_cli_baseline_tasks.py` → `claude -p`）。**纯 Cursor**：复制文末「**一键话术 · 纯 Cursor 阶段一**」，不跑 `run_claude_cli_baseline_tasks.py`。 | **做题**并留下与 DevShell 对齐的 summary；CLI 路径下 token 来自 `claude -p --output-format json`。 |
| **阶段二** | 新开会话（Claude Code、Cursor 或其它均可） | **阅卷与上报**：按题库 `scoring_checklist` 逐条打分，算百分制，对 `pending_ingest/` 下每个 `.json` 执行 `eval_ingest_submit_pending.py`。 |

---

## 纯 Cursor baseline（无 Anthropic claude CLI）

本机**没有**、也**不打算安装**可执行文件 `claude`（无法使用 `run_claude_cli_baseline_tasks.py`）时，阶段一改为：**prepare 与 finalize 仍用 `uv run python`**，题目在 Cursor 对话里按 `_devshell_prompt.txt` 完成，**自行写入** `_devshell_summary.json`。

**与 CLI 自动化路径的差异**

| 步骤 | 纯 Cursor |
|------|-----------|
| Prepare | 在下列命令中增加 **`--baseline-channel cursor`**，使 `manifest.json` 记录 `baseline_channel: "cursor"`，与入库一致。 |
| 做题 | 在 Cursor 中针对 **`RUN_DIR/workspaces/<task_id>/`** 工作（可 `@` 该目录），勿修改或删除 `_eval_task_meta.json`。 |
| Token / 耗时 | 无 `claude -p` JSON；`usage` 可填 Cursor 用量（若可抄）、或合理占位并在阶段二 `score_reason` 中说明「token 未精确记录」。客观耗时仍依赖 **`mark_external_baseline_task_start.py`**（见下）。 |
| Finalize | 只跑 `finalize_external_baseline_ingest.py`，**不要**跑 `run_claude_cli_baseline_tasks.py`。 |

**1）Prepare（结构生成类示例）**

在本文「阶段之间：终端命令（结构生成类）」一节的 prepare 命令上，于 `--eval-ingest-pending-only` **之前**加入 `--baseline-channel cursor`，例如：

```bash
cd "$(git rev-parse --show-toplevel)"
uv run python evaluation/scripts/devshell/run_devshell_eval.py --prepare-cc-baseline --run-label baseline_cc_struct \
  --modes direct --capabilities structure_construction --baseline-channel cursor --eval-ingest-pending-only
```

仅部分题目时仍可用 `--limit N` 或 `--questions <question_id> ...`（与现有文档一致）。

**2）每题开始前：打墙钟起点（与 finalize 中 `duration_ms` 对齐）**

```bash
uv run python evaluation/scripts/baseline/mark_external_baseline_task_start.py \
  --workspace "$RUN_DIR/workspaces/<task_id>"
```

将 `<task_id>` 换成实际目录名（如 `SC_struct_007_direct_r0`）。`RUN_DIR` 用「RUN_DIR 自动解析」导出。

**3）在 Cursor 中做题**

- 阅读该目录下的 `_devshell_prompt.txt`，在同一目录内生成题目要求的文件。
- 完成后写入 **`_devshell_summary.json`**（单行 JSON，UTF-8），字段与下文「阶段一：做题（`_devshell_summary.json` 字段）」一节一致，建议：
  - `profile_key`: `"cursor"`
  - `model`: 本次实际使用的模型名（与 Cursor 设置一致即可）
  - `status` / `reason`: 正常完成用 `"completed"` / `"natural"`
  - `final_content`: 简短摘要（勿替代对交付物文件的阅卷）
  - `num_turns`: 对话轮数或合理估计
  - `usage`: 尽量填写 `prompt_tokens` / `completion_tokens` / `total_tokens`；若无可靠数据可省略或填 `0`，并在后续 `eval_ingest_submit_pending.py` 的 `--score-reason` 中说明
  - **不要**伪造 `claude_cli_meta`；纯 Cursor 可不写该字段

**4）Finalize**

全部题目的 `_devshell_summary.json` 就绪后，在仓库根执行：

```bash
cd "$(git rev-parse --show-toplevel)"
uv run python evaluation/scripts/baseline/finalize_external_baseline_ingest.py --run-dir "$RUN_DIR" --eval-ingest-pending-only
```

`baseline_channel` 默认从 `manifest.json` 读取；若曾手工改动 manifest，可显式追加 `--baseline-channel cursor`。

**5）阶段二**

与本文「阶段二：阅卷与上报」及文末「一键话术 · 阶段二」相同；阅卷会话可开在 **Cursor**。**可一键复制的话术**见文末 **「一键话术 · 纯 Cursor 阶段一」**。

---

## 阶段之间：终端命令（结构生成类）

**1）搭工作区（pending，避免即时入库代理分）**

在终端执行（整段复制；**不要**加 `--no-clean-results`，以便默认清空 `results/` 后再创建本次 run）：

```bash
cd "$(git rev-parse --show-toplevel)"
uv run python evaluation/scripts/devshell/run_devshell_eval.py --prepare-cc-baseline --run-label baseline_cc_struct \
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

**1.5）自动执行阶段一（推荐，替代手动 Claude Code 会话）**

使用 `run_claude_cli_baseline_tasks.py` 自动调用 `claude -p` 非交互模式执行每道题，token 用量从 CLI JSON 输出中自动提取（无需 `/cost` 差值）：

```bash
cd "$(git rev-parse --show-toplevel)"
# 自动检测 RUN_DIR，执行全部任务，完成后自动 finalize
uv run python evaluation/scripts/baseline/run_claude_cli_baseline_tasks.py \
  --run-label baseline_cc_struct --finalize --eval-ingest-pending-only
```

常用选项：

| 选项 | 说明 |
|------|------|
| `--run-dir "$RUN_DIR"` | 显式指定 RUN_DIR（与 `--run-label` 二选一） |
| `--run-label baseline_cc_struct` | 从 `results/` 自动检测最新匹配目录 |
| `--tasks SC_struct_007_direct_r0` | 只跑指定 task（可多个，空格分隔） |
| `--model opus` | 指定模型（默认跟随 claude CLI 默认） |
| `--max-turns 50` | 每题最大对话轮数（默认 50） |
| `--timeout 600` | 每题超时秒数（默认 600） |
| `--skip-completed` | 跳过已有 `_devshell_summary.json` 的任务 |
| `--finalize` | 任务完成后自动跑 `finalize_external_baseline_ingest.py` |
| `--eval-ingest-pending-only` | finalize 时写 pending（需配合 `--finalize`） |

脚本自动为每个任务：(1) 写 `_cc_baseline_task_start.json`；(2) 执行 `claude -p --output-format json --dangerously-skip-permissions --bare`；(3) 从 JSON 输出提取全部 token 字段写入 `_devshell_summary.json`。

`_devshell_summary.json` 中 `usage` 字段包含完整明细：

```json
{
  "prompt_tokens": 52622,
  "completion_tokens": 3254,
  "total_tokens": 55876,
  "input_tokens": 5,
  "cache_creation_input_tokens": 3244,
  "cache_read_input_tokens": 49373,
  "output_tokens": 3254,
  "total_cost_usd": 0.126,
  "model_usage": {"<model_id>": {"inputTokens": ..., "outputTokens": ..., "costUSD": ..., ...}}
}
```

**2）阶段一全部 workspace 完成后（仍在仓库根）**

若使用了 `--finalize`，此步自动完成。否则手动执行：

```bash
cd "$(git rev-parse --show-toplevel)"
uv run python evaluation/scripts/baseline/finalize_external_baseline_ingest.py --run-dir "$RUN_DIR" --eval-ingest-pending-only
```

成功时 **RUN_DIR** 下应出现目录 `pending_ingest/`（内含若干 `*.json`）和文件 `raw_runs.jsonl`。

**3）阶段二**：新开 Claude Code 会话，粘贴下文「一键话术 · 阶段二」；**RUN_DIR** 由执行者在仓库根用上文「RUN_DIR 自动解析」得到，无需你在对话里事先提供路径。

---

## 阶段一：做题（`_devshell_summary.json` 字段）

每个任务的根目录路径为：**RUN_DIR/workspaces/任务目录名/**。任务目录名与 `pending_ingest` 里 JSON 文件名（不含 `.json`）一致，例如 `SC_struct_007_direct_r0`。

使用 `run_claude_cli_baseline_tasks.py`（推荐，且**仅**适用于本机已安装 `claude` CLI）时，以下步骤**全部自动完成**，无需手动操作。

- **客观耗时**：脚本自动写入 `_cc_baseline_task_start.json`（Unix 毫秒时间戳）。`finalize` 时 `duration_ms` = summary mtime − 该时间戳。
- **做题**：`claude -p` 读取 `_devshell_prompt.txt`，在 workspace 目录内完成交付物。
- **Token 用量**：从 `claude -p --output-format json` 返回的 JSON 中直接提取，包含完整明细。
- **写入 `_devshell_summary.json`**：单行 JSON，UTF-8，字段：
  - `model`、`profile_key`、`route_key`、`status`、`reason`（正常完成填 `"natural"`）、`final_content`、`num_turns`
  - `usage`：包含兼容字段（`prompt_tokens`、`completion_tokens`、`total_tokens`）和详细字段（`input_tokens`、`cache_creation_input_tokens`、`cache_read_input_tokens`、`output_tokens`、`total_cost_usd`、`model_usage`）
  - `claude_cli_meta`：`duration_ms`（API 耗时）、`duration_api_ms`、`session_id`、`stop_reason`、`total_cost_usd`
- 禁止修改、禁止删除 `_eval_task_meta.json`。

最小示例（自动生成，写入文件时压缩为**一行**）：

```json
{"model":"claude-opus-4-6","profile_key":"claude_code","route_key":null,"status":"completed","reason":"natural","final_content":"（摘要）","num_turns":4,"usage":{"prompt_tokens":52622,"completion_tokens":3254,"total_tokens":55876,"input_tokens":5,"cache_creation_input_tokens":3244,"cache_read_input_tokens":49373,"output_tokens":3254,"total_cost_usd":0.126,"model_usage":{"us.anthropic.claude-opus-4-6-v1[1m]":{"inputTokens":5,"outputTokens":3254,"cacheReadInputTokens":49373,"cacheCreationInputTokens":3244,"costUSD":0.126}}},"claude_cli_meta":{"duration_ms":60479,"session_id":"..."}}
```

---

## 阶段二：阅卷与上报（百分制 + 原因 + 建议）

- 先阅读 `evaluation/docs/devshell/devshell_claude_code_eval.md` **第 3 节**，按其中公式用 `scoring_checklist` 的 `weight` 计算 **0–100 的整数**。
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

即时 POST 或未走 pending 时，入库 `score` 可能是 **100/0 代理分**（见 `evaluation.eval_ingest_client.score_for_eval_ingest`）。两阶段 + pending + `eval_ingest_submit_pending.py` 才能把 **真实百分制 + 原因 + 建议** 写入库。

---

## 库里如何认出外部 Baseline

`finalize_external_baseline_ingest.py` 会在入库项上设置 `item.model` 后缀 `| cc_baseline`，以及 `item.extra.matter_eval_source` / `eval_runner` 为 `claude_code_baseline` 等字段；请求体带 `baseline_channel`（`claude_code` / `cursor`）。细节见脚本源码。

---

## 一键话术 · 阶段一（粘贴到 Claude Code / Cursor，由其代跑终端与非交互 `claude`）

**前提**：本机可执行 **`claude` CLI**。若**纯 Cursor、不装 claude CLI**，不要用本段；改用下文 **「一键话术 · 纯 Cursor 阶段一」** 整段。

**意图**：你只复制下面整段到 **Claude Code（或装了 claude CLI 的 Cursor）会话**，由 IDE 里的助手**在仓库根依次执行终端命令**；做题本身由脚本调用 **`claude -p` 非交互模式**完成，**不需要**你再开交互式 Claude Code 逐题手搓。

将下面整段复制到 **Claude Code / Cursor 会话 A**（与阶段二阅卷会话分开即可）。

> **【外部 Baseline · 阶段一 · 执行者】**
> 你的职责：在**本仓库根目录**通过**终端工具**依次执行命令，完成 **prepare → 批量非交互做题（`claude -p`，由 Python 脚本封装）→ finalize**。不要要求用户自己在系统终端里手动复制多段命令；由你执行并汇报 stdout/stderr 与是否成功。
> **前置**：用户本机已安装并可调用 `claude` CLI（非交互）；项目使用 `uv run python` 可运行。若 `claude` 或 `uv` 不可用，先说明并停止，不要编造结果。
> 1. 进入仓库根：`cd "$(git rev-parse --show-toplevel)"`
> 2. **Prepare**（搭工作区；默认会按文档清空 `results/`，勿擅自加 `--no-clean-results` 除非用户明确要求与历史并存）：
> `uv run python evaluation/scripts/devshell/run_devshell_eval.py --prepare-cc-baseline --run-label baseline_cc_struct --modes direct --capabilities structure_construction --eval-ingest-pending-only`
> 若用户要求只跑前 N 题：在 `--eval-ingest-pending-only` **之前**插入 `--limit N`。
> 3. **自动做题 + finalize**（内部对每题调用 `claude -p --output-format json --dangerously-skip-permissions --bare`；token 从 JSON 写入 `_devshell_summary.json`）：
> `uv run python evaluation/scripts/baseline/run_claude_cli_baseline_tasks.py --run-label baseline_cc_struct --finalize --eval-ingest-pending-only`
> 若用户指定部分任务：在本命令中加 `--tasks <task_id> ...`；指定模型：加 `--model opus`（或 `sonnet` 等）。
> 4. 从命令输出或按 `baseline_cc_eval.md`「RUN_DIR 自动解析」确认 **RUN_DIR**；检查 **RUN_DIR** 下存在 **`pending_ingest/`**（含 `.json`）与 **`raw_runs.jsonl`**。
> 5. 在回复中写明：**RUN_DIR 绝对路径**、完成/失败任务摘要、以及下一步「新开会话 + 一键话术 · 阶段二」阅卷上报。

脚本自动为每个任务：(1) 写 `_cc_baseline_task_start.json`；(2) `claude -p --output-format json --dangerously-skip-permissions --bare` 执行题目；(3) 从 JSON 输出提取全部 token 字段写 `_devshell_summary.json`；(4) `--finalize` 自动跑 `finalize_external_baseline_ingest.py`。

完成后 **RUN_DIR** 下应有 `pending_ingest/`（内含 `.json`）和 `raw_runs.jsonl`。**阅卷与上报在新开的 Claude Code 会话中完成**，使用下文「一键话术 · 阶段二」。

---

## 一键话术 · 纯 Cursor 阶段一（无 `claude` CLI；粘贴到 Cursor）

**前提**：本机**没有** Anthropic **`claude` CLI**。**不要**使用上一节「一键话术 · 阶段一」（该节依赖 `claude -p`）。

**意图**：将下面**整段**复制到 **Cursor 会话**（与阶段二阅卷分开）。由助手在仓库根执行终端命令，并在各 `workspaces/<task_id>/` 内完成题目与 `_devshell_summary.json`；**不要**使用 `claude` CLI 或 `run_claude_cli_baseline_tasks.py`。分步说明见上文 **「纯 Cursor baseline（无 Anthropic claude CLI）」**。

> **【外部 Baseline · 纯 Cursor · 阶段一 · 执行者】**
> 你的职责：用户环境**没有** Anthropic **`claude` CLI**。**禁止**调用 `run_claude_cli_baseline_tasks.py`、`claude -p` 或假装已跑通上述命令。在**本仓库根**通过终端完成 **prepare → 逐题在 workspace 内做题并写 `_devshell_summary.json` → finalize**。不要要求用户自己去系统终端里手动复制多段命令；由你执行并汇报 stdout/stderr 与是否成功。
> **前置**：`uv run python` 可用。若不可用，先说明并停止，不要编造结果。
> 1. 进入仓库根：`cd "$(git rev-parse --show-toplevel)"`
> 2. **Prepare**（默认会清空 `results/`；勿擅自加 `--no-clean-results` 除非用户明确要求与历史并存）：
> `uv run python evaluation/scripts/devshell/run_devshell_eval.py --prepare-cc-baseline --run-label baseline_cc_struct --modes direct --capabilities structure_construction --baseline-channel cursor --eval-ingest-pending-only`
> 若用户要求只跑前 N 题：在 `--eval-ingest-pending-only` **之前**插入 `--limit N`。若只跑指定题目：插入 `--questions <question_id> ...`（与文档一致）。
> 3. 用 stderr 的 **`Run directory:`** 或 `baseline_cc_eval.md` 中「RUN_DIR 自动解析」得到 **RUN_DIR** 绝对路径，在回复中写出。
> 4. **对 `RUN_DIR/workspaces/` 下每个任务目录**（目录名为 `task_id`），**按顺序**：
>    - 先执行：`uv run python evaluation/scripts/baseline/mark_external_baseline_task_start.py --workspace "$RUN_DIR/workspaces/<task_id>"`（把 `<task_id>` 换成真实目录名）。
>    - 在本会话中 **@** 该目录，阅读 `_devshell_prompt.txt`，在**该目录内**完成题目要求的交付物；**禁止**修改或删除 `_eval_task_meta.json`。
>    - 完成后在该目录写入**单行** `_devshell_summary.json`（UTF-8）：`profile_key` 为 `"cursor"`，`model` 为本次实际模型名，`status`/`reason` 成功时为 `completed`/`natural`，`final_content` 为简短摘要，`num_turns` 可填估计值；`usage` 尽量填写（若无法获取则在后续阅卷 `--score-reason` 中说明）；**不要**伪造 `claude_cli_meta`，可不写该字段。
> 5. 全部任务的 `_devshell_summary.json` 就绪后 **Finalize**：
> `uv run python evaluation/scripts/baseline/finalize_external_baseline_ingest.py --run-dir "$RUN_DIR" --eval-ingest-pending-only`
> 6. 确认 **RUN_DIR** 下存在 **`pending_ingest/`**（含 `.json`）与 **`raw_runs.jsonl`**；在回复中写明 **RUN_DIR 绝对路径**、各任务完成/失败摘要，并提示下一步：**新开会话**，使用下文「**一键话术 · 阶段二**」做阅卷与上报。

---

## 一键话术 · 阶段二（只阅卷上报；新开会话；**无需**粘贴 RUN_DIR）

将下面整段复制到 **新开的 Claude Code 会话 B**。执行者在仓库根用 `baseline_cc_eval.md` 中「RUN_DIR 自动解析」得到 **RUN_DIR**（与阶段一 `finalize` 所用目录一致；若 `results` 下仅有一个匹配目录，即为该次测评）。

> **【外部 Baseline · 阶段二 · 阅卷者】**
> 你的职责只有：**阅卷**与**调用上报命令**，不修改 **RUN_DIR/workspaces/** 下已有交付物（除非用户明确要求修复明显损坏并说明）。
> **RUN_DIR**：**不要**要求用户粘贴路径；在仓库根按 `evaluation/docs/baseline/baseline_cc_eval.md`「RUN_DIR 自动解析」导出 `RUN_DIR`。若存在多个 `*_` 目录无法唯一确定，请用户确认本次应对应哪一次 run（或提供 stderr / `manifest.json` 所在路径）。
> 前置检查：**RUN_DIR/pending_ingest/** 下至少有一个 `.json` 文件；**RUN_DIR/raw_runs.jsonl** 存在。若不满足，先让用户在仓库根补跑 finalize，不要编造路径。
> 先阅读文件 `evaluation/docs/devshell/devshell_claude_code_eval.md` 的**第 3 节**，严格用其中 **weight 与 0.5×weight** 规则计算每题 **0–100 整数**。
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

将 prepare 命令中的 `--capabilities structure_construction` 删除，或改为 `evaluation/core/schemas.py` 中 `CapabilityLiteral` 允许的其他能力名；阶段一、二话术里凡写「结构生成」处按你的筛选条件改写即可。

纯 Cursor（无 claude CLI）时 prepare 须带 **`--baseline-channel cursor`**；一键整段见 **「一键话术 · 纯 Cursor 阶段一」**。Cursor / Claude Code 可 **@** 本文件与 `evaluation/docs/devshell/devshell_claude_code_eval.md`。
