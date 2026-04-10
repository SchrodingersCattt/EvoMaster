"""In-process MCP tools for Claude Agent SDK (requires ``claude-agent-sdk``)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool  # type: ignore[import-untyped]

from evaluation.devshell_agent import mcp_tool_schemas as _mts
from evaluation.devshell_agent.config_state import (
    AgentLoopSharedState,
    DevshellAgentCliDefaults,
    parallel_scoring_checklist_workers_from_jobs,
)
from evaluation.devshell_agent.feishu_round_notify import notify_after_scoring_async
from evaluation.devshell_agent.path_policy import (
    PROPOSED_MATMASTER_EXPS_CHANGES_NAME,
    devshell_main_agent_history_root,
    is_blocked_matmaster_exps_path,
)
from evaluation.devshell_agent.path_policy import is_under as _path_is_under
from evaluation.devshell_agent.subprocess_runner import (
    DevshellEvalSubprocess,
    RunDevshellEvalParams,
    run_score_devshell_tasks_submit,
)


class MatmasterEvalMcpToolkit:
    """Builds the in-process MCP server bound to a shared :class:`AgentLoopSharedState`."""

    MCP_SERVER_NAME = "matmaster_eval"

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
    def main_agent_fs_tool_names(cls) -> list[str]:
        prefix = f"mcp__{cls.MCP_SERVER_NAME}__main_"
        return [
            prefix + "read_text",
            prefix + "glob_paths",
            prefix + "grep_text",
        ]

    @classmethod
    def allowed_tool_names(cls) -> list[str]:
        """MCP tools for the main (product) iteration agent."""
        return [
            *cls.main_agent_mcp_tool_names(),
            *cls.main_agent_fs_tool_names(),
            f"mcp__{cls.MCP_SERVER_NAME}__delegate_optimization",
        ]

    @classmethod
    def optimization_agent_mcp_tool_names(cls) -> list[str]:
        return [f"mcp__{cls.MCP_SERVER_NAME}__report_optimization_result"]

    @classmethod
    def optimization_agent_fs_tool_names(cls) -> list[str]:
        prefix = f"mcp__{cls.MCP_SERVER_NAME}__optimization_"
        return [
            prefix + "read_text",
            prefix + "glob_paths",
            prefix + "grep_text",
            prefix + "write_text",
            prefix + "replace_text",
        ]

    @classmethod
    def optimization_agent_tool_names(cls) -> list[str]:
        return [
            *cls.optimization_agent_mcp_tool_names(),
            *cls.optimization_agent_fs_tool_names(),
        ]

    @classmethod
    def checklist_agent_fs_tool_names(cls) -> list[str]:
        prefix = f"mcp__{cls.MCP_SERVER_NAME}__checklist_"
        return [
            prefix + "read_text",
            prefix + "glob_paths",
            prefix + "grep_text",
            prefix + "write_text",
            prefix + "replace_text",
        ]

    @classmethod
    def checklist_agent_tool_names(cls) -> list[str]:
        return [
            *cls.checklist_agent_mcp_tool_names(),
            *cls.checklist_agent_fs_tool_names(),
        ]

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

    def _append_optimization_delegation_jsonl(self, row: dict[str, Any]) -> None:
        path = self._state.session_dir / "optimization_delegations.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _append_optimization_report_jsonl(self, row: dict[str, Any]) -> None:
        path = self._state.session_dir / "optimization_reports.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    @staticmethod
    def _optimization_execution_track(args: dict[str, Any]) -> str:
        layers = [
            str(x).strip()
            for x in (args.get("candidate_layers") or [])
            if str(x).strip()
        ]
        if layers == ["system_prompt"]:
            return "proposal_only"
        return "code_edit"

    def _display_path(self, path: Path) -> str:
        repo_root = self._state.repo_root.resolve()
        session_dir = self._state.session_dir.resolve()
        if _path_is_under(path, repo_root):
            return str(path.relative_to(repo_root))
        if _path_is_under(path, session_dir):
            return str(path)
        return str(path)

    def _resolve_agent_path(self, raw_path: str, *, role: str, write: bool) -> Path:
        repo_root = self._state.repo_root.resolve()
        session_dir = self._state.session_dir.resolve()
        evaluation_root = (repo_root / "evaluation").resolve()
        question_bank_root = (evaluation_root / "question_bank").resolve()
        evaluation_core_root = (evaluation_root / "core").resolve()
        git_root = (repo_root / ".git").resolve()

        candidate = Path(raw_path)
        path = (
            candidate.resolve()
            if candidate.is_absolute()
            else (repo_root / candidate).resolve()
        )

        if _path_is_under(path, git_root):
            raise ValueError(f"{role} path access denied: {raw_path}")

        if role == "optimization":
            if write:
                # Human-reviewed queue for _base.toml / direct.toml (forbidden to edit below).
                if _path_is_under(path, session_dir):
                    if path.name != PROPOSED_MATMASTER_EXPS_CHANGES_NAME:
                        raise ValueError(
                            "optimization path access denied: under the session directory, "
                            f"only {PROPOSED_MATMASTER_EXPS_CHANGES_NAME!r} may be written "
                            f"(Markdown proposals for matmaster/exps/*.toml); got {path.name!r}"
                        )
                    return path
                if not _path_is_under(path, repo_root) or _path_is_under(
                    path, evaluation_root
                ):
                    raise ValueError(f"optimization path access denied: {raw_path}")
                if is_blocked_matmaster_exps_path(repo_root, path):
                    rel_proposal = (
                        session_dir.resolve().relative_to(repo_root.resolve())
                        / PROPOSED_MATMASTER_EXPS_CHANGES_NAME
                    )
                    raise ValueError(
                        "optimization cannot edit any file under matmaster/exps/. "
                        "If a change is truly needed, it must be justified as cross-domain "
                        "and generic. Write the proposal as Markdown in "
                        f"{PROPOSED_MATMASTER_EXPS_CHANGES_NAME!r} under this session "
                        f"(repo-relative: {rel_proposal.as_posix()}), for human review."
                    )
            else:
                if _path_is_under(path, session_dir):
                    return path
                if not _path_is_under(path, repo_root) or _path_is_under(
                    path, evaluation_root
                ):
                    raise ValueError(f"optimization path access denied: {raw_path}")
            return path

        if role == "main":
            if write:
                raise ValueError(
                    "main agent path access denied: read-only tools "
                    "(evaluation/devshell_agent_history/ only)"
                )
            history_root = devshell_main_agent_history_root(repo_root)
            if not _path_is_under(path, history_root):
                raise ValueError(f"main path access denied: {raw_path}")
            return path

        if role == "checklist":
            if write:
                if not (
                    _path_is_under(path, question_bank_root)
                    or _path_is_under(path, evaluation_core_root)
                ):
                    raise ValueError(f"checklist path access denied: {raw_path}")
            else:
                if not (
                    _path_is_under(path, evaluation_root)
                    or _path_is_under(path, session_dir)
                ):
                    raise ValueError(f"checklist path access denied: {raw_path}")
            return path

        raise ValueError(f"unknown role: {role}")

    async def _read_text(self, *, role: str, args: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_agent_path(str(args["path"]), role=role, write=False)
        text = path.read_text(encoding="utf-8")
        return {
            "content": [
                {
                    "type": "text",
                    "text": DevshellEvalSubprocess.format_tool_result_text(
                        {"path": self._display_path(path), "content": text}
                    ),
                }
            ]
        }

    async def _write_text(self, *, role: str, args: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_agent_path(str(args["path"]), role=role, write=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(args["content"]), encoding="utf-8")
        return {
            "content": [
                {
                    "type": "text",
                    "text": DevshellEvalSubprocess.format_tool_result_text(
                        {"written": True, "path": self._display_path(path)}
                    ),
                }
            ]
        }

    async def _replace_text(self, *, role: str, args: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_agent_path(str(args["path"]), role=role, write=True)
        old_text = str(args["old_text"])
        new_text = str(args["new_text"])
        replace_all = bool(args.get("replace_all", False))
        content = path.read_text(encoding="utf-8")
        count = content.count(old_text)
        if count == 0:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": DevshellEvalSubprocess.format_tool_result_text(
                            {
                                "replaced": False,
                                "path": self._display_path(path),
                                "reason": "old_text_not_found",
                            }
                        ),
                    }
                ],
                "is_error": True,
            }
        updated = (
            content.replace(old_text, new_text)
            if replace_all
            else content.replace(old_text, new_text, 1)
        )
        path.write_text(updated, encoding="utf-8")
        return {
            "content": [
                {
                    "type": "text",
                    "text": DevshellEvalSubprocess.format_tool_result_text(
                        {
                            "replaced": True,
                            "path": self._display_path(path),
                            "matches_found": count,
                            "replace_all": replace_all,
                        }
                    ),
                }
            ]
        }

    async def _glob_paths(self, *, role: str, args: dict[str, Any]) -> dict[str, Any]:
        base_dir = self._resolve_agent_path(
            str(args["base_dir"]), role=role, write=False
        )
        pattern = str(args["pattern"])
        matches = sorted(self._display_path(path) for path in base_dir.rglob(pattern))
        return {
            "content": [
                {
                    "type": "text",
                    "text": DevshellEvalSubprocess.format_tool_result_text(
                        {"base_dir": self._display_path(base_dir), "matches": matches}
                    ),
                }
            ]
        }

    async def _grep_text(self, *, role: str, args: dict[str, Any]) -> dict[str, Any]:
        base_dir = self._resolve_agent_path(
            str(args["base_dir"]), role=role, write=False
        )
        needle = str(args["pattern"])
        file_pattern = str(args.get("file_pattern") or "*")
        limit = max(1, int(args.get("limit") or 20))
        hits: list[dict[str, Any]] = []
        for path in sorted(base_dir.rglob(file_pattern)):
            if len(hits) >= limit or not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for lineno, line in enumerate(lines, start=1):
                if needle in line:
                    hits.append(
                        {
                            "path": self._display_path(path),
                            "line": lineno,
                            "text": line,
                        }
                    )
                    if len(hits) >= limit:
                        break
        return {
            "content": [
                {
                    "type": "text",
                    "text": DevshellEvalSubprocess.format_tool_result_text(
                        {"base_dir": self._display_path(base_dir), "hits": hits}
                    ),
                }
            ]
        }

    def _build_sanitized_run_summary(self, run_dir: Path) -> dict[str, Any]:
        pending_dir = run_dir / "pending_ingest"
        rows: list[dict[str, Any]] = []
        if pending_dir.is_dir():
            for path in sorted(pending_dir.glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                item = payload.get("item") or {}
                score = item.get("score")
                if isinstance(score, (int, float)):
                    rows.append(
                        {
                            "task_id": path.stem,
                            "score": int(score),
                        }
                    )
        macro_mean = 0
        if rows:
            macro_mean = round(sum(row["score"] for row in rows) / len(rows))
        low_score_tasks = [row for row in rows if row["score"] < 100]
        return {
            "macro_mean_0_100": macro_mean,
            "task_scores": rows,
            "low_score_tasks": low_score_tasks,
            "sanitized": True,
            "notes": [
                "Raw score_reason is intentionally withheld from the main agent.",
                "Use delegate_optimization or escalate_checklist_revision with sanitized summaries only.",
            ],
        }

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
        slices = d.slices
        if args.get("slices") is not None:
            slices = str(args["slices"]).strip() or None
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
            slices=slices,
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
            score_jobs=params.jobs,
            parallel_checklist_workers=parallel_scoring_checklist_workers_from_jobs(
                params.jobs
            ),
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
        result = {
            "attempted": True,
            "ok": rc == 0,
            "exit_code": rc,
            "stdout_tail": (out or "")[-4000:],
            "stderr_tail": (err or "")[-4000:],
        }
        notify_after_scoring_async(run_dir=run_dir, ingest_result=result)
        return result

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
        payload["sanitized_summary"] = self._build_sanitized_run_summary(
            params.output_dir
        )
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

    async def _delegate_optimization(self, args: dict[str, Any]) -> dict[str, Any]:
        iteration_index = int(args["iteration_index"])
        next_round = (
            self._state.optimization_rounds_by_iteration.get(iteration_index, 0) + 1
        )
        self._state.optimization_rounds_by_iteration[iteration_index] = next_round
        row = {
            "iteration_index": iteration_index,
            "optimization_round": next_round,
            "problem_summary": str(args["problem_summary"]),
            "symptom": str(args["symptom"]),
            "suggested_focus": list(args.get("suggested_focus") or []),
            "candidate_layers": list(args.get("candidate_layers") or []),
            "execution_track": self._optimization_execution_track(args),
            "failure_buckets": list(args.get("failure_buckets") or []),
            "capabilities_affected": list(args.get("capabilities_affected") or []),
            "allowed_evidence_paths": list(args.get("allowed_evidence_paths") or []),
            "notes": str(args["notes"]),
        }
        self._state.optimization_delegations_pending.append(row)
        self._append_optimization_delegation_jsonl(row)
        return {
            "content": [
                {
                    "type": "text",
                    "text": DevshellEvalSubprocess.format_tool_result_text(
                        {
                            "queued": True,
                            "iteration_index": row["iteration_index"],
                            "optimization_round": row["optimization_round"],
                        }
                    ),
                }
            ],
            "is_error": False,
        }

    async def _report_optimization_result(self, args: dict[str, Any]) -> dict[str, Any]:
        row = dict(args)
        self._state.optimization_reports.append(row)
        self._append_optimization_report_jsonl(row)
        return {
            "content": [
                {
                    "type": "text",
                    "text": DevshellEvalSubprocess.format_tool_result_text(
                        {
                            "recorded": True,
                            "iteration_index": row.get("iteration_index"),
                            "optimization_round": row.get("optimization_round"),
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
            _mts.RUN_DEVSHELL_EVAL_SCHEMA,
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
            _mts.REPORT_ITERATION_SCHEMA,
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
            _mts.ESCALATE_CHECKLIST_SCHEMA,
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
            _mts.REPORT_CHECKLIST_REVISION_SCHEMA,
        )
        async def report_checklist_revision_tool(
            args: dict[str, Any],
        ) -> dict[str, Any]:
            return await toolkit._report_checklist_revision(args)

        @tool(
            "delegate_optimization",
            (
                "Queue a follow-up product-only optimization agent for this iteration. "
                "Use sanitized summaries only; never include raw rubric or score_reason text. "
                "Prefer failure_buckets, candidate_layers, and capabilities_affected "
                "over per-question paths; "
                "keep allowed_evidence_paths session-level (e.g. raw_runs.jsonl) when possible."
            ),
            _mts.DELEGATE_OPTIMIZATION_SCHEMA,
        )
        async def delegate_optimization_tool(args: dict[str, Any]) -> dict[str, Any]:
            return await toolkit._delegate_optimization(args)

        @tool(
            "main_read_text",
            (
                "Read a file under ``evaluation/devshell_agent_history/`` only "
                "(orchestrator-written snapshots, any session subfolder, and index.jsonl). "
                "No other ``evaluation/`` paths."
            ),
            _mts.READ_TEXT_SCHEMA,
        )
        async def main_read_text_tool(args: dict[str, Any]) -> dict[str, Any]:
            return await toolkit._read_text(role="main", args=args)

        @tool(
            "main_glob_paths",
            (
                "Glob under ``evaluation/devshell_agent_history/`` only (all session "
                "subfolders and ``index.jsonl``)."
            ),
            _mts.GLOB_PATHS_SCHEMA,
        )
        async def main_glob_paths_tool(args: dict[str, Any]) -> dict[str, Any]:
            return await toolkit._glob_paths(role="main", args=args)

        @tool(
            "main_grep_text",
            (
                "Search text under ``evaluation/devshell_agent_history/`` only "
                "(all sessions)."
            ),
            _mts.GREP_TEXT_SCHEMA,
        )
        async def main_grep_text_tool(args: dict[str, Any]) -> dict[str, Any]:
            return await toolkit._grep_text(role="main", args=args)

        @tool(
            "report_optimization_result",
            (
                "Call exactly once at the end of an optimization sub-round to record "
                "what product-side changes were made."
            ),
            _mts.REPORT_OPTIMIZATION_RESULT_SCHEMA,
        )
        async def report_optimization_result_tool(
            args: dict[str, Any],
        ) -> dict[str, Any]:
            return await toolkit._report_optimization_result(args)

        @tool(
            "optimization_read_text",
            "Read a product-side file with evaluation paths hard-blocked.",
            _mts.READ_TEXT_SCHEMA,
        )
        async def optimization_read_text_tool(args: dict[str, Any]) -> dict[str, Any]:
            return await toolkit._read_text(role="optimization", args=args)

        @tool(
            "optimization_glob_paths",
            "Glob product-side paths with evaluation paths hard-blocked.",
            _mts.GLOB_PATHS_SCHEMA,
        )
        async def optimization_glob_paths_tool(args: dict[str, Any]) -> dict[str, Any]:
            return await toolkit._glob_paths(role="optimization", args=args)

        @tool(
            "optimization_grep_text",
            "Search product-side text with evaluation paths hard-blocked.",
            _mts.GREP_TEXT_SCHEMA,
        )
        async def optimization_grep_text_tool(args: dict[str, Any]) -> dict[str, Any]:
            return await toolkit._grep_text(role="optimization", args=args)

        @tool(
            "optimization_write_text",
            "Write a product-side file with evaluation paths hard-blocked.",
            _mts.WRITE_TEXT_SCHEMA,
        )
        async def optimization_write_text_tool(args: dict[str, Any]) -> dict[str, Any]:
            return await toolkit._write_text(role="optimization", args=args)

        @tool(
            "optimization_replace_text",
            "Replace text in a product-side file with evaluation paths hard-blocked.",
            _mts.REPLACE_TEXT_SCHEMA,
        )
        async def optimization_replace_text_tool(
            args: dict[str, Any],
        ) -> dict[str, Any]:
            return await toolkit._replace_text(role="optimization", args=args)

        @tool(
            "checklist_read_text",
            "Read an evaluation-side file or session evidence file with product paths blocked.",
            _mts.READ_TEXT_SCHEMA,
        )
        async def checklist_read_text_tool(args: dict[str, Any]) -> dict[str, Any]:
            return await toolkit._read_text(role="checklist", args=args)

        @tool(
            "checklist_glob_paths",
            "Glob evaluation-side paths or session evidence paths with product paths blocked.",
            _mts.GLOB_PATHS_SCHEMA,
        )
        async def checklist_glob_paths_tool(args: dict[str, Any]) -> dict[str, Any]:
            return await toolkit._glob_paths(role="checklist", args=args)

        @tool(
            "checklist_grep_text",
            "Search evaluation-side text or session evidence with product paths blocked.",
            _mts.GREP_TEXT_SCHEMA,
        )
        async def checklist_grep_text_tool(args: dict[str, Any]) -> dict[str, Any]:
            return await toolkit._grep_text(role="checklist", args=args)

        @tool(
            "checklist_write_text",
            "Write an evaluation/question_bank or evaluation/core file only.",
            _mts.WRITE_TEXT_SCHEMA,
        )
        async def checklist_write_text_tool(args: dict[str, Any]) -> dict[str, Any]:
            return await toolkit._write_text(role="checklist", args=args)

        @tool(
            "checklist_replace_text",
            "Replace text in an evaluation/question_bank or evaluation/core file only.",
            _mts.REPLACE_TEXT_SCHEMA,
        )
        async def checklist_replace_text_tool(args: dict[str, Any]) -> dict[str, Any]:
            return await toolkit._replace_text(role="checklist", args=args)

        return create_sdk_mcp_server(
            name=self.MCP_SERVER_NAME,
            version="1.2.0",
            tools=[
                run_devshell_eval_tool,
                report_iteration_outcome_tool,
                escalate_checklist_revision_tool,
                report_checklist_revision_tool,
                delegate_optimization_tool,
                main_read_text_tool,
                main_glob_paths_tool,
                main_grep_text_tool,
                report_optimization_result_tool,
                optimization_read_text_tool,
                optimization_glob_paths_tool,
                optimization_grep_text_tool,
                optimization_write_text_tool,
                optimization_replace_text_tool,
                checklist_read_text_tool,
                checklist_glob_paths_tool,
                checklist_grep_text_tool,
                checklist_write_text_tool,
                checklist_replace_text_tool,
            ],
        )


def create_matmaster_eval_mcp_server(state: AgentLoopSharedState) -> Any:
    """Backward-compatible wrapper for :meth:`MatmasterEvalMcpToolkit.build_mcp_server`."""
    return MatmasterEvalMcpToolkit(state).build_mcp_server()


def mcp_allowed_tool_names() -> list[str]:
    return MatmasterEvalMcpToolkit.allowed_tool_names()
