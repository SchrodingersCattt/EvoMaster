"""End-to-end MATTER v5 evaluation runner.

v5 changes (vs v4):
- Uses BinaryEvaluator (was RubricEvaluator)
- evaluate() returns EvalRunRecord directly (no raw dict intermediary)
- load_question_banks() supports both v5 YAML and v4 YAML via _convert_v4_to_v5 shim
- _flatten_banks() no longer returns a rubric_map (Rubric class removed)
- _apply_filters() uses capability instead of level; still accepts include_levels
  for backward compat (maps old level values to capabilities)
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
    SafetyVetoRecord,
    TokenUsageRecord,
)
from .simulator import HumanSimulator

_runner_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# v4 → v5 compatibility: level → capability mapping
# ---------------------------------------------------------------------------

_LEVEL_TO_CAPABILITY: dict[str, str] = {
    'L1': 'batch_processing',
    'L2': 'workflow_orchestration',
    'L3': 'data_diagnosis',
    'L4': 'data_diagnosis',
    'Safety': 'safety_refusal',
}

_DIMENSION_TO_AXIS: dict[str, str] = {
    'accuracy': 'correctness',
    'grounding': 'grounding',
    'efficiency': 'efficiency',
}

_VERIFY_REMAP: dict[str, str] = {
    'llm_judge': 'llm_binary_judge',
    'llm_judge_grounding': 'llm_binary_judge',
    'llm_judge_efficiency': 'llm_binary_judge',
}


def _convert_v4_to_v5(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a v4 YAML question bank dict to v5 format.

    Handles:
    - top-level ``level`` → ``capability`` (via _LEVEL_TO_CAPABILITY)
    - per-question ``level``, ``rubric_id``, ``touchpoints``, ``repeat_override`` removal
    - ``ScoringCheckItem.dimension`` → ``axis`` with value mapping
    - ``ScoringCheckItem.weight`` removal
    - ``llm_judge_grounding`` / ``llm_judge_efficiency`` → ``llm_binary_judge``
    - adds missing ``capability`` and ``domain`` fields with sensible defaults
    """
    level = str(raw.get('level', 'L1'))
    default_capability = _LEVEL_TO_CAPABILITY.get(level, 'batch_processing')

    converted_questions: list[dict[str, Any]] = []
    for q in raw.get('questions', []):
        q_level = str(q.get('level', level))
        capability = _LEVEL_TO_CAPABILITY.get(q_level, default_capability)
        domain = str(q.get('domain', 'general'))

        new_checklist: list[dict[str, Any]] = []
        for item in q.get('scoring_checklist', []):
            new_item: dict[str, Any] = {
                'id': item['id'],
                'criterion': item.get('criterion', ''),
            }
            # dimension → axis with value rename
            raw_dim = item.get('dimension', item.get('axis', 'accuracy'))
            new_item['axis'] = _DIMENSION_TO_AXIS.get(str(raw_dim), 'correctness')
            # verify: remap legacy LLM judge types
            raw_verify = str(item.get('verify', 'exact_match'))
            new_item['verify'] = _VERIFY_REMAP.get(raw_verify, raw_verify)
            # weight silently dropped
            new_checklist.append(new_item)

        new_q: dict[str, Any] = {
            'id': q['id'],
            'capability': capability,
            'domain': domain,
            'intent': q.get('intent', ''),
            'human_prompt_seed': q.get('human_prompt_seed', ''),
            'tags': q.get('tags', []),
            'mode_scope': q.get('mode_scope', ['direct', 'planner']),
            'required_tools': q.get('required_tools', []),
            'optional_tools': q.get('optional_tools', []),
            'data_files': q.get('data_files', []),
            'reference_answers': q.get('reference_answers', []),
            'scoring_checklist': new_checklist,
        }
        converted_questions.append(new_q)

    return {
        'version': 'v5',
        'capability': default_capability,
        'domain': 'general',
        'questions': converted_questions,
    }


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
    evidence_extractor = EvidenceExtractor()

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
    """Filter questions by capability and/or explicit IDs.

    Also accepts legacy ``include_levels`` and maps level names to capability
    codes for backward compatibility with v4 configs.
    """
    # v5 native filter: by capability
    if config.include_capabilities:
        caps = {c.lower() for c in config.include_capabilities}
        questions = [q for q in questions if q.capability.lower() in caps]

    # v4 backward-compat: include_levels → map to capabilities
    if config.include_levels and not config.include_capabilities:
        mapped_caps = {
            _LEVEL_TO_CAPABILITY.get(lvl.upper(), lvl.lower())
            for lvl in config.include_levels
        }
        questions = [q for q in questions if q.capability.lower() in mapped_caps]

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
