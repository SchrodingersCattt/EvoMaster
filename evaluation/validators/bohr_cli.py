"""Deterministic checks backed by process-level Bohr-CLI receipts."""

from __future__ import annotations

import json
import re
from pathlib import Path

from evaluation.core.evidence import BohrCliReceiptRecord
from evaluation.validators.json_file import check_bohr_parameter_sweep_record


def _normalise_key(value: object) -> str:
    return re.sub(r'[^a-z0-9]', '', str(value).lower())


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def _walk_json(value: object):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _resolve_file(root: Path, filename: str) -> Path | None:
    direct = root / filename
    if direct.is_file():
        return direct
    matches = sorted(path for path in root.rglob(filename) if path.is_file())
    return matches[0] if len(matches) == 1 else None


def _artifact_jobs(root: Path, filename: str) -> dict[int, int]:
    path = _resolve_file(root, filename)
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    jobs: dict[int, int] = {}
    for node in _walk_json(data):
        if not isinstance(node, dict):
            continue
        normalised = {_normalise_key(key): value for key, value in node.items()}
        if 'temperaturek' not in normalised or 'jobid' not in normalised:
            continue
        temperature = _positive_int(normalised['temperaturek'])
        job_id = _positive_int(normalised['jobid'])
        if temperature is not None and job_id is not None:
            jobs[temperature] = job_id
    return jobs


def _successful_mutation(receipt: BohrCliReceiptRecord, operation: str) -> bool:
    return (
        receipt.operation == operation
        and receipt.ok
        and receipt.exit_code == 0
        and not receipt.help_requested
        and not receipt.dry_run
    )


def check_bohr_parameter_sweep_execution(
    workspace_dir: str | Path,
    *,
    filename: str,
    receipts: list[BohrCliReceiptRecord],
) -> tuple[bool, str]:
    """Cross-check a grouped parameter sweep against actual Bohr-CLI executions."""
    artifact_ok, artifact_reason = check_bohr_parameter_sweep_record(
        workspace_dir,
        filename=filename,
    )
    if not artifact_ok:
        return False, artifact_reason

    creates = [
        receipt
        for receipt in receipts
        if _successful_mutation(receipt, 'job_group.create')
    ]
    submits = [
        receipt for receipt in receipts if _successful_mutation(receipt, 'job.submit')
    ]
    if len(creates) != 1:
        return (
            False,
            f'expected 1 successful job-group creation, recorded {len(creates)}',
        )
    if len(submits) != 8:
        return False, f'expected 8 successful job submissions, recorded {len(submits)}'

    create = creates[0]
    if not create.captured_json or not create.ids.group_ids:
        return False, 'job-group creation has no parsed JSON group identifier'

    submitted_group_ids: set[int] = set()
    by_temperature: dict[int, BohrCliReceiptRecord] = {}
    for receipt in submits:
        if not receipt.captured_json or not receipt.ids.job_ids:
            return False, 'a job submission has no parsed JSON job identifier'
        if len(receipt.request.job_group_ids) != 1:
            return False, 'each job submission must target one recorded job group'
        submitted_group_ids.update(receipt.request.job_group_ids)
        if len(receipt.request.temperatures_k) != 1:
            return False, 'each job submission must contain one recoverable temperature'
        temperature = receipt.request.temperatures_k[0]
        if temperature in by_temperature:
            return False, f'temperature {temperature} K was submitted more than once'
        by_temperature[temperature] = receipt

    if len(submitted_group_ids) != 1:
        return False, 'job submissions do not all target the same job group'
    submitted_group_id = next(iter(submitted_group_ids))
    if submitted_group_id not in create.ids.group_ids:
        return (
            False,
            'submitted job group was not returned by the recorded group creation',
        )

    artifact_jobs = _artifact_jobs(Path(workspace_dir), filename)
    expected_temperatures = set(range(300, 1001, 100))
    if set(by_temperature) != expected_temperatures:
        return False, 'execution receipts do not cover 300-1000 K by 100 K once'
    for temperature, artifact_job_id in artifact_jobs.items():
        receipt = by_temperature.get(temperature)
        if receipt is None or artifact_job_id not in receipt.ids.job_ids:
            return False, (
                f'{temperature} K artifact job ID does not match its CLI response'
            )

    return (
        True,
        'one group creation and eight grouped submissions match b3_jobs.json',
    )
