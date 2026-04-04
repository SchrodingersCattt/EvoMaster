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
- **双 Agent（防作弊）**：主 Agent **禁止** Edit/Write `evaluation/question_bank/**`；若认为 `scoring_checklist` / `reference_answers` 不公或错误，应调用 MCP 工具 **`escalate_checklist_revision`**。编排器在本轮主会话结束后，若队列非空，再启**第二个** SDK 会话（checklist 专责）：`allowed_tools` 仅含 `report_checklist_revision` + 读写工具，系统提示约束**只能**改 `evaluation/question_bank/`。关闭：`--no-enable-checklist-agent`。
- **提示词优化策略与体量**：主 Agent 系统提示要求**先删减/合并重复或矛盾表述再增补**；完整初始系统 prompt（`system_prompt` + `developer_instructions` + tool descriptions + skill meta，即 `ContextBuilder.build()` 产出，gpt-4o tiktoken）**推荐 ≤ 12000**，且**硬上限 ≤ 15000**。自检：`uv run python -m evaluation.devshell_agent.exp_prompt_budget <exp>`。
- **判分与改仓库**：由 SDK 会话先调用仓库脚本 `evaluation/scripts/devshell/score_devshell_tasks.py --dry-run` 获取真实分数，再视需要检查低分任务的 workspace / events；原则与 [devshell_claude_code_eval.md](devshell_claude_code_eval.md) 一致。
- **每轮结束**：模型应调用 `report_iteration_outcome`；外层在 `macro_mean_0_100 >= --target-mean-score` 或 `target_met` 时提前停止。
- **每轮 ingest 上报**：在 **`--eval-ingest-pending-only`**（默认）下，外层在主 Agent 回合结束后、**checklist 专责回合开始前**，对本轮**每一次** `run_devshell_eval` 的输出目录（按顺序、去重）分别执行 `score_devshell_tasks.py --submit`（写回 `pending_ingest` 分数并 POST），保证题库尚未被 checklist 改写时与 `raw_runs` 中的 `question_id` 一致，且中间 tag（如先 `iter_01` 再 `iter_01b`）不会只上报最后一次。日志追加到会话目录 `ingest_submit.jsonl`。若内层已改为即时 POST（`--no-eval-ingest-pending-only`），则不再自动 `--submit`，以免重复。关闭自动上报：`--no-eval-ingest-submit-each-iteration`；超时：`--eval-ingest-submit-timeout`。

### Git：每改一次提交，无效则回滚

- **系统提示**要求：每次 `Edit`/`Write` 后单独 `git commit`（消息建议带 `devshell_agent iter=…`），便于按条回滚；若某次改动经复评宏平均相对改动前**没有变好**，应对**该 commit** `git revert`（或本地未 push 时用 `git reset --hard HEAD~1`）。
- **编排层保险**（默认开启）：每轮开始前记录 `HEAD` 写入会话目录 `git_iteration_heads.jsonl`；若本轮 `report_iteration_outcome` 的宏平均**严格低于**上一轮，则对该仓库执行 `git reset --hard` 到**本轮开始**时的 `HEAD`（撤销本轮全部未达标退化）。关闭：`--no-git-reset-on-regression`。

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
| `--enable-checklist-agent` / `--no-enable-checklist-agent` | 是否在本轮主 Agent 之后按需运行题库专责 Agent（默认开） |
| `--max-checklist-sdk-turns` | 题库专责会话的最大 turn |
| `--checklist-permission-mode` | 专责会话的 `permission_mode`（默认与 `--permission-mode` 相同） |
| `--extra-instruction` | 附加到每轮 user 消息的约束/重点 |
| `--eval-ingest-pending-only` / `--no-eval-ingest-pending-only` | 与 `run_devshell_eval.py` 一致 |

其余 `--modes`、`--jobs`、`--limit`、`--questions`、`--capabilities`、`--model`、`--exp`、`--eval-config`、`--task-timeout` 等均转发给内层 `run_devshell_eval.py`。

## 实现位置

- `evaluation/devshell_agent/subprocess_runner.py`：`DevshellEvalSubprocess`（组装 argv、子进程、`RunDevshellEvalParams`）
- `evaluation/devshell_agent/sdk_tools.py`：`MatmasterEvalMcpToolkit`（`build_mcp_server` / `allowed_tool_names`）
- `evaluation/devshell_agent/loop.py`：`DevshellAgentLoop`（`ClaudeSDKClient` 多轮 `query`）、`AgentLoopConfig`
- `evaluation/scripts/devshell/run_devshell_agent_loop.py`：`DevshellAgentLoopCli`（argparse + `DevshellAgentLoop.run_sync`）
