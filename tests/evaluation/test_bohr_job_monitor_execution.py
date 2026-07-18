"""Tests for receipt-backed Bohr job monitoring evaluation."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.core.evaluator import BinaryEvaluator
from evaluation.core.evidence import BohrCliReceiptRecord, EvidenceBundle
from evaluation.core.runner import flatten_banks, load_question_banks
from tests.evaluation.bohr_cli_receipt_helpers import make_receipt as _receipt

REPO_ROOT = Path(__file__).resolve().parents[2]
QUESTION_BANK_DIR = REPO_ROOT / 'evaluation' / 'question_bank'
IMAGE = 'registry.dp.tech/dptech/ubuntu:22.04-py3.10-cuda12.1'
MACHINE = 'c2_m4_cpu'
COMMAND = 'echo "hello from bohrium" | tee result.txt && sleep 60'
EQUIVALENT_COMMAND = "echo 'hello from bohrium' | tee result.txt && sleep 60"
BOHR_JOB_ID = 20402319
PLATFORM_JOB_ID = 23053839


def _execution_receipts(
    *,
    terminal: bool = True,
    final_status: int = 2,
    log_job_id: int = PLATFORM_JOB_ID,
) -> list[BohrCliReceiptRecord]:
    final_state = {'status': final_status, 'web_status': final_status}
    if terminal:
        final_state.update({'exit_code': 0, 'end_time': '2026-07-16 11:35:55'})
    return [
        _receipt(
            operation='job.submit',
            argv=['job', 'submit', '-i', 'job.json', '-o', 'json'],
            captured_json=True,
            request={
                'input_path': 'job.json',
                'image_address': IMAGE,
                'machine_type': MACHINE,
                'command': EQUIVALENT_COMMAND,
            },
            ids={
                'job_ids': [BOHR_JOB_ID, PLATFORM_JOB_ID],
                'bohr_job_ids': [BOHR_JOB_ID],
                'platform_job_ids': [PLATFORM_JOB_ID],
            },
        ),
        _receipt(
            operation='job.describe',
            argv=['job', 'describe', '-i', str(BOHR_JOB_ID), '-o', 'json'],
            captured_json=True,
            request={'bohr_job_ids': [BOHR_JOB_ID]},
            ids={
                'job_ids': [BOHR_JOB_ID, PLATFORM_JOB_ID],
                'bohr_job_ids': [BOHR_JOB_ID],
                'platform_job_ids': [PLATFORM_JOB_ID],
            },
            job_state={'status': 3, 'web_status': 9},
        ),
        _receipt(
            operation='job.describe',
            argv=['job', 'describe', '-i', str(BOHR_JOB_ID), '-o', 'json'],
            captured_json=True,
            request={'bohr_job_ids': [BOHR_JOB_ID]},
            ids={
                'job_ids': [BOHR_JOB_ID, PLATFORM_JOB_ID],
                'bohr_job_ids': [BOHR_JOB_ID],
                'platform_job_ids': [PLATFORM_JOB_ID],
            },
            job_state=final_state,
        ),
        _receipt(
            operation='job.log',
            argv=['job', 'log', '-j', str(log_job_id)],
            request={'platform_job_ids': [log_job_id]},
        ),
    ]


def _write_artifacts(
    tmp_path: Path,
    *,
    bohr_job_id: int = BOHR_JOB_ID,
    final_status: str | None = None,
) -> None:
    monitor = {
        'task': {
            'id': bohr_job_id,
            'platform_id': PLATFORM_JOB_ID,
        },
        'submission': {
            'image': IMAGE,
            'machine': MACHINE,
            'command': EQUIVALENT_COMMAND,
        },
        'observations': [
            {'time': '2026-07-16T03:34:45Z', 'status': 3, 'webStatus': 9},
            {'time': '2026-07-16T03:35:55Z', 'status': 2, 'webStatus': 2},
        ],
        'result': {
            'exitCode': 0,
            'log_saved': 'd6_job.log',
            'extra_diagnostic': 'allowed',
        },
    }
    if final_status is not None:
        monitor['final_status'] = final_status
    (tmp_path / 'd6_monitor.json').write_text(
        json.dumps(monitor, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    (tmp_path / 'd6_job.log').write_text('\n', encoding='utf-8')


def _evaluate(tmp_path: Path, receipts: list[BohrCliReceiptRecord]):
    questions = flatten_banks(load_question_banks(QUESTION_BANK_DIR))
    question = next(
        item for item in questions if item.id == 'BWO_monitor_D6_20260715_v6'
    )
    return BinaryEvaluator().evaluate(
        question=question,
        answer='done',
        evidence=EvidenceBundle(
            task_id=question.id,
            workspace_dir=str(tmp_path),
            total_steps=8,
            bohr_cli_receipts=receipts,
        ),
    )


def test_monitor_execution_accepts_receipt_backed_lifecycle_and_flexible_record(
    tmp_path: Path,
) -> None:
    _write_artifacts(tmp_path)

    record = _evaluate(tmp_path, _execution_receipts())

    result = record.criteria_results['monitor_execution']
    assert result.passed is True, result.reason


def test_monitor_execution_accepts_terminal_recheck_after_log(
    tmp_path: Path,
) -> None:
    _write_artifacts(tmp_path)
    receipts = _execution_receipts()
    receipts.append(receipts[2].model_copy(deep=True))

    record = _evaluate(tmp_path, receipts)

    result = record.criteria_results['monitor_execution']
    assert result.passed is True, result.reason


def test_monitor_execution_accepts_semantic_saved_log_boolean(
    tmp_path: Path,
) -> None:
    _write_artifacts(tmp_path)
    artifact_path = tmp_path / 'd6_monitor.json'
    artifact = json.loads(artifact_path.read_text(encoding='utf-8'))
    artifact['result'].pop('log_saved')
    artifact['result']['saved_to_d6_job_log'] = True
    artifact_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )

    record = _evaluate(tmp_path, _execution_receipts())

    result = record.criteria_results['monitor_execution']
    assert result.passed is True, result.reason


def test_monitor_execution_rejects_status_without_terminal_evidence(
    tmp_path: Path,
) -> None:
    _write_artifacts(tmp_path)

    record = _evaluate(tmp_path, _execution_receipts(terminal=False))

    result = record.criteria_results['monitor_execution']
    assert result.passed is False
    assert 'terminal' in result.reason


def test_monitor_execution_rejects_nonterminal_status_with_exit_metadata(
    tmp_path: Path,
) -> None:
    _write_artifacts(tmp_path)

    record = _evaluate(tmp_path, _execution_receipts(final_status=1))

    result = record.criteria_results['monitor_execution']
    assert result.passed is False
    assert 'terminal' in result.reason


def test_monitor_execution_rejects_artifact_reporting_timeout(
    tmp_path: Path,
) -> None:
    _write_artifacts(tmp_path, final_status='timeout')

    record = _evaluate(tmp_path, _execution_receipts())

    result = record.criteria_results['monitor_execution']
    assert result.passed is False
    assert 'timeout' in result.reason


def test_monitor_execution_rejects_log_retrieval_for_another_job(
    tmp_path: Path,
) -> None:
    _write_artifacts(tmp_path)

    record = _evaluate(tmp_path, _execution_receipts(log_job_id=99999999))

    result = record.criteria_results['monitor_execution']
    assert result.passed is False
    assert 'same job' in result.reason


def test_monitor_execution_rejects_unrelated_artifact_identifier(
    tmp_path: Path,
) -> None:
    _write_artifacts(tmp_path, bohr_job_id=99999999)

    record = _evaluate(tmp_path, _execution_receipts())

    result = record.criteria_results['monitor_execution']
    assert result.passed is False
    assert 'identifier' in result.reason
