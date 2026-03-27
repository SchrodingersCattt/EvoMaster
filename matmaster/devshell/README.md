# mm-devshell

无需 Redis/MySQL/前端，在终端直接测试 matmaster agent 链路的交互式 CLI。

## LLM 从哪里来

与 **`AgentRunService` 一致**：读取仓库根目录 **`matmaster_config/llm_config.yaml`**，用 **`build_provider`** 构造 `OpenAIProvider`；默认 profile 来自 **`matmaster_config/config.yaml`** 的 **`agents.general.llm`**（若缺省则用 `llm_config.yaml` 的 `default`）。

鉴权与环境变量写在 **`.env`**（或已 export 的环境变量）中，由 **`llm_config.yaml` 里 `${LITELLM_PROXY_API_KEY}` 等** 在加载时展开 —— 与线上一致，无需在 devshell 里再配一套 Key/Base URL。

## 快速开始

```bash
# .env 中提供 LiteLLM 代理所需变量（与 llm_config.yaml 中引用一致）
# LITELLM_PROXY_API_KEY=...
# LITELLM_PROXY_API_BASE=...

# --model 为 llm_config.yaml 里 routes 的 key（例如 claude-sonnet-4-6）
mm-devshell --workdir ./workspace --log-dir ./logs --model claude-sonnet-4-6
```

省略 **`--model`** 时，使用 `config.yaml` 的 `agents.general.llm` 指向的 profile，或 `llm_config.yaml` 顶层的 **`default`**。

可选 **`--config`**：仅覆盖 agent / session / tools（见 `configs/devshell/dev.yaml.example`），**不包含** LLM 连接信息。

```bash
uv run python -m matmaster.devshell --workdir ./workspace --log-dir ./logs --model gemini-3-flash-preview
```

启动时会 **`load_dotenv()`**（当前工作目录下的 `.env`；不覆盖已在 shell 里 export 的变量）。

## CLI 参数

| 参数 | 必选 | 说明 |
|------|------|------|
| `--workdir` | 是 | 工作区目录（持久化） |
| `--log-dir` | 是 | 事件日志目录（JSONL） |
| `--config` | 否 | 可选 devshell YAML（仅 agent/session/tools） |
| `--model` | 否 | `llm_config.yaml` 中 **routes** 的路由 key；省略则用默认 profile |
| `--session` | 否 | Session 类型：`local` / `docker` / `ssh` |
| `--verbose` | 否 | 详细输出 |

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

每个会话生成 `events_YYYYMMDD_HHMMSS.jsonl`，一行一条 JSON 事件。

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
