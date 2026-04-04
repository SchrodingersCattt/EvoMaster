"""Claude Agent SDK session: multi-iteration DevShell eval → judge → edit loop."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from evaluation.devshell_agent.config_state import (
    AgentLoopSharedState,
    DevshellAgentCliDefaults,
)
from evaluation.devshell_agent.git_iteration import (
    append_iteration_head,
    git_reset_hard,
    git_rev_parse_head,
    head_at_iteration_start,
)


@dataclass
class AgentLoopConfig:
    repo_root: Path
    session_dir: Path
    defaults: DevshellAgentCliDefaults
    max_iterations: int
    target_mean_score: int
    permission_mode: str
    max_sdk_turns: int
    extra_instruction: str = ""
    git_reset_on_regression: bool = True


class DevshellAgentLoop:
    """Runs the Claude SDK client for multiple outer iterations."""

    SYSTEM_PROMPT = """你是 MatMaster 仓库内的 **DevShell 评测迭代编排助手**。

## 工具分工
- **run_devshell_eval**：在仓库根目录下执行 `evaluation/scripts/devshell/run_devshell_eval.py`（子进程，优先 `uv run python`）。输出目录为会话下的 `eval_runs/<iteration_tag>/`。
- **report_iteration_outcome**：每一轮结束时**必须**调用一次，记录宏平均分数与是否达标。
- **Read / Glob / Grep / Edit / Write / Bash**：用于阅读题库与产物、修改配置与提示词。Bash 用于 `git` 与必要命令；避免与本流程无关的破坏性操作。

## Git 工作流（自迭代必守）
- **每次实质性修改**（每次 `Edit`/`Write` 落盘后）：对相应文件 `git add` 并 **`git commit` 一条独立记录**，消息建议 `devshell_agent iter=<轮次> <简述>`，使改动与 commit 一一对应、便于回滚。
- **判断单次改动是否改善**：在该次改动前记下当时的宏平均（来自 `score_devshell_tasks.py`）；改动并 commit 后，若需用分数验证，应再次对**能反映新代码**的产物跑分（通常需新的 `run_devshell_eval` + `iteration_tag`，或按题库说明复评）。若新宏平均 **不高于** 改动前基准（改善无效），应回滚**该条** commit：优先 `git revert HEAD --no-edit`；若该 commit 尚未 push 且历史仅本地迭代，可用 `git reset --hard HEAD~1`。
- 不要用 `git push --force` 等破坏协作历史的操作。

## 判分原则（与 `evaluation/docs/devshell/devshell_claude_code_eval.md` 一致）
- 优先使用仓库脚本 `evaluation/scripts/devshell/score_devshell_tasks.py` 自动评分；它会基于 `raw_runs.jsonl`、`workspaces/<task_id>/` 与 `logs/<task_id>/events_*.jsonl` 调用同一套 `BinaryEvaluator`。
- 宏平均以 `score_devshell_tasks.py` 输出为准；不要手工估算一个与脚本不一致的分数。
- 如需解释低分原因，可再阅读题库 YAML、workspace 交付物和事件日志；**不得**仅凭 `devshell_summary` / `final_content` 断言 checklist 通过。
- 若使用 `--eval-ingest-pending-only`：优先通过 `score_devshell_tasks.py --submit` 写回并提交 `pending_ingest/*.json`。

## 修改范围
- 优先修改与 Agent 行为直接相关的路径，例如 `configs/mat_master/`、`matmaster/exps/`、`playground/mat_master/` 下的提示、技能、工具描述；避免无关大重构。
- 保持改动可审：尽量小步、可解释。

## 轮次结束
- 调用 **report_iteration_outcome**，`iteration_index` 必须与当前轮次编号一致，`macro_mean_0_100` 为整数 0–100，`target_met` 表示是否达到用户给定目标分，`rationale` 用 Markdown 简述判分与下一步。
"""

    def __init__(self, config: AgentLoopConfig) -> None:
        self._cfg = config

    @classmethod
    def default_session_dir(
        cls, *, repo_root: Path, label: str = "devshell_agent_loop"
    ) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return repo_root / "results" / f"{label}_{ts}"

    def _iteration_user_message(self, *, it: int) -> str:
        cfg = self._cfg
        extra = cfg.extra_instruction.strip()
        extra_block = f"\n\n## 用户附加说明\n{extra}\n" if extra else ""
        session_dir = cfg.session_dir.resolve()
        return f"""## 第 {it} / {cfg.max_iterations} 轮迭代

- **目标宏平均分数**：{cfg.target_mean_score}/100（若 `macro_mean_0_100 >= {cfg.target_mean_score}` 或你认为已充分达标，将 `target_met` 设为 true）。
- **会话目录**（本机路径，用于阅读产物）：`{session_dir}`

### 你必须完成的步骤
1. 调用 **run_devshell_eval**，`iteration_tag` 使用新目录名（建议 `iter_{it:02d}`），勿复用旧 tag。
2. 对本次 run 先执行 `uv run python evaluation/scripts/devshell/score_devshell_tasks.py --run-dir <本轮目录> --dry-run`，以脚本输出的每题分数与宏平均作为本轮判分结果；必要时再阅读 `eval_runs/.../workspaces/<task_id>/`、`logs/<task_id>/events_*.jsonl` 与题库 YAML 解释低分原因。
3. 若未达标：修改仓库内相关提示词/工具/配置。**每处修改后立刻 `git commit` 一条**；若某次 commit 后经复评宏平均相对该次修改前**没有变好**，对该 commit **回滚**（`git revert` 或安全的 `reset`）。若已达标：可不改。
4. 调用 **report_iteration_outcome**（`iteration_index={it}`），填写**反映当前仓库状态**的真实宏平均与 `files_touched`（如有）；若本轮曾回滚无效改动，分数应以回滚后的最终状态为准。
{extra_block}
"""

    def _write_session_manifest(self) -> None:
        cfg = self._cfg
        session_dir = cfg.session_dir
        payload = {
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "repo_root": str(cfg.repo_root.resolve()),
            "session_dir": str(session_dir.resolve()),
            "max_iterations": cfg.max_iterations,
            "target_mean_score": cfg.target_mean_score,
            "permission_mode": cfg.permission_mode,
            "max_sdk_turns": cfg.max_sdk_turns,
            "defaults": {
                k: (str(v) if isinstance(v, Path) else v)
                for k, v in asdict(cfg.defaults).items()
            },
            "extra_instruction": cfg.extra_instruction,
            "git_reset_on_regression": cfg.git_reset_on_regression,
        }
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "session_manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    async def run(self) -> int:
        """Run up to ``max_iterations`` SDK rounds; return 0 on clean stop, 1 on warnings."""
        try:
            from claude_agent_sdk import (  # type: ignore[import-untyped]
                AssistantMessage,
                ClaudeAgentOptions,
                ClaudeSDKClient,
                TextBlock,
            )
        except ImportError as e:
            print(
                "Missing dependency: install with `uv sync --extra eval-agent` "
                f"({e})",
                file=sys.stderr,
            )
            return 2

        from evaluation.devshell_agent.sdk_tools import MatmasterEvalMcpToolkit

        cfg = self._cfg
        state = AgentLoopSharedState(
            repo_root=cfg.repo_root,
            session_dir=cfg.session_dir,
            outcomes=[],
            defaults=cfg.defaults,
        )
        toolkit = MatmasterEvalMcpToolkit(state)
        mcp_server = toolkit.build_mcp_server()

        allowed_tools = [
            *MatmasterEvalMcpToolkit.allowed_tool_names(),
            "Read",
            "Glob",
            "Grep",
            "Edit",
            "Write",
            "Bash",
        ]

        options = ClaudeAgentOptions(
            system_prompt=self.SYSTEM_PROMPT,
            cwd=str(cfg.repo_root.resolve()),
            max_turns=cfg.max_sdk_turns,
            mcp_servers={MatmasterEvalMcpToolkit.MCP_SERVER_NAME: mcp_server},
            allowed_tools=allowed_tools,
            permission_mode=cfg.permission_mode,
        )

        self._write_session_manifest()

        exit_code = 0
        async with ClaudeSDKClient(options=options) as client:
            for it in range(1, cfg.max_iterations + 1):
                head0 = git_rev_parse_head(repo_root=cfg.repo_root)
                if head0:
                    append_iteration_head(
                        session_dir=cfg.session_dir, iteration=it, head=head0
                    )
                await client.query(self._iteration_user_message(it=it))
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock) and block.text.strip():
                                print(block.text, file=sys.stderr, flush=True)

                matching = [
                    o for o in state.outcomes if int(o.get("iteration_index", -1)) == it
                ]
                if not matching:
                    print(
                        f"warning: no report_iteration_outcome for iteration {it}",
                        file=sys.stderr,
                    )
                    exit_code = 1
                    continue

                last = matching[-1]
                score = int(last.get("macro_mean_0_100", 0))
                met = bool(last.get("target_met"))

                if cfg.git_reset_on_regression and it > 1:
                    prev_rows = [
                        o
                        for o in state.outcomes
                        if int(o.get("iteration_index", -1)) == it - 1
                    ]
                    if prev_rows:
                        prev_score = int(prev_rows[-1].get("macro_mean_0_100", 0))
                        if score < prev_score:
                            saved = head_at_iteration_start(cfg.session_dir, it)
                            if saved:
                                ok, msg = git_reset_hard(
                                    repo_root=cfg.repo_root, rev=saved
                                )
                                print(
                                    f"git regression guard: iter {it} mean {score} "
                                    f"< iter {it - 1} mean {prev_score}; "
                                    f"reset --hard {saved[:7]}… -> "
                                    f"{'ok' if ok else 'failed'} {msg}",
                                    file=sys.stderr,
                                )
                                if not ok:
                                    exit_code = 1
                            else:
                                print(
                                    f"warning: regression at iter {it} but no "
                                    f"head_at_start recorded; skip auto reset",
                                    file=sys.stderr,
                                )

                if met or score >= cfg.target_mean_score:
                    print(
                        f"Stopping after iteration {it}: target_met={met} "
                        f"macro_mean={score} (target {cfg.target_mean_score})",
                        file=sys.stderr,
                    )
                    break

        return exit_code

    def run_sync(self) -> int:
        return asyncio.run(self.run())


async def run_devshell_agent_loop(cfg: AgentLoopConfig) -> int:
    """Delegate to :class:`DevshellAgentLoop` (stable import path for callers)."""
    return await DevshellAgentLoop(cfg).run()


def run_devshell_agent_loop_sync(cfg: AgentLoopConfig) -> int:
    return DevshellAgentLoop(cfg).run_sync()


def default_session_dir(*, repo_root: Path, label: str = "devshell_agent_loop") -> Path:
    """Delegate to :meth:`DevshellAgentLoop.default_session_dir`."""
    return DevshellAgentLoop.default_session_dir(repo_root=repo_root, label=label)
