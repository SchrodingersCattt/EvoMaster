#!/usr/bin/env python3
"""Batch-run MATTER v5 question bank through ``mm-devshell run`` (matmaster kernel).

Reads the same ``question_bank/`` layout as ``playground/mat_master/evaluation``,
stages data files per task workspace, then invokes (inherit terminal; ``--json-out`` for aggregation)::

    python -u -m matmaster.devshell run ... --prompt-file ... --json-out .../_devshell_summary.json

Aggregate output: ``raw_runs.jsonl`` (one JSON object per line) + ``manifest.json``.

Usage (from repository root)::

    uv run python scripts/run_devshell_eval.py --model claude-sonnet-4-6 --limit 3
    uv run python scripts/run_devshell_eval.py --help

This does **not** run MATTER's BinaryEvaluator or Playground ``run_mat_task``; it only
collects devshell JSON summaries for downstream review or custom scoring.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Repo root = scripts/..
REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _merge_eval_config(path: Path | None, overrides: dict[str, Any]) -> dict[str, Any]:
    base: dict[str, Any] = {}
    if path and path.is_file():
        base = _load_yaml(path)
    for k, v in overrides.items():
        if v is not None:
            base[k] = v
    return base


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run MATTER question bank through mm-devshell (matmaster devshell run).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--eval-config",
        type=Path,
        default=REPO_ROOT / "playground/mat_master/evaluation/config.yaml",
        help="MATTER eval YAML (filters: capabilities, question ids, use_seed_prompt, …)",
    )
    parser.add_argument(
        "--question-bank-dir",
        type=Path,
        default=None,
        help="Override question bank directory (default from eval config)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: results/devshell_eval_<UTC timestamp>)",
    )
    parser.add_argument(
        "--run-label",
        type=str,
        default="devshell_eval",
        help="Prefix for the run folder name",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="LLM route key passed to ``mm-devshell run --model`` (see llm_config.yaml routes)",
    )
    parser.add_argument(
        "--capabilities",
        nargs="+",
        default=None,
        help="Only run questions in these capabilities (e.g. batch_processing)",
    )
    parser.add_argument(
        "--questions",
        nargs="+",
        default=None,
        help="Only run these question IDs",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of plan items to run (after expand); for smoke tests",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan only, do not invoke devshell",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first non-zero devshell exit code",
    )
    parser.add_argument(
        "--python",
        type=Path,
        default=None,
        help="Python executable (default: sys.executable)",
    )
    args = parser.parse_args()

    py = args.python or Path(sys.executable)

    cfg_dict = _merge_eval_config(
        args.eval_config if args.eval_config.is_file() else None,
        {
            "question_bank_dir": (
                str(args.question_bank_dir) if args.question_bank_dir else None
            ),
            "include_capabilities": args.capabilities,
            "include_question_ids": args.questions,
        },
    )

    # Lazy imports after potential chdir
    sys.path.insert(0, str(REPO_ROOT))
    from playground.mat_master.evaluation.runner import (
        _apply_filters,
        _flatten_banks,
        _resolve_to_project_root,
        _stage_data_files,
        expand_run_plan,
        load_question_banks,
    )
    from playground.mat_master.evaluation.schemas import EvalConfig
    from playground.mat_master.evaluation.simulator import HumanSimulator

    cfg = EvalConfig.model_validate(cfg_dict)
    bank_dir = Path(_resolve_to_project_root(cfg.question_bank_dir))

    question_banks = load_question_banks(bank_dir)
    questions = _flatten_banks(question_banks)
    questions = _apply_filters(questions, cfg)
    run_plan = expand_run_plan(questions=questions, config=cfg)

    if args.limit is not None:
        run_plan = run_plan[: max(0, args.limit)]

    if not run_plan:
        print(
            "No tasks in plan (check modes vs question.mode_scope, filters, --limit).",
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        print(f"Planned tasks: {len(run_plan)}", file=sys.stderr)
        for item in run_plan:
            q = item["question"]
            mode = item["mode"]
            ridx = item["repeat_idx"]
            tid = f"{q.id}_{mode}_r{ridx}"
            print(f"  [dry-run] {tid} capability={q.capability}", file=sys.stderr)
        return 0

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = args.output_dir
    if out is None:
        out = REPO_ROOT / "results" / f"{args.run_label}_{ts}"
    else:
        if not out.is_absolute():
            out = (REPO_ROOT / out).resolve()
    run_dir = out
    run_dir.mkdir(parents=True, exist_ok=True)
    raw_path = run_dir / "raw_runs.jsonl"

    manifest = {
        "run_label": args.run_label,
        "started_at_utc": ts,
        "question_bank_dir": str(bank_dir),
        "eval_config": str(args.eval_config),
        "model": args.model,
        "plan_count": len(run_plan),
        "dry_run": False,
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    simulator = HumanSimulator(
        llm_cfg=cfg.simulator_llm,
        use_seed_prompt=cfg.use_seed_prompt,
    )

    print(f"Run directory: {run_dir}", file=sys.stderr)
    print(f"Planned tasks: {len(run_plan)}", file=sys.stderr)

    any_failed = False
    env = os.environ.copy()
    # Child stdout is a pipe (not a TTY) → CPython uses block buffering; streaming
    # from DevStreamHook would not appear until buffer fills unless unbuffered.
    env.setdefault("PYTHONUNBUFFERED", "1")
    # Ensure subprocess finds matmaster_config / .env relative to cwd
    cwd = str(REPO_ROOT)

    for item in run_plan:
        question = item["question"]
        mode: str = item["mode"]
        repeat_idx: int = item["repeat_idx"]
        task_id = f"{question.id}_{mode}_r{repeat_idx}"

        task = simulator.formulate(question)
        prompt = task.prompt
        workspace_path = run_dir / "workspaces" / task_id
        workspace_path.mkdir(parents=True, exist_ok=True)
        log_dir = run_dir / "logs" / task_id
        log_dir.mkdir(parents=True, exist_ok=True)

        prompt = _stage_data_files(question, bank_dir, workspace_path, prompt)

        prompt_file = workspace_path / "_devshell_prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        # Same JSON line as stdout; read after run (child stdout inherits the terminal — no PIPE).
        summary_file = workspace_path / "_devshell_summary.json"

        cmd: list[str | Path] = [
            py,
            "-u",
            "-m",
            "matmaster.devshell",
            "run",
            "--workdir",
            workspace_path,
            "--log-dir",
            log_dir,
            "--prompt-file",
            prompt_file,
            "--json-out",
            summary_file,
        ]
        if args.model:
            cmd.extend(["--model", args.model])

        print(
            f"  [running] {task_id} (devshell prints to this terminal; summary → {summary_file.name})…",
            file=sys.stderr,
            flush=True,
        )
        # Inherit stdout/stderr so output is not piped (piping + uv/Cursor often buffers).
        proc = subprocess.run(cmd, cwd=cwd, env=env)
        rc = proc.returncode

        row: dict[str, Any] = {
            "task_id": task_id,
            "question_id": question.id,
            "capability": question.capability,
            "domain": question.domain,
            "mode": mode,
            "repeat_idx": repeat_idx,
            "devshell_exit_code": rc,
            "devshell_summary_path": str(summary_file),
        }
        summary: dict[str, Any] | None = None
        if summary_file.is_file():
            try:
                text = summary_file.read_text(encoding="utf-8").strip()
                if not text:
                    summary = {"parse_error": True, "empty_file": True}
                else:
                    last_line = text.splitlines()[-1].strip()
                    summary = json.loads(last_line)
            except (json.JSONDecodeError, OSError) as e:
                summary = {"parse_error": True, "error": str(e)}
        else:
            summary = {"parse_error": True, "missing_file": str(summary_file)}
        row["devshell_summary"] = summary

        with raw_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

        if rc != 0:
            any_failed = True
            if args.fail_fast:
                break
        status = "ok" if rc == 0 else "fail"
        print(f"  [{status}] {task_id} exit={rc}", file=sys.stderr, flush=True)

    print(f"Wrote {raw_path}", file=sys.stderr)
    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
