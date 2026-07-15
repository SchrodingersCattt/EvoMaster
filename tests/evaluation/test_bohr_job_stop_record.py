"""Tests for layout-independent Bohr job stop records."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.core.evaluator import BinaryEvaluator
from evaluation.core.evidence import EvidenceBundle
from evaluation.core.runner import flatten_banks, load_question_banks
from evaluation.validators.json_file import check_bohr_job_stop_record

REPO_ROOT = Path(__file__).resolve().parents[2]
QUESTION_BANK_DIR = REPO_ROOT / 'evaluation' / 'question_bank'
IMAGE = 'registry.dp.tech/dptech/ubuntu:22.04-py3.10-cuda12.1'
MACHINE_TYPE = 'c2_m4_cpu'
COMMAND = 'echo "b9 started" > b9_started.txt && sleep 600'
JOB_NAME = 'b9-stop-running-1721123456'


def _write_record(tmp_path: Path, value: object) -> None:
    (tmp_path / 'b9_actions.json').write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def _check(tmp_path: Path) -> tuple[bool, str]:
    return check_bohr_job_stop_record(
        tmp_path,
        filename='b9_actions.json',
        image=IMAGE,
        machine_type=MACHINE_TYPE,
        command=COMMAND,
        job_name_prefix='b9-stop-running-',
    )


def _flat_record() -> dict[str, object]:
    return {
        'job_id': 23050001,
        'job_name': JOB_NAME,
        'image': IMAGE,
        'machine_type': MACHINE_TYPE,
        'command': COMMAND,
        'polls': [
            {'status': 3, 'webStatus': 9},
            {'status': 2, 'webStatus': 5, 'errorInfo': 'terminated by user'},
        ],
        'action': 'bohr job terminate 23050001',
    }


def test_bohr_job_stop_record_accepts_flat_document(tmp_path: Path) -> None:
    _write_record(tmp_path, _flat_record())

    ok, reason = _check(tmp_path)

    assert ok, reason


def test_bohr_job_stop_record_accepts_nested_document(tmp_path: Path) -> None:
    _write_record(
        tmp_path,
        {
            'task': {
                'task_id': '23050001',
                'name': JOB_NAME,
                'spec': {
                    'container': IMAGE,
                    'resource': MACHINE_TYPE,
                    'entrypoint': COMMAND,
                },
            },
            'history': {
                'before': {'status_code': 3, 'web_status_code': 9},
                'after': {'status_code': 2, 'web_status_code': 5},
            },
            'operation': {'command_line': 'bohr job terminate 23050001'},
        },
    )

    ok, reason = _check(tmp_path)

    assert ok, reason


def test_bohr_job_stop_record_does_not_treat_raw_status_as_web_status(
    tmp_path: Path,
) -> None:
    record = _flat_record()
    record['polls'] = [
        {'status': 3, 'webStatus': 9},
        {'status': 5, 'webStatus': 6},
    ]
    _write_record(tmp_path, record)

    ok, reason = _check(tmp_path)

    assert not ok
    assert 'webStatus' in reason


def test_bohr_job_stop_record_requires_two_status_queries(tmp_path: Path) -> None:
    record = _flat_record()
    record['polls'] = [{'status': 3, 'webStatus': 5}]
    _write_record(tmp_path, record)

    ok, reason = _check(tmp_path)

    assert not ok
    assert 'fewer than two' in reason


def test_bohr_job_stop_record_is_registered_with_evaluator(tmp_path: Path) -> None:
    _write_record(tmp_path, _flat_record())
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(
        item for item in questions if item.id == 'BWO_stop_running_009_20260715_v3'
    )

    record = BinaryEvaluator().evaluate(
        question=question,
        answer='done',
        evidence=EvidenceBundle(
            task_id=question.id,
            workspace_dir=str(tmp_path),
            total_steps=5,
        ),
        tool_calls=[
            {
                'tool_name': 'Bash',
                'tool_args': {
                    'command': (
                        "bohr job submit --command 'echo \"b9 started\" > "
                        "b9_started.txt && sleep 600'"
                    )
                },
            },
            {
                'tool_name': 'Bash',
                'tool_args': {'command': 'bohr job describe -i 20400001 --json'},
            },
            {
                'tool_name': 'Bash',
                'tool_args': {'command': 'bohr job terminate 23050001'},
            },
            {
                'tool_name': 'Bash',
                'tool_args': {'command': 'bohr job describe -i 20400001 --json'},
            },
        ],
    )

    assert record.criteria_results['stop_record'].passed is True
    assert record.criteria_results['submitted_via_cli'].passed is True
    assert record.criteria_results['one_control_action_via_cli'].passed is True
    assert record.criteria_results['terminated_via_cli'].passed is True
