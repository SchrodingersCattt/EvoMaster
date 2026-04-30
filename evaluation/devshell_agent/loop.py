"""Claude Agent SDK session: multi-iteration DevShell eval → judge → edit loop."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from evaluation.devshell_agent import loop_prompts as _loop_prompts
from evaluation.devshell_agent.config_state import (
    AgentLoopConfig,
    AgentLoopSharedState,
    checklist_revision_sdk_max_turns_from_jobs,
    parallel_scoring_checklist_workers_from_jobs,
)
from evaluation.devshell_agent.git_iteration import (
    append_iteration_head,
    git_rev_parse_head,
)
from evaluation.devshell_agent.loop_proposal_notify import (
    notify_proposed_matmaster_exps_if_present,
    notify_proposed_question_bank_if_present,
)
from evaluation.devshell_agent.sdk_client_retry import sdk_client_with_retry
from evaluation.devshell_agent.sdk_logging import log_line, log_sdk_message

# ``ClaudeAgentOptions(tools=...)``: empty built-in tool set (``--tools`` with no
# Claude Code Read/Bash/Agent/...). MCP tools come only from ``mcp_servers``.
# ``allowed_tools`` maps to ``--allowedTools`` (permission pre-approval), not
# an exclusive allowlist.
_DEVSHELL_SDK_BUILTIN_TOOLS_DISABLED: list[str] = []


def checklist_max_turns_for_shared_state(state: AgentLoopSharedState) -> int:
    """Claude SDK ``max_turns`` for the question_bank checklist-revision agent."""
    return checklist_revision_sdk_max_turns_from_jobs(int(state.defaults.jobs))


class DevshellAgentLoop:
    """Runs the Claude SDK client for multiple outer iterations."""

    # Upper bounds for sdk_loop_console.log lines (avoid huge single lines / OOM).
    _SDK_LOG_TOOL_RESULT_MAX_CHARS = 24_000
    _SDK_LOG_STREAM_EVENT_MAX_CHARS = 4_000
    _SDK_LOG_TEXT_BLOCK_MAX_CHARS = 100_000
    _SDK_LOG_SYSTEM_DATA_MAX_CHARS = 24_000
    # SDK subprocess transport JSON buffer (default 1 MB is too small for
    # large glob_paths results); 10 MB gives comfortable headroom.
    _SDK_MAX_BUFFER_SIZE = 10 * 1024 * 1024
    # Retry count for transient SDK client initialization timeouts.
    _SDK_CONNECT_RETRIES = 2
    _SDK_CONNECT_RETRY_DELAY = 5.0

    SYSTEM_PROMPT_MAIN = _loop_prompts.SYSTEM_PROMPT_MAIN
    SYSTEM_PROMPT_CHECKLIST = _loop_prompts.SYSTEM_PROMPT_CHECKLIST
    SYSTEM_PROMPT_OPTIMIZATION = _loop_prompts.SYSTEM_PROMPT_OPTIMIZATION
    SYSTEM_PROMPT_OPTIMIZATION_P0_REVERT = (
        _loop_prompts.SYSTEM_PROMPT_OPTIMIZATION_P0_REVERT
    )

    def __init__(self, config: AgentLoopConfig) -> None:
        self._cfg = config

    @staticmethod
    def main_agent_allowed_tools() -> list[str]:
        from evaluation.devshell_agent.sdk_tools import MatmasterEvalMcpToolkit

        return [*MatmasterEvalMcpToolkit.allowed_tool_names()]

    @staticmethod
    def _optimization_escalations_for_iteration(
        it: int, state: AgentLoopSharedState
    ) -> list[dict[str, Any]]:
        return [
            row
            for row in state.optimization_delegations_pending
            if int(row.get("iteration_index", -1)) == it
        ]

    @classmethod
    def default_session_dir(
        cls, *, repo_root: Path, label: str = "devshell_agent_loop"
    ) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return repo_root / "results" / f"{label}_{ts}"

    def _budget_exp_name(self) -> str:
        exp = (self._cfg.defaults.exp or "").strip()
        return exp if exp else "direct"

    def _history_root(self) -> Path:
        if self._cfg.history_root is not None:
            return self._cfg.history_root.resolve()
        return (self._cfg.repo_root / "evaluation" / "devshell_agent_history").resolve()

    def _history_session_dir(self) -> Path:
        return self._history_root() / self._cfg.session_dir.name

    def _iteration_user_message(self, *, it: int) -> str:
        cfg = self._cfg
        extra = cfg.extra_instruction.strip()
        extra_block = f"\n\n## 用户附加说明\n{extra}\n" if extra else ""
        session_dir = cfg.session_dir.resolve()
        return f"""## 第 {it} / {cfg.max_iterations} 轮迭代

- **目标全项通过率**：{cfg.target_pass_rate}/100（与 `macro_mean_0_100` 同刻度）。`macro_mean_0_100` 为：每道题在 **k 次 repeat** 下，除可选项 **`token_budget_total`、`turn_budget`** 外 checklist **均全过** 才算该题通过（该题计 100），否则该题计 0；再对所有题取算术平均，即 **（完全通过的题目数 ÷ 题目数）× 100**。编排器在 `macro_mean_0_100 >= {cfg.target_pass_rate}` 时结束迭代。若你认为已充分达标，也可将 `target_met` 设为 true。
- **会话目录**（本机路径，用于阅读产物）：`{session_dir}`

### 你必须完成的步骤
1. 调用 **run_devshell_eval**，`iteration_tag` 使用新目录名（建议 `iter_{it:02d}`），勿复用旧 tag。
2. 检查返回结果中是否有 `p0_gate_failed: true`（P0 回归门控失败）：
   - **若 P0 回归**：不要调用 delegate_optimization 或 escalate_checklist_revision，直接跳到步骤 4 报告本轮失败。
   - **若 P0 通过或无 P0 题目**：继续步骤 3。
3. 读取**脱敏摘要**（`macro_mean_0_100`、`task_scores`；单题 `score` 为 0/100：**该题 k 次 repeat 在「除 `token_budget_total`、`turn_budget` 外 checklist 全过」口径下** 才为 100）。除 **main_read_text / main_glob_paths / main_grep_text** 允许的 ``evaluation/devshell_agent_history/`` 整目录外，不要自行读取 `evaluation/**` 其它路径或原始 `score_reason`。若未达标：根据脱敏摘要做分流。若问题更像产品侧实现/提示问题，调用 **delegate_optimization**（优先填写 **candidate_layers** 与 **failure_buckets**、**capabilities_affected**；`candidate_layers` 用 ``skill / tool / system_prompt / runtime`` 标注你判断最像哪一层；**allowed_evidence_paths** 尽量用会话级路径如 ``eval_runs/iter_XX/raw_runs.jsonl``，避免逐题 workspace）；若问题更像 checklist / reference answers / evaluator 口径问题，调用 **escalate_checklist_revision**。你可以在同一轮内多次调用 `delegate_optimization`，但你**不能**亲自改文件。
4. 调用 **report_iteration_outcome**（`iteration_index={it}`），填写**反映当前仓库状态**的真实 `macro_mean_0_100`（完全通过题占比×100，与 `target_pass_rate` 同口径）与 `files_touched`（主 Agent 自身通常为空）；在 `rationale` 中总结本轮分流、子 Agent 结果与下一步。若 P0 回归，在 rationale 中说明回归详情。
{extra_block}
"""

    @staticmethod
    def _detect_p0_regression_from_eval_dirs(
        state: AgentLoopSharedState, loop_log: TextIO
    ) -> bool:
        """Detect P0 regression: p0_gate dir exists but remaining dir does not.

        ``run_devshell_eval`` two-phase layout is ``<tag>/p0_gate`` and
        ``<tag>/remaining`` as **siblings**. ``eval_output_dirs`` records those
        paths (or a single-phase ``<tag>``). We must resolve ``remaining`` as
        ``p0_gate.parent / "remaining"``, not ``p0_gate / "remaining"``.
        """
        for d in state.eval_output_dirs:
            if d.name == "p0_gate":
                p0_gate_dir = d
                remaining_dir = d.parent / "remaining"
            elif d.name == "remaining":
                p0_gate_dir = d.parent / "p0_gate"
                remaining_dir = d
            else:
                p0_gate_dir = d / "p0_gate"
                remaining_dir = d / "remaining"
            if p0_gate_dir.is_dir() and not remaining_dir.is_dir():
                log_line(
                    f"P0 gate directory found without remaining: {p0_gate_dir}",
                    loop_log,
                )
                return True
        return False

    def _write_session_manifest(self) -> None:
        cfg = self._cfg
        session_dir = cfg.session_dir
        payload = {
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "repo_root": str(cfg.repo_root.resolve()),
            "session_dir": str(session_dir.resolve()),
            "history_root": str(self._history_root()),
            "max_iterations": cfg.max_iterations,
            "target_pass_rate": cfg.target_pass_rate,
            "permission_mode": cfg.permission_mode,
            "max_sdk_turns": cfg.max_sdk_turns,
            "defaults": {
                k: (str(v) if isinstance(v, Path) else v)
                for k, v in asdict(cfg.defaults).items()
            },
            "extra_instruction": cfg.extra_instruction,
            "eval_ingest_submit_each_iteration": cfg.eval_ingest_submit_each_iteration,
            "eval_ingest_submit_timeout": cfg.eval_ingest_submit_timeout,
            "enable_checklist_agent": cfg.enable_checklist_agent,
            "enable_optimization_agent": True,
            "enable_optimization_auto_commit": cfg.enable_optimization_auto_commit,
            "optimization_auto_commit_skip_budget": cfg.optimization_auto_commit_skip_budget,
            "max_checklist_sdk_turns": checklist_revision_sdk_max_turns_from_jobs(
                cfg.defaults.jobs
            ),
            "parallel_scoring_checklist_workers": parallel_scoring_checklist_workers_from_jobs(
                cfg.defaults.jobs
            ),
            "checklist_permission_mode": cfg.checklist_permission_mode or None,
        }
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "session_manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_iteration_history(
        self,
        *,
        it: int,
        state: AgentLoopSharedState,
        outcome: dict[str, Any],
    ) -> None:
        history_dir = self._history_session_dir() / "iterations"
        history_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "iteration_index": it,
            "outcome": outcome,
            "optimization_reports": [
                row
                for row in state.optimization_reports
                if int(row.get("iteration_index", -1)) == it
            ],
            "checklist_reports": [
                row
                for row in state.checklist_revision_reports
                if int(row.get("iteration_index", -1)) == it
            ],
            "optimization_delegations": [
                row
                for row in state.optimization_delegations_pending
                if int(row.get("iteration_index", -1)) == it
            ],
        }
        (history_dir / f"iter_{it:02d}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_session_history_summary(
        self,
        *,
        state: AgentLoopSharedState,
        exit_code: int,
    ) -> None:
        session_dir = self._history_session_dir()
        session_dir.mkdir(parents=True, exist_ok=True)
        summary = {
            "session_dir": str(self._cfg.session_dir.resolve()),
            "exit_code": exit_code,
            "outcomes": state.outcomes,
            "optimization_reports": state.optimization_reports,
            "checklist_revision_reports": state.checklist_revision_reports,
        }
        (session_dir / "session_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        index_path = self._history_root() / "index.jsonl"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "session_name": self._cfg.session_dir.name,
            "session_dir": str(self._cfg.session_dir.resolve()),
            "exit_code": exit_code,
            "history_dir": str(session_dir),
            "outcome_count": len(state.outcomes),
        }
        with index_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    async def run(self) -> int:
        """Run up to ``max_iterations`` SDK rounds; return 0 on clean stop, 1 on warnings."""
        try:
            from claude_agent_sdk import (  # type: ignore[import-untyped]
                ClaudeAgentOptions,
                ClaudeSDKClient,
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
            eval_ingest_submit_each_iteration=cfg.eval_ingest_submit_each_iteration,
            eval_ingest_submit_timeout=cfg.eval_ingest_submit_timeout,
        )
        toolkit = MatmasterEvalMcpToolkit(state)
        mcp_server = toolkit.build_mcp_server()

        allowed_tools = self.main_agent_allowed_tools()

        options = ClaudeAgentOptions(
            system_prompt=self.SYSTEM_PROMPT_MAIN,
            cwd=str(cfg.repo_root.resolve()),
            max_turns=cfg.max_sdk_turns,
            mcp_servers={MatmasterEvalMcpToolkit.MCP_SERVER_NAME: mcp_server},
            tools=_DEVSHELL_SDK_BUILTIN_TOOLS_DISABLED,
            allowed_tools=allowed_tools,
            permission_mode=cfg.permission_mode,
            max_buffer_size=self._SDK_MAX_BUFFER_SIZE,
        )

        self._write_session_manifest()

        log_path = cfg.session_dir / "sdk_loop_console.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        exit_code = 0
        #: HEAD at the **start** of the previous iteration; used as ``git revert`` base when
        #: the current iteration's P0 gate regresses (``last_p0_scores`` matches that snapshot).
        prev_iter_start_head: str | None = None
        with log_path.open("a", encoding="utf-8") as loop_log:
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
                        log_sdk_message(
                            message,
                            loop_log=loop_log,
                            tool_result_max_chars=self._SDK_LOG_TOOL_RESULT_MAX_CHARS,
                            stream_event_max_chars=self._SDK_LOG_STREAM_EVENT_MAX_CHARS,
                            text_block_max_chars=self._SDK_LOG_TEXT_BLOCK_MAX_CHARS,
                            system_data_max_chars=self._SDK_LOG_SYSTEM_DATA_MAX_CHARS,
                        )

                    opt_rc = await self._run_optimization_followups_if_needed(
                        it=it,
                        state=state,
                        mcp_server=mcp_server,
                        loop_log=loop_log,
                    )
                    if opt_rc >= 1:
                        exit_code = 1

                    follow_rc = await self._run_checklist_followup_if_needed(
                        it=it,
                        state=state,
                        mcp_server=mcp_server,
                        loop_log=loop_log,
                    )
                    if follow_rc >= 1:
                        exit_code = 1
                    if follow_rc == 2:
                        log_line(
                            "Stopping outer iterations: question_bank question id set "
                            "changed during checklist follow-up (see "
                            "question_bank_id_drift.json in session dir).",
                            loop_log,
                        )
                        break

                    # --- P0 regression handling ---
                    p0_regressed = self._detect_p0_regression_from_eval_dirs(
                        state, loop_log
                    )

                    matching = [
                        o
                        for o in state.outcomes
                        if int(o.get("iteration_index", -1)) == it
                    ]
                    if not matching:
                        log_line(
                            f"warning: no report_iteration_outcome for iteration {it}",
                            loop_log,
                        )
                        exit_code = 1
                        if head0:
                            prev_iter_start_head = head0
                        continue

                    last = matching[-1]
                    if p0_regressed:
                        last["p0_regression"] = True

                    if p0_regressed and prev_iter_start_head:
                        rev_rc = await self._run_p0_revert_followup_if_needed(
                            it=it,
                            revert_base_sha=prev_iter_start_head,
                            state=state,
                            mcp_server=mcp_server,
                            loop_log=loop_log,
                        )
                        if rev_rc >= 1:
                            exit_code = 1
                    elif p0_regressed and not prev_iter_start_head:
                        log_line(
                            "P0 regression but prev_iter_start_head is unset "
                            f"(iteration {it}); skipping git revert sub-round",
                            loop_log,
                        )

                    self._write_iteration_history(it=it, state=state, outcome=last)
                    score = int(last.get("macro_mean_0_100", 0))
                    met = bool(last.get("target_met"))

                    if p0_regressed:
                        tail = git_rev_parse_head(repo_root=cfg.repo_root)
                        if tail:
                            prev_iter_start_head = tail
                        log_line(
                            f"Iteration {it} marked as optimization failure "
                            f"(P0 regression); continuing to next iteration.",
                            loop_log,
                        )
                        continue

                    if head0:
                        prev_iter_start_head = head0

                    if met or score >= cfg.target_pass_rate:
                        log_line(
                            f"Stopping after iteration {it}: target_met={met} "
                            f"macro_mean={score} (target_pass_rate {cfg.target_pass_rate})",
                            loop_log,
                        )
                        break

        self._write_session_history_summary(state=state, exit_code=exit_code)
        return exit_code

    def _checklist_permission_mode_resolved(self) -> str:
        raw = (self._cfg.checklist_permission_mode or "").strip()
        return raw if raw else self._cfg.permission_mode

    def _write_question_bank_id_drift(
        self,
        *,
        it: int,
        ids_removed: list[str],
        ids_added: list[str],
        load_error: str | None = None,
    ) -> None:
        path = self._cfg.session_dir / "question_bank_id_drift.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "iteration_index": it,
            "ids_removed": ids_removed,
            "ids_added": ids_added,
        }
        if load_error is not None:
            payload["load_error"] = load_error
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

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

- **会话目录**（可写 proposal）：`{session_dir}`
- 任务：**不要**直接改 ``evaluation/question_bank/`` 或 ``evaluation/core/``；将必要修订写入 **`proposed_question_bank_changes.md`**（与会话目录下 `eval_runs/` 同级），遵守 `evaluation/AGENTS_evaluation.md` 的题库 `id` 规则（在提案中写明合入时是否需 bump `id`）。
- 结束前**必须**调用 **report_checklist_revision**（`iteration_index={it}`）。
"""

    @staticmethod
    def _optimization_delegation_slug(delegation: dict[str, Any]) -> str:
        for key in ("notes", "problem_summary"):
            s = str(delegation.get(key) or "").strip()
            if s:
                return s[:80]
        return "optimization"

    @staticmethod
    def _candidate_layers_for_delegation(delegation: dict[str, Any]) -> list[str]:
        seen: set[str] = set()
        layers: list[str] = []
        for raw in delegation.get("candidate_layers") or []:
            layer = str(raw).strip()
            if not layer or layer in seen:
                continue
            seen.add(layer)
            layers.append(layer)
        return layers

    def _optimization_layer_guidance(self, delegation: dict[str, Any]) -> str:
        layers = self._candidate_layers_for_delegation(delegation)
        if not layers:
            return (
                "- 未显式提供 `candidate_layers`：先自行判断更像 `skill`、`tool`、"
                "`system_prompt` 还是 `runtime`，再决定主改动面。\n"
            )

        lines = ["- `candidate_layers`: " + ", ".join(f"`{layer}`" for layer in layers)]
        if layers == ["system_prompt"]:
            lines.extend(
                [
                    "- 仅命中 `system_prompt`：默认不要修改 `matmaster/skills/`、"
                    "`matmaster/tools/`、`src/` 等产品代码。",
                    "- 优先读取现有 `matmaster/exps/_base.toml` / "
                    "`matmaster/exps/direct.toml`，识别重复、冲突或可合并规则。",
                    "- 若判断确实需要改 exp：只在 "
                    "`proposed_matmaster_exps_changes.md` 中写 proposal，"
                    "不要尝试绕过限制落代码改动。",
                    "- proposal 使用固定模板并逐项填写："
                    "`Target file`、`Existing rule(s) to replace or merge`、"
                    "`Proposed text`、`Why not skill/tool layer`、"
                    "`Expected cross-task benefit`、`Prompt budget impact`。",
                ]
            )
            return "\n".join(lines) + "\n"

        if "skill" in layers:
            lines.append(
                "- 命中 `skill`：优先检查 `matmaster/skills/`，并遵守 "
                "`SKILL.md` / `references` / `scripts` 分层约束。"
            )
        if "tool" in layers:
            lines.append(
                "- 命中 `tool`：优先检查 `matmaster/tools/` 与相关 tool descriptions；"
                "避免把工具契约问题错误堆进 Skills。"
            )
        if "runtime" in layers:
            lines.append(
                "- 命中 `runtime`：优先检查 `config/`、`matmaster/adaptors/`、"
                "`matmaster/devshell/`、必要时 `src/` 的运行时链路。"
            )
        if "system_prompt" in layers:
            lines.append(
                "- 同时命中 `system_prompt`：只有在确认问题属于跨任务执行契约，且"
                "不能更合理地下沉到 skill/tool/runtime 层时，才写 exp proposal。"
            )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _optimization_execution_track(delegation: dict[str, Any]) -> str:
        return "proposal_only"

    def _record_optimization_proposal_track(
        self,
        *,
        it: int,
        delegation: dict[str, Any],
        state: AgentLoopSharedState,
        loop_log: TextIO,
    ) -> None:
        rnd = int(delegation.get("optimization_round", -1))
        matching_report = None
        for row in state.optimization_reports:
            if (
                int(row.get("iteration_index", -1)) == it
                and int(row.get("optimization_round", -1)) == rnd
            ):
                matching_report = row
                break

        payload = {
            "iteration_index": it,
            "optimization_round": rnd,
            "execution_track": "proposal_only",
            "candidate_layers": self._candidate_layers_for_delegation(delegation),
            "problem_summary": delegation.get("problem_summary"),
            "suggested_focus": delegation.get("suggested_focus") or [],
            "report_summary": (
                None if matching_report is None else matching_report.get("summary")
            ),
            "files_touched": (
                []
                if matching_report is None
                else matching_report.get("files_touched") or []
            ),
        }
        path = self._cfg.session_dir / "optimization_proposal_tracks.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        log_line(
            "optimization proposal-only track: "
            f"iteration {it}, round {rnd}, candidate_layers={payload['candidate_layers']!r}",
            loop_log,
        )

    def _apply_optimization_auto_commit(
        self,
        *,
        it: int,
        delegation: dict[str, Any],
        state: AgentLoopSharedState,
        loop_log: TextIO,
    ) -> None:
        cfg = self._cfg
        if not cfg.enable_optimization_auto_commit:
            return
        if self._optimization_execution_track(delegation) == "proposal_only":
            self._record_optimization_proposal_track(
                it=it,
                delegation=delegation,
                state=state,
                loop_log=loop_log,
            )
            return
        from evaluation.devshell_agent.optimization_auto_commit import (
            commit_optimization_changes,
        )

        rnd = int(delegation.get("optimization_round", -1))
        slug = self._optimization_delegation_slug(delegation)
        res = commit_optimization_changes(
            cfg.repo_root,
            cfg.session_dir,
            iteration_index=it,
            optimization_round=rnd,
            slug=slug,
            skip_exp_budget=cfg.optimization_auto_commit_skip_budget,
            log=loop_log,
        )
        if res.commit_sha:
            for row in state.optimization_reports:
                if (
                    int(row.get("iteration_index", -1)) == it
                    and int(row.get("optimization_round", -1)) == rnd
                ):
                    row["commit_shas"] = [res.commit_sha]
                    break

    def _optimization_user_message(self, *, it: int, delegation: dict[str, Any]) -> str:
        session_dir = self._cfg.session_dir.resolve()
        blob = json.dumps(delegation, ensure_ascii=False, indent=2)
        guidance = self._optimization_layer_guidance(delegation)
        return f"""## 产品侧优化子回合（第 {it} 轮）

主 Agent 已提交以下 **delegate_optimization** 工单（JSON）：

```json
{blob}
```

- **会话目录**（仅供查看非 `evaluation/` 产物）：`{session_dir}`
- 任务：仅在**产品侧目录**做必要优化，不得查看或编辑 `evaluation/**`。
- 分层指导：
{guidance}
- 结束前**必须**调用 **report_optimization_result**（`iteration_index={it}`，`optimization_round={delegation.get("optimization_round")}`）。
"""

    @staticmethod
    def _p0_revert_optimization_round(it: int) -> int:
        """Synthetic ``optimization_round`` for P0 revert sub-rounds (avoid colliding with 1..n)."""
        return 10_000 + int(it)

    def _p0_revert_user_message(self, *, it: int, revert_base_sha: str) -> str:
        session_dir = self._cfg.session_dir.resolve()
        rnd = self._p0_revert_optimization_round(it)
        return f"""## P0 回归 — Git revert 专责回合（第 {it} 轮迭代）

本轮 P0 相对 ``last_p0_scores`` 基线**下降**；基线对应**上一轮迭代开局**（本轮优化提交之前）的仓库快照。编排器授权的 ``base_sha`` = ``{revert_base_sha}``。

- **任务**：先调用 **git_revert_commits_after_base**，参数 ``base_sha`` **必须**为上述完整 SHA。该工具对 ``{revert_base_sha}..HEAD`` 上每个提交**从新到旧**执行 ``git revert --no-edit``（**不**使用 ``git reset``），用于撤销**上一轮** optimization auto-commit 等在基线之后累积的提交。
- 若区间为空（无可 revert 的提交），工具会返回成功说明；你仍须调用 **report_optimization_result**。
- **会话目录**（只读证据）：`{session_dir}`
- 结束前**必须**调用 **report_optimization_result**（``iteration_index={it}``，``optimization_round={rnd}``），在摘要中说明 revert 结果。
"""

    async def _run_p0_revert_followup_if_needed(
        self,
        *,
        it: int,
        revert_base_sha: str,
        state: AgentLoopSharedState,
        mcp_server: Any,
        loop_log: TextIO,
    ) -> int:
        """Run optimization-shaped sub-round to ``git revert`` after P0 regression.

        ``revert_base_sha`` is the **start-of-(it-1)** HEAD (snapshot when the last
        successful P0 gate updated ``last_p0_scores``), not the current iteration start.

        Returns:
            0: ok
            1: warning (e.g. missing ``report_optimization_result``)
        """
        base = revert_base_sha.strip()
        if not base:
            return 0

        from claude_agent_sdk import ClaudeAgentOptions

        from evaluation.devshell_agent.sdk_tools import MatmasterEvalMcpToolkit

        rnd = self._p0_revert_optimization_round(it)
        optimization_allowed = MatmasterEvalMcpToolkit.optimization_agent_tool_names()
        n_reports_before = len(state.optimization_reports)
        state.p0_revert_allowed_base_sha = base
        try:
            co = ClaudeAgentOptions(
                system_prompt=self.SYSTEM_PROMPT_OPTIMIZATION_P0_REVERT,
                cwd=str(self._cfg.repo_root.resolve()),
                max_turns=24,
                mcp_servers={MatmasterEvalMcpToolkit.MCP_SERVER_NAME: mcp_server},
                tools=_DEVSHELL_SDK_BUILTIN_TOOLS_DISABLED,
                allowed_tools=optimization_allowed,
                permission_mode=self._cfg.permission_mode,
                max_buffer_size=self._SDK_MAX_BUFFER_SIZE,
            )
            log_line(
                f"P0 revert sub-round: iteration {it}, base={base[:12]}…",
                loop_log,
            )
            cc = await sdk_client_with_retry(
                co,
                retries=self._SDK_CONNECT_RETRIES,
                delay=self._SDK_CONNECT_RETRY_DELAY,
                log_file=loop_log,
            )
            async with cc:
                await cc.query(
                    self._p0_revert_user_message(it=it, revert_base_sha=revert_base_sha)
                )
                async for message in cc.receive_response():
                    log_sdk_message(
                        message,
                        loop_log=loop_log,
                        tool_result_max_chars=self._SDK_LOG_TOOL_RESULT_MAX_CHARS,
                        stream_event_max_chars=self._SDK_LOG_STREAM_EVENT_MAX_CHARS,
                        text_block_max_chars=self._SDK_LOG_TEXT_BLOCK_MAX_CHARS,
                        system_data_max_chars=self._SDK_LOG_SYSTEM_DATA_MAX_CHARS,
                    )
        finally:
            state.p0_revert_allowed_base_sha = None

        reports = [
            row
            for row in state.optimization_reports[n_reports_before:]
            if int(row.get("iteration_index", -1)) == it
            and int(row.get("optimization_round", -1)) == rnd
        ]
        if not reports:
            log_line(
                "warning: P0 revert agent did not call report_optimization_result "
                f"for iteration {it} round {rnd}",
                loop_log,
            )
            return 1
        return 0

    async def _run_optimization_followups_if_needed(
        self,
        *,
        it: int,
        state: AgentLoopSharedState,
        mcp_server: Any,
        loop_log: TextIO,
    ) -> int:
        """Run optimization agent(s) if needed.

        Returns:
            0: ok
            1: warning (e.g. missing report_optimization_result)
        """
        delegations = self._optimization_escalations_for_iteration(it, state)
        if not delegations:
            return 0

        from claude_agent_sdk import ClaudeAgentOptions

        from evaluation.devshell_agent.sdk_tools import MatmasterEvalMcpToolkit

        optimization_allowed = MatmasterEvalMcpToolkit.optimization_agent_tool_names()
        warning = 0
        for delegation in delegations:
            n_reports_before = len(state.optimization_reports)
            co = ClaudeAgentOptions(
                system_prompt=self.SYSTEM_PROMPT_OPTIMIZATION,
                cwd=str(self._cfg.repo_root.resolve()),
                max_turns=max(32, int(self._cfg.defaults.jobs) * 6),
                mcp_servers={MatmasterEvalMcpToolkit.MCP_SERVER_NAME: mcp_server},
                tools=_DEVSHELL_SDK_BUILTIN_TOOLS_DISABLED,
                allowed_tools=optimization_allowed,
                permission_mode=self._cfg.permission_mode,
                max_buffer_size=self._SDK_MAX_BUFFER_SIZE,
            )
            log_line(
                "optimization agent: "
                f"iteration {it}, round {delegation.get('optimization_round')}",
                loop_log,
            )
            cc = await sdk_client_with_retry(
                co,
                retries=self._SDK_CONNECT_RETRIES,
                delay=self._SDK_CONNECT_RETRY_DELAY,
                log_file=loop_log,
            )
            async with cc:
                await cc.query(
                    self._optimization_user_message(it=it, delegation=delegation)
                )
                async for message in cc.receive_response():
                    log_sdk_message(
                        message,
                        loop_log=loop_log,
                        tool_result_max_chars=self._SDK_LOG_TOOL_RESULT_MAX_CHARS,
                        stream_event_max_chars=self._SDK_LOG_STREAM_EVENT_MAX_CHARS,
                        text_block_max_chars=self._SDK_LOG_TEXT_BLOCK_MAX_CHARS,
                        system_data_max_chars=self._SDK_LOG_SYSTEM_DATA_MAX_CHARS,
                    )

            reports = [
                row
                for row in state.optimization_reports[n_reports_before:]
                if int(row.get("iteration_index", -1)) == it
                and int(row.get("optimization_round", -1))
                == int(delegation.get("optimization_round", -1))
            ]
            if not reports:
                log_line(
                    "warning: optimization agent did not call "
                    f"report_optimization_result for iteration {it} round "
                    f"{delegation.get('optimization_round')}",
                    loop_log,
                )
                warning = 1
            else:
                self._apply_optimization_auto_commit(
                    it=it,
                    delegation=delegation,
                    state=state,
                    loop_log=loop_log,
                )
            notify_proposed_matmaster_exps_if_present(
                session_dir=self._cfg.session_dir,
                iteration_index=it,
                delegation=delegation,
                optimization_reports=reports,
            )

        state.optimization_delegations_pending = [
            row
            for row in state.optimization_delegations_pending
            if int(row.get("iteration_index", -1)) != it
        ]
        return warning

    async def _run_checklist_followup_if_needed(
        self,
        *,
        it: int,
        state: AgentLoopSharedState,
        mcp_server: Any,
        loop_log: TextIO,
    ) -> int:
        """Run checklist agent if needed.

        Returns:
            0: ok
            1: warning (e.g. missing report_checklist_revision)
            2: stop outer loop — question_bank id set changed or unloadable after follow-up
        """
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

        from evaluation.devshell_agent.question_bank_ids import (
            collect_question_bank_question_ids,
        )

        ids_before: frozenset[str] | None
        try:
            ids_before = collect_question_bank_question_ids(cfg.repo_root)
        except Exception as e:
            log_line(
                f"warning: cannot snapshot question_bank ids before checklist "
                f"(id-drift guard skipped): {e}",
                loop_log,
            )
            ids_before = None

        from claude_agent_sdk import ClaudeAgentOptions

        from evaluation.devshell_agent.sdk_tools import MatmasterEvalMcpToolkit

        n_reports_before = len(state.checklist_revision_reports)
        checklist_allowed = MatmasterEvalMcpToolkit.checklist_agent_tool_names()
        co = ClaudeAgentOptions(
            system_prompt=self.SYSTEM_PROMPT_CHECKLIST,
            cwd=str(cfg.repo_root.resolve()),
            max_turns=checklist_max_turns_for_shared_state(state),
            mcp_servers={MatmasterEvalMcpToolkit.MCP_SERVER_NAME: mcp_server},
            tools=_DEVSHELL_SDK_BUILTIN_TOOLS_DISABLED,
            allowed_tools=checklist_allowed,
            permission_mode=self._checklist_permission_mode_resolved(),
            max_buffer_size=self._SDK_MAX_BUFFER_SIZE,
        )
        log_line(
            f"checklist agent: iteration {it}, {len(escalations)} escalation(s)",
            loop_log,
        )
        cc = await sdk_client_with_retry(
            co,
            retries=self._SDK_CONNECT_RETRIES,
            delay=self._SDK_CONNECT_RETRY_DELAY,
            log_file=loop_log,
        )
        async with cc:
            await cc.query(self._checklist_user_message(it=it, escalations=escalations))
            async for message in cc.receive_response():
                log_sdk_message(
                    message,
                    loop_log=loop_log,
                    tool_result_max_chars=self._SDK_LOG_TOOL_RESULT_MAX_CHARS,
                    stream_event_max_chars=self._SDK_LOG_STREAM_EVENT_MAX_CHARS,
                    text_block_max_chars=self._SDK_LOG_TEXT_BLOCK_MAX_CHARS,
                    system_data_max_chars=self._SDK_LOG_SYSTEM_DATA_MAX_CHARS,
                )

        state.checklist_escalations_pending = [
            e
            for e in state.checklist_escalations_pending
            if int(e.get("iteration_index", -1)) != it
        ]
        new_reports = state.checklist_revision_reports[n_reports_before:]
        ok_reports = [r for r in new_reports if int(r.get("iteration_index", -1)) == it]
        notify_proposed_question_bank_if_present(
            session_dir=self._cfg.session_dir,
            iteration_index=it,
            checklist_reports=ok_reports,
        )
        if not ok_reports:
            log_line(
                f"warning: checklist agent did not call report_checklist_revision "
                f"for iteration {it}",
                loop_log,
            )
            return 1

        if ids_before is not None:
            try:
                ids_after = collect_question_bank_question_ids(cfg.repo_root)
            except Exception as e:
                log_line(
                    f"error: question_bank unreadable after checklist agent: {e}",
                    loop_log,
                )
                self._write_question_bank_id_drift(
                    it=it, ids_removed=[], ids_added=[], load_error=str(e)
                )
                return 2

            if ids_before != ids_after:
                removed = sorted(ids_before - ids_after)
                added = sorted(ids_after - ids_before)
                log_line(
                    f"checklist follow-up changed question_bank id set; stopping "
                    f"outer loop (removed={removed!r} added={added!r})",
                    loop_log,
                )
                self._write_question_bank_id_drift(
                    it=it, ids_removed=removed, ids_added=added
                )
                return 2

        return 0

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
