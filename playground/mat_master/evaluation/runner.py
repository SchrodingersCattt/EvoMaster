"""End-to-end MATTER v5 evaluation runner.

Current v5 runner behavior:
- Uses BinaryEvaluator (was RubricEvaluator)
- evaluate() returns EvalRunRecord directly (no raw dict intermediary)
- load_question_banks() accepts only v5 question banks
- _flatten_banks() no longer returns a rubric_map (Rubric class removed)
- _apply_filters() uses capability filters and explicit question IDs
- expand_run_plan() no longer reads repeat_override from QuestionItem
"""

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .aggregator import build_summary
from .evidence import EvidenceExtractor
from .evaluator import BinaryEvaluator
from .mat_runner import run_mat_task
from .reporter import append_raw_run, write_reports
from .schemas import (
    EvalConfig,
    EvalRunRecord,
    QuestionBank,
    QuestionItem,
    TokenUsageRecord,
)
from .simulator import HumanSimulator

_runner_logger = logging.getLogger(__name__)
_EVOMASTER_EVIDENCE_MAPPING_PATH = Path(__file__).parent / 'evidence_mapping.yaml'


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_evaluation(config: EvalConfig) -> dict[str, Any]:
    """Run MATTER evaluation according to config."""
    bank_dir = Path(_resolve_to_project_root(config.question_bank_dir))
    question_banks = load_question_banks(bank_dir)
    questions = _flatten_banks(question_banks)
    questions = _apply_filters(questions, config)

    output_dir = Path(_resolve_to_project_root(config.output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    run_dir = output_dir / f"{config.run_label}_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    mat_runs_dir = run_dir / 'mat_runs'
    mat_runs_dir.mkdir(parents=True, exist_ok=True)

    simulator = HumanSimulator(
        llm_cfg=config.simulator_llm, use_seed_prompt=config.use_seed_prompt
    )
    evaluator = BinaryEvaluator(llm_cfg=config.evaluator_llm)
    # The core extractor is runtime-agnostic. EvoMaster-specific tool/event
    # compatibility is injected here by the current runner.
    evidence_extractor = EvidenceExtractor(mapping_path=_EVOMASTER_EVIDENCE_MAPPING_PATH)

    records: list[EvalRunRecord] = []
    mat_config_path = Path(_resolve_to_project_root(config.mat_config_path))
    run_plan = expand_run_plan(questions=questions, config=config)

    for plan_item in run_plan:
        question: QuestionItem = plan_item['question']
        mode: str = plan_item['mode']
        repeat_idx: int = plan_item['repeat_idx']

        task = simulator.formulate(question)
        prompt = task.prompt
        task_id = f"{question.id}_{mode}_r{repeat_idx}"
        workspace_path = mat_runs_dir / 'workspaces' / task_id
        workspace_path.mkdir(parents=True, exist_ok=True)
        prompt = _stage_data_files(question, bank_dir, workspace_path, prompt)

        mat_result = run_mat_task(
            prompt=prompt,
            mode=mode,
            task_id=task_id,
            run_dir=mat_runs_dir,
            mat_config_path=mat_config_path,
        )
        answer = str(mat_result.get('answer', '') or '')
        tool_calls: list[dict[str, Any]] = mat_result.get('tool_calls', [])

        # Extract evidence bundle from trajectory
        trajectory_path = mat_result.get('trajectory_path')
        evidence = None
        if trajectory_path:
            try:
                evidence = evidence_extractor.extract(
                    trajectory_path=trajectory_path,
                    task_id=task_id,
                    final_answer=answer,
                )
            except Exception as exc:  # noqa: BLE001
                _runner_logger.warning(
                    "EvidenceExtractor failed for %s: %s", task_id, exc
                )

        # Populate token usage from evidence if available
        token_usage = TokenUsageRecord()
        if evidence is not None:
            token_usage = TokenUsageRecord(
                prompt_tokens=evidence.token_usage.prompt_tokens,
                completion_tokens=evidence.token_usage.completion_tokens,
                total_tokens=evidence.token_usage.total_tokens,
            )

        # BinaryEvaluator.evaluate() returns EvalRunRecord directly
        record = evaluator.evaluate(
            question=question,
            answer=answer,
            tool_calls=tool_calls,
            evidence=evidence,
            mode=mode,
            repeat_idx=repeat_idx,
            prompt=prompt,
            run_status=str(mat_result.get('status', 'unknown')),
            model_name=evidence.model_name if evidence is not None else None,
            token_usage=token_usage,
        )
        # Attach raw result for debugging
        record.raw_result = mat_result

        records.append(record)
        append_raw_run(output_dir=run_dir, record=record)

    summary = build_summary(records)
    report_paths = write_reports(output_dir=run_dir, records=records, summary=summary)
    return {
        'run_dir': str(run_dir),
        'records': records,
        'summary': summary,
        'report_paths': report_paths,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_filters(
    questions: list[QuestionItem], config: EvalConfig
) -> list[QuestionItem]:
    """Filter questions by capability and/or explicit IDs."""
    if config.include_capabilities:
        caps = {c.lower() for c in config.include_capabilities}
        questions = [q for q in questions if q.capability.lower() in caps]

    if config.include_question_ids:
        ids = set(config.include_question_ids)
        questions = [q for q in questions if q.id in ids]

    if not questions:
        raise ValueError(
            'No questions remaining after applying --capabilities / --questions filters'
        )
    return questions


def expand_run_plan(
    *, questions: list[QuestionItem], config: EvalConfig
) -> list[dict[str, Any]]:
    """Expand mode × k repeat plan.

    v5: QuestionItem no longer has repeat_override — always uses config.k.
    """
    plan: list[dict[str, Any]] = []
    for question in questions:
        active_modes = [
            mode for mode in config.modes if mode in question.mode_scope
        ]
        for mode in active_modes:
            for repeat_idx in range(config.k):
                plan.append(
                    {'question': question, 'mode': mode, 'repeat_idx': repeat_idx}
                )
    return plan


def load_question_banks(bank_dir: Path) -> list[QuestionBank]:
    """Load all YAML question banks from a directory.

    Recurses into one level of subdirectories to support the v5 directory
    layout. Non-bank YAML files (for example, ``manifest.yaml``) are skipped
    automatically if they lack a ``questions`` key.
    """
    v5_sub_paths: list[Path] = []
    for subdir in sorted(bank_dir.iterdir()):
        if subdir.is_dir() and subdir.name != 'data':
            v5_sub_paths.extend(sorted(subdir.glob('*.yaml')))

    top_paths: list[Path] = sorted(bank_dir.glob('*.yaml'))
    yaml_paths = top_paths + v5_sub_paths

    banks: list[QuestionBank] = []
    for path in yaml_paths:
        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        if 'questions' not in raw:
            _runner_logger.debug("Skipping non-bank YAML: %s", path.name)
            continue
        version = str(raw.get('version', ''))
        if version != 'v5':
            raise ValueError(
                f"Unsupported question bank version in {path}: expected 'v5', got {version!r}"
            )
        banks.append(QuestionBank.model_validate(raw))

    if not banks:
        raise ValueError(f"No question bank files found under {bank_dir}")
    return banks


def _flatten_banks(question_banks: list[QuestionBank]) -> list[QuestionItem]:
    """Flatten all question banks into a single list of QuestionItems."""
    questions: list[QuestionItem] = []
    for bank in question_banks:
        questions.extend(bank.questions)
    return questions


def _stage_data_files(
    question: QuestionItem,
    bank_dir: Path,
    workspace: Path,
    prompt: str,
) -> str:
    """Copy question data files into the agent workspace and rewrite prompt paths."""
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
        listing = ', '.join(f"`{name}`" for name in staged)
        prompt += (
            f"\n\n[The following data files are already in your working directory: {listing}]"
        )
    return prompt


def _resolve_to_project_root(path_str: str) -> str:
    path = Path(path_str)
    if path.is_absolute():
        return str(path)
    return str((_project_root() / path).resolve())


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]
