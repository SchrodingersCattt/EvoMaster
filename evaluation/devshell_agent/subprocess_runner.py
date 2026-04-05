"""Build argv / run ``run_devshell_eval.py`` (no Claude SDK import)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunDevshellEvalParams:
    """Arguments forwarded to ``run_devshell_eval.py``."""

    output_dir: Path
    modes: list[str]
    jobs: int
    limit: int | None
    questions: list[str] | None
    capabilities: list[str] | None
    model: str | None
    exp: str | None
    eval_ingest_pending_only: bool
    no_export_review: bool
    task_timeout_sec: float
    eval_config: Path | None
    extra_args: list[str]


class DevshellEvalSubprocess:
    """Invoke ``run_devshell_eval.py`` from the repository root."""

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root

    @staticmethod
    def python_prefix() -> list[str]:
        """Prefer ``uv run python`` (project convention); fall back to ``sys.executable``."""
        if shutil.which("uv"):
            return ["uv", "run", "python"]
        return [sys.executable]

    def build_argv(self, script: Path, params: RunDevshellEvalParams) -> list[str]:
        cmd: list[str] = [
            *self.python_prefix(),
            str(script),
            "--modes",
            *params.modes,
            "--jobs",
            str(params.jobs),
        ]
        if params.limit is not None:
            cmd.extend(["--limit", str(params.limit)])
        if params.questions:
            cmd.append("--questions")
            cmd.extend(params.questions)
        if params.capabilities:
            cmd.append("--capabilities")
            cmd.extend(params.capabilities)
        if params.model:
            cmd.extend(["--model", params.model])
        if params.exp:
            cmd.extend(["--exp", params.exp])
        if params.eval_ingest_pending_only:
            cmd.append("--eval-ingest-pending-only")
        if params.no_export_review:
            cmd.append("--no-export-review")
        if params.task_timeout_sec > 0:
            cmd.extend(["--task-timeout", str(params.task_timeout_sec)])
        if params.eval_config is not None:
            cmd.extend(["--eval-config", str(params.eval_config)])
        cmd.extend(["--output-dir", str(params.output_dir)])
        # Agent loop should not wipe unrelated results under results/
        cmd.append("--no-clean-results")
        cmd.extend(params.extra_args)
        return cmd

    def run_capture(
        self,
        argv: list[str],
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        """Run evaluator; capture stdout/stderr. Returns ``(rc, stdout, stderr)``."""
        merged = os.environ.copy()
        merged.setdefault("PYTHONUNBUFFERED", "1")
        if env:
            merged.update(env)
        proc = subprocess.run(
            argv,
            cwd=str(self._repo_root),
            env=merged,
            text=True,
            capture_output=True,
            timeout=None,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    @staticmethod
    def summarize_run_dir(
        run_dir: Path, *, max_tail_chars: int = 24_000
    ) -> dict[str, Any]:
        """Load manifest + light stats from ``raw_runs.jsonl`` for tool return text."""
        out: dict[str, Any] = {"run_dir": str(run_dir.resolve())}
        manifest = run_dir / "manifest.json"
        if manifest.is_file():
            try:
                out["manifest"] = json.loads(manifest.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                out["manifest_error"] = str(e)
        raw = run_dir / "raw_runs.jsonl"
        if raw.is_file():
            lines = [
                ln for ln in raw.read_text(encoding="utf-8").splitlines() if ln.strip()
            ]
            out["raw_runs_line_count"] = len(lines)
            snippets: list[dict[str, Any]] = []
            for ln in lines[:5]:
                try:
                    row = json.loads(ln)
                    snippets.append(
                        {
                            "task_id": row.get("task_id"),
                            "devshell_exit_code": row.get("devshell_exit_code"),
                        }
                    )
                except json.JSONDecodeError:
                    snippets.append({"parse_error": True})
            out["raw_runs_sample"] = snippets
        tail = ""
        log_path = run_dir / "orchestrator_subprocess.log"
        if log_path.is_file():
            body = log_path.read_text(encoding="utf-8", errors="replace")
            tail = body[-max_tail_chars:] if len(body) > max_tail_chars else body
        out["subprocess_log_tail"] = tail
        return out

    @staticmethod
    def format_tool_result_text(payload: dict[str, Any]) -> str:
        return textwrap.dedent(f"""
            ```json
            {json.dumps(payload, ensure_ascii=False, indent=2)}
            ```
            """).strip()


def run_score_devshell_tasks_submit(
    *,
    repo_root: Path,
    run_dir: Path,
    eval_config: Path | None,
    eval_ingest_timeout: float,
    score_jobs: int,
) -> tuple[int, str, str]:
    """Run ``score_devshell_tasks.py --run-dir … --submit`` (writes scores + POST ingest)."""
    runner = DevshellEvalSubprocess(repo_root)
    script = (
        repo_root / "evaluation" / "scripts" / "devshell" / "score_devshell_tasks.py"
    )
    sj = max(1, int(score_jobs))
    cmd: list[str] = [
        *DevshellEvalSubprocess.python_prefix(),
        str(script),
        "--run-dir",
        str(run_dir),
        "--score-jobs",
        str(sj),
        "--submit",
        "--eval-ingest-timeout",
        str(eval_ingest_timeout),
    ]
    if eval_config is not None:
        cmd.extend(["--eval-config", str(eval_config)])
    return runner.run_capture(cmd)
