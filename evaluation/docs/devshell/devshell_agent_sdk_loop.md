# DevShell 外层编排：Claude Agent SDK

在「批量跑 `mm-devshell` → 用 `score_devshell_tasks.py` 自动评分 → 改 MatMaster 提示词/工具 → 再跑」的闭环上，本仓库提供基于 **Claude Agent SDK**（内置 Claude Code 工具集）的编排入口，替代纯 `claude -p` 对话驱动。

## 依赖

```bash
uv sync --extra eval-agent
```

需已登录/可用的 Claude Code / Anthropic 凭据（与官方 Agent SDK 要求一致）。

## 入口

在**仓库根**执行：

```bash
uv run python evaluation/scripts/devshell/run_devshell_agent_loop.py \
  --max-iterations 3 \
  --target-mean-score 80 \
  --modes direct \
  --jobs 4 \
  --limit 2 \
  --questions SC_struct_007
```

- **会话目录**（默认 `results/devshell_agent_loop_<UTC>/`）：写入 `session_manifest.json`、`outcomes.jsonl`，评测子目录在 `eval_runs/<iteration_tag>/`。
- **内层评测**：由工具 `run_devshell_eval` 子进程调用 `evaluation/scripts/devshell/run_devshell_eval.py`（默认 `--no-clean-results` 且显式 `--output-dir`，避免清空整个 `results/`）。
- **判分与改仓库**：由 SDK 会话先调用仓库脚本 `evaluation/scripts/devshell/score_devshell_tasks.py --dry-run` 获取真实分数，再视需要检查低分任务的 workspace / events；原则与 [devshell_claude_code_eval.md](devshell_claude_code_eval.md) 一致。
- **每轮结束**：模型应调用 `report_iteration_outcome`；外层在 `macro_mean_0_100 >= --target-mean-score` 或 `target_met` 时提前停止。

## 与「仅 Claude Code 文档驱动」的关系

- [devshell_claude_code_eval.md](devshell_claude_code_eval.md)：适用于在 IDE 里跑 `run_devshell_eval.py` 后，再用 `score_devshell_tasks.py` 自动评分、上报的流程。
- 本文档：**程序化**外层循环与工具白名单（`allowed_tools`），便于重复实验与稍后的 CI 集成。

## 常用参数

| 参数 | 含义 |
|------|------|
| `--session-dir` | 指定会话根目录（默认时间戳目录） |
| `--max-iterations` | 最大外层轮数 |
| `--target-mean-score` | 宏平均目标分 (0–100)，达到即停 |
| `--permission-mode` | 默认 `acceptEdits`；见官方 Agent SDK 文档 |
| `--max-sdk-turns` | 每轮允许的最大 SDK turn |
| `--extra-instruction` | 附加到每轮 user 消息的约束/重点 |
| `--eval-ingest-pending-only` / `--no-eval-ingest-pending-only` | 与 `run_devshell_eval.py` 一致 |

其余 `--modes`、`--jobs`、`--limit`、`--questions`、`--capabilities`、`--model`、`--exp`、`--eval-config`、`--task-timeout` 等均转发给内层 `run_devshell_eval.py`。

## 实现位置

- `evaluation/devshell_agent/subprocess_runner.py`：`DevshellEvalSubprocess`（组装 argv、子进程、`RunDevshellEvalParams`）
- `evaluation/devshell_agent/sdk_tools.py`：`MatmasterEvalMcpToolkit`（`build_mcp_server` / `allowed_tool_names`）
- `evaluation/devshell_agent/loop.py`：`DevshellAgentLoop`（`ClaudeSDKClient` 多轮 `query`）、`AgentLoopConfig`
- `evaluation/scripts/devshell/run_devshell_agent_loop.py`：`DevshellAgentLoopCli`（argparse + `DevshellAgentLoop.run_sync`）
