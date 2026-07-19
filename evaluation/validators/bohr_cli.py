"""Deterministic checks backed by process-level Bohr-CLI receipts."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

from evaluation.core.evidence import BohrCliReceiptRecord
from evaluation.validators._common import collect_positive_ids as _artifact_job_ids
from evaluation.validators._common import normalise_key as _normalise_key
from evaluation.validators._common import positive_int as _positive_int
from evaluation.validators._common import resolve_file as _resolve_file
from evaluation.validators._common import walk_json as _walk_json
from evaluation.validators.json_file import (
    check_bohr_job_stop_record_data,
    check_bohr_job_upgrade_record_data,
    check_bohr_parameter_sweep_record_data,
)


def _artifact_temperature_jobs(data: dict) -> dict[int, int]:
    jobs: dict[int, int] = {}
    for node in _walk_json(data):
        if not isinstance(node, dict):
            continue
        normalised = {_normalise_key(key): value for key, value in node.items()}
        if "temperaturek" not in normalised or "jobid" not in normalised:
            continue
        temperature = _positive_int(normalised["temperaturek"])
        job_id = _positive_int(normalised["jobid"])
        if temperature is not None and job_id is not None:
            jobs[temperature] = job_id
    return jobs


def _load_json_object(root: Path, filename: str) -> tuple[dict, str | None]:
    path = _resolve_file(root, filename)
    if path is None:
        return {}, f"{filename} not found in workspace"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, f"{filename}: invalid JSON ({exc})"
    if not isinstance(data, dict):
        return {}, f"{filename}: top-level JSON value must be an object"
    return data, None


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
                    & {"status", "webstatus", "state"}
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
        "error",
        "failed",
        "failure",
        "pending",
        "running",
        "stopped",
        "timedout",
        "timeout",
    }
    for node in _walk_json(data):
        if not isinstance(node, dict):
            continue
        for raw_key, raw_value in node.items():
            if _normalise_key(raw_key) not in {
                "finalstatus",
                "outcome",
                "resultstatus",
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
            records_saved_log = normalised_key == "logsaved" or (
                "log" in normalised_key and "saved" in normalised_key
            )
            if not records_saved_log:
                continue
            if raw_value is True:
                return True
            if isinstance(raw_value, str):
                normalised = raw_value.strip().lower()
                if Path(raw_value).name == log_filename or normalised in {
                    "saved",
                    "success",
                    "succeeded",
                    "true",
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


def check_bohr_cli_operation_invoked(
    *,
    receipts: list[BohrCliReceiptRecord],
    operations: list[str],
    min_matches: int = 1,
    require_ok: bool = True,
    max_matches: int | None = None,
    argv_regex: str | None = None,
) -> tuple[bool, str]:
    """Ground a step on execution receipts rather than command-string regex.

    Counts Bohr-CLI invocations whose launcher-parsed ``operation`` is in the
    allow-list. Unlike ``tool_args_regex`` this is agnostic to flags, quoting,
    pipes, env prefixes, ``+shortcut`` forms and command synonyms, and (with the
    default ``require_ok``) proves the command actually ran and exited cleanly.
    Help and dry-run invocations never count.

    Matching is exact or prefix: an entry ``noun`` also matches ``noun.<x>``.
    This lets a bare noun (e.g. ``mentor``) cover positional-argument commands
    whose parsed operation embeds the argument (``mentor.<question text>``),
    while a fully qualified entry (``pdf.parse``) stays exact.

    ``max_matches`` bounds the count from above for discipline checks
    ("at most N attempts", "exactly one call"). ``argv_regex`` further narrows
    matches by searching the space-joined **redacted** argv — flags are
    preserved verbatim while secret values and auth positionals appear as
    ``<redacted>``, so patterns must key on flags (e.g. ``--device``), never on
    values. Whitespace inside a single argv token is replaced with ``_`` before
    joining, so flag-shaped text embedded in a free-text value cannot straddle
    token boundaries and spuriously match a flag-keyed pattern.
    """
    wanted = {
        str(operation).strip() for operation in operations if str(operation).strip()
    }
    if not wanted:
        return False, "bohr_cli_operation_invoked: no operations configured"
    argv_pattern: re.Pattern[str] | None = None
    if argv_regex is not None:
        try:
            argv_pattern = re.compile(argv_regex)
        except re.error as exc:
            return False, f"bohr_cli_operation_invoked: invalid argv_regex: {exc}"

    def _matches(operation: str) -> bool:
        return any(
            operation == entry or operation.startswith(f"{entry}.") for entry in wanted
        )

    matched = 0
    observed: set[str] = set()
    for receipt in receipts:
        if not _matches(receipt.operation):
            continue
        if receipt.help_requested or receipt.dry_run:
            continue
        if require_ok and not (receipt.ok and receipt.exit_code == 0):
            continue
        if argv_pattern is not None and not argv_pattern.search(
            " ".join(re.sub(r"\s+", "_", token) for token in receipt.argv)
        ):
            continue
        matched += 1
        observed.add(receipt.operation)
    ok_note = "ok" if require_ok else "any-exit"
    expected = (
        f">={min_matches}" if max_matches is None else f"[{min_matches},{max_matches}]"
    )
    passed = matched >= min_matches and (max_matches is None or matched <= max_matches)
    return (
        passed,
        f"operation receipts matched={matched} ({ok_note}) "
        f"for {sorted(wanted)}; observed={sorted(observed)}; expected={expected}",
    )


def _successful_terminal_status(value: int | str | None) -> bool:
    if value == 2:
        return True
    if not isinstance(value, str):
        return False
    return value.strip().lower() in {
        "2",
        "completed",
        "finished",
        "succeeded",
        "success",
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


def _receipt_argv_ids(receipt: BohrCliReceiptRecord) -> set[int]:
    identifiers: set[int] = set()
    for argument in receipt.argv:
        match = re.search(r"(?:^|=)([1-9]\d*)$", argument)
        if match:
            identifiers.add(int(match.group(1)))
    return identifiers


def _receipt_flag_value(receipt: BohrCliReceiptRecord, names: set[str]) -> str | None:
    for index, argument in enumerate(receipt.argv):
        key, separator, value = argument.partition("=")
        if key in names and separator:
            return value
        if argument in names and index + 1 < len(receipt.argv):
            return receipt.argv[index + 1]
    return None


def _is_job_gpu_machine_query(receipt: BohrCliReceiptRecord) -> bool:
    if not _successful_mutation(receipt, "machine.list"):
        return False
    choose_type = _receipt_flag_value(
        receipt,
        {"-c", "--chooseType", "--choose-type", "--choose_type"},
    )
    scene = _receipt_flag_value(receipt, {"-s", "--scene"})
    return (
        isinstance(choose_type, str)
        and choose_type.casefold() == "gpu"
        and isinstance(scene, str)
        and scene.casefold() == "job"
    )


def check_bohr_job_upgrade_execution(
    workspace_dir: str | Path,
    *,
    filename: str,
    seed_id: int,
    source_machine_pattern: str,
    target_machine_pattern: str,
    image: str,
    command: str,
    receipts: list[BohrCliReceiptRecord],
) -> tuple[bool, str]:
    """Validate one source-inspect, machine-query, A100-submit lifecycle."""
    artifact, artifact_error = _load_json_object(Path(workspace_dir), filename)
    if artifact_error:
        return False, artifact_error
    record_ok, record_reason = check_bohr_job_upgrade_record_data(
        filename,
        artifact,
        seed_id=seed_id,
        source_machine_pattern=source_machine_pattern,
        target_machine_pattern=target_machine_pattern,
        image=image,
        command=command,
    )
    if not record_ok:
        return False, record_reason

    indexed_receipts = list(enumerate(receipts))
    submits = [
        (index, receipt)
        for index, receipt in indexed_receipts
        if _successful_mutation(receipt, "job.submit")
    ]
    if len(submits) != 1:
        return False, f"expected 1 successful job submission, recorded {len(submits)}"

    submit_index, submit = submits[0]
    if not submit.captured_json or not submit.ids.job_ids:
        return False, "job submission has no parsed job identifier"
    if submit.request.image_address != image:
        return False, "job submission does not preserve the source image"
    if not _shell_commands_equivalent(submit.request.command, command):
        return False, "job submission does not preserve the source command"
    machine_type = submit.request.machine_type
    if not isinstance(machine_type, str) or not re.search(
        target_machine_pattern, machine_type
    ):
        return False, "job submission does not use an A100 machine type"

    source_queries = [
        receipt
        for index, receipt in indexed_receipts
        if index < submit_index
        and _successful_mutation(receipt, "job.describe")
        and seed_id in receipt.request.bohr_job_ids
    ]
    if not source_queries:
        return False, "source job was not successfully queried before submission"

    machine_queries = [
        receipt
        for index, receipt in indexed_receipts
        if index < submit_index and _is_job_gpu_machine_query(receipt)
    ]
    if not machine_queries:
        return False, "job-scene GPU machines were not queried before submission"

    artifact_ids = _artifact_job_ids(artifact)
    submitted_ids = set(submit.ids.job_ids)
    if seed_id not in artifact_ids:
        return False, f"{filename}: source job identifier does not match CLI request"
    if not submitted_ids.intersection(artifact_ids):
        return (
            False,
            f"{filename}: resubmitted job identifier does not match CLI response",
        )
    if seed_id in submitted_ids:
        return False, "job submission response reuses the source job identifier"

    return (
        True,
        "source job and GPU machines were queried before one matching A100 "
        "resubmission, and the CLI identifiers were recorded",
    )


def check_bohr_job_stop_execution(
    workspace_dir: str | Path,
    *,
    filename: str,
    image: str,
    machine_type: str,
    command: str,
    job_name_prefix: str,
    receipts: list[BohrCliReceiptRecord],
) -> tuple[bool, str]:
    """Validate one isolated submit-poll-stop-poll lifecycle and its record."""
    artifact, artifact_error = _load_json_object(Path(workspace_dir), filename)
    if artifact_error:
        return False, artifact_error
    record_ok, record_reason = check_bohr_job_stop_record_data(
        filename,
        artifact,
        image=image,
        machine_type=machine_type,
        command=command,
        job_name_prefix=job_name_prefix,
    )
    if not record_ok:
        return False, record_reason

    indexed_receipts = list(enumerate(receipts))
    submits = [
        (index, receipt)
        for index, receipt in indexed_receipts
        if _successful_mutation(receipt, "job.submit")
    ]
    if len(submits) != 1:
        return False, f"expected 1 successful job submission, recorded {len(submits)}"

    submit_index, submit = submits[0]
    if not submit.captured_json or not submit.ids.bohr_job_ids:
        return False, "job submission has no parsed Bohr job identifier"
    if not submit.ids.platform_job_ids:
        return False, "job submission has no parsed platform job identifier"
    if submit.request.image_address != image:
        return False, "job submission does not match expected image"
    if submit.request.machine_type != machine_type:
        return False, "job submission does not match expected machine type"
    if not _shell_commands_equivalent(submit.request.command, command):
        return False, "job submission does not match expected command"
    if not (submit.request.job_name or "").startswith(job_name_prefix):
        return False, "job submission does not use the expected unique name prefix"

    bohr_job_ids = set(submit.ids.bohr_job_ids)
    describes = [
        (index, receipt)
        for index, receipt in indexed_receipts
        if index > submit_index
        and _successful_mutation(receipt, "job.describe")
        and receipt.captured_json
        and bohr_job_ids.intersection(receipt.request.bohr_job_ids)
    ]
    if len(describes) < 2:
        return False, "submitted job has fewer than two successful status queries"

    submitted_job_ids = set(submit.ids.job_ids)
    stop_operations = {"job.terminate", "job.kill", "job.cancel", "job.+cancel"}
    successful_controls: list[tuple[int, BohrCliReceiptRecord]] = []
    for index, receipt in indexed_receipts:
        if index <= submit_index or receipt.operation not in stop_operations:
            continue
        if not _successful_mutation(receipt, receipt.operation):
            continue
        target_ids = (
            set(receipt.request.platform_job_ids)
            | set(receipt.request.bohr_job_ids)
            | _receipt_argv_ids(receipt)
        )
        if submitted_job_ids.intersection(target_ids):
            successful_controls.append((index, receipt))

    lifecycle_control = next(
        (
            (index, receipt)
            for index, receipt in successful_controls
            if any(describe_index < index for describe_index, _receipt in describes)
            and any(describe_index > index for describe_index, _receipt in describes)
        ),
        None,
    )
    if lifecycle_control is None:
        return False, "no successful stop of the submitted job between status queries"

    if not submitted_job_ids.intersection(_artifact_job_ids(artifact)):
        return False, f"{filename}: recorded job identifier does not match CLI receipts"

    return (
        True,
        "one self-submitted job was queried, stopped, queried again, and recorded",
    )


def check_bohr_parameter_sweep_execution(
    workspace_dir: str | Path,
    *,
    filename: str,
    receipts: list[BohrCliReceiptRecord],
) -> tuple[bool, str]:
    """Cross-check a grouped parameter sweep against actual Bohr-CLI executions."""
    artifact, artifact_error = _load_json_object(Path(workspace_dir), filename)
    if artifact_error:
        return False, artifact_error
    artifact_ok, artifact_reason = check_bohr_parameter_sweep_record_data(
        filename, artifact
    )
    if not artifact_ok:
        return False, artifact_reason

    creates = [
        receipt
        for receipt in receipts
        if _successful_mutation(receipt, "job_group.create")
    ]
    submits = [
        receipt for receipt in receipts if _successful_mutation(receipt, "job.submit")
    ]
    if len(creates) != 1:
        return (
            False,
            f"expected 1 successful job-group creation, recorded {len(creates)}",
        )
    if len(submits) != 8:
        return False, f"expected 8 successful job submissions, recorded {len(submits)}"

    create = creates[0]
    if not create.captured_json or not create.ids.group_ids:
        return False, "job-group creation has no parsed JSON group identifier"

    submitted_group_ids: set[int] = set()
    by_temperature: dict[int, BohrCliReceiptRecord] = {}
    for receipt in submits:
        if not receipt.captured_json or not receipt.ids.job_ids:
            return False, "a job submission has no parsed JSON job identifier"
        if len(receipt.request.job_group_ids) != 1:
            return False, "each job submission must target one recorded job group"
        submitted_group_ids.update(receipt.request.job_group_ids)
        if len(receipt.request.temperatures_k) != 1:
            return False, "each job submission must contain one recoverable temperature"
        temperature = receipt.request.temperatures_k[0]
        if temperature in by_temperature:
            return False, f"temperature {temperature} K was submitted more than once"
        by_temperature[temperature] = receipt

    if len(submitted_group_ids) != 1:
        return False, "job submissions do not all target the same job group"
    submitted_group_id = next(iter(submitted_group_ids))
    if submitted_group_id not in create.ids.group_ids:
        return (
            False,
            "submitted job group was not returned by the recorded group creation",
        )

    artifact_jobs = _artifact_temperature_jobs(artifact)
    expected_temperatures = set(range(300, 1001, 100))
    if set(by_temperature) != expected_temperatures:
        return False, "execution receipts do not cover 300-1000 K by 100 K once"
    for temperature, artifact_job_id in artifact_jobs.items():
        receipt = by_temperature.get(temperature)
        if receipt is None or artifact_job_id not in receipt.ids.job_ids:
            return False, (
                f"{temperature} K artifact job ID does not match its CLI response"
            )

    return (
        True,
        "one group creation and eight grouped submissions match b3_jobs.json",
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
        return False, f"{log_filename} not found in workspace"

    indexed_receipts = list(enumerate(receipts))
    submits = [
        (index, receipt)
        for index, receipt in indexed_receipts
        if _successful_mutation(receipt, "job.submit")
    ]
    if len(submits) != 1:
        return False, f"expected 1 successful job submission, recorded {len(submits)}"

    submit_index, submit = submits[0]
    if not submit.captured_json or not submit.ids.bohr_job_ids:
        return False, "job submission has no parsed Bohr job identifier"
    expected_request = {
        "image": (submit.request.image_address, image),
        "machine type": (submit.request.machine_type, machine_type),
    }
    mismatches = [
        label
        for label, (actual, expected) in expected_request.items()
        if actual != expected
    ]
    if mismatches:
        return False, f'job submission does not match expected {", ".join(mismatches)}'
    if not _shell_commands_equivalent(submit.request.command, command):
        return False, "job submission does not match expected command"

    selected_describes: list[tuple[int, BohrCliReceiptRecord]] | None = None
    terminal_index: int | None = None
    for bohr_job_id in submit.ids.bohr_job_ids:
        describes = [
            (index, receipt)
            for index, receipt in indexed_receipts
            if index > submit_index
            and _successful_mutation(receipt, "job.describe")
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
            "no submitted job has two successful polls and terminal "
            "successful-status/exitCode=0/endTime evidence",
        )

    platform_job_ids = set(submit.ids.platform_job_ids)
    for _index, receipt in selected_describes:
        platform_job_ids.update(receipt.ids.platform_job_ids)
    successful_logs = [
        receipt
        for index, receipt in indexed_receipts
        if index > terminal_index
        and _successful_mutation(receipt, "job.log")
        and platform_job_ids.intersection(receipt.request.platform_job_ids)
    ]
    if not successful_logs:
        return False, "no successful post-completion log retrieval for the same job"

    receipt_job_ids = set(submit.ids.job_ids)
    for _index, receipt in selected_describes:
        receipt_job_ids.update(receipt.ids.job_ids)
    if not receipt_job_ids.intersection(_artifact_job_ids(artifact)):
        return False, f"{filename}: recorded job identifier does not match CLI receipts"
    if _artifact_poll_count(artifact) < 2:
        return False, f"{filename}: monitoring record contains fewer than two polls"
    for label, expected in (("image", image), ("machine type", machine_type)):
        if not _artifact_contains(artifact, expected):
            return False, f"{filename}: monitoring record does not contain {label}"
    if not _artifact_contains_command(artifact, command):
        return False, f"{filename}: monitoring record does not contain command"
    unsuccessful_outcome = _artifact_unsuccessful_outcome(artifact)
    if unsuccessful_outcome is not None:
        return False, f"{filename}: monitoring record reports {unsuccessful_outcome}"
    if not _artifact_records_saved_log(artifact, log_filename):
        return False, f"{filename}: monitoring record does not record the saved log"

    return (
        True,
        "one submitted job was polled to exitCode=0 and its log was retrieved",
    )
