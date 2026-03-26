"""End-to-end MATTER evaluation runner."""

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .aggregator import build_summary
from .evidence import EvidenceExtractor
from .evaluator import RubricEvaluator
from .mat_runner import run_mat_task
from .reporter import append_raw_run, write_reports
from .schemas import (
    EvalConfig,
    EvalRunRecord,
    QuestionBank,
    QuestionItem,
    Rubric,
    SafetyVetoRecord,
    TokenUsageRecord,
)
from .simulator import HumanSimulator

_runner_logger = logging.getLogger(__name__)


def run_evaluation(config: EvalConfig) -> dict[str, Any]:
    """Run MATTER evaluation according to config."""
    bank_dir = Path(_resolve_to_project_root(config.question_bank_dir))
    question_banks = load_question_banks(bank_dir)
    questions, rubric_map = _flatten_banks(question_banks)
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
    evaluator = RubricEvaluator(llm_cfg=config.evaluator_llm)
    # EvidenceExtractor is shared across all runs (loads mapping once)
    evidence_extractor = EvidenceExtractor()

    records: list[EvalRunRecord] = []
    mat_config_path = Path(_resolve_to_project_root(config.mat_config_path))
    run_plan = expand_run_plan(questions=questions, config=config)
    for plan_item in run_plan:
        question = plan_item['question']
        mode = plan_item['mode']
        repeat_idx = plan_item['repeat_idx']
        rubric = rubric_map[question.rubric_id]

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
        tool_calls = mat_result.get('tool_calls', [])

        # --- Phase 3: extract evidence bundle from trajectory ---
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

        eval_payload = evaluator.evaluate(
            question=question,
            rubric=rubric,
            answer=answer,
            tool_calls=tool_calls,
            evidence=evidence,
        )
        safety_payload = eval_payload.get('safety_veto', {})
        safety_record = (
            SafetyVetoRecord.model_validate(safety_payload)
            if isinstance(safety_payload, dict)
            else SafetyVetoRecord()
        )

        # --- Phase 3: populate three-dimensional scores + model/token info ---
        token_usage_record = TokenUsageRecord()
        if evidence is not None:
            token_usage_record = TokenUsageRecord(
                prompt_tokens=evidence.token_usage.prompt_tokens,
                completion_tokens=evidence.token_usage.completion_tokens,
                total_tokens=evidence.token_usage.total_tokens,
            )

        record = EvalRunRecord(
            question_id=question.id,
            level=question.level,
            mode=mode,
            repeat_idx=repeat_idx,
            prompt=prompt,
            answer=answer,
            run_status=str(mat_result.get('status', 'unknown')),
            band_score=float(eval_payload.get('band_score', 0.0)),
            touchpoints=eval_payload.get('touchpoints', {}),
            deductions=eval_payload.get('deductions', []),
            confidence=float(eval_payload.get('confidence', 0.0)),
            safety_veto=safety_record,
            tool_calls=tool_calls,
            raw_result=mat_result,
            # Phase 3 additions
            accuracy_score=eval_payload.get('accuracy_score'),
            grounding_score=eval_payload.get('grounding_score'),
            efficiency_score=eval_payload.get('efficiency_score'),
            strict_final=eval_payload.get('strict_final'),
            analysis_final=eval_payload.get('analysis_final'),
            model_name=evidence.model_name if evidence is not None else None,
            token_usage=token_usage_record,
        )
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


def _apply_filters(
    questions: list[QuestionItem], config: EvalConfig
) -> list[QuestionItem]:
    """Filter questions by level and/or explicit IDs when CLI overrides are set."""
    if config.include_levels:
        levels = {lvl.upper() for lvl in config.include_levels}
        questions = [q for q in questions if q.level.upper() in levels]
    if config.include_question_ids:
        ids = set(config.include_question_ids)
        questions = [q for q in questions if q.id in ids]
    if not questions:
        raise ValueError(
            'No questions remaining after applying --levels / --questions filters'
        )
    return questions


def expand_run_plan(
    *, questions: list[QuestionItem], config: EvalConfig
) -> list[dict[str, Any]]:
    """Expand mode x k repeat plan."""
    plan: list[dict[str, Any]] = []
    for question in questions:
        repeats = question.repeat_override or config.k
        active_modes = [mode for mode in config.modes if mode in question.mode_scope]
        for mode in active_modes:
            for repeat_idx in range(repeats):
                plan.append(
                    {'question': question, 'mode': mode, 'repeat_idx': repeat_idx}
                )
    return plan


def load_question_banks(bank_dir: Path) -> list[QuestionBank]:
    """Load all YAML question banks from a directory.

    Supports both v5 YAML (version: 'v5') and v4 YAML (version: 'v2' or absent).
    v4 files are converted via _convert_v4_to_v5() before validation.
    Also recurses into one level of subdirectories to support the v5 directory
    layout (capability/domain/*.yaml).

    When v5 subdirectory banks are found, deprecated top-level v4 files
    (level1.yaml, level2.yaml, safety_refusal.yaml) are skipped to avoid
    double-counting.  Non-bank YAML files (e.g. manifest.yaml) are also
    skipped automatically if they lack a ``questions`` key.
    """
    # Collect v5 subdirectory files first
    v5_sub_paths: list[Path] = []
    for subdir in sorted(bank_dir.iterdir()):
        if subdir.is_dir() and subdir.name != 'data':
            v5_sub_paths.extend(sorted(subdir.glob('*.yaml')))

    # Top-level YAML files
    top_paths: list[Path] = sorted(bank_dir.glob('*.yaml'))

    # If v5 subdirectory banks exist, skip deprecated top-level v4 files
    _deprecated_stems = {'level1', 'level2', 'level3', 'level4', 'safety_refusal'}
    if v5_sub_paths:
        top_paths = [
            p for p in top_paths
            if p.stem not in _deprecated_stems
        ]

    yaml_paths = top_paths + v5_sub_paths

    banks: list[QuestionBank] = []
    for path in yaml_paths:
        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        # Skip non-bank YAML files (e.g. manifest.yaml, evidence_mapping.yaml)
        if 'questions' not in raw:
            _runner_logger.debug("Skipping non-bank YAML: %s", path.name)
            continue
        version = str(raw.get('version', 'v2'))
        if version not in ('v5',):
            # v2, v4 or anything else → run compatibility shim
            raw = _convert_v4_to_v5(raw)
        banks.append(QuestionBank.model_validate(raw))

    if not banks:
        raise ValueError(f"No question bank files found under {bank_dir}")
    return banks


def _flatten_banks(
    question_banks: list[QuestionBank],
) -> tuple[list[QuestionItem], dict[str, Rubric]]:
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
        listing = ', '.join(f"`{name}`" for name in staged)
        prompt += f"\n\n[The following data files are already in your working directory: {listing}]"
    return prompt


def _resolve_to_project_root(path_str: str) -> str:
    path = Path(path_str)
    if path.is_absolute():
        return str(path)
    return str((_project_root() / path).resolve())


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]
