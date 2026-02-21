"""End-to-end MATTER evaluation runner."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .aggregator import build_summary
from .evaluator import RubricEvaluator
from .mat_runner import run_mat_task
from .reporter import append_raw_run, write_reports
from .schemas import EvalConfig, EvalRunRecord, QuestionBank, QuestionItem, Rubric, SafetyVetoRecord
from .simulator import SingleTurnSimulator


def run_evaluation(config: EvalConfig) -> dict[str, Any]:
    """Run MATTER evaluation according to config."""
    bank_dir = Path(_resolve_to_project_root(config.question_bank_dir))
    question_banks = load_question_banks(bank_dir)
    questions, rubric_map = _flatten_banks(question_banks)

    output_dir = Path(_resolve_to_project_root(config.output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = output_dir / f"{config.run_label}_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    mat_runs_dir = run_dir / "mat_runs"
    mat_runs_dir.mkdir(parents=True, exist_ok=True)

    simulator = SingleTurnSimulator(llm_cfg=config.simulator_llm, use_seed_prompt=config.use_seed_prompt)
    evaluator = RubricEvaluator(llm_cfg=config.evaluator_llm)

    records: list[EvalRunRecord] = []
    mat_config_path = Path(_resolve_to_project_root(config.mat_config_path))
    run_plan = expand_run_plan(questions=questions, config=config)
    for plan_item in run_plan:
        question = plan_item["question"]
        mode = plan_item["mode"]
        repeat_idx = plan_item["repeat_idx"]
        rubric = rubric_map[question.rubric_id]

        prompt = simulator.render_prompt(question)
        task_id = f"{question.id}_{mode}_r{repeat_idx}"
        workspace_path = mat_runs_dir / "workspaces" / task_id
        workspace_path.mkdir(parents=True, exist_ok=True)
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
        record = EvalRunRecord(
            question_id=question.id,
            level=question.level,
            mode=mode,
            repeat_idx=repeat_idx,
            prompt=prompt,
            answer=answer,
            run_status=str(mat_result.get("status", "unknown")),
            band_score=float(eval_payload.get("band_score", 0.0)),
            touchpoints=eval_payload.get("touchpoints", {}),
            deductions=eval_payload.get("deductions", []),
            confidence=float(eval_payload.get("confidence", 0.0)),
            safety_veto=safety_record,
            raw_result=mat_result,
        )
        records.append(record)
        append_raw_run(output_dir=run_dir, record=record)

    summary = build_summary(records)
    report_paths = write_reports(output_dir=run_dir, records=records, summary=summary)
    return {
        "run_dir": str(run_dir),
        "records": records,
        "summary": summary,
        "report_paths": report_paths,
    }


def expand_run_plan(*, questions: list[QuestionItem], config: EvalConfig) -> list[dict[str, Any]]:
    """Expand mode x k repeat plan."""
    plan: list[dict[str, Any]] = []
    for question in questions:
        repeats = question.repeat_override or config.k
        active_modes = [mode for mode in config.modes if mode in question.mode_scope]
        for mode in active_modes:
            for repeat_idx in range(repeats):
                plan.append({"question": question, "mode": mode, "repeat_idx": repeat_idx})
    return plan


def load_question_banks(bank_dir: Path) -> list[QuestionBank]:
    """Load all yaml question banks from directory."""
    banks: list[QuestionBank] = []
    for path in sorted(bank_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        banks.append(QuestionBank.model_validate(data))
    if not banks:
        raise ValueError(f"No question bank files found under {bank_dir}")
    return banks


def _flatten_banks(question_banks: list[QuestionBank]) -> tuple[list[QuestionItem], dict[str, Rubric]]:
    questions: list[QuestionItem] = []
    rubric_map: dict[str, Rubric] = {}
    for bank in question_banks:
        rubric_map[bank.rubric.id] = bank.rubric
        questions.extend(bank.questions)
    return questions, rubric_map


def _stage_data_files(
    question: QuestionItem,
    bank_dir: Path,
    workspace: Path,
    prompt: str,
) -> str:
    """Copy question data files into the agent workspace and rewrite prompt paths.

    After copying, appends a note listing every staged file so the agent knows
    exactly what is available in its working directory regardless of how the
    original prompt references the files.
    """
    staged: list[str] = []
    for df in question.data_files:
        src = bank_dir / df.path
        if not src.exists():
            continue
        dest = workspace / src.name
        shutil.copy2(src, dest)
        staged.append(src.name)
        if df.path in prompt:
            prompt = prompt.replace(df.path, src.name)
        old_legacy = f"question_bank/{df.path}"
        if old_legacy in prompt:
            prompt = prompt.replace(old_legacy, src.name)
    if staged:
        listing = ", ".join(f"`{name}`" for name in staged)
        prompt += f"\n\n[The following data files are already in your working directory: {listing}]"
    return prompt


def _resolve_to_project_root(path_str: str) -> str:
    path = Path(path_str)
    if path.is_absolute():
        return str(path)
    return str((_project_root() / path).resolve())


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]

