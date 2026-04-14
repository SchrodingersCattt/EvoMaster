"""``run_devshell_eval`` MCP 实现（含 P0 gate、子进程）。从 ``sdk_tools`` 拆出以符合单文件行数上限。"""

from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from evaluation.devshell_agent.config_state import (
    DevshellAgentCliDefaults,
    parallel_scoring_checklist_workers_from_jobs,
)
from evaluation.devshell_agent.feishu_round_notify import notify_after_scoring_async
from evaluation.devshell_agent.subprocess_runner import (
    DevshellEvalSubprocess,
    RunDevshellEvalParams,
    run_score_devshell_tasks,
    run_score_devshell_tasks_submit,
    submit_scored_pending_ingest_dir,
)


class MatmasterEvalMcpEvalRunMixin:
    """供 :class:`MatmasterEvalMcpToolkit` 混入：评测子进程与 P0 门控。"""

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
                    row: dict[str, Any] = {
                        "task_id": path.stem,
                        "score": int(score),
                    }
                    if "all_criteria_passed" in item:
                        row["all_criteria_passed"] = bool(item["all_criteria_passed"])
                    rows.append(row)
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
                "Per-task score is 100 only when every scoring_checklist item passed; "
                "otherwise 0. macro_mean_0_100 is the mean of those 0/100 values "
                "(all-criteria pass rate × 100).",
                "Raw score_reason is intentionally withheld from the main agent.",
                "Use delegate_optimization or escalate_checklist_revision with sanitized summaries only.",
            ],
        }

    def _merge_run_args(
        self,
        args: dict[str, Any],
        *,
        questions_override: list[str] | None = None,
        exclude_question_ids: list[str] | None = None,
        output_dir_override: Path | None = None,
        eval_ingest_run_id: str | None = None,
    ) -> RunDevshellEvalParams:
        d: DevshellAgentCliDefaults = self._state.defaults
        tag = str(args["iteration_tag"]).strip()
        out_dir = output_dir_override or (self._state.session_dir / "eval_runs" / tag)
        jobs = d.jobs if args.get("jobs") is None else int(args["jobs"])
        limit = d.limit if args.get("limit") is None else int(args["limit"])
        questions = questions_override
        if questions is None:
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
        k_repeat = d.k
        if args.get("k") is not None:
            k_repeat = int(args["k"])
        return RunDevshellEvalParams(
            output_dir=out_dir,
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
            eval_ingest_run_id=eval_ingest_run_id,
            exclude_question_ids=exclude_question_ids,
            k=k_repeat,
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

    def _run_score_devshell_tasks_no_submit(
        self, params: RunDevshellEvalParams
    ) -> tuple[int, str, str]:
        state = self._state
        return run_score_devshell_tasks(
            repo_root=state.repo_root,
            run_dir=params.output_dir,
            eval_config=params.eval_config,
            eval_ingest_timeout=float(state.eval_ingest_submit_timeout),
            score_jobs=params.jobs,
            parallel_checklist_workers=parallel_scoring_checklist_workers_from_jobs(
                params.jobs
            ),
            submit=False,
        )

    def _maybe_post_scored_pending_ingest(
        self,
        *,
        run_dir: Path,
        params: RunDevshellEvalParams,
    ) -> dict[str, Any]:
        """POST ``pending_ingest/*.json`` after ``run_score_devshell_tasks`` (no ``--submit``)."""
        state = self._state
        if not state.eval_ingest_submit_each_iteration:
            return {"attempted": False, "reason": "auto_submit_disabled"}
        if not params.eval_ingest_pending_only:
            return {"attempted": False, "reason": "pending_only_disabled"}
        if "--no-eval-ingest" in params.extra_args:
            return {"attempted": False, "reason": "eval_ingest_disabled"}
        pending_dir = run_dir / "pending_ingest"
        if not pending_dir.is_dir() or not any(pending_dir.glob("*.json")):
            return {"attempted": False, "reason": "missing_pending_ingest"}

        rc, out, err = submit_scored_pending_ingest_dir(
            run_dir=run_dir,
            eval_ingest_timeout=float(state.eval_ingest_submit_timeout),
        )
        log_path = state.session_dir / "ingest_submit.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_row = {
            "run_dir": str(run_dir),
            "mode": "post_scored_pending_only",
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

    def _read_scores_from_pending(self, run_dir: Path) -> dict[str, int]:
        """Read scored ``pending_ingest/*.json`` → ``{question_id: score}``."""
        pending_dir = run_dir / "pending_ingest"
        scores: dict[str, int] = {}
        if not pending_dir.is_dir():
            return scores
        for path in sorted(pending_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            item = payload.get("item") or {}
            score = item.get("score")
            q_id = item.get("question_id") or ""
            if isinstance(score, (int, float)) and q_id:
                scores[q_id] = int(score)
        return scores

    @staticmethod
    def _merge_p0_and_rest_into_base_run_dir(
        base_dir: Path, p0_dir: Path, rest_dir: Path
    ) -> None:
        """Merge phase dirs into ``base_dir`` for a single ``score_devshell_tasks --submit``.

        ``run_devshell_eval`` writes sibling ``p0_gate/`` and ``remaining/``. Copying
        ``raw_runs.jsonl``, ``workspaces/``, ``logs/``, and ``pending_ingest/`` into
        the parent tag directory lets ingest + Feishu notify once with ``tag`` as title.
        (P0 gate passes the same ``--eval-ingest-run-id`` to both phases so merged
        ``pending_ingest`` shares one tools-server ``run_id``.)
        """
        base_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("workspaces", "logs", "pending_ingest"):
            target = base_dir / sub
            if target.is_dir():
                shutil.rmtree(target)
            target.mkdir(parents=True, exist_ok=True)

        merged_lines: list[str] = []
        for src in (p0_dir / "raw_runs.jsonl", rest_dir / "raw_runs.jsonl"):
            if src.is_file():
                for line in src.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        merged_lines.append(line)
        (base_dir / "raw_runs.jsonl").write_text(
            "\n".join(merged_lines) + ("\n" if merged_lines else ""),
            encoding="utf-8",
        )

        for phase_dir in (p0_dir, rest_dir):
            ws = phase_dir / "workspaces"
            if ws.is_dir():
                for child in sorted(ws.iterdir()):
                    if not child.is_dir():
                        continue
                    dest = base_dir / "workspaces" / child.name
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(child, dest)
            logs = phase_dir / "logs"
            if logs.is_dir():
                for child in sorted(logs.iterdir()):
                    if not child.is_dir():
                        continue
                    dest = base_dir / "logs" / child.name
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(child, dest)
            pending = phase_dir / "pending_ingest"
            if pending.is_dir():
                for jf in sorted(pending.glob("*.json")):
                    shutil.copy2(jf, base_dir / "pending_ingest" / jf.name)

    def _check_p0_regression(
        self, current_scores: dict[str, int]
    ) -> dict[str, Any] | None:
        """Compare current P0 scores with baseline; return regression info or None."""
        baseline = self._state.last_p0_scores
        if not baseline:
            return None
        baseline_mean = round(sum(baseline.values()) / len(baseline)) if baseline else 0
        current_mean = (
            round(sum(current_scores.values()) / len(current_scores))
            if current_scores
            else 0
        )
        if current_mean >= baseline_mean:
            return None
        per_question: list[dict[str, Any]] = []
        for qid in sorted(set(baseline) | set(current_scores)):
            prev = baseline.get(qid)
            curr = current_scores.get(qid)
            if prev is not None and curr is not None and curr < prev:
                per_question.append(
                    {"question_id": qid, "previous": prev, "current": curr}
                )
        return {
            "baseline_mean": baseline_mean,
            "current_mean": current_mean,
            "delta": current_mean - baseline_mean,
            "regressed_questions": per_question,
        }

    def _run_subprocess_and_log(
        self,
        params: RunDevshellEvalParams,
        *,
        phase_label: str = "",
    ) -> tuple[int, str, str]:
        """Run ``run_devshell_eval.py`` subprocess, write log, return (rc, stdout, stderr)."""
        script = (
            self._state.repo_root
            / "evaluation"
            / "scripts"
            / "devshell"
            / "run_devshell_eval.py"
        )
        argv = self._subprocess.build_argv(script, params)
        rc, stdout, stderr = self._subprocess.run_capture(argv)
        suffix = f"_{phase_label}" if phase_label else ""
        log_file = params.output_dir / f"orchestrator_subprocess{suffix}.log"
        log_file.write_text(
            f"command: {' '.join(argv)!r}\nexit_code: {rc}\n\n--- STDOUT ---\n{stdout}\n"
            f"\n--- STDERR ---\n{stderr}\n",
            encoding="utf-8",
        )
        return rc, stdout, stderr

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

        from evaluation.devshell_agent.question_bank_ids import collect_p0_question_ids

        p0_ids = collect_p0_question_ids(self._state.repo_root)
        if p0_ids:
            return await self._run_devshell_eval_with_p0_gate(args, tag, p0_ids)
        return await self._run_devshell_eval_standard(args, tag)

    async def _run_devshell_eval_standard(
        self, args: dict[str, Any], tag: str
    ) -> dict[str, Any]:
        """Original single-phase eval run."""
        params = self._merge_run_args(args)
        params.output_dir.mkdir(parents=True, exist_ok=True)
        rc, _stdout, _stderr = self._run_subprocess_and_log(params)
        self._state.last_eval_output_dir = params.output_dir
        self._state.eval_output_dirs.append(params.output_dir)
        payload = DevshellEvalSubprocess.summarize_run_dir(params.output_dir)
        payload["sanitized_summary"] = self._build_sanitized_run_summary(
            params.output_dir
        )
        payload["ingest_submit"] = self._maybe_submit_run_dir_ingest(
            run_dir=params.output_dir,
            params=params,
        )
        payload["exit_code"] = rc
        return {
            "content": [
                {
                    "type": "text",
                    "text": DevshellEvalSubprocess.format_tool_result_text(payload),
                }
            ],
            "is_error": rc != 0,
        }

    async def _run_devshell_eval_with_p0_gate(
        self, args: dict[str, Any], tag: str, p0_ids: list[str]
    ) -> dict[str, Any]:
        """Two-phase eval: run P0 gate first, then remaining questions if gate passes."""
        base_dir = self._state.session_dir / "eval_runs" / tag
        shared_ingest_run_id = str(uuid.uuid4())

        # --- Phase 1: P0 gate ---
        p0_dir = base_dir / "p0_gate"
        p0_params = self._merge_run_args(
            args,
            questions_override=list(p0_ids),
            output_dir_override=p0_dir,
            eval_ingest_run_id=shared_ingest_run_id,
        )
        p0_dir.mkdir(parents=True, exist_ok=True)
        p0_rc, _, _ = self._run_subprocess_and_log(p0_params, phase_label="p0_gate")

        p0_score_rc, _, _ = self._run_score_devshell_tasks_no_submit(p0_params)
        p0_scores = self._read_scores_from_pending(p0_dir)
        p0_summary = self._build_sanitized_run_summary(p0_dir)

        regression = self._check_p0_regression(p0_scores)

        if regression is not None:
            self._state.last_eval_output_dir = p0_dir
            self._state.eval_output_dirs.append(p0_dir)
            p0_ingest = self._maybe_post_scored_pending_ingest(
                run_dir=p0_dir, params=p0_params
            )
            payload: dict[str, Any] = {
                "p0_gate_failed": True,
                "p0_gate_regression": regression,
                "p0_gate_scores": p0_summary,
                "p0_gate_ingest_submit": p0_ingest,
                "p0_gate_exit_code": max(p0_rc, p0_score_rc),
                "run_dir": str(p0_dir.resolve()),
                "sanitized_summary": {
                    **p0_summary,
                    "p0_gate_failed": True,
                    "p0_gate_regression": regression,
                    "notes": [
                        "P0 regression detected: non-P0 questions were skipped.",
                        f"P0 mean dropped from {regression['baseline_mean']} to "
                        f"{regression['current_mean']} (delta {regression['delta']}).",
                        "This iteration should be treated as an optimization failure.",
                    ],
                },
            }
            return {
                "content": [
                    {
                        "type": "text",
                        "text": DevshellEvalSubprocess.format_tool_result_text(payload),
                    }
                ],
                "is_error": True,
            }

        if p0_scores:
            self._state.last_p0_scores = dict(p0_scores)

        # --- Phase 2: remaining (non-P0) questions ---
        rest_dir = base_dir / "remaining"
        rest_params = self._merge_run_args(
            args,
            exclude_question_ids=list(p0_ids),
            output_dir_override=rest_dir,
            eval_ingest_run_id=shared_ingest_run_id,
        )
        rest_dir.mkdir(parents=True, exist_ok=True)
        rest_rc, _, _ = self._run_subprocess_and_log(
            rest_params, phase_label="remaining"
        )

        rest_score_rc, _, _ = self._run_score_devshell_tasks_no_submit(rest_params)
        rest_scores = self._read_scores_from_pending(rest_dir)

        self._merge_p0_and_rest_into_base_run_dir(base_dir, p0_dir, rest_dir)
        ingest_submit = self._maybe_post_scored_pending_ingest(
            run_dir=base_dir, params=rest_params
        )

        self._state.last_eval_output_dir = base_dir
        self._state.eval_output_dirs.append(p0_dir)
        self._state.eval_output_dirs.append(rest_dir)

        combined_summary = self._build_combined_sanitized_summary(
            p0_scores=p0_scores,
            rest_scores=rest_scores,
            p0_dir=p0_dir,
            rest_dir=rest_dir,
        )

        payload = DevshellEvalSubprocess.summarize_run_dir(base_dir)
        payload.update(
            {
                "p0_gate_passed": True,
                "p0_gate_scores": p0_summary,
                "rest_scores_count": len(rest_scores),
                "sanitized_summary": combined_summary,
                "ingest_submit": ingest_submit,
                "p0_gate_dir": str(p0_dir.resolve()),
                "remaining_dir": str(rest_dir.resolve()),
                "exit_code": max(p0_rc, p0_score_rc, rest_rc, rest_score_rc),
            }
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": DevshellEvalSubprocess.format_tool_result_text(payload),
                }
            ],
            "is_error": max(p0_rc, p0_score_rc, rest_rc, rest_score_rc) != 0,
        }

    def _build_combined_sanitized_summary(
        self,
        *,
        p0_scores: dict[str, int],
        rest_scores: dict[str, int],
        p0_dir: Path,
        rest_dir: Path,
    ) -> dict[str, Any]:
        """Merge P0 and remaining scores into a single sanitized summary."""
        all_rows: list[dict[str, Any]] = []
        for qid, score in p0_scores.items():
            all_rows.append({"task_id": qid, "score": score, "p0": True})
        for qid, score in rest_scores.items():
            all_rows.append({"task_id": qid, "score": score, "p0": False})

        macro_mean = 0
        if all_rows:
            macro_mean = round(sum(r["score"] for r in all_rows) / len(all_rows))
        p0_mean = 0
        if p0_scores:
            p0_mean = round(sum(p0_scores.values()) / len(p0_scores))
        low_score_tasks = [r for r in all_rows if r["score"] < 100]

        return {
            "macro_mean_0_100": macro_mean,
            "p0_mean_0_100": p0_mean,
            "p0_gate_passed": True,
            "task_scores": all_rows,
            "low_score_tasks": low_score_tasks,
            "p0_task_count": len(p0_scores),
            "rest_task_count": len(rest_scores),
            "sanitized": True,
            "notes": [
                "P0 gate passed — all questions completed.",
                "Per-task score is 100 only when every scoring_checklist item passed; "
                "macro_mean_0_100 is the mean of those 0/100 scores (pass rate × 100).",
                "Raw score_reason is intentionally withheld from the main agent.",
                "Use delegate_optimization or escalate_checklist_revision with sanitized summaries only.",
            ],
        }
