"""Tests for layout-independent Bohr parameter-sweep records."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.core.evaluator import BinaryEvaluator
from evaluation.core.evidence import BohrCliReceiptRecord, EvidenceBundle
from evaluation.core.runner import flatten_banks, load_question_banks
from evaluation.validators.json_file import check_bohr_parameter_sweep_record

REPO_ROOT = Path(__file__).resolve().parents[2]
QUESTION_BANK_DIR = REPO_ROOT / 'evaluation' / 'question_bank'


def _record(group_key: str = 'job_group_id') -> dict[str, object]:
    return {
        group_key: 16377695,
        'jobs': [
            {
                'temperature_K': temperature,
                'job_id': 23053718 + index,
                'status': 'submitted',
            }
            for index, temperature in enumerate(range(300, 1001, 100))
        ],
        'submitted_at': '2026-07-16T02:30:00Z',
    }


def _write_record(tmp_path: Path, value: object) -> None:
    (tmp_path / 'b3_jobs.json').write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def _check(tmp_path: Path) -> tuple[bool, str]:
    return check_bohr_parameter_sweep_record(
        tmp_path,
        filename='b3_jobs.json',
    )


def _execution_receipts() -> list[BohrCliReceiptRecord]:
    receipts = [
        BohrCliReceiptRecord.model_validate(
            {
                'schema_version': 'bohr_cli_receipt_v1',
                'operation': 'job_group.create',
                'argv': ['job_group', 'create', '-o', 'json'],
                'exit_code': 0,
                'ok': True,
                'captured_json': True,
                'ids': {'group_ids': [7247676, 16377695]},
            }
        )
    ]
    for index, temperature in enumerate(range(300, 1001, 100)):
        receipts.append(
            BohrCliReceiptRecord.model_validate(
                {
                    'schema_version': 'bohr_cli_receipt_v1',
                    'operation': 'job.submit',
                    'argv': ['job', 'submit', '-g', '7247676', '-o', 'json'],
                    'exit_code': 0,
                    'ok': True,
                    'captured_json': True,
                    'request': {
                        'job_group_ids': [7247676],
                        'command': f'echo "T={temperature}" > result.txt',
                        'temperatures_k': [temperature],
                    },
                    'ids': {
                        'job_ids': [23053718 + index, 33053718 + index],
                    },
                }
            )
        )
    return receipts


@pytest.mark.parametrize('group_key', ['job_group_id', 'group_id', 'task_group_id'])
def test_parameter_sweep_accepts_common_group_id_names(
    tmp_path: Path,
    group_key: str,
) -> None:
    _write_record(tmp_path, _record(group_key))

    ok, reason = _check(tmp_path)

    assert ok, reason


def test_parameter_sweep_accepts_nested_layout(tmp_path: Path) -> None:
    flat = _record('task_group_id')
    _write_record(
        tmp_path,
        {
            'batch': {'task_group_id': flat['task_group_id']},
            'results': flat['jobs'],
        },
    )

    ok, reason = _check(tmp_path)

    assert ok, reason


def test_parameter_sweep_requires_every_temperature_once(tmp_path: Path) -> None:
    value = _record()
    jobs = value['jobs']
    assert isinstance(jobs, list)
    jobs[-1]['temperature_K'] = 900
    _write_record(tmp_path, value)

    ok, reason = _check(tmp_path)

    assert not ok
    assert 'temperatures' in reason


def test_parameter_sweep_requires_distinct_positive_job_ids(tmp_path: Path) -> None:
    value = _record()
    jobs = value['jobs']
    assert isinstance(jobs, list)
    jobs[-1]['job_id'] = jobs[0]['job_id']
    _write_record(tmp_path, value)

    ok, reason = _check(tmp_path)

    assert not ok
    assert 'distinct' in reason


def test_parameter_sweep_rejects_conflicting_group_ids(tmp_path: Path) -> None:
    value = _record()
    value['metadata'] = {'task_group_id': 16377696}
    _write_record(tmp_path, value)

    ok, reason = _check(tmp_path)

    assert not ok
    assert 'conflicting' in reason


def test_parameter_sweep_execution_without_reference_fails_cleanly() -> None:
    from pydantic import ValidationError

    from evaluation.core.schemas import QuestionBank

    with pytest.raises(ValidationError, match='requires a matching reference_answers'):
        QuestionBank.model_validate(
            {
                'version': 'v5',
                'capability': 'workflow_orchestration',
                'domain': 'agnostic',
                'questions': [
                    {
                        'id': 'BWO_sweep_needs_ref_001',
                        'capability': 'workflow_orchestration',
                        'domain': 'agnostic',
                        'intent': 'sweep execution requires a reference answer',
                        'human_prompt_seed': 'run the sweep',
                        'reference_answers': [],
                        'scoring_checklist': [
                            {
                                'id': 'sweep_execution',
                                'criterion': 'sweep receipts match the record',
                                'verify': 'bohr_parameter_sweep_execution',
                            }
                        ],
                    }
                ],
            }
        )


def test_parameter_sweep_checker_is_registered_with_evaluator(
    tmp_path: Path,
) -> None:
    _write_record(tmp_path, _record('task_group_id'))
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(
        item for item in questions if item.id == 'BWO_param_sweep_003_20260715_v5'
    )

    record = BinaryEvaluator().evaluate(
        question=question,
        answer='done',
        evidence=EvidenceBundle(
            task_id=question.id,
            workspace_dir=str(tmp_path),
            total_steps=4,
            bohr_cli_receipts=_execution_receipts(),
        ),
    )

    assert record.criteria_results['sweep_execution'].passed is True


def test_parameter_sweep_execution_rejects_artifact_job_id_not_returned_by_cli(
    tmp_path: Path,
) -> None:
    value = _record('task_group_id')
    jobs = value['jobs']
    assert isinstance(jobs, list)
    jobs[-1]['job_id'] = 99999999
    _write_record(tmp_path, value)
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(
        item for item in questions if item.id == 'BWO_param_sweep_003_20260715_v5'
    )

    record = BinaryEvaluator().evaluate(
        question=question,
        answer='done',
        evidence=EvidenceBundle(
            task_id=question.id,
            workspace_dir=str(tmp_path),
            total_steps=4,
            bohr_cli_receipts=_execution_receipts(),
        ),
    )

    assert record.criteria_results['sweep_execution'].passed is False
    assert 'artifact job ID' in record.criteria_results['sweep_execution'].reason
