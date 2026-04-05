"""In-process MCP tools for Claude Agent SDK (requires ``claude-agent-sdk``)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool  # type: ignore[import-untyped]

from evaluation.devshell_agent.config_state import (
    AgentLoopSharedState,
    DevshellAgentCliDefaults,
)
from evaluation.devshell_agent.subprocess_runner import (
    DevshellEvalSubprocess,
    RunDevshellEvalParams,
    run_score_devshell_tasks_submit,
)


class MatmasterEvalMcpToolkit:
    """Builds the in-process MCP server bound to a shared :class:`AgentLoopSharedState`."""

    MCP_SERVER_NAME = "matmaster_eval"

    RUN_DEVSHELL_EVAL_SCHEMA: dict[str, Any] = {
        "type": "object",
        "properties": {
            "iteration_tag": {
                "type": "string",
                "description": (
                    "Directory name under session eval_runs/, e.g. iter_01. "
                    "Each call should use a fresh tag."
                ),
            },
            "modes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Forwarded to run_devshell_eval --modes (default: CLI defaults).",
            },
            "jobs": {
                "type": "integer",
                "description": "Parallel mm-devshell tasks (default: CLI defaults).",
            },
            "limit": {
                "type": "integer",
                "description": "Max plan items (default: CLI defaults).",
            },
            "questions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Question IDs (default: CLI defaults).",
            },
            "capabilities": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Capability filter (default: CLI defaults).",
            },
            "model": {
                "type": "string",
                "description": (
                    "LLM route for inner mm-devshell --model (default: claude-opus-4-6)."
                ),
            },
            "exp": {
                "type": "string",
                "description": "Optional mm-devshell --exp (default: CLI defaults).",
            },
            "eval_ingest_pending_only": {
                "type": "boolean",
                "description": (
                    "Whether to write pending_ingest without POST (default: CLI defaults)."
                ),
            },
            "no_export_review": {
                "type": "boolean",
                "description": "Skip claude_review.md export (default: CLI defaults).",
            },
            "task_timeout_sec": {
                "type": "number",
                "description": (
                    "Per-task wall timeout for mm-devshell (default: CLI defaults)."
                ),
            },
            "extra_args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Extra argv tokens appended to run_devshell_eval.py.",
            },
        },
        "required": ["iteration_tag"],
    }

    REPORT_ITERATION_SCHEMA: dict[str, Any] = {
        "type": "object",
        "properties": {
            "iteration_index": {
                "type": "integer",
                "description": "1-based iteration index matching the user message.",
            },
            "macro_mean_0_100": {
                "type": "integer",
                "description": "Macro-averaged 0–100 score from score_devshell_tasks.py.",
            },
            "target_met": {
                "type": "boolean",
                "description": "True if macro_mean_0_100 >= configured target.",
            },
            "rationale": {
                "type": "string",
                "description": (
                    "Short Markdown: auto-score summary, low-score evidence paths, stop/continue."
                ),
            },
            "files_touched": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Repo-relative paths edited this iteration (if any).",
            },
        },
        "required": [
            "iteration_index",
            "macro_mean_0_100",
            "target_met",
            "rationale",
        ],
    }

    ESCALATE_CHECKLIST_SCHEMA: dict[str, Any] = {
        "type": "object",
        "properties": {
            "iteration_index": {
                "type": "integer",
                "description": "Same 1-based iteration as the current user message.",
            },
            "question_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Question id(s) whose scoring_checklist / rubric seem unfair.",
            },
            "rationale": {
                "type": "string",
                "description": (
                    "Why the checklist or reference_answers need human-aligned fixes; "
                    "cite evidence paths (logs, workspace, YAML)."
                ),
            },
            "evidence_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional repo-relative or session paths supporting the case.",
            },
        },
        "required": ["iteration_index", "rationale"],
    }

    REPORT_CHECKLIST_REVISION_SCHEMA: dict[str, Any] = {
        "type": "object",
        "properties": {
            "iteration_index": {
                "type": "integer",
                "description": "Must match the checklist follow-up round.",
            },
            "no_changes": {
                "type": "boolean",
                "description": "True if after review no YAML edit was needed.",
            },
            "rationale": {
                "type": "string",
                "description": "What was reviewed and what was changed or skipped.",
            },
            "files_touched": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Repo-relative paths under evaluation/question_bank/ (if any).",
            },
        },
        "required": ["iteration_index", "no_changes", "rationale"],
    }

    def __init__(self, state: AgentLoopSharedState) -> None:
        self._state = state
        self._subprocess = DevshellEvalSubprocess(state.repo_root)

    @classmethod
    def main_agent_mcp_tool_names(cls) -> list[str]:
        return [
            f"mcp__{cls.MCP_SERVER_NAME}__run_devshell_eval",
            f"mcp__{cls.MCP_SERVER_NAME}__report_iteration_outcome",
            f"mcp__{cls.MCP_SERVER_NAME}__escalate_checklist_revision",
        ]

    @classmethod
    def checklist_agent_mcp_tool_names(cls) -> list[str]:
        return [f"mcp__{cls.MCP_SERVER_NAME}__report_checklist_revision"]

    @classmethod
    def allowed_tool_names(cls) -> list[str]:
        """MCP tools for the main (playground/product) iteration agent."""
        return cls.main_agent_mcp_tool_names()

    def _append_outcome_jsonl(self, row: dict[str, Any]) -> None:
        path = self._state.session_dir / "outcomes.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _append_checklist_revision_jsonl(self, row: dict[str, Any]) -> None:
        path = self._state.session_dir / "checklist_revisions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _merge_run_args(self, args: dict[str, Any]) -> RunDevshellEvalParams:
        d: DevshellAgentCliDefaults = self._state.defaults
        tag = str(args["iteration_tag"]).strip()
        out_dir = self._state.session_dir / "eval_runs" / tag
        modes = list(d.modes)
        if args.get("modes"):
            modes = list(args["modes"])
        jobs = d.jobs if args.get("jobs") is None else int(args["jobs"])
        limit = d.limit if args.get("limit") is None else int(args["limit"])
        questions = d.questions
        if args.get("questions") is not None:
            questions = list(args["questions"])
        capabilities = d.capabilities
        if args.get("capabilities") is not None:
            capabilities = list(args["capabilities"])
        model = d.model if args.get("model") is None else str(args["model"])
        exp = d.exp if args.get("exp") is None else str(args["exp"])
        pending = (
            d.eval_ingest_pending_only
            if args.get("eval_ingest_pending_only") is None
            else bool(args["eval_ingest_pending_only"])
        )
        no_rev = (
            d.no_export_review
            if args.get("no_export_review") is None
            else bool(args["no_export_review"])
        )
        task_timeout = (
            d.task_timeout_sec
            if args.get("task_timeout_sec") is None
            else float(args["task_timeout_sec"])
        )
        extra = list(d.extra_args)
        if args.get("extra_args") is not None:
            extra = list(args["extra_args"])
        return RunDevshellEvalParams(
            output_dir=out_dir,
            modes=modes,
            jobs=jobs,
            limit=limit,
            questions=questions,
            capabilities=capabilities,
            model=model,
            exp=exp,
            eval_ingest_pending_only=pending,
            no_export_review=no_rev,
            task_timeout_sec=task_timeout,
            eval_config=d.eval_config,
            extra_args=extra,
        )

    def _maybe_submit_run_dir_ingest(
        self,
        *,
        run_dir: Path,
        params: RunDevshellEvalParams,
    ) -> dict[str, Any]:
        state = self._state
        if not state.eval_ingest_submit_each_iteration:
            return {"attempted": False, "reason": "auto_submit_disabled"}
        if not params.eval_ingest_pending_only:
            return {"attempted": False, "reason": "pending_only_disabled"}
        if "--no-eval-ingest" in params.extra_args:
            return {"attempted": False, "reason": "eval_ingest_disabled"}
        if not (run_dir / "raw_runs.jsonl").is_file():
            return {"attempted": False, "reason": "missing_raw_runs_jsonl"}
        pending_dir = run_dir / "pending_ingest"
        if not pending_dir.is_dir() or not any(pending_dir.glob("*.json")):
            return {"attempted": False, "reason": "missing_pending_ingest"}

        rc, out, err = run_score_devshell_tasks_submit(
            repo_root=state.repo_root,
            run_dir=run_dir,
            eval_config=params.eval_config,
            eval_ingest_timeout=float(state.eval_ingest_submit_timeout),
        )
        log_path = state.session_dir / "ingest_submit.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_row = {
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
        return {
            "attempted": True,
            "ok": rc == 0,
            "exit_code": rc,
            "stdout_tail": (out or "")[-4000:],
            "stderr_tail": (err or "")[-4000:],
        }

    async def _run_devshell_eval(self, args: dict[str, Any]) -> dict[str, Any]:
        tag = str(args["iteration_tag"]).strip()
        if not tag or ".." in tag or "/" in tag or "\\" in tag:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "invalid iteration_tag (must be a single path segment)"
                        ),
                    }
                ],
                "is_error": True,
            }

        params = self._merge_run_args(args)
        params.output_dir.mkdir(parents=True, exist_ok=True)

        script = (
            self._state.repo_root
            / "evaluation"
            / "scripts"
            / "devshell"
            / "run_devshell_eval.py"
        )
        argv = self._subprocess.build_argv(script, params)
        rc, stdout, stderr = self._subprocess.run_capture(argv)
        self._state.last_eval_output_dir = params.output_dir
        self._state.eval_output_dirs.append(params.output_dir)
        log_file = params.output_dir / "orchestrator_subprocess.log"
        log_file.write_text(
            f"command: {' '.join(argv)!r}\nexit_code: {rc}\n\n--- STDOUT ---\n{stdout}\n"
            f"\n--- STDERR ---\n{stderr}\n",
            encoding="utf-8",
        )
        payload = DevshellEvalSubprocess.summarize_run_dir(params.output_dir)
        payload["ingest_submit"] = self._maybe_submit_run_dir_ingest(
            run_dir=params.output_dir,
            params=params,
        )
        payload["exit_code"] = rc
        payload["argv"] = argv
        return {
            "content": [
                {
                    "type": "text",
                    "text": DevshellEvalSubprocess.format_tool_result_text(payload),
                }
            ],
            "is_error": rc != 0,
        }

    async def _report_iteration_outcome(self, args: dict[str, Any]) -> dict[str, Any]:
        row = dict(args)
        self._state.outcomes.append(row)
        self._append_outcome_jsonl(row)
        return {
            "content": [
                {
                    "type": "text",
                    "text": DevshellEvalSubprocess.format_tool_result_text(
                        {
                            "recorded": True,
                            "iteration_index": row.get("iteration_index"),
                        }
                    ),
                }
            ]
        }

    async def _escalate_checklist_revision(
        self, args: dict[str, Any]
    ) -> dict[str, Any]:
        row = {
            "iteration_index": int(args["iteration_index"]),
            "question_ids": list(args.get("question_ids") or []),
            "rationale": str(args.get("rationale") or ""),
            "evidence_paths": list(args.get("evidence_paths") or []),
        }
        self._state.checklist_escalations_pending.append(row)
        return {
            "content": [
                {
                    "type": "text",
                    "text": DevshellEvalSubprocess.format_tool_result_text(
                        {
                            "queued": True,
                            "iteration_index": row["iteration_index"],
                            "note": (
                                "Orchestrator runs a separate checklist-only agent after "
                                "this iteration's main agent turn. Do not edit "
                                "evaluation/question_bank/ yourself."
                            ),
                        }
                    ),
                }
            ]
        }

    async def _report_checklist_revision(self, args: dict[str, Any]) -> dict[str, Any]:
        row = dict(args)
        self._state.checklist_revision_reports.append(row)
        self._append_checklist_revision_jsonl(row)
        return {
            "content": [
                {
                    "type": "text",
                    "text": DevshellEvalSubprocess.format_tool_result_text(
                        {
                            "recorded": True,
                            "iteration_index": row.get("iteration_index"),
                        }
                    ),
                }
            ]
        }

    def build_mcp_server(self) -> Any:
        """Return ``McpSdkServerConfig`` for ``ClaudeAgentOptions.mcp_servers``."""
        toolkit = self

        @tool(
            "run_devshell_eval",
            (
                "Run ``evaluation/scripts/devshell/run_devshell_eval.py`` under the repo root. "
                "Uses ``uv run python`` when available. Writes outputs under "
                "``<session_dir>/eval_runs/<iteration_tag>/``. After completion, read "
                "``raw_runs.jsonl``, ``workspaces/<task_id>/``, and question YAML for grading."
            ),
            self.RUN_DEVSHELL_EVAL_SCHEMA,
        )
        async def run_devshell_eval_tool(args: dict[str, Any]) -> dict[str, Any]:
            return await toolkit._run_devshell_eval(args)

        @tool(
            "report_iteration_outcome",
            (
                "Call exactly once at the end of each iteration after grading "
                "(and after any edits). Records macro_mean_0_100 and whether the "
                "configured target score was met."
            ),
            self.REPORT_ITERATION_SCHEMA,
        )
        async def report_iteration_outcome_tool(args: dict[str, Any]) -> dict[str, Any]:
            return await toolkit._report_iteration_outcome(args)

        @tool(
            "escalate_checklist_revision",
            (
                "Queue a follow-up **checklist-only** agent for this iteration. "
                "Use when scoring_checklist / reference_answers in question_bank "
                "seem unfair or broken — you must NOT edit evaluation/question_bank/ "
                "yourself. Call before or when summarizing in report_iteration_outcome."
            ),
            self.ESCALATE_CHECKLIST_SCHEMA,
        )
        async def escalate_checklist_revision_tool(
            args: dict[str, Any],
        ) -> dict[str, Any]:
            return await toolkit._escalate_checklist_revision(args)

        @tool(
            "report_checklist_revision",
            (
                "Call exactly once at the end of the checklist follow-up turn. "
                "Record whether you edited any question_bank YAML and why."
            ),
            self.REPORT_CHECKLIST_REVISION_SCHEMA,
        )
        async def report_checklist_revision_tool(
            args: dict[str, Any],
        ) -> dict[str, Any]:
            return await toolkit._report_checklist_revision(args)

        return create_sdk_mcp_server(
            name=self.MCP_SERVER_NAME,
            version="1.1.0",
            tools=[
                run_devshell_eval_tool,
                report_iteration_outcome_tool,
                escalate_checklist_revision_tool,
                report_checklist_revision_tool,
            ],
        )


def create_matmaster_eval_mcp_server(state: AgentLoopSharedState) -> Any:
    """Backward-compatible wrapper for :meth:`MatmasterEvalMcpToolkit.build_mcp_server`."""
    return MatmasterEvalMcpToolkit(state).build_mcp_server()


def mcp_allowed_tool_names() -> list[str]:
    return MatmasterEvalMcpToolkit.allowed_tool_names()
