"""CLI entry point for the literature reproduction benchmark.

Usage examples::

    # Run specific papers from tasks.yaml
    python -m playground.mat_master.evaluation.literature_benchmark.scripts.run_benchmark \\
        run --papers A1,D1,J1

    # Run a whole category
    python -m ... run --category A

    # Run all tasks in tasks.yaml
    python -m ... run --all

    # Ad-hoc: reproduce a paper PDF directly (no tasks.yaml entry needed)
    python -m ... run-paper papers/A1_common_workflows_EOS_npjCM2021.pdf --hint "Si EOS"

    # Regenerate the dashboard report
    python -m ... report

Flow:
    load tasks.yaml  ->  HumanSimulator.spec_to_question()  ->  MATTER runner  ->  report
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

_BENCHMARK_DIR = Path(__file__).resolve().parents[1]
_TASKS_PATH = _BENCHMARK_DIR / "tasks.yaml"
_PROGRESS_PATH = _BENCHMARK_DIR / "progress.yaml"
_PAPERS_DIR = _BENCHMARK_DIR / "papers"

_LIT_RUBRIC = {
    "id": "R_LIT_001",
    "level": "L3",
    "score_bands": [0.0, 0.5, 1.0],
    "pass_threshold": 1.0,
    "description": (
        "Literature reproduction benchmark. "
        "1.0 = all values within tolerance; "
        "0.5 = workflow correct but values outside tolerance; "
        "0.0 = calculation failed or physically inconsistent."
    ),
    "criteria": {
        "1.0": "All target values within tolerance, workflow complete.",
        "0.5": "Workflow correct but one or more values outside tolerance.",
        "0.0": "Calculation failed or results physically inconsistent.",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def _load_progress() -> dict[str, Any]:
    if _PROGRESS_PATH.exists():
        return yaml.safe_load(_PROGRESS_PATH.read_text(encoding="utf-8")) or {}
    return {}


def _save_progress(progress: dict[str, Any]) -> None:
    with open(_PROGRESS_PATH, "w", encoding="utf-8") as fh:
        yaml.dump(progress, fh, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _update_progress(
    progress: dict[str, Any],
    question_id: str,
    *,
    status: str,
    score: float | None = None,
    verdict: str | None = None,
    walltime: str | None = None,
    error: str | None = None,
) -> None:
    entry = progress.setdefault(question_id, {})
    entry["status"] = status
    if score is not None:
        entry["score"] = score
    if verdict is not None:
        entry["verdict"] = verdict
    if walltime is not None:
        entry["walltime"] = walltime
    if error is not None:
        entry["error"] = error
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_progress(progress)


def _score_to_verdict(score: float) -> str:
    if score >= 1.0:
        return "PASS"
    if score >= 0.5:
        return "PARTIAL"
    return "FAIL"


# ---------------------------------------------------------------------------
# Task loading
# ---------------------------------------------------------------------------


def load_task_specs() -> list[dict[str, Any]]:
    """Load lightweight task specs from tasks.yaml."""
    raw = yaml.safe_load(_TASKS_PATH.read_text(encoding="utf-8")) or {}
    return raw.get("tasks", [])


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> None:
    """Run the benchmark pipeline."""

    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
    from playground.mat_master.evaluation.schemas import (
        EvalConfig,
        EvalRunRecord,
        Rubric,
        SafetyVetoRecord,
        TaskSpec,
    )
    from playground.mat_master.evaluation.simulator import HumanSimulator
    from playground.mat_master.evaluation.mat_runner import run_mat_task
    from playground.mat_master.evaluation.evaluator import RubricEvaluator
    from playground.mat_master.evaluation.aggregator import build_summary
    from playground.mat_master.evaluation.reporter import append_raw_run, write_reports
    from playground.mat_master.evaluation.runner import _resolve_to_project_root, _stage_data_files

    # Step 1: Load tasks
    raw_specs = load_task_specs()
    _log(f"Loaded {len(raw_specs)} task specs from {_TASKS_PATH.name}")

    # Step 2: Build simulator and convert specs to QuestionItems
    papers_dir = Path(args.papers_dir) if args.papers_dir else _PAPERS_DIR
    simulator = HumanSimulator(difficulty=1, papers_dir=papers_dir)

    specs = [TaskSpec.model_validate(raw) for raw in raw_specs]

    # Step 3: Filter
    if args.papers:
        paper_ids = {p.strip() for p in args.papers.split(",")}
        specs = [s for s in specs if s.paper_id in paper_ids]
        _log(f"Filtered to {len(specs)} specs for papers: {paper_ids}")
    elif args.category:
        cats = {c.strip().upper() for c in args.category.split(",")}
        specs = [s for s in specs if s.paper_id and s.paper_id[0].upper() in cats]
        _log(f"Filtered to {len(specs)} specs for categories: {cats}")

    if not specs:
        _log("No tasks to run after filtering")
        sys.exit(0)

    # Step 4: Convert to QuestionItems
    questions = [simulator.spec_to_question(s) for s in specs]
    rubric = Rubric.model_validate(_LIT_RUBRIC)
    rubric_map = {rubric.id: rubric}
    _log(f"{len(questions)} questions ready")

    # Step 5: Setup output directory
    config_path = _BENCHMARK_DIR / "config.yaml"
    if config_path.exists():
        config_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    else:
        config_data = {}
    config = EvalConfig.model_validate({
        "modes": config_data.get("modes", ["planner"]),
        "k": config_data.get("k", 1),
        "output_dir": config_data.get("output_dir", str(_BENCHMARK_DIR / "runs")),
        "run_label": config_data.get("run_label", "lit_bench"),
        "mat_config_path": config_data.get("mat_config_path", "configs/mat_master/config.yaml"),
    })

    output_dir = Path(_resolve_to_project_root(config.output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = output_dir / f"{config.run_label}_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    mat_runs_dir = run_dir / "mat_runs"
    mat_runs_dir.mkdir(parents=True, exist_ok=True)

    evaluator = RubricEvaluator(llm_cfg=config.evaluator_llm)
    mat_config_path = Path(_resolve_to_project_root(config.mat_config_path))

    # Step 6: Execute
    progress = _load_progress()
    records: list[EvalRunRecord] = []
    n_total = len(questions)
    n_done = 0
    counters = {"PASS": 0, "PARTIAL": 0, "FAIL": 0}

    for question in questions:
        mode = "planner"
        task_id = f"{question.id}_{mode}_r0"

        existing = progress.get(question.id, {})
        if existing.get("status") == "scored" and not args.force:
            verdict = existing.get("verdict", "?")
            _log(f"{question.id:<30s} SKIPPED   (already: {verdict})")
            n_done += 1
            counters[verdict] = counters.get(verdict, 0) + 1
            continue

        _update_progress(progress, question.id, status="running")
        _log(f"{question.id:<30s} RUNNING")
        t0 = time.time()

        try:
            prompt = question.human_prompt_seed
            workspace_path = mat_runs_dir / "workspaces" / task_id
            workspace_path.mkdir(parents=True, exist_ok=True)

            bank_dir = papers_dir.parent if papers_dir.exists() else _BENCHMARK_DIR
            prompt = _stage_data_files(question, bank_dir, workspace_path, prompt)

            mat_result = run_mat_task(
                prompt=prompt,
                mode=mode,
                task_id=task_id,
                run_dir=mat_runs_dir,
                mat_config_path=mat_config_path,
            )
            answer = str(mat_result.get("answer", "") or "")
            eval_payload = evaluator.evaluate(question=question, rubric=rubric, answer=answer)

            safety_payload = eval_payload.get("safety_veto", {})
            safety_record = (
                SafetyVetoRecord.model_validate(safety_payload)
                if isinstance(safety_payload, dict)
                else SafetyVetoRecord()
            )

            score = float(eval_payload.get("band_score", 0.0))
            verdict = _score_to_verdict(score)
            walltime = f"{time.time() - t0:.0f}s"

            record = EvalRunRecord(
                question_id=question.id,
                level=question.level,
                mode=mode,
                repeat_idx=0,
                prompt=prompt,
                answer=answer,
                run_status=str(mat_result.get("status", "unknown")),
                band_score=score,
                touchpoints=eval_payload.get("touchpoints", {}),
                deductions=eval_payload.get("deductions", []),
                confidence=float(eval_payload.get("confidence", 0.0)),
                safety_veto=safety_record,
                raw_result=mat_result,
            )
            records.append(record)
            append_raw_run(output_dir=run_dir, record=record)

            _update_progress(progress, question.id, status="scored", score=score, verdict=verdict, walltime=walltime)
            _log(f"{question.id:<30s} SCORED    score={score} {verdict} ({walltime})")
            counters[verdict] = counters.get(verdict, 0) + 1

        except Exception as exc:
            walltime = f"{time.time() - t0:.0f}s"
            _update_progress(progress, question.id, status="failed", score=0.0, verdict="FAIL",
                             walltime=walltime, error=str(exc)[:200])
            _log(f"{question.id:<30s} FAILED    {exc!r}")
            counters["FAIL"] = counters.get("FAIL", 0) + 1

        n_done += 1
        _log(f"{'=' * 60}")
        _log(f"Progress: {n_done}/{n_total} | PASS={counters['PASS']} PARTIAL={counters['PARTIAL']} FAIL={counters['FAIL']}")
        _log(f"{'=' * 60}")

    # Step 7: Reports
    if records:
        summary = build_summary(records)
        write_reports(output_dir=run_dir, records=records, summary=summary)
        _log(f"Reports written to {run_dir}")

    # Step 8: Dashboard
    _log("Generating literature dashboard...")
    from .lit_report import generate_dashboard
    generate_dashboard(progress=progress, run_dir=run_dir if records else None)
    _log("Done")


def cmd_run_paper(args: argparse.Namespace) -> None:
    """Ad-hoc: reproduce a single paper PDF (no tasks.yaml entry needed)."""

    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
    from playground.mat_master.evaluation.simulator import HumanSimulator
    from playground.mat_master.evaluation.mat_runner import run_mat_task
    from playground.mat_master.evaluation.runner import _resolve_to_project_root

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        _log(f"PDF not found: {pdf_path}")
        sys.exit(1)

    simulator = HumanSimulator()
    task = simulator.formulate(pdf_path, hint=args.hint or "")
    _log(f"Prompt: {task.prompt[:120]}...")

    mat_config_path = Path(_resolve_to_project_root("configs/mat_master/config.yaml"))
    output_dir = _BENCHMARK_DIR / "runs"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = output_dir / f"adhoc_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    workspace = run_dir / "workspaces" / "adhoc"
    workspace.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf_path, workspace / pdf_path.name)

    _log("Running mat_master in planner mode...")
    mat_result = run_mat_task(
        prompt=task.prompt,
        mode="planner",
        task_id="adhoc",
        run_dir=run_dir,
        mat_config_path=mat_config_path,
    )
    answer = str(mat_result.get("answer", "") or "")
    _log(f"Answer ({len(answer)} chars): {answer[:300]}...")


def cmd_report(args: argparse.Namespace) -> None:
    """Regenerate the dashboard from existing progress."""
    progress = _load_progress()
    run_dir = Path(args.run_dir) if args.run_dir else None
    from .lit_report import generate_dashboard
    generate_dashboard(progress=progress, run_dir=run_dir)
    _log("Dashboard updated")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Literature Reproduction Benchmark")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run benchmark from tasks.yaml")
    run_p.add_argument("--papers", type=str, default="", help="Comma-separated paper IDs (e.g. A1,B1,J1)")
    run_p.add_argument("--category", type=str, default="", help="Comma-separated categories (e.g. A,B)")
    run_p.add_argument("--all", action="store_true", help="Run all tasks")
    run_p.add_argument("--force", action="store_true", help="Re-run even if already scored")
    run_p.add_argument("--papers-dir", type=str, default="", help="Override papers directory")

    paper_p = sub.add_parser("run-paper", help="Ad-hoc: reproduce a single paper PDF")
    paper_p.add_argument("pdf", type=str, help="Path to paper PDF")
    paper_p.add_argument("--hint", type=str, default="", help="Focus hint (e.g. 'Si EOS')")

    rep_p = sub.add_parser("report", help="Regenerate dashboard report")
    rep_p.add_argument("--run-dir", type=str, default="", help="Path to specific run directory")

    args = parser.parse_args()
    if args.command == "run":
        cmd_run(args)
    elif args.command == "run-paper":
        cmd_run_paper(args)
    elif args.command == "report":
        cmd_report(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
