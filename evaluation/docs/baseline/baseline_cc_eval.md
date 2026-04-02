# Claude Code Baseline 测评（与 DevShell 对照）

目标：用 **Claude Code**（或同布局的 **Cursor / Codex** 等）直接完成题库任务（不跑 `mm-devshell`），产物与 `evaluation/scripts/devshell/run_devshell_eval.py` / `evaluation/scripts/baseline/finalize_external_baseline_ingest.py` 对齐，便于和 MatMaster kernel 对比。入库时由 `manifest` / `--baseline-channel` 区分 `claude_code`、`cursor` 与 `codex`。**不安装 Anthropic claude CLI、只在 Cursor / Codex 里做题**时：直接复制文末对应的 **「一键话术 · 纯 Cursor 阶段一」** 或 **「一键话术 · 纯 Codex 阶段一」**；分步说明见下文相应章节。

**术语（全文统一）**

- **仓库根**：本 Git 仓库根目录；MATTER 评测代码与题库在 `evaluation/`（跑题脚本在 `evaluation/scripts/`）。在终端可用 `cd "$(git rev-parse --show-toplevel)"` 进入。
- **RUN_DIR**：本次测评产物根目录（其下有 `workspaces/` 等）。**推荐不手工填写**：默认 `prepare` 会清空 `results/`，可在仓库根用下面「RUN_DIR 自动解析」中的一行命令得到绝对路径；若曾用 `--no-clean-results` 导致同前缀目录多个并存，再以 stderr 里 **`Run directory: `** 冒号后的路径为准。

流程分两阶段：

| 阶段 | 执行方式 | 职责 |
|------|----------|------|
| **阶段一** | **有 claude CLI**：终端跑脚本，或将「一键话术 · 阶段一」交给 IDE 代跑（`run_claude_cli_baseline_tasks.py` → `claude -p`）。**纯 Cursor / Codex**：复制文末对应的「**一键话术 · 纯 Cursor 阶段一**」或「**一键话术 · 纯 Codex 阶段一**」，不跑 `run_claude_cli_baseline_tasks.py`。 | **做题**并留下与 DevShell 对齐的 summary；CLI 路径下 token 来自 `claude -p --output-format json`。 |
| **阶段二** | 在仓库根执行一条命令（无需新开 IDE 会话） | **自动评分与上报**：`score_baseline_tasks.py` 用与 MatMaster 相同的 `BinaryEvaluator` + 结构验证器（pymatgen）对各 workspace 评分，自动将分数写入 `pending_ingest/` 并可一键提交。 |

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

与本文「阶段二：自动评分与上报」相同——在仓库根运行 `score_baseline_tasks.py`；详见上文该节与文末 **「一键话术 · 阶段二」**。

---

## 纯 Codex baseline（无 Anthropic claude CLI）

本机**没有**、也**不打算安装**可执行文件 `claude`，且阶段一由 **Codex** 在仓库里直接做题时，流程与纯 Cursor 相同：**prepare 与 finalize 仍用 `uv run python`**，题目在 Codex 对话里按 `_devshell_prompt.txt` 完成，**自行写入** `_devshell_summary.json`。

**与 CLI 自动化路径的差异**

| 步骤 | 纯 Codex |
|------|-----------|
| Prepare | 在下列命令中增加 **`--baseline-channel codex`**，使 `manifest.json` 记录 `baseline_channel: "codex"`，与入库一致。 |
| 做题 | 在 Codex 中针对 **`RUN_DIR/workspaces/<task_id>/`** 工作，阅读 `_devshell_prompt.txt` 并在该目录完成交付物；勿修改或删除 `_eval_task_meta.json`。 |
| Token / 耗时 | 无 `claude -p` JSON；`usage` 可按 Codex 可见信息尽量填写，若无可靠 token 数据则在阶段二 `score_reason` 中说明。客观耗时仍依赖 **`mark_external_baseline_task_start.py`**。 |
| Finalize | 只跑 `finalize_external_baseline_ingest.py`，**不要**跑 `run_claude_cli_baseline_tasks.py`。 |

**1）Prepare（结构生成类示例）**

在本文「阶段之间：终端命令（结构生成类）」一节的 prepare 命令上，于 `--eval-ingest-pending-only` **之前**加入 `--baseline-channel codex`，例如：

```bash
cd "$(git rev-parse --show-toplevel)"
uv run python evaluation/scripts/devshell/run_devshell_eval.py --prepare-cc-baseline --run-label baseline_cc_struct \
  --modes direct --capabilities structure_construction --baseline-channel codex --eval-ingest-pending-only
```

仅部分题目时仍可用 `--limit N` 或 `--questions <question_id> ...`。

**2）每题开始前：打墙钟起点（与 finalize 中 `duration_ms` 对齐）**

```bash
uv run python evaluation/scripts/baseline/mark_external_baseline_task_start.py \
  --workspace "$RUN_DIR/workspaces/<task_id>"
```

将 `<task_id>` 换成实际目录名。`RUN_DIR` 用「RUN_DIR 自动解析」导出。

**3）在 Codex 中做题**

- 阅读该目录下的 `_devshell_prompt.txt`，在同一目录内生成题目要求的文件。
- 完成后写入 **`_devshell_summary.json`**（单行 JSON，UTF-8），字段与本文「阶段一：做题（`_devshell_summary.json` 字段）」一致，建议：
  - `profile_key`: `"codex"`
  - `model`: 本次实际使用的模型名
  - `status` / `reason`: 正常完成用 `"completed"` / `"natural"`
  - `final_content`: 简短摘要
  - `num_turns`: 对话轮数或合理估计
  - `usage`: 尽量填写；若拿不到可靠 token 数据可省略或填 `0`，并在后续 `eval_ingest_submit_pending.py` 的 `--score-reason` 中说明
  - **不要**伪造 `claude_cli_meta`

**4）Finalize**

全部题目的 `_devshell_summary.json` 就绪后，在仓库根执行：

```bash
cd "$(git rev-parse --show-toplevel)"
uv run python evaluation/scripts/baseline/finalize_external_baseline_ingest.py --run-dir "$RUN_DIR" --eval-ingest-pending-only
```

`baseline_channel` 默认从 `manifest.json` 读取；若曾手工改动 manifest，可显式追加 `--baseline-channel codex`。

**5）阶段二**

与本文「阶段二：自动评分与上报」相同——在仓库根运行 `score_baseline_tasks.py`；详见上文该节与文末 **「一键话术 · 阶段二」**。

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

**3）阶段二：自动评分与上报**

`finalize` 完成后，在仓库根直接运行（**无需**新开 IDE 会话）：

```bash
cd "$(git rev-parse --show-toplevel)"
# 仅打印分数，不写文件（dry-run 确认）
uv run python evaluation/scripts/baseline/score_baseline_tasks.py \
  --run-label baseline_cc_struct --dry-run

# 写分数到 pending_ingest/*.json 并自动提交到 ingest API
uv run python evaluation/scripts/baseline/score_baseline_tasks.py \
  --run-label baseline_cc_struct --submit
```

- 脚本使用与 MatMaster 完全相同的 `BinaryEvaluator`：`struct_file_*` 验证器直接用 pymatgen 解析 workspace 内的 CIF/POSCAR；`llm_binary_judge` 调用 `evaluator_llm`（见 `evaluation/config.yaml`）。
- `--dry-run`：只打印每题的分数与 per-criterion 判定，不修改任何文件，不提交。
- `--submit`：将算出的分数写入 `pending_ingest/<task_id>.json` 并调用 ingest API POST。若暂不提交，可省略 `--submit`，稍后用 `eval_ingest_submit_pending.py` 手动逐题提交。
- 需要 `evaluator_llm` 时（题目含 `llm_binary_judge`），确保 `evaluation/config.yaml` 里 `evaluator_llm` 配置正确，或相关环境变量已设置（`LITELLM_PROXY_API_KEY` 等）。未配置时 `llm_binary_judge` 条目自动标记为 `fail`（reason: `no evaluator LLM configured`）。
- 如需只评部分题目：加 `--tasks SC_struct_007_direct_r0 SC_struct_008_direct_r0`。
- 如需显式指定 RUN_DIR：用 `--run-dir "$RUN_DIR"` 替换 `--run-label`。

常用选项：

| 选项 | 说明 |
|------|------|
| `--run-dir "$RUN_DIR"` | 显式指定 RUN_DIR |
| `--run-label baseline_cc_struct` | 从 `results/` 自动检测最新匹配目录 |
| `--eval-config evaluation/config.yaml` | 指定 evaluator_llm 配置（默认即此路径） |
| `--tasks <task_id> ...` | 只评指定 task（空格分隔） |
| `--dry-run` | 打印分数，不写文件，不提交 |
| `--submit` | 写分数到 pending JSON 并 POST 到 ingest API |
| `--eval-ingest-timeout 120` | 每题提交 HTTP 超时秒数 |

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

## 阶段二：自动评分与上报（BinaryEvaluator）

阶段二**不再需要人工阅卷**——改为运行 `score_baseline_tasks.py`，该脚本使用与 MatMaster 自动评测完全相同的 `BinaryEvaluator`：

- `struct_file_*` 验证器（`check_atom_count`、`check_formula`、`check_bond_length` 等）调用 **pymatgen** 直接解析 workspace 内的 CIF / POSCAR，结果与 MatMaster kernel 评分完全一致。
- `llm_binary_judge` 调用 `evaluator_llm`（`evaluation/config.yaml`）做二元判定。
- `tool_called` / `tool_args_match` 等依赖 EvoMaster 工具轨迹的 verify 类型：baseline 无轨迹，自动判 `fail`（grounding 轴）——这是预期行为，因为 baseline 不走 MatMaster 的工具体系。

**运行命令**（在仓库根，`finalize` 完成后执行）：

```bash
cd "$(git rev-parse --show-toplevel)"

# Step 1: dry-run 确认分数（不写文件，不提交）
uv run python evaluation/scripts/baseline/score_baseline_tasks.py \
  --run-label baseline_cc_struct --dry-run

# Step 2: 写分数到 pending JSON 并提交到 ingest API
uv run python evaluation/scripts/baseline/score_baseline_tasks.py \
  --run-label baseline_cc_struct --submit
```

- 成功提交时各题输出 `[ingest] <task_id> ok`。
- 若仅写分数到 `pending_ingest/` 而不立即提交（稍后手动 review），省略 `--submit`，之后用 `eval_ingest_submit_pending.py` 逐题提交。
- 需要 pymatgen：`uv sync --extra calculation`（否则 `struct_file_*` 类验证器报 `pymatgen not installed`）。

**手动补充 suggestion**（可选）：`score_baseline_tasks.py` 目前不写 `suggestion`。若需要填写可执行改进，仍可在 `--submit` 之前或之后用 `eval_ingest_submit_pending.py --pending <path> --score <已算出的分> --suggestion "..."` 覆盖提交。

---

## 为何不要在一个会话里又做题又打分？

即时 POST 或未走 pending 时，入库 `score` 可能是 **100/0 代理分**（见 [`evaluation.eval_ingest_client.score_for_eval_ingest`](evaluation/eval_ingest_client.py)）。两阶段 + pending + `score_baseline_tasks.py --submit` 才能把 **BinaryEvaluator 真实算出的百分制 + per-criterion 判定** 写入库。

---

## 库里如何认出外部 Baseline

`finalize_external_baseline_ingest.py` 会在入库项上设置 `item.model` 后缀 `| cc_baseline`，以及 `item.extra.matter_eval_source` / `eval_runner` 为 `claude_code_baseline` 等字段；请求体带 `baseline_channel`（`claude_code` / `cursor` / `codex`）。细节见脚本源码。

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

## 一键话术 · 纯 Codex 阶段一（无 `claude` CLI；粘贴到 Codex）

**前提**：本机**没有** Anthropic **`claude` CLI**，或本轮明确要求由 **Codex** 直接完成阶段一。**不要**使用上文依赖 `claude -p` 的阶段一脚本。

**意图**：将下面**整段**复制到 **Codex 会话**（与阶段二阅卷分开）。由助手在仓库根执行终端命令，并在各 `workspaces/<task_id>/` 内完成题目与 `_devshell_summary.json`；**不要**使用 `claude` CLI 或 `run_claude_cli_baseline_tasks.py`。

> **【外部 Baseline · 纯 Codex · 阶段一 · 执行者】**
> 你的职责：用户本轮要用 **Codex** 直接完成外部 baseline 的阶段一。**禁止**调用 `run_claude_cli_baseline_tasks.py`、`claude -p`，也不要假装这些命令已经执行成功。在**本仓库根**通过终端完成 **prepare → 逐题在 workspace 内做题并写 `_devshell_summary.json` → finalize**。不要把多段命令甩给用户手动执行；由你执行并汇报 stdout/stderr 与是否成功。
> **前置**：`uv run python` 可用。若不可用，先说明并停止，不要编造结果。
> 1. 进入仓库根：`cd "$(git rev-parse --show-toplevel)"`
> 2. **Prepare**（默认会清空 `results/`；勿擅自加 `--no-clean-results`，除非用户明确要求保留历史结果）：
> `uv run python evaluation/scripts/devshell/run_devshell_eval.py --prepare-cc-baseline --run-label baseline_cc_struct --modes direct --capabilities structure_construction --baseline-channel codex --eval-ingest-pending-only`
> 若用户要求只跑前 N 题：在 `--eval-ingest-pending-only` **之前**插入 `--limit N`。若只跑指定题目：插入 `--questions <question_id> ...`。
> 3. 用 stderr 的 **`Run directory:`** 或 `baseline_cc_eval.md` 中「RUN_DIR 自动解析」得到 **RUN_DIR** 绝对路径，并在回复中写出。
> 4. **对 `RUN_DIR/workspaces/` 下每个任务目录**（目录名为 `task_id`），**按顺序**：
>    - 先执行：`uv run python evaluation/scripts/baseline/mark_external_baseline_task_start.py --workspace "$RUN_DIR/workspaces/<task_id>"`。
>    - 阅读该目录内 `_devshell_prompt.txt`，在**该目录内**完成题目要求的交付物；**禁止**修改或删除 `_eval_task_meta.json`。
>    - 完成后在该目录写入**单行** `_devshell_summary.json`（UTF-8）：`profile_key` 为 `"codex"`，`model` 为本次实际模型名，`status`/`reason` 成功时为 `completed`/`natural`，`final_content` 为简短摘要，`num_turns` 可填估计值；`usage` 尽量填写，若无法可靠获取则在后续阅卷 `--score-reason` 中说明；**不要**伪造 `claude_cli_meta`。
> 5. 全部任务的 `_devshell_summary.json` 就绪后 **Finalize**：
> `uv run python evaluation/scripts/baseline/finalize_external_baseline_ingest.py --run-dir "$RUN_DIR" --eval-ingest-pending-only`
> 6. 确认 **RUN_DIR** 下存在 **`pending_ingest/`**（含 `.json`）与 **`raw_runs.jsonl`**；在回复中写明 **RUN_DIR 绝对路径**、各任务完成/失败摘要，并提示下一步：**新开会话**，使用下文「**一键话术 · 阶段二**」做阅卷与上报。

---

## 一键话术 · 阶段二（自动评分提交；**无需**新开 IDE 会话）

阶段二改为在终端运行 `score_baseline_tasks.py`，**不再需要人工阅卷**。将下面整段复制到任意 IDE 会话（或直接在终端执行）。

> **【外部 Baseline · 阶段二 · 自动评分者】**
> 你的职责：在**本仓库根**通过**终端工具**依次执行命令，对 baseline workspace 自动评分并提交到 ingest API。不要人工阅读 CIF/POSCAR 判分，也不要调用 `eval_ingest_submit_pending.py` 手动赋分——评分由 `BinaryEvaluator` 程序化完成。
> **前置**：`uv run python` 可用；已运行过 `finalize_external_baseline_ingest.py`（`RUN_DIR/pending_ingest/` 存在）；pymatgen 已安装（`uv sync --extra calculation`，否则 `struct_file_*` 验证器不可用）。
> 1. 进入仓库根：`cd "$(git rev-parse --show-toplevel)"`
> 2. 解析 RUN_DIR（与阶段一 finalize 所用目录一致）：
>    ```bash
>    ROOT="$(git rev-parse --show-toplevel)"
>    export RUN_DIR="$(find "$ROOT/results" -maxdepth 1 -type d -name 'baseline_cc_struct_*' | sort | tail -1)"
>    echo "$RUN_DIR"
>    ```
>    若存在多个目录或改了 `--run-label`，用对应前缀，或直接用 `--run-dir "$RUN_DIR"`。
> 3. **Dry-run 确认**（打印每题分数，不修改文件）：
>    `uv run python evaluation/scripts/baseline/score_baseline_tasks.py --run-label baseline_cc_struct --dry-run`
>    检查各题 score 与 per-criterion 判定是否合理（`✓ pass` / `✗ fail`）。若有 `pymatgen not installed` 报错，先补安装：`uv sync --extra calculation`。
> 4. **正式评分 + 提交**：
>    `uv run python evaluation/scripts/baseline/score_baseline_tasks.py --run-label baseline_cc_struct --submit`
>    各题输出 `[ingest] <task_id> ok` 表示提交成功。
> 5. 在回复中输出表格：列包括 **question_id**、**task_id**、**score/100**、**主要 pass/fail 条目**；若有多题，最后一行给出 **宏平均（整数，四舍五入）**。

---

## 附录：不限结构生成类时

将 prepare 命令中的 `--capabilities structure_construction` 删除，或改为 `evaluation/core/schemas.py` 中 `CapabilityLiteral` 允许的其他能力名；阶段一、二话术里凡写「结构生成」处按你的筛选条件改写即可。

纯 Cursor / Codex（无 claude CLI）时 prepare 须分别带 **`--baseline-channel cursor`** / **`--baseline-channel codex`**；一键整段见 **「一键话术 · 纯 Cursor 阶段一」** 与 **「一键话术 · 纯 Codex 阶段一」**。Cursor / Claude Code / Codex 均可引用本文件与 `evaluation/docs/devshell/devshell_claude_code_eval.md`。
