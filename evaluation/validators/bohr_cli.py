"""Deterministic checks backed by process-level Bohr-CLI receipts."""

from __future__ import annotations

import json
import re
import shlex
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


def _load_json_object(root: Path, filename: str) -> tuple[dict, str | None]:
    path = _resolve_file(root, filename)
    if path is None:
        return {}, f'{filename}: file not found or ambiguous'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return {}, f'{filename}: invalid JSON ({exc})'
    if not isinstance(data, dict):
        return {}, f'{filename}: top-level JSON value must be an object'
    return data, None


def _artifact_job_ids(data: dict) -> set[int]:
    identifiers: set[int] = set()
    for node in _walk_json(data):
        if not isinstance(node, dict):
            continue
        for raw_key, raw_value in node.items():
            if _normalise_key(raw_key) not in {
                'id',
                'jobid',
                'bohrid',
                'bohrjobid',
                'platformjobid',
            }:
                continue
            identifier = _positive_int(raw_value)
            if identifier is not None:
                identifiers.add(identifier)
    return identifiers


def _artifact_poll_count(data: dict) -> int:
    counts: list[int] = []
    for node in _walk_json(data):
        if not isinstance(node, list):
            continue
        counts.append(
            sum(
                isinstance(item, dict)
                and bool(
                    {_normalise_key(key) for key in item}
                    & {'status', 'webstatus', 'state'}
                )
                for item in node
            )
        )
    return max(counts, default=0)


def _artifact_contains(data: dict, expected: str) -> bool:
    return any(node == expected for node in _walk_json(data))


def _shell_commands_equivalent(actual: str | None, expected: str) -> bool:
    if actual is None:
        return False
    try:
        return shlex.split(actual) == shlex.split(expected)
    except ValueError:
        return actual == expected


def _artifact_contains_command(data: dict, expected: str) -> bool:
    return any(
        isinstance(node, str) and _shell_commands_equivalent(node, expected)
        for node in _walk_json(data)
    )


def _artifact_unsuccessful_outcome(data: dict) -> str | None:
    unsuccessful = {
        'error',
        'failed',
        'failure',
        'pending',
        'running',
        'stopped',
        'timedout',
        'timeout',
    }
    for node in _walk_json(data):
        if not isinstance(node, dict):
            continue
        for raw_key, raw_value in node.items():
            if _normalise_key(raw_key) not in {
                'finalstatus',
                'outcome',
                'resultstatus',
            }:
                continue
            if isinstance(raw_value, str):
                normalised = _normalise_key(raw_value)
                if normalised in unsuccessful:
                    return raw_value
    return None


def _artifact_records_saved_log(data: dict, log_filename: str) -> bool:
    for node in _walk_json(data):
        if isinstance(node, str) and Path(node).name == log_filename:
            return True
        if not isinstance(node, dict):
            continue
        for raw_key, raw_value in node.items():
            normalised_key = _normalise_key(raw_key)
            records_saved_log = normalised_key == 'logsaved' or (
                'log' in normalised_key and 'saved' in normalised_key
            )
            if not records_saved_log:
                continue
            if raw_value is True:
                return True
            if isinstance(raw_value, str):
                normalised = raw_value.strip().lower()
                if Path(raw_value).name == log_filename or normalised in {
                    'saved',
                    'success',
                    'succeeded',
                    'true',
                }:
                    return True
    return False


def _successful_mutation(receipt: BohrCliReceiptRecord, operation: str) -> bool:
    return (
        receipt.operation == operation
        and receipt.ok
        and receipt.exit_code == 0
        and not receipt.help_requested
        and not receipt.dry_run
    )


def _successful_terminal_status(value: int | str | None) -> bool:
    if value == 2:
        return True
    if not isinstance(value, str):
        return False
    return value.strip().lower() in {
        '2',
        'completed',
        'finished',
        'succeeded',
        'success',
    }


def _successful_terminal_receipt(receipt: BohrCliReceiptRecord) -> bool:
    state = receipt.job_state
    return bool(
        state.exit_code == 0
        and state.end_time
        and (
            _successful_terminal_status(state.status)
            or _successful_terminal_status(state.web_status)
        )
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


def check_bohr_job_monitor_execution(
    workspace_dir: str | Path,
    *,
    filename: str,
    log_filename: str,
    image: str,
    machine_type: str,
    command: str,
    receipts: list[BohrCliReceiptRecord],
) -> tuple[bool, str]:
    """Validate a submit-poll-complete-log lifecycle from Bohr-CLI receipts."""
    root = Path(workspace_dir)
    artifact, artifact_error = _load_json_object(root, filename)
    if artifact_error:
        return False, artifact_error
    if _resolve_file(root, log_filename) is None:
        return False, f'{log_filename}: file not found or ambiguous'

    indexed_receipts = list(enumerate(receipts))
    submits = [
        (index, receipt)
        for index, receipt in indexed_receipts
        if _successful_mutation(receipt, 'job.submit')
    ]
    if len(submits) != 1:
        return False, f'expected 1 successful job submission, recorded {len(submits)}'

    submit_index, submit = submits[0]
    if not submit.captured_json or not submit.ids.bohr_job_ids:
        return False, 'job submission has no parsed Bohr job identifier'
    expected_request = {
        'image': (submit.request.image_address, image),
        'machine type': (submit.request.machine_type, machine_type),
    }
    mismatches = [
        label
        for label, (actual, expected) in expected_request.items()
        if actual != expected
    ]
    if mismatches:
        return False, f'job submission does not match expected {", ".join(mismatches)}'
    if not _shell_commands_equivalent(submit.request.command, command):
        return False, 'job submission does not match expected command'

    selected_describes: list[tuple[int, BohrCliReceiptRecord]] | None = None
    terminal_index: int | None = None
    for bohr_job_id in submit.ids.bohr_job_ids:
        describes = [
            (index, receipt)
            for index, receipt in indexed_receipts
            if index > submit_index
            and _successful_mutation(receipt, 'job.describe')
            and receipt.captured_json
            and bohr_job_id in receipt.request.bohr_job_ids
        ]
        last_describe = describes[-1] if describes else None
        first_terminal_describe = next(
            (
                (index, receipt)
                for index, receipt in describes
                if _successful_terminal_receipt(receipt)
            ),
            None,
        )
        if (
            len(describes) >= 2
            and last_describe is not None
            and _successful_terminal_receipt(last_describe[1])
            and first_terminal_describe is not None
        ):
            selected_describes = describes
            terminal_index = first_terminal_describe[0]
            break
    if selected_describes is None or terminal_index is None:
        return (
            False,
            'no submitted job has two successful polls and terminal '
            'successful-status/exitCode=0/endTime evidence',
        )

    platform_job_ids = set(submit.ids.platform_job_ids)
    for _index, receipt in selected_describes:
        platform_job_ids.update(receipt.ids.platform_job_ids)
    successful_logs = [
        receipt
        for index, receipt in indexed_receipts
        if index > terminal_index
        and _successful_mutation(receipt, 'job.log')
        and platform_job_ids.intersection(receipt.request.platform_job_ids)
    ]
    if not successful_logs:
        return False, 'no successful post-completion log retrieval for the same job'

    receipt_job_ids = set(submit.ids.job_ids)
    for _index, receipt in selected_describes:
        receipt_job_ids.update(receipt.ids.job_ids)
    if not receipt_job_ids.intersection(_artifact_job_ids(artifact)):
        return False, f'{filename}: recorded job identifier does not match CLI receipts'
    if _artifact_poll_count(artifact) < 2:
        return False, f'{filename}: monitoring record contains fewer than two polls'
    for label, expected in (('image', image), ('machine type', machine_type)):
        if not _artifact_contains(artifact, expected):
            return False, f'{filename}: monitoring record does not contain {label}'
    if not _artifact_contains_command(artifact, command):
        return False, f'{filename}: monitoring record does not contain command'
    unsuccessful_outcome = _artifact_unsuccessful_outcome(artifact)
    if unsuccessful_outcome is not None:
        return False, f'{filename}: monitoring record reports {unsuccessful_outcome}'
    if not _artifact_records_saved_log(artifact, log_filename):
        return False, f'{filename}: monitoring record does not record the saved log'

    return (
        True,
        'one submitted job was polled to exitCode=0 and its log was retrieved',
    )
