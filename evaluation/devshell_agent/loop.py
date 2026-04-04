"""Claude Agent SDK session: multi-iteration DevShell eval → judge → edit loop."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
from evaluation.devshell_agent.subprocess_runner import run_score_devshell_tasks_submit


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
    eval_ingest_submit_each_iteration: bool = True
    eval_ingest_submit_timeout: float = 120.0
    enable_checklist_agent: bool = True
    max_checklist_sdk_turns: int = 60
    checklist_permission_mode: str = ""


class DevshellAgentLoop:
    """Runs the Claude SDK client for multiple outer iterations."""

    SYSTEM_PROMPT_MAIN = """你是 MatMaster 仓库内的 **DevShell 评测迭代编排助手（产品 / Agent 行为侧）**。

## 工具分工
- **run_devshell_eval**：在仓库根目录下执行 `evaluation/scripts/devshell/run_devshell_eval.py`（子进程，优先 `uv run python`）。输出目录为会话下的 `eval_runs/<iteration_tag>/`。
- **report_iteration_outcome**：每一轮结束时**必须**调用一次，记录宏平均分数与是否达标。
- **escalate_checklist_revision**：当你判断低分主要来自 **题库评分项 / scoring_checklist / reference_answers** 不公或错误时调用；**不得**亲自改题库。编排器会在本轮主会话结束后启动**另一 Agent** 专改 `evaluation/question_bank/`。
- **Read / Glob / Grep / Edit / Write / Bash**：用于阅读题库与产物、修改配置与提示词。Bash 用于 `git` 与必要命令；避免与本流程无关的破坏性操作。

## 防作弊：题库与 checklist（硬约束）
- **禁止**使用 Edit/Write 创建或修改 `evaluation/question_bank/` 下**任何**路径（含 `scoring_checklist`、`reference_answers`、题干、`human_prompt_seed` 等）。禁止通过脚本或其它目录间接改写题库 YAML。
- 需要调整评测标准时：**仅**能 **Read / Grep** 读题库；修改必须走 **escalate_checklist_revision**，由 checklist 专责 Agent 执行。
- 你应把迭代精力放在 `configs/mat_master/`、`matmaster/exps/`、`playground/mat_master/` 等与 **被测 Agent 行为**直接相关的资产上。

## Git 工作流（自迭代必守）
- **每次实质性修改**（每次 `Edit`/`Write` 落盘后）：对相应文件 `git add` 并 **`git commit` 一条独立记录**，消息建议 `devshell_agent iter=<轮次> <简述>`，使改动与 commit 一一对应、便于回滚。
- **判断单次改动是否改善**：在该次改动前记下当时的宏平均（来自 `score_devshell_tasks.py`）；改动并 commit 后，若需用分数验证，应再次对**能反映新代码**的产物跑分（通常需新的 `run_devshell_eval` + `iteration_tag`，或按题库说明复评）。若新宏平均 **不高于** 改动前基准（改善无效），应回滚**该条** commit：优先 `git revert HEAD --no-edit`；若该 commit 尚未 push 且历史仅本地迭代，可用 `git reset --hard HEAD~1`。
- 不要用 `git push --force` 等破坏协作历史的操作。

## 判分原则（与 `evaluation/docs/devshell/devshell_claude_code_eval.md` 一致）
- 优先使用仓库脚本 `evaluation/scripts/devshell/score_devshell_tasks.py` 自动评分；它会基于 `raw_runs.jsonl`、`workspaces/<task_id>/` 与 `logs/<task_id>/events_*.jsonl` 调用同一套 `BinaryEvaluator`。
- 宏平均以 `score_devshell_tasks.py` 输出为准；不要手工估算一个与脚本不一致的分数。
- 如需解释低分原因，可再阅读题库 YAML、workspace 交付物和事件日志；**不得**仅凭 `devshell_summary` / `final_content` 断言 checklist 通过。
- 若使用 `--eval-ingest-pending-only`（本编排默认）：判分时请只用 `score_devshell_tasks.py --dry-run`；**ingest POST** 由外层在每轮主 Agent 会话结束后、**checklist 专责回合开始前**，对本轮**各次** `run_devshell_eval` 输出目录依次执行 `score_devshell_tasks.py --submit`，你无需再手动 `--submit`（避免重复上报）。

## 修改范围
- **可写**：`configs/mat_master/`、`matmaster/exps/`、`playground/mat_master/` 等与运行中 Agent 相关的提示、技能、工具描述。
- **不可写**：`evaluation/question_bank/**`（见上节）；避免无关大重构。
- 保持改动可审：尽量小步、可解释。

## 轮次结束
- 调用 **report_iteration_outcome**，`iteration_index` 必须与当前轮次编号一致，`macro_mean_0_100` 为整数 0–100，`target_met` 表示是否达到用户给定目标分，`rationale` 用 Markdown 简述判分与下一步。
"""

    SYSTEM_PROMPT_CHECKLIST = """你是 MatMaster 仓库内的 **DevShell 评测迭代 — checklist / 题库专责助手**。

你与上一会话中的「产品侧」Agent **不是同一角色**：你只负责 **评测语义与题库 YAML**，不负责改 `configs/mat_master/`、`matmaster/exps/`、`playground/mat_master/` 等运行配置。

## 硬约束
- **仅允许**使用 Edit/Write 修改路径前缀为 `evaluation/question_bank/` 的文件（题库 YAML）。**禁止**编辑上述产品侧目录及 `evaluation/core/`、`evaluation/scripts/` 等（除非只读）。
- 修改 `scoring_checklist`、`reference_answers`、题干等时遵守仓库 `evaluation/AGENTS_evaluation.md`：若变更影响评测语义，须按该文档更新对应题目的顶层 `id`。
- 使用 **Read / Glob / Grep** 阅读证据（含本会话目录下的 `eval_runs/`、workspace、events、题库）。
- **report_checklist_revision**：本专责回合结束时**必须**调用一次，说明是否改动了题库、改了哪些文件、或为何维持不变。

## Git
- 每次改动题库后单独 `git commit`，消息建议 `devshell_agent_checklist iter=<轮次> <简述>`。

## 工具
- 无 `run_devshell_eval`；不调用 `report_iteration_outcome` 或 `escalate_checklist_revision`。仅使用 **report_checklist_revision** 与本仓库读写工具。
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
2. 对**需要判分的**每个 `run_devshell_eval` 目录分别执行 `uv run python evaluation/scripts/devshell/score_devshell_tasks.py --run-dir <该目录> --dry-run` 获取判分（**不要**加 `--submit`）；ingest 由编排器在本轮主会话结束且 checklist 开始前对各目录自动提交。
3. 若未达标：在**允许的路径**内修改提示词/工具/配置（**不要**改 `evaluation/question_bank/`）。**每处修改后立刻 `git commit` 一条**；若某次 commit 后经复评宏平均相对该次修改前**没有变好**，对该 commit **回滚**。若你认为问题在 **checklist / 参考答案** 而非产品侧，调用 **escalate_checklist_revision** 并仍在第 4 步前完成主流程。
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
            "eval_ingest_submit_each_iteration": cfg.eval_ingest_submit_each_iteration,
            "eval_ingest_submit_timeout": cfg.eval_ingest_submit_timeout,
            "enable_checklist_agent": cfg.enable_checklist_agent,
            "max_checklist_sdk_turns": cfg.max_checklist_sdk_turns,
            "checklist_permission_mode": cfg.checklist_permission_mode or None,
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
            system_prompt=self.SYSTEM_PROMPT_MAIN,
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
                state.last_eval_output_dir = None
                state.eval_output_dirs.clear()
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

                # Ingest submit must run against the question bank as it was when the
                # run was produced, and every run_devshell_eval directory this iteration
                # must be submitted (not only the last tag). Checklist follow-up may
                # rename question ids; submitting after it breaks scoring for stale ids.
                dirs_to_submit: list[Path] = []
                if state.eval_output_dirs:
                    seen: set[Path] = set()
                    for p in state.eval_output_dirs:
                        r = p.resolve()
                        if r not in seen:
                            seen.add(r)
                            dirs_to_submit.append(r)
                elif state.last_eval_output_dir is not None:
                    dirs_to_submit.append(state.last_eval_output_dir.resolve())
                for run_dir in dirs_to_submit:
                    if not self._maybe_submit_iteration_ingest(
                        state=state, iteration=it, run_dir=run_dir
                    ):
                        exit_code = 1

                if await self._run_checklist_followup_if_needed(
                    it=it, state=state, mcp_server=mcp_server
                ):
                    exit_code = 1

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

    def _checklist_permission_mode_resolved(self) -> str:
        raw = (self._cfg.checklist_permission_mode or "").strip()
        return raw if raw else self._cfg.permission_mode

    def _checklist_user_message(
        self, *, it: int, escalations: list[dict[str, Any]]
    ) -> str:
        session_dir = self._cfg.session_dir.resolve()
        blob = json.dumps(escalations, ensure_ascii=False, indent=2)
        return f"""## Checklist 专责回合（第 {it} 轮迭代）

主 Agent 已提交以下 **escalate_checklist_revision** 队列（JSON）：

```json
{blob}
```

- **会话目录**（只读产物）：`{session_dir}`
- 任务：仅在 **evaluation/question_bank/** 内做必要 YAML 修订，或论证无需修改；遵守 `evaluation/AGENTS_evaluation.md` 的题库 `id` 规则。
- 结束前**必须**调用 **report_checklist_revision**（`iteration_index={it}`）。
"""

    async def _run_checklist_followup_if_needed(
        self,
        *,
        it: int,
        state: AgentLoopSharedState,
        mcp_server: Any,
    ) -> int:
        """Run isolated checklist agent if escalations exist; return 1 if warning."""
        cfg = self._cfg
        if not cfg.enable_checklist_agent:
            return 0
        escalations = [
            e
            for e in state.checklist_escalations_pending
            if int(e.get("iteration_index", -1)) == it
        ]
        if not escalations:
            return 0
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            TextBlock,
        )

        from evaluation.devshell_agent.sdk_tools import MatmasterEvalMcpToolkit

        n_reports_before = len(state.checklist_revision_reports)
        checklist_allowed = [
            *MatmasterEvalMcpToolkit.checklist_agent_mcp_tool_names(),
            "Read",
            "Glob",
            "Grep",
            "Edit",
            "Write",
            "Bash",
        ]
        co = ClaudeAgentOptions(
            system_prompt=self.SYSTEM_PROMPT_CHECKLIST,
            cwd=str(cfg.repo_root.resolve()),
            max_turns=max(1, cfg.max_checklist_sdk_turns),
            mcp_servers={MatmasterEvalMcpToolkit.MCP_SERVER_NAME: mcp_server},
            allowed_tools=checklist_allowed,
            permission_mode=self._checklist_permission_mode_resolved(),
        )
        print(
            f"checklist agent: iteration {it}, {len(escalations)} escalation(s)",
            file=sys.stderr,
        )
        async with ClaudeSDKClient(options=co) as cc:
            await cc.query(self._checklist_user_message(it=it, escalations=escalations))
            async for message in cc.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            print(block.text, file=sys.stderr, flush=True)

        state.checklist_escalations_pending = [
            e
            for e in state.checklist_escalations_pending
            if int(e.get("iteration_index", -1)) != it
        ]
        new_reports = state.checklist_revision_reports[n_reports_before:]
        ok_reports = [r for r in new_reports if int(r.get("iteration_index", -1)) == it]
        if not ok_reports:
            print(
                f"warning: checklist agent did not call report_checklist_revision "
                f"for iteration {it}",
                file=sys.stderr,
            )
            return 1
        return 0

    def _defaults_disable_eval_ingest(self) -> bool:
        return "--no-eval-ingest" in self._cfg.defaults.extra_args

    def _maybe_submit_iteration_ingest(
        self,
        *,
        state: AgentLoopSharedState,
        iteration: int,
        run_dir: Path | None = None,
    ) -> bool:
        """Return False if ``--submit`` was run and exited non-zero."""
        cfg = self._cfg
        if not cfg.eval_ingest_submit_each_iteration:
            return True
        if not cfg.defaults.eval_ingest_pending_only:
            return True
        if self._defaults_disable_eval_ingest():
            return True
        if run_dir is None:
            run_dir = state.last_eval_output_dir
        if run_dir is None:
            print(
                f"warning: iter {iteration}: skip ingest submit (no run_devshell_eval "
                f"output dir recorded)",
                file=sys.stderr,
            )
            return True
        run_dir = run_dir.resolve()
        if not (run_dir / "raw_runs.jsonl").is_file():
            print(
                f"warning: iter {iteration}: skip ingest submit (no raw_runs.jsonl in "
                f"{run_dir})",
                file=sys.stderr,
            )
            return True
        pending_dir = run_dir / "pending_ingest"
        if not pending_dir.is_dir() or not any(pending_dir.glob("*.json")):
            print(
                f"warning: iter {iteration}: skip ingest submit (no pending_ingest/*.json "
                f"under {run_dir})",
                file=sys.stderr,
            )
            return True
        rc, out, err = run_score_devshell_tasks_submit(
            repo_root=cfg.repo_root,
            run_dir=run_dir,
            eval_config=cfg.defaults.eval_config,
            eval_ingest_timeout=float(cfg.eval_ingest_submit_timeout),
        )
        log_path = cfg.session_dir / "ingest_submit.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_row = {
            "iteration": iteration,
            "run_dir": str(run_dir),
            "exit_code": rc,
            "stdout_tail": (out or "")[-8000:],
            "stderr_tail": (err or "")[-8000:],
        }
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(log_row, ensure_ascii=False) + "\n")
        if out.strip():
            print(out, file=sys.stderr, end="" if out.endswith("\n") else "\n")
        if err.strip():
            print(err, file=sys.stderr, end="" if err.endswith("\n") else "\n")
        if rc != 0:
            print(
                f"warning: iter {iteration}: score_devshell_tasks --submit exit {rc}",
                file=sys.stderr,
            )
            return False
        return True

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
