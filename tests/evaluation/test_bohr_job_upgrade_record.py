"""Tests for layout-independent Bohr job upgrade records."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.core.evaluator import BinaryEvaluator
from evaluation.core.evidence import BohrCliReceiptRecord, EvidenceBundle
from evaluation.core.runner import flatten_banks, load_question_banks
from evaluation.validators.bohr_cli import check_bohr_job_upgrade_execution
from evaluation.validators.json_file import check_bohr_job_upgrade_record
from tests.evaluation.bohr_cli_receipt_helpers import make_receipt as _receipt

REPO_ROOT = Path(__file__).resolve().parents[2]
QUESTION_BANK_DIR = REPO_ROOT / 'evaluation' / 'question_bank'
SEED_ID = 20400341
IMAGE = 'registry.dp.tech/dptech/dpmd-cu126-outisli:v20260712'
COMMAND = "echo 'T4 test for eval E6' > result.txt"
TARGET_MACHINE = 'c16_m60_1 * NVIDIA A100_80g'
NEW_BOHR_JOB_ID = 20409999
NEW_PLATFORM_JOB_ID = 23059999


def _execution_receipts(
    *,
    include_machine_query: bool = True,
    target_machine: str = TARGET_MACHINE,
) -> list[BohrCliReceiptRecord]:
    receipts = [
        _receipt(
            operation='job.describe',
            argv=['job', 'describe', '-i', str(SEED_ID), '-o', 'json'],
            captured_json=True,
            request={'bohr_job_ids': [SEED_ID]},
            ids={
                'job_ids': [SEED_ID, 23052040],
                'bohr_job_ids': [SEED_ID],
                'platform_job_ids': [23052040],
            },
        )
    ]
    if include_machine_query:
        receipts.append(
            _receipt(
                operation='machine.list',
                argv=['machine', 'list', '-c', 'gpu', '-s', 'job', '-o', 'json'],
            )
        )
    receipts.append(
        _receipt(
            operation='job.submit',
            argv=['job', 'submit', '-i', 'job_a100.json', '-o', 'json'],
            captured_json=True,
            request={
                'input_path': 'job_a100.json',
                'image_address': IMAGE,
                'machine_type': target_machine,
                'command': COMMAND,
            },
            ids={
                'job_ids': [NEW_BOHR_JOB_ID, NEW_PLATFORM_JOB_ID],
                'bohr_job_ids': [NEW_BOHR_JOB_ID],
                'platform_job_ids': [NEW_PLATFORM_JOB_ID],
            },
        )
    )
    return receipts


def _write_record(tmp_path: Path, value: object) -> None:
    (tmp_path / 'e6_upgrade.json').write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def _check(tmp_path: Path) -> tuple[bool, str]:
    return check_bohr_job_upgrade_record(
        tmp_path,
        filename='e6_upgrade.json',
        seed_id=SEED_ID,
        source_machine_pattern=r'(?i:T4)',
        target_machine_pattern=r'(?i:A100)',
        image=IMAGE,
        command=COMMAND,
    )


def _flat_record() -> dict[str, object]:
    return {
        'original_job': {
            'bohr_id': SEED_ID,
            'job_id': 23052040,
            'machine': 'c16_m62_1 * NVIDIA T4',
        },
        'resubmitted_job': {
            'job_id': NEW_PLATFORM_JOB_ID,
            'machine': TARGET_MACHINE,
        },
        'preserved': {'image': IMAGE, 'command': COMMAND},
    }


def test_bohr_job_upgrade_record_accepts_flat_document(tmp_path: Path) -> None:
    _write_record(tmp_path, _flat_record())

    ok, reason = _check(tmp_path)

    assert ok, reason


def test_bohr_job_upgrade_record_accepts_nested_document(tmp_path: Path) -> None:
    _write_record(
        tmp_path,
        {
            'operation': {
                'source': {
                    'identifiers': [str(SEED_ID), 23052040],
                    'hardware': '1 * NVIDIA T4',
                },
                'target': {
                    'identifier': '23059999',
                    'hardware': '1 * NVIDIA A100_80g',
                },
            },
            'unchanged_configuration': {
                'container': IMAGE,
                'entrypoint': COMMAND,
            },
        },
    )

    ok, reason = _check(tmp_path)

    assert ok, reason


def test_bohr_job_upgrade_record_accepts_pending_a100_submission(
    tmp_path: Path,
) -> None:
    record = _flat_record()
    target = record['resubmitted_job']
    assert isinstance(target, dict)
    target['status'] = 'Pending'
    _write_record(tmp_path, record)

    ok, reason = _check(tmp_path)

    assert ok, reason


def test_bohr_job_upgrade_record_requires_distinct_new_identifier(
    tmp_path: Path,
) -> None:
    record = _flat_record()
    record['original_job'] = {
        'bohr_id': SEED_ID,
        'machine': 'NVIDIA T4',
    }
    record['resubmitted_job'] = {
        'job_id': SEED_ID,
        'machine': 'NVIDIA A100',
        'gpu_count': 1,
    }
    _write_record(tmp_path, record)

    ok, reason = _check(tmp_path)

    assert not ok
    assert 'distinct' in reason


def test_bohr_job_upgrade_record_requires_a100_target(tmp_path: Path) -> None:
    record = _flat_record()
    record['resubmitted_job'] = {
        'job_id': 23059999,
        'machine': 'c16_m62_1 * NVIDIA T4',
    }
    _write_record(tmp_path, record)

    ok, reason = _check(tmp_path)

    assert not ok
    assert 'A100' in reason


def test_bohr_job_upgrade_record_requires_preserved_command(tmp_path: Path) -> None:
    record = _flat_record()
    record['preserved'] = {'image': IMAGE, 'command': 'echo changed > result.txt'}
    _write_record(tmp_path, record)

    ok, reason = _check(tmp_path)

    assert not ok
    assert 'command' in reason


def test_bohr_job_upgrade_record_is_registered_with_evaluator(tmp_path: Path) -> None:
    _write_record(tmp_path, _flat_record())
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(
        item for item in questions if item.id == 'BEC_upgrade_machine_006_20260715_v4'
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

    result = record.criteria_results['upgrade_execution']
    assert result.passed is True, result.reason


def test_bohr_job_upgrade_execution_requires_machine_query(tmp_path: Path) -> None:
    _write_record(tmp_path, _flat_record())

    ok, reason = check_bohr_job_upgrade_execution(
        tmp_path,
        filename='e6_upgrade.json',
        seed_id=SEED_ID,
        source_machine_pattern=r'(?i:T4)',
        target_machine_pattern=r'(?i:A100)',
        image=IMAGE,
        command=COMMAND,
        receipts=_execution_receipts(include_machine_query=False),
    )

    assert not ok
    assert 'GPU machines' in reason


def test_bohr_job_upgrade_execution_rejects_non_a100_submit(
    tmp_path: Path,
) -> None:
    _write_record(tmp_path, _flat_record())

    ok, reason = check_bohr_job_upgrade_execution(
        tmp_path,
        filename='e6_upgrade.json',
        seed_id=SEED_ID,
        source_machine_pattern=r'(?i:T4)',
        target_machine_pattern=r'(?i:A100)',
        image=IMAGE,
        command=COMMAND,
        receipts=_execution_receipts(target_machine='c16_m62_1 * NVIDIA T4'),
    )

    assert not ok
    assert 'A100' in reason


def test_bohr_job_upgrade_execution_matches_recorded_new_identifier(
    tmp_path: Path,
) -> None:
    record = _flat_record()
    target = record['resubmitted_job']
    assert isinstance(target, dict)
    target['job_id'] = 23058888
    _write_record(tmp_path, record)

    ok, reason = check_bohr_job_upgrade_execution(
        tmp_path,
        filename='e6_upgrade.json',
        seed_id=SEED_ID,
        source_machine_pattern=r'(?i:T4)',
        target_machine_pattern=r'(?i:A100)',
        image=IMAGE,
        command=COMMAND,
        receipts=_execution_receipts(),
    )

    assert not ok
    assert 'does not match CLI response' in reason
