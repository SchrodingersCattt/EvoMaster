"""Tests for layout-independent Bohr job stop records."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.core.evaluator import BinaryEvaluator
from evaluation.core.evidence import BohrCliReceiptRecord, EvidenceBundle
from evaluation.core.runner import flatten_banks, load_question_banks
from evaluation.validators.bohr_cli import check_bohr_job_stop_execution
from evaluation.validators.json_file import check_bohr_job_stop_record
from tests.evaluation.bohr_cli_receipt_helpers import make_receipt as _receipt

REPO_ROOT = Path(__file__).resolve().parents[2]
QUESTION_BANK_DIR = REPO_ROOT / 'evaluation' / 'question_bank'
IMAGE = 'registry.dp.tech/dptech/ubuntu:22.04-py3.10-cuda12.1'
MACHINE_TYPE = 'c2_m4_cpu'
COMMAND = 'echo "b9 started" > b9_started.txt && sleep 600'
JOB_NAME = 'b9-stop-running-1721123456'
BOHR_JOB_ID = 20400001
PLATFORM_JOB_ID = 23050001


def _execution_receipts(
    *, control_job_id: int = PLATFORM_JOB_ID
) -> list[BohrCliReceiptRecord]:
    submit_ids = {
        'job_ids': [BOHR_JOB_ID, PLATFORM_JOB_ID],
        'bohr_job_ids': [BOHR_JOB_ID],
        'platform_job_ids': [PLATFORM_JOB_ID],
    }
    return [
        _receipt(
            operation='job.submit',
            argv=['job', 'submit', '-o', 'json'],
            captured_json=True,
            request={
                'image_address': IMAGE,
                'machine_type': MACHINE_TYPE,
                'command': COMMAND,
                'job_name': JOB_NAME,
            },
            ids=submit_ids,
        ),
        _receipt(
            operation='job.describe',
            argv=['job', 'describe', '-i', str(BOHR_JOB_ID), '-o', 'json'],
            captured_json=True,
            request={'bohr_job_ids': [BOHR_JOB_ID]},
            ids=submit_ids,
            job_state={'status': 3, 'web_status': 9},
        ),
        _receipt(
            operation='job.terminate',
            argv=['job', 'terminate', str(control_job_id), '-o', 'json'],
        ),
        _receipt(
            operation='job.describe',
            argv=['job', 'describe', '-i', str(BOHR_JOB_ID), '-o', 'json'],
            captured_json=True,
            request={'bohr_job_ids': [BOHR_JOB_ID]},
            ids=submit_ids,
            job_state={'status': 6, 'web_status': 6},
        ),
    ]


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


def test_bohr_job_stop_record_accepts_bohr_id_key(tmp_path: Path) -> None:
    record = _flat_record()
    record['bohr_id'] = record.pop('job_id')
    _write_record(tmp_path, record)

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


def test_bohr_job_stop_record_accepts_successful_kill_during_status_propagation(
    tmp_path: Path,
) -> None:
    record = _flat_record()
    record['polls'] = [
        {'status': 3, 'webStatus': 9},
        {'status': 1, 'webStatus': 1},
    ]
    record['action'] = {
        'command': 'bohr job kill 23050001',
        'result': 'success',
    }
    _write_record(tmp_path, record)

    ok, reason = _check(tmp_path)

    assert ok, reason


def test_bohr_job_stop_record_rejects_failed_control_without_stopped_state(
    tmp_path: Path,
) -> None:
    record = _flat_record()
    record['polls'] = [
        {'status': 3, 'webStatus': 9},
        {'status': 1, 'webStatus': 1},
    ]
    record['action'] = {
        'command': 'bohr job terminate 23050001',
        'result': 'failed',
    }
    _write_record(tmp_path, record)

    ok, reason = _check(tmp_path)

    assert not ok
    assert 'successful stop action or stopped state' in reason


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
        item for item in questions if item.id == 'BWO_stop_running_009_20260715_v4'
    )

    record = BinaryEvaluator().evaluate(
        question=question,
        answer='done',
        evidence=EvidenceBundle(
            task_id=question.id,
            workspace_dir=str(tmp_path),
            total_steps=5,
            bohr_cli_receipts=_execution_receipts(),
        ),
    )

    result = record.criteria_results['stop_execution']
    assert result.passed is True, result.reason


def test_bohr_job_stop_execution_rejects_control_of_another_job(
    tmp_path: Path,
) -> None:
    _write_record(tmp_path, _flat_record())

    ok, reason = check_bohr_job_stop_execution(
        tmp_path,
        filename='b9_actions.json',
        image=IMAGE,
        machine_type=MACHINE_TYPE,
        command=COMMAND,
        job_name_prefix='b9-stop-running-',
        receipts=_execution_receipts(control_job_id=23059999),
    )

    assert not ok
    assert 'submitted job' in reason
