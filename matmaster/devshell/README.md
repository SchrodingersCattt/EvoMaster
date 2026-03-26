# mm-devshell

无需 Redis/MySQL/前端，在终端直接测试 matmaster agent 链路的交互式 CLI。

## 快速开始

```bash
# 1. 设置 API Key
export OPENAI_API_KEY=sk-xxx

# 2. 准备配置（可选，有内置默认值）
cp configs/devshell/dev.yaml.example configs/devshell/dev.yaml
# 编辑 dev.yaml 调整 model、base_url 等

# 3. 启动
mm-devshell --workdir ./workspace --log-dir ./logs

# 或使用自定义配置
mm-devshell --workdir ./workspace --log-dir ./logs --config configs/devshell/dev.yaml

# 或通过 python -m 启动
uv run python -m matmaster.devshell --workdir ./workspace --log-dir ./logs
```

## CLI 参数

| 参数 | 必选 | 说明 |
|------|------|------|
| `--workdir` | 是 | 工作区目录（持久化，agent 文件操作在此目录下） |
| `--log-dir` | 是 | 事件日志目录（JSONL 格式） |
| `--config` | 否 | YAML 配置文件路径，不指定则使用内置默认值 |
| `--session` | 否 | Session 类型覆盖：`local` / `docker` / `ssh` |
| `--verbose` | 否 | 启用详细输出 |

## REPL 命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/config` | 显示当前配置 |
| `/tools` | 列出已注册的工具 |
| `/clear` | 清屏 |
| `/history` | 显示对话历史摘要 |
| `/verbose` | 切换详细模式 |
| `Ctrl+C` | 取消当前 agent 运行 |
| `Ctrl+D` | 退出 |

## 配置

配置文件为 YAML 格式，支持 `${ENV_VAR}` 环境变量展开。完整示例见 `configs/devshell/dev.yaml.example`。

```yaml
llm:
  api_key: ${OPENAI_API_KEY}
  base_url: "https://api.openai.com/v1"
  model: "gpt-4o"
  temperature: 0.7

agent:
  name: "general"
  mode: "direct"
  max_turns: 20
  identity: "You are a materials science AI assistant."

session:
  type: "local"

tools:
  builtin:
    - "*"
```

不提供 `--config` 时，默认使用 `gpt-4o` + `direct` 模式 + 20 轮上限 + 全部内建工具。API Key 从 `OPENAI_API_KEY` 环境变量读取。

## 架构

```
mm-devshell CLI
  │
  ├── DevConfig        ← YAML 配置加载 + Pydantic 校验
  ├── DevRunner        ← 每轮组装 Exp → build_runtime → kernel.run
  │     ├── PlaygroundContext（手动构造，不依赖 Playground 类）
  │     └── history 累积（多轮对话）
  ├── DevStreamHook    ← 实时终端输出（流式文本、工具调用、guard 拦截）
  ├── EventLogger      ← JSONL 事件持久化（thought 合并、assistant_state 跳过）
  └── REPL             ← 输入循环 + 内建命令 + SIGINT 取消
```

核心复用路径：`Exp.build_runtime()` → `AgentKernel.run()` — 与生产环境 `AgentRunService` 共享同一套 kernel/exp 管线。

## 事件日志

每个 REPL 会话生成一个 `events_YYYYMMDD_HHMMSS.jsonl` 文件，每行一个 JSON 记录：

```jsonl
{"type":"tool_call","tool":"bash","call_id":"tc-1","args":{"command":"ls"},"ts":"...","run_id":"run-001"}
{"type":"tool_result","tool":"bash","call_id":"tc-1","content":"file1.py","ts":"...","run_id":"run-001"}
{"type":"thought","content":"Let me check the files...","ts":"...","run_id":"run-001"}
{"type":"run_result","status":"completed","reason":"natural","ts":"...","run_id":"run-001"}
```

## 模块说明

| 模块 | 职责 |
|------|------|
| `config.py` | `DevConfig` Pydantic 模型 + `load_dev_config()` YAML 加载 |
| `runner.py` | `DevRunner` 每轮组装运行时，管理多轮 history |
| `stream_hook.py` | `DevStreamHook` 终端实时输出格式化 |
| `event_logger.py` | `EventLogger` JSONL 事件写入，streaming thought 合并 |
| `repl.py` | REPL 循环、命令解析、线程管理、SIGINT 处理 |
| `cli.py` | argparse 入口、配置加载、provider 创建 |
| `__main__.py` | `python -m matmaster.devshell` 支持 |
