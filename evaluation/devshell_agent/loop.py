"""Claude Agent SDK session: multi-iteration DevShell eval → judge → edit loop."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from evaluation.devshell_agent.config_state import (
    AgentLoopSharedState,
    DevshellAgentCliDefaults,
    checklist_revision_sdk_max_turns_from_jobs,
    parallel_scoring_checklist_workers_from_jobs,
)
from evaluation.devshell_agent.git_iteration import (
    append_iteration_head,
    git_reset_hard,
    git_rev_parse_head,
    head_at_iteration_start,
)


def checklist_max_turns_for_shared_state(state: AgentLoopSharedState) -> int:
    """Claude SDK ``max_turns`` for the question_bank checklist-revision agent."""
    return checklist_revision_sdk_max_turns_from_jobs(int(state.defaults.jobs))


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
    checklist_permission_mode: str = ""
    history_root: Path | None = None


class DevshellAgentLoop:
    """Runs the Claude SDK client for multiple outer iterations."""

    # Upper bounds for sdk_loop_console.log lines (avoid huge single lines / OOM).
    _SDK_LOG_TOOL_RESULT_MAX_CHARS = 24_000
    _SDK_LOG_STREAM_EVENT_MAX_CHARS = 4_000
    _SDK_LOG_TEXT_BLOCK_MAX_CHARS = 100_000
    _SDK_LOG_SYSTEM_DATA_MAX_CHARS = 24_000

    SYSTEM_PROMPT_MAIN = """你是 MatMaster 仓库内的 **DevShell 评测迭代编排助手（产品 / Agent 行为侧）**。

## 工具分工
- **run_devshell_eval**：在仓库根目录下执行 `evaluation/scripts/devshell/run_devshell_eval.py`（子进程，优先 `uv run python`）。输出目录为会话下的 `eval_runs/<iteration_tag>/`，并返回**脱敏后的**评分摘要。
- **report_iteration_outcome**：每一轮结束时**必须**调用一次，记录宏平均分数与是否达标。
- **escalate_checklist_revision**：当你判断低分主要来自 **题库评分项 / scoring_checklist / reference_answers** 不公或错误时调用；**不得**亲自改题库。编排器会在本轮主会话结束后启动**另一 Agent** 专改 `evaluation/question_bank/`。
- **delegate_optimization**：当你判断问题主要在产品侧实现、提示或工具契约时调用。编排器会在本轮主会话结束后启动**另一 Agent** 专做产品侧优化。

## 防作弊：题库与 checklist（硬约束）
- **禁止**读取 `evaluation/**`，也**禁止**编辑任何代码或文件。
- 需要调整评测标准时：调用 **escalate_checklist_revision**，由 checklist 专责 Agent 执行。
- 需要产品侧优化时：调用 **delegate_optimization**，由 optimization 专责 Agent 执行。
- 你的职责是根据 `run_devshell_eval` 返回的**脱敏摘要**做分流、总结与停止决策，而不是亲自改仓库。

## Git 工作流（自迭代必守）
- 你自己**不提交代码改动**；产品侧 commit 由 optimization Agent 执行，题库/evaluator 侧 commit 由 checklist Agent 执行。
- 你需要在每轮总结里如实说明本轮触发了哪些子 Agent、它们是否产出了 commit，以及为何继续或停止。

## 判分原则（与 `evaluation/docs/devshell/devshell_claude_code_eval.md` 一致）
- 单次任务的**权威判分**来自 `evaluation/scripts/devshell/score_devshell_tasks.py`（`BinaryEvaluator`，基于 `raw_runs.jsonl`、`workspaces/<task_id>/` 与 `logs/<task_id>/events_*.jsonl`）。
- 你看到的是编排器提供的**脱敏摘要**，其中宏平均与任务分数仍与 `pending_ingest` 口径一致，但不暴露原始 `score_reason` 文本。
- 你**不得**自行再读题库、evaluator 或原始 checklist 文本来解释低分。

## 修改范围
- 你自己**不可写任何路径**。
- 对产品侧的建议应通过 **delegate_optimization** 转交。
- 对评测侧的建议应通过 **escalate_checklist_revision** 转交。

## 产品侧改动优先级与系统提示词泛化（硬约束）
- **优先顺序**：先 **`matmaster/skills/`**（领域流程与可复用约束；**现有 Skill 不足时允许新建**，见上节 `skills_root` 约定）、再 **`matmaster/tools/`**（工具行为与描述），然后 **`config/`**、MCP、`matmaster/adaptors/calculation/`、`matmaster/devshell/` 等；**`matmaster/exps/` 仅在与「通用角色 / 安全 / 全会话一致的工作方式」相关、且难以在 Skill 或工具中表达时再改**，且每次改动都须能说明**为何不**放在 skills/tools。
- **`matmaster/exps/` 中的系统提示与 developer 指令须保持通用**：不得把某次评测里具体题目的 **`scoring_checklist` 逐条改写进 TOML**、不得仅为对齐某题判分项而堆叠题目专属规则（这是对题库的**过拟合**，会损害非评测场景下的行为与可维护性）。
- 若 `item.score_reason` 指向 checklist 某条：先判断能否用 **Skill 文案** 或 **工具契约** 稳定满足该类要求；确需动 exp 时，只增加**可跨题复用**的抽象表述，并仍遵守下文 token 预算与 `exp_prompt_budget`。

## MatMaster 实验提示词（优化策略 + 体量硬上限）
- **优先删减与合并**：在增补新规则前，先删除或合并与 `_base.toml` / 同文件内已有条目**重复、矛盾或过时**的表述；禁止仅靠堆叠新段落规避问题。
- **系统 prompt token 预算**：对 `ContextBuilder.build()` 产出的**完整初始系统 prompt**（含 `system_prompt` + `developer_instructions` + tool descriptions + skill meta info）使用 tiktoken **gpt-4o 编码**计数；**推荐控制在 12000 以内**，**硬上限为 15000（含 15000）**。
- **自检命令**：每次修改 `matmaster/exps/` 下相关 TOML 后、在 `git commit` 前于仓库根执行
  `uv run python -m evaluation.devshell_agent.exp_prompt_budget <exp>`
  其中 `<exp>` 与本轮 `run_devshell_eval` 所用 `--exp` 一致；若未传 `--exp`，默认按 `direct` 自检（若你改的是其它 exp 名则改用该名）。**命令 exit 非 0 时不得提交**，应先压缩文案直至达标。

## 轮次结束
- 调用 **report_iteration_outcome**，`iteration_index` 必须与当前轮次编号一致，`macro_mean_0_100` 为整数 0–100，`target_met` 表示是否达到用户给定目标分，`rationale` 用 Markdown 简述判分与下一步。
"""

    SYSTEM_PROMPT_CHECKLIST = """你是 MatMaster 仓库内的 **DevShell 评测迭代 — checklist / 题库专责助手**。

你与上一会话中的「产品侧」Agent **不是同一角色**：你只负责 **评测语义与题库 YAML / evaluator**，不负责改 `config/`、`matmaster/exps/`、`matmaster/skills/`、`matmaster/tools/`、`matmaster/adaptors/`、`matmaster/devshell/`、`matmaster/core/` 等产品侧目录。

## 硬约束
- **仅允许**使用 Edit/Write 修改路径前缀为 `evaluation/question_bank/`（题库 YAML）或 `evaluation/core/`（evaluator / checker 代码）的文件。**禁止**编辑产品侧目录（`config/`、`matmaster/exps/`、`matmaster/skills/`、`matmaster/tools/`、`matmaster/adaptors/`、`matmaster/devshell/`、`matmaster/core/` 等）及 `evaluation/scripts/`。
- 修改 `scoring_checklist`、`reference_answers`、题干等时遵守仓库 `evaluation/AGENTS_evaluation.md`：若变更影响评测语义，须按该文档更新对应题目的顶层 `id`。
- 使用 **Read / Glob / Grep** 阅读证据（含本会话目录下的 `eval_runs/`、workspace、events、题库）。
- **report_checklist_revision**：本专责回合结束时**必须**调用一次，说明是否改动了题库、改了哪些文件、或为何维持不变。

## Git
- 每次改动题库后单独 `git commit`，消息建议 `devshell_agent_checklist iter=<轮次> <简述>`。

## 工具
- 无 `run_devshell_eval`；不调用 `report_iteration_outcome` 或 `escalate_checklist_revision`。仅使用 **report_checklist_revision** 与本仓库读写工具。
"""

    SYSTEM_PROMPT_OPTIMIZATION = """你是 MatMaster 仓库内的 **DevShell 评测迭代 — 产品侧优化助手**。

你与主 Agent、Checklist Agent 都不是同一角色：你只负责 **产品侧代码与提示优化**，不得修改或读取 `evaluation/` 下任何目录，不得查看题库、reference_answers、scoring_checklist 或评测代码。

## 硬约束
- **禁止**读取或编辑 `evaluation/**`。
- **允许**编辑产品侧路径，如 `matmaster/`、`config/`，以及在失败明确与服务链路相关时审慎编辑 `src/`。
- 仅根据主 Agent 交给你的**脱敏问题摘要**与允许查看的非 `evaluation/` 证据路径工作。
- 结束前**必须**调用 **report_optimization_result**，汇报本次子回合的修改摘要、文件与 commit。

## Git
- 本子回合内的实质性修改应独立 commit，消息建议 `devshell_agent_opt iter=<轮次> round=<子回合> <简述>`。
"""

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

    @staticmethod
    def _log_line(msg: str, loop_log: TextIO) -> None:
        print(msg, file=sys.stderr, flush=True)
        loop_log.write(msg + "\n")
        loop_log.flush()

    @classmethod
    def _truncate_log_text(cls, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + f"\n… [log truncated, total_len={len(text)} chars]"

    def _log_content_block_sdk(
        self,
        block: Any,
        prefix: str,
        loop_log: TextIO,
    ) -> None:
        from claude_agent_sdk import (  # type: ignore[import-untyped]
            TextBlock,
            ThinkingBlock,
            ToolResultBlock,
            ToolUseBlock,
        )

        tmax = self._SDK_LOG_TOOL_RESULT_MAX_CHARS
        xmax = self._SDK_LOG_TEXT_BLOCK_MAX_CHARS

        if isinstance(block, TextBlock):
            raw = block.text
            if not raw.strip():
                self._log_line(f"{prefix}:text (empty)", loop_log)
            else:
                self._log_line(
                    f"{prefix}:text\n{self._truncate_log_text(raw, xmax)}",
                    loop_log,
                )
        elif isinstance(block, ThinkingBlock):
            sig = block.signature
            sig_short = f"{sig[:24]}…" if len(sig) > 24 else sig
            self._log_line(
                f"{prefix}:thinking chars={len(block.thinking)} "
                f"signature_prefix={sig_short!r}",
                loop_log,
            )
            if block.thinking.strip():
                self._log_line(
                    f"{prefix}:thinking_body\n"
                    f"{self._truncate_log_text(block.thinking, tmax)}",
                    loop_log,
                )
        elif isinstance(block, ToolUseBlock):
            try:
                inp = json.dumps(block.input, ensure_ascii=False, default=str)
            except TypeError:
                inp = repr(block.input)
            inp = self._truncate_log_text(inp, tmax)
            self._log_line(
                f"{prefix}:tool_use id={block.id!r} name={block.name!r} input={inp}",
                loop_log,
            )
        elif isinstance(block, ToolResultBlock):
            c = block.content
            if c is None:
                body = "(none)"
            elif isinstance(c, list):
                try:
                    body = json.dumps(c, ensure_ascii=False, default=str)
                except TypeError:
                    body = repr(c)
            else:
                body = str(c)
            self._log_line(
                f"{prefix}:tool_result tool_use_id={block.tool_use_id!r} "
                f"is_error={block.is_error!r}",
                loop_log,
            )
            self._log_line(
                f"{prefix}:tool_result_body\n{self._truncate_log_text(body, tmax)}",
                loop_log,
            )
        else:
            self._log_line(
                f"{prefix}:unknown_block {type(block).__name__} {block!r}", loop_log
            )

    def _log_sdk_message(self, message: Any, loop_log: TextIO) -> None:
        """Log one Claude Agent SDK stream message (all known types)."""
        from claude_agent_sdk import (  # type: ignore[import-untyped]
            AssistantMessage,
            RateLimitEvent,
            ResultMessage,
            StreamEvent,
            SystemMessage,
            TaskNotificationMessage,
            TaskProgressMessage,
            TaskStartedMessage,
            UserMessage,
        )

        tmax = self._SDK_LOG_TOOL_RESULT_MAX_CHARS
        smax = self._SDK_LOG_SYSTEM_DATA_MAX_CHARS
        xev = self._SDK_LOG_STREAM_EVENT_MAX_CHARS

        def j(x: Any) -> str:
            try:
                return json.dumps(x, ensure_ascii=False, default=str)
            except TypeError:
                return repr(x)

        if isinstance(message, UserMessage):
            self._log_line(
                f"[sdk:user] uuid={message.uuid!r} "
                f"parent_tool_use_id={message.parent_tool_use_id!r}",
                loop_log,
            )
            if message.tool_use_result is not None:
                self._log_line(
                    f"[sdk:user] tool_use_result="
                    f"{self._truncate_log_text(j(message.tool_use_result), tmax)}",
                    loop_log,
                )
            c = message.content
            if isinstance(c, str):
                if c.strip():
                    self._log_line(
                        "[sdk:user:text]\n"
                        f"{self._truncate_log_text(c, self._SDK_LOG_TEXT_BLOCK_MAX_CHARS)}",
                        loop_log,
                    )
                else:
                    self._log_line("[sdk:user:text] (empty)", loop_log)
            else:
                for i, block in enumerate(c):
                    self._log_content_block_sdk(
                        block, f"[sdk:user:block:{i}]", loop_log
                    )
            return

        if isinstance(message, AssistantMessage):
            parts = [
                f"model={message.model!r}",
                f"message_id={message.message_id!r}",
                f"uuid={message.uuid!r}",
                f"parent_tool_use_id={message.parent_tool_use_id!r}",
            ]
            if message.error:
                parts.append(f"error={message.error!r}")
            if message.stop_reason:
                parts.append(f"stop_reason={message.stop_reason!r}")
            if message.usage is not None:
                parts.append(f"usage={j(message.usage)}")
            self._log_line("[sdk:assistant] " + " ".join(parts), loop_log)
            for i, block in enumerate(message.content):
                self._log_content_block_sdk(
                    block, f"[sdk:assistant:block:{i}]", loop_log
                )
            return

        if isinstance(message, TaskStartedMessage):
            self._log_line(
                f"[sdk:system:task_started] subtype={message.subtype!r} "
                f"task_id={message.task_id!r} description={message.description!r} "
                f"uuid={message.uuid!r} session_id={message.session_id!r} "
                f"tool_use_id={message.tool_use_id!r} task_type={message.task_type!r}",
                loop_log,
            )
            return

        if isinstance(message, TaskProgressMessage):
            self._log_line(
                f"[sdk:system:task_progress] subtype={message.subtype!r} "
                f"task_id={message.task_id!r} description={message.description!r} "
                f"uuid={message.uuid!r} session_id={message.session_id!r} "
                f"tool_use_id={message.tool_use_id!r} "
                f"last_tool_name={message.last_tool_name!r} "
                f"usage={j(message.usage)}",
                loop_log,
            )
            return

        if isinstance(message, TaskNotificationMessage):
            self._log_line(
                f"[sdk:system:task_notification] subtype={message.subtype!r} "
                f"task_id={message.task_id!r} status={message.status!r} "
                f"output_file={message.output_file!r} summary={message.summary!r} "
                f"uuid={message.uuid!r} session_id={message.session_id!r} "
                f"tool_use_id={message.tool_use_id!r}",
                loop_log,
            )
            if message.usage is not None:
                self._log_line(
                    f"[sdk:system:task_notification:usage] {j(message.usage)}",
                    loop_log,
                )
            return

        if isinstance(message, SystemMessage):
            self._log_line(
                f"[sdk:system] subtype={message.subtype!r} "
                f"data={self._truncate_log_text(j(message.data), smax)}",
                loop_log,
            )
            return

        if isinstance(message, ResultMessage):
            self._log_line(
                f"[sdk:result] subtype={message.subtype!r} num_turns={message.num_turns} "
                f"duration_ms={message.duration_ms} duration_api_ms={message.duration_api_ms} "
                f"is_error={message.is_error} stop_reason={message.stop_reason!r} "
                f"total_cost_usd={message.total_cost_usd!r} session_id={message.session_id!r} "
                f"uuid={message.uuid!r}",
                loop_log,
            )
            if message.usage:
                self._log_line(f"[sdk:result:usage] {j(message.usage)}", loop_log)
            if message.model_usage:
                self._log_line(
                    f"[sdk:result:model_usage] {j(message.model_usage)}", loop_log
                )
            if message.errors:
                self._log_line(f"[sdk:result:errors] {j(message.errors)}", loop_log)
            if message.permission_denials:
                self._log_line(
                    f"[sdk:result:permission_denials] "
                    f"{self._truncate_log_text(j(message.permission_denials), tmax)}",
                    loop_log,
                )
            if message.result:
                self._log_line(
                    f"[sdk:result:result]\n"
                    f"{self._truncate_log_text(message.result, tmax)}",
                    loop_log,
                )
            if message.structured_output is not None:
                self._log_line(
                    f"[sdk:result:structured_output] "
                    f"{self._truncate_log_text(j(message.structured_output), tmax)}",
                    loop_log,
                )
            return

        if isinstance(message, StreamEvent):
            ev = j(message.event)
            self._log_line(
                f"[sdk:stream_event] uuid={message.uuid!r} "
                f"session_id={message.session_id!r} "
                f"parent_tool_use_id={message.parent_tool_use_id!r} "
                f"event={self._truncate_log_text(ev, xev)}",
                loop_log,
            )
            return

        if isinstance(message, RateLimitEvent):
            ri = message.rate_limit_info
            self._log_line(
                f"[sdk:rate_limit] uuid={message.uuid!r} session_id={message.session_id!r} "
                f"status={ri.status!r} rate_limit_type={ri.rate_limit_type!r} "
                f"resets_at={ri.resets_at!r} utilization={ri.utilization!r}",
                loop_log,
            )
            if ri.raw:
                self._log_line(
                    f"[sdk:rate_limit:raw] {self._truncate_log_text(j(ri.raw), smax)}",
                    loop_log,
                )
            return

        self._log_line(
            f"[sdk:unhandled] {type(message).__name__}: {message!r}", loop_log
        )

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

- **目标宏平均分数**：{cfg.target_mean_score}/100（若 `macro_mean_0_100 >= {cfg.target_mean_score}` 或你认为已充分达标，将 `target_met` 设为 true）。
- **会话目录**（本机路径，用于阅读产物）：`{session_dir}`

### 你必须完成的步骤
1. 调用 **run_devshell_eval**，`iteration_tag` 使用新目录名（建议 `iter_{it:02d}`），勿复用旧 tag。
2. 读取 **run_devshell_eval** 返回的**脱敏摘要**，以其中的 `macro_mean_0_100` 与 `task_scores` 作为本轮判断依据。不要自行读取 `evaluation/**` 或原始 `score_reason`。
3. 若未达标：根据脱敏摘要做分流。若问题更像产品侧实现/提示问题，调用 **delegate_optimization**；若问题更像 checklist / reference answers / evaluator 口径问题，调用 **escalate_checklist_revision**。你可以在同一轮内多次调用 `delegate_optimization`，但你**不能**亲自改文件。
4. 调用 **report_iteration_outcome**（`iteration_index={it}`），填写**反映当前仓库状态**的真实宏平均与 `files_touched`（主 Agent 自身通常为空）；在 `rationale` 中总结本轮分流、子 Agent 结果与下一步。
{extra_block}
"""

    def _write_session_manifest(self) -> None:
        cfg = self._cfg
        session_dir = cfg.session_dir
        payload = {
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "repo_root": str(cfg.repo_root.resolve()),
            "session_dir": str(session_dir.resolve()),
            "history_root": str(self._history_root()),
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
            "enable_optimization_agent": True,
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
            allowed_tools=allowed_tools,
            permission_mode=cfg.permission_mode,
        )

        self._write_session_manifest()

        log_path = cfg.session_dir / "sdk_loop_console.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        exit_code = 0
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
                        self._log_sdk_message(message, loop_log)

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
                        self._log_line(
                            "Stopping outer iterations: question_bank question id set "
                            "changed during checklist follow-up (see "
                            "question_bank_id_drift.json in session dir).",
                            loop_log,
                        )
                        break

                    matching = [
                        o
                        for o in state.outcomes
                        if int(o.get("iteration_index", -1)) == it
                    ]
                    if not matching:
                        self._log_line(
                            f"warning: no report_iteration_outcome for iteration {it}",
                            loop_log,
                        )
                        exit_code = 1
                        continue

                    last = matching[-1]
                    self._write_iteration_history(it=it, state=state, outcome=last)
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
                                    self._log_line(
                                        f"git regression guard: iter {it} mean {score} "
                                        f"< iter {it - 1} mean {prev_score}; "
                                        f"reset --hard {saved[:7]}… -> "
                                        f"{'ok' if ok else 'failed'} {msg}",
                                        loop_log,
                                    )
                                    if not ok:
                                        exit_code = 1
                                else:
                                    self._log_line(
                                        f"warning: regression at iter {it} but no "
                                        f"head_at_start recorded; skip auto reset",
                                        loop_log,
                                    )

                    if met or score >= cfg.target_mean_score:
                        self._log_line(
                            f"Stopping after iteration {it}: target_met={met} "
                            f"macro_mean={score} (target {cfg.target_mean_score})",
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

- **会话目录**（只读产物）：`{session_dir}`
- 任务：仅在 **evaluation/question_bank/** 内做必要 YAML 修订；若判分器 / validator 本身存在明确口径缺陷，可在 **evaluation/core/** 内做对应修复。遵守 `evaluation/AGENTS_evaluation.md` 的题库 `id` 规则。
- 结束前**必须**调用 **report_checklist_revision**（`iteration_index={it}`）。
"""

    def _optimization_user_message(
        self, *, it: int, delegation: dict[str, Any]
    ) -> str:
        session_dir = self._cfg.session_dir.resolve()
        blob = json.dumps(delegation, ensure_ascii=False, indent=2)
        return f"""## 产品侧优化子回合（第 {it} 轮）

主 Agent 已提交以下 **delegate_optimization** 工单（JSON）：

```json
{blob}
```

- **会话目录**（仅供查看非 `evaluation/` 产物）：`{session_dir}`
- 任务：仅在**产品侧目录**做必要优化，不得查看或编辑 `evaluation/**`。
- 结束前**必须**调用 **report_optimization_result**（`iteration_index={it}`，`optimization_round={delegation.get("optimization_round")}`）。
"""

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

        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

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
                allowed_tools=optimization_allowed,
                permission_mode=self._cfg.permission_mode,
            )
            self._log_line(
                "optimization agent: "
                f"iteration {it}, round {delegation.get('optimization_round')}",
                loop_log,
            )
            async with ClaudeSDKClient(options=co) as cc:
                await cc.query(
                    self._optimization_user_message(it=it, delegation=delegation)
                )
                async for message in cc.receive_response():
                    self._log_sdk_message(message, loop_log)

            reports = [
                row
                for row in state.optimization_reports[n_reports_before:]
                if int(row.get("iteration_index", -1)) == it
                and int(row.get("optimization_round", -1))
                == int(delegation.get("optimization_round", -1))
            ]
            if not reports:
                self._log_line(
                    "warning: optimization agent did not call "
                    f"report_optimization_result for iteration {it} round "
                    f"{delegation.get('optimization_round')}",
                    loop_log,
                )
                warning = 1

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
            self._log_line(
                f"warning: cannot snapshot question_bank ids before checklist "
                f"(id-drift guard skipped): {e}",
                loop_log,
            )
            ids_before = None

        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        from evaluation.devshell_agent.sdk_tools import MatmasterEvalMcpToolkit

        n_reports_before = len(state.checklist_revision_reports)
        checklist_allowed = MatmasterEvalMcpToolkit.checklist_agent_tool_names()
        co = ClaudeAgentOptions(
            system_prompt=self.SYSTEM_PROMPT_CHECKLIST,
            cwd=str(cfg.repo_root.resolve()),
            max_turns=checklist_max_turns_for_shared_state(state),
            mcp_servers={MatmasterEvalMcpToolkit.MCP_SERVER_NAME: mcp_server},
            allowed_tools=checklist_allowed,
            permission_mode=self._checklist_permission_mode_resolved(),
        )
        self._log_line(
            f"checklist agent: iteration {it}, {len(escalations)} escalation(s)",
            loop_log,
        )
        async with ClaudeSDKClient(options=co) as cc:
            await cc.query(self._checklist_user_message(it=it, escalations=escalations))
            async for message in cc.receive_response():
                self._log_sdk_message(message, loop_log)

        state.checklist_escalations_pending = [
            e
            for e in state.checklist_escalations_pending
            if int(e.get("iteration_index", -1)) != it
        ]
        new_reports = state.checklist_revision_reports[n_reports_before:]
        ok_reports = [r for r in new_reports if int(r.get("iteration_index", -1)) == it]
        if not ok_reports:
            self._log_line(
                f"warning: checklist agent did not call report_checklist_revision "
                f"for iteration {it}",
                loop_log,
            )
            return 1

        if ids_before is not None:
            try:
                ids_after = collect_question_bank_question_ids(cfg.repo_root)
            except Exception as e:
                self._log_line(
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
                self._log_line(
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
