"""Tests for layout-independent Bohr parameter-sweep records."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.core.evaluator import BinaryEvaluator
from evaluation.core.evidence import EvidenceBundle
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


def test_parameter_sweep_checker_is_registered_with_evaluator(
    tmp_path: Path,
) -> None:
    _write_record(tmp_path, _record('task_group_id'))
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(
        item for item in questions if item.id == 'BWO_param_sweep_003_20260715_v4'
    )
    script = (
        "cat > submit_sweep.sh <<'SCRIPT'\n"
        "#!/bin/bash\n"
        "OUTPUT=$(bohr job submit \\\n"
        "  --project_id 14844 \\\n"
        '  --job_group_id "$GROUP_ID" \\\n'
        "  -i job.json -o json)\n"
        "SCRIPT"
    )

    record = BinaryEvaluator().evaluate(
        question=question,
        answer='done',
        evidence=EvidenceBundle(
            task_id=question.id,
            workspace_dir=str(tmp_path),
            total_steps=4,
        ),
        tool_calls=[
            {
                'tool_name': 'Bash',
                'tool_args': {
                    'command': (
                        'bohr job_group create -n sweep --project_id 14844 -o json'
                    )
                },
            },
            {'tool_name': 'Bash', 'tool_args': {'command': script}},
            {
                'tool_name': 'Bash',
                'tool_args': {'command': 'bash submit_sweep.sh'},
            },
        ],
    )

    assert record.criteria_results['sweep_record'].passed is True
    assert record.criteria_results['group_created_via_cli'].passed is True
    assert record.criteria_results['group_jobs_submitted_via_cli'].passed is True
