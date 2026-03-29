# mm-devshell

无需 Redis/MySQL/前端，在终端直接测试 matmaster agent 链路的交互式 CLI。

## LLM 从哪里来

与 **`AgentRunService` 一致**：读取仓库根目录 **`matmaster_config/llm_config.yaml`**，用 **`build_provider`** 构造 `OpenAIProvider`；默认 profile 来自 **`matmaster_config/config.yaml`** 的 **`agents.general.llm`**（若缺省则用 `llm_config.yaml` 的 `default`）。

鉴权与环境变量写在 **`.env`**（或已 export 的环境变量）中，由 **`llm_config.yaml` 里 `${LITELLM_PROXY_API_KEY}` 等** 在加载时展开 —— 与线上一致，无需在 devshell 里再配一套 Key/Base URL。

## 快速开始

### 交互式 REPL（默认）

子命令 **`repl`** 可省略：若第一个参数不是 `run`，会自动当作 **`repl`**（兼容旧用法）。

```bash
# .env 中提供 LiteLLM 代理所需变量（与 llm_config.yaml 中引用一致）
# LITELLM_PROXY_API_KEY=...
# LITELLM_PROXY_API_BASE=...

# --model 为 llm_config.yaml 里 routes 的 key（例如 claude-sonnet-4-6）
mm-devshell --workdir ./workspace --log-dir ./logs --model claude-sonnet-4-6
# 等价于
mm-devshell repl --workdir ./workspace --log-dir ./logs --model claude-sonnet-4-6
```

省略 **`--model`** 时，使用 `config.yaml` 的 `agents.general.llm` 指向的 profile，或 `llm_config.yaml` 顶层的 **`default`**。

可选 **`--config`**：仅覆盖 agent / session / tools（见 `configs/devshell/dev.yaml.example`），**不包含** LLM 连接信息。

```bash
uv run python -m matmaster.devshell --workdir ./workspace --log-dir ./logs --model gemini-3-flash-preview
```

### 单轮非交互 `run`

用于脚本 / CI：执行一条用户 prompt，向 **stdout** 打印 **一行 JSON**（含 `status`、`reason`、`final_content`、`model`、`profile_key` 等），进程退出码 **`0`** 表示 `reason == natural`，否则 **`1`**。

```bash
mm-devshell run --workdir ./ws --log-dir ./logs -p "用一句话介绍你自己"
mm-devshell run --workdir ./ws --log-dir ./logs --prompt-file ./task.txt
# 同时写入文件
mm-devshell run --workdir ./ws --log-dir ./logs -p "hi" --json-out ./summary.json
```

`--prompt` / `-p` 与 **`--prompt-file`** 二选一。

启动时会 **`load_dotenv()`**（当前工作目录下的 `.env`；不覆盖已在 shell 里 export 的变量）。

### 批量跑 MATTER 题库（`evaluation/scripts/run_devshell_eval.py`）

从仓库根目录读取 **`evaluation/question_bank`**（与 MATTER 评估模块相同布局），对每题调用 **`python -u -m matmaster.devshell run`**，**子进程直接继承当前终端的 stdout/stderr**（不经管道转发，避免 `uv run` / IDE 终端里「跑时无输出、结束才刷」）；脚本用 **`--json-out`** 把工作区内的 **`_devshell_summary.json`** 写入 **`raw_runs.jsonl`**；每题的 **`--log-dir`**（即 **`results/.../logs/<task_id>/`**）下会有 **`events_*.jsonl`**（总线事件，与单独 `mm-devshell run` 一致）。不跑 BinaryEvaluator / `run_mat_task`，仅收集 devshell 摘要供人工或后续判分。

```bash
# 仓库根目录；建议与 uv 环境一致
uv run python evaluation/scripts/run_devshell_eval.py --dry-run --limit 5
uv run python evaluation/scripts/run_devshell_eval.py --model claude-sonnet-4-6 --limit 3
```

筛选与 **`evaluation/config.yaml`** 一致（`--eval-config`、`--capabilities`、`--questions` 等）；详见脚本 **`--help`**。

**默认**在跑完后同目录生成 **`claude_review.md`**（单文件汇总，便于 **@** Claude）。不需要再跑第二个命令。

```bash
# 跳过自动生成 Markdown
uv run python evaluation/scripts/run_devshell_eval.py --limit 3 --no-export-review

# 生成 claude_review.md 时附带题库 human_prompt_seed
uv run python evaluation/scripts/run_devshell_eval.py --limit 3 --export-review-with-questions
```

仅补生成或重跑打包时：

```bash
uv run python evaluation/scripts/export_devshell_review_bundle.py --run-dir results/devshell_eval_YYYYMMDD_HHMMSS
uv run python evaluation/scripts/export_devshell_review_bundle.py --run-dir results/devshell_eval_* --with-questions
```

**Claude Code（Cursor Agent）自跑自评：** 判分由对话里的 Agent 执行命令并阅读产物完成，而不是仓库内另一条自动判分流水线。完整说明见 **`evaluation/docs/devshell_claude_code_eval.md`**。

## CLI 参数（`repl` 与 `run` 共用）

| 参数 | 必选 | 说明 |
|------|------|------|
| `--workdir` | 是 | 工作区目录（持久化） |
| `--log-dir` | 是 | 事件日志目录（REPL 下为 JSONL；`run` 仍需要有效路径） |
| `--config` | 否 | 可选 devshell YAML（仅 agent/session/tools） |
| `--model` | 否 | `llm_config.yaml` 中 **routes** 的路由 key；省略则用默认 profile |
| `--session` | 否 | Session 类型：`local` / `docker` / `ssh` |
| `--verbose` | 否 | 详细输出 |

**仅 `run`：** `--prompt` / `-p` 或 `--prompt-file`（必选其一）；`--json-out` 可选，额外把 stdout 那行 JSON 写入文件。

## REPL 命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/config` | 显示当前配置（含解析后的 LLM model / profile / base_url） |
| `/tools` | 列出已注册的工具 |
| `/clear` | 清屏 |
| `/history` | 对话历史摘要 |
| `/verbose` | 切换详细模式 |
| `Ctrl+C` | 取消当前 run |
| `Ctrl+D` | 退出 |

## 可选 devshell YAML

仅用于 **agent（max_turns、identity 等）、session.type、tools.builtin**。示例：`configs/devshell/dev.yaml.example`。

## 架构

```
mm-devshell CLI
  │
  ├── load_llm_config(matmaster_config/llm_config.yaml)
  ├── build_provider(...)     ← 与 AgentRunService 相同
  ├── DevConfig               ← 仅 agent / session / tools（可选 YAML）
  ├── DevRunner               ← PlaygroundContext.llm_config + llm_provider
  ├── DevStreamHook / EventLogger / REPL
```

核心链路：`Exp.build_runtime()` → `AgentKernel.run()` — 与生产一致。

## 事件日志

在 **`--log-dir`** 下写入 **`events_YYYYMMDD_HHMMSS.jsonl`**（一行一条 JSON 事件，与 REPL 相同格式）：

- **`repl`**：整个会话一个文件，多轮对话追加事件。
- **`run`**：单次非交互运行也会写**一个**该文件（此前未接 EventLogger，现已与 REPL 对齐）。

加 **`--verbose`** 时，`run` 会在 stderr 打印本次事件文件路径（`Event log: ...`）。

## 模块说明

| 模块 | 职责 |
|------|------|
| `config.py` | `DevConfig`（非 LLM）+ `load_dev_config()` |
| `runner.py` | `DevRunner`、history |
| `stream_hook.py` | 终端流式输出 |
| `event_logger.py` | JSONL |
| `repl.py` | REPL |
| `cli.py` | 入口、`build_provider` |
| `__main__.py` | `python -m matmaster.devshell` |
