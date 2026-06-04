"""MATTER v5 question-bank loading, filtering, and run-plan expansion.

The end-to-end MATTER Core runner (``run_evaluation`` + Playground
``run_mat_task``) has been removed. Evaluation now runs exclusively through the
devshell path (``evaluation/scripts/devshell/run_devshell_eval.py``); scoring is
done by ``score_devshell_tasks.py`` / ``score_baseline_tasks.py``, both built on
``BinaryEvaluator``. This module retains the question-bank loaders, slice/ID
filtering, data-file staging, and run-plan expansion that those paths (and the
catalog-sync tooling) still share.
"""

import logging
import shutil
from pathlib import Path
from typing import Any

import yaml

from .schemas import CapabilitySlice, EvalConfig, QuestionBank, QuestionItem

_runner_logger = logging.getLogger(__name__)


def _question_matches_slice(question: QuestionItem, sl: CapabilitySlice) -> bool:
    if (
        sl.capability is not None
        and question.capability.lower() != sl.capability.lower()
    ):
        return False
    if sl.domains is not None:
        allowed = {d.lower() for d in sl.domains}
        if question.domain.lower() not in allowed:
            return False
    if sl.tags is not None:
        have = {str(t).lower() for t in question.tags}
        if not all(req.lower() in have for req in sl.tags):
            return False
    if sl.scope is not None and question.scope != sl.scope:
        return False
    return True


def _apply_filters(
    questions: list[QuestionItem], config: EvalConfig
) -> list[QuestionItem]:
    """Filter questions by OR-of-slices and/or explicit IDs."""
    if config.include_slices:
        picked: list[QuestionItem] = []
        seen: set[str] = set()
        for q in questions:
            if any(_question_matches_slice(q, sl) for sl in config.include_slices):
                if q.id not in seen:
                    seen.add(q.id)
                    picked.append(q)
        questions = picked

    if config.include_question_ids:
        ids = set(config.include_question_ids)
        questions = [q for q in questions if q.id in ids]

    if config.exclude_question_ids:
        excluded = set(config.exclude_question_ids)
        questions = [q for q in questions if q.id not in excluded]

    if not questions:
        raise ValueError(
            'No questions remaining after applying --slices / --questions filters'
        )
    return questions


def expand_run_plan(
    *, questions: list[QuestionItem], config: EvalConfig
) -> list[dict[str, Any]]:
    """Expand mode × k repeat plan.

    ``config.exp`` selects the matmaster exp (default ``direct``).
    """
    mode = config.exp
    plan: list[dict[str, Any]] = []
    for question in questions:
        for repeat_idx in range(config.k):
            plan.append({'question': question, 'mode': mode, 'repeat_idx': repeat_idx})
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
            _runner_logger.debug('Skipping non-bank YAML: %s', path.name)
            continue
        version = str(raw.get('version', ''))
        if version != 'v5':
            raise ValueError(
                f"Unsupported question bank version in {path}: expected 'v5', got {version!r}"
            )
        banks.append(QuestionBank.model_validate(raw))

    if not banks:
        raise ValueError(f'No question bank files found under {bank_dir}')
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
        old_legacy = f'question_bank/{df.path}'
        if old_legacy in prompt:
            prompt = prompt.replace(old_legacy, src.name)
    if staged:
        listing = ', '.join(f'`{name}`' for name in staged)
        prompt += f'\n\n[The following data files are already in your working directory: {listing}]'
    return prompt


def _resolve_to_project_root(path_str: str) -> str:
    path = Path(path_str)
    if path.is_absolute():
        return str(path)
    return str((_project_root() / path).resolve())


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]
