"""Process-level tests for the transparent Bohr-CLI audit launcher."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from evaluation.scripts.devshell.bohr_cli_audit import (
    RECEIPT_PATH_ENV,
    prepare_bohr_cli_audit_environment,
)


def _fake_bohr(tmp_path: Path) -> Path:
    bin_dir = tmp_path / 'real_bin'
    bin_dir.mkdir()
    script = bin_dir / 'bohr'
    script.write_text(
        """#!/usr/bin/env python3
import json
import sys

args = sys.argv[1:]
if args[:2] == ['job_group', 'create']:
    sys.stdout.buffer.write(b'{"id":7247676,"jobGroupId":16377774}')
elif args[:2] == ['job', 'submit']:
    sys.stdout.buffer.write(b'{"bohrJobId":20402319,"jobId":23053718}')
elif args[:2] == ['job', 'describe']:
    sys.stdout.buffer.write(b'{"ok":true,"data":{"id":23053718,"bohrId":20402319,"webStatus":2,"status":2,"endTime":"2026-07-16 11:35:55","exitCode":0},"secret":"must-not-be-recorded"}')
elif args[:2] == ['job', 'log']:
    sys.stdout.buffer.write(b'{"ok":true,"data":{"log":"signed-secret-output"}}')
else:
    data = sys.stdin.buffer.read()
    sys.stdout.buffer.write(b'OUT:' + data)
    sys.stderr.buffer.write(b'ERR')
    raise SystemExit(7)
""",
        encoding='utf-8',
    )
    script.chmod(0o755)
    return bin_dir


def _environment(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    real_bin = _fake_bohr(tmp_path)
    receipt_path = tmp_path / 'logs' / 'bohr_cli_receipts.jsonl'
    shim_dir = tmp_path / 'shim'
    base_env = os.environ.copy()
    base_env['PATH'] = f'{real_bin}{os.pathsep}{base_env.get("PATH", "")}'
    env, enabled = prepare_bohr_cli_audit_environment(
        base_env,
        receipt_path=receipt_path,
        shim_dir=shim_dir,
    )
    assert enabled
    return env, shim_dir / 'bohr', receipt_path


def _receipts(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def test_transparent_mode_preserves_stdin_stdout_stderr_and_exit_code(
    tmp_path: Path,
) -> None:
    env, bohr, receipt_path = _environment(tmp_path)

    result = subprocess.run(
        [str(bohr), 'version'],
        input=b'hello',
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 7
    assert result.stdout == b'OUT:hello'
    assert result.stderr == b'ERR'
    receipt = _receipts(receipt_path)[0]
    assert receipt['exit_code'] == 7
    assert receipt['captured_json'] is False
    assert 'stdout' not in receipt
    assert 'stderr' not in receipt


def test_operation_detection_does_not_treat_flag_value_as_command_noun(
    tmp_path: Path,
) -> None:
    env, bohr, receipt_path = _environment(tmp_path)

    subprocess.run(
        [str(bohr), 'machine', 'list', '-c', 'cpu', '-s', 'job', '-o', 'json'],
        capture_output=True,
        env=env,
        check=False,
    )

    assert _receipts(receipt_path)[0]['operation'] == 'machine.list'


def test_json_submit_is_replayed_and_snapshots_safe_input_fields(
    tmp_path: Path,
) -> None:
    env, bohr, receipt_path = _environment(tmp_path)
    input_path = tmp_path / 'job.json'
    input_path.write_text(
        json.dumps(
            {
                'projectId': 14844,
                'jobGroupId': 7247676,
                'jobName': 'sweep-300K',
                'jobConfig': {
                    'imageAddress': 'registry.example/image:latest',
                    'machineType': 'c2_m4_cpu',
                    'command': 'echo "T=300" > result.txt',
                },
                'accessKey': 'must-not-be-recorded',
            }
        ),
        encoding='utf-8',
    )

    result = subprocess.run(
        [
            str(bohr),
            'job',
            'submit',
            '-i',
            str(input_path),
            '-o',
            'json',
            '--ak',
            'evo-secret-value-123456789',
        ],
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == b'{"bohrJobId":20402319,"jobId":23053718}'
    receipt_text = receipt_path.read_text(encoding='utf-8')
    assert 'evo-secret-value-123456789' not in receipt_text
    assert 'must-not-be-recorded' not in receipt_text
    receipt = _receipts(receipt_path)[0]
    assert receipt['operation'] == 'job.submit'
    assert receipt['captured_json'] is True
    assert receipt['ids'] == {
        'job_ids': [20402319, 23053718],
        'bohr_job_ids': [20402319],
        'platform_job_ids': [23053718],
        'group_ids': [],
    }
    assert receipt['request']['job_group_ids'] == [7247676]
    assert receipt['request']['temperatures_k'] == [300]
    assert receipt['request']['command'] == 'echo "T=300" > result.txt'
    assert receipt['argv'][-1] == '<redacted>'


def test_job_describe_captures_allow_listed_state_without_changing_output(
    tmp_path: Path,
) -> None:
    env, bohr, receipt_path = _environment(tmp_path)

    result = subprocess.run(
        [str(bohr), 'job', 'describe', '-i', '20402319'],
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert b'must-not-be-recorded' in result.stdout
    receipt_text = receipt_path.read_text(encoding='utf-8')
    assert 'must-not-be-recorded' not in receipt_text
    receipt = _receipts(receipt_path)[0]
    assert receipt['operation'] == 'job.describe'
    assert receipt['captured_json'] is True
    assert receipt['request']['bohr_job_ids'] == [20402319]
    assert receipt['ids']['bohr_job_ids'] == [20402319]
    assert receipt['ids']['platform_job_ids'] == [23053718]
    assert receipt['job_state'] == {
        'status': 2,
        'web_status': 2,
        'exit_code': 0,
        'end_time': '2026-07-16 11:35:55',
    }


def test_job_log_records_target_and_success_without_capturing_output(
    tmp_path: Path,
) -> None:
    env, bohr, receipt_path = _environment(tmp_path)

    result = subprocess.run(
        [str(bohr), 'job', 'log', '-j', '23053718'],
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert b'signed-secret-output' in result.stdout
    receipt_text = receipt_path.read_text(encoding='utf-8')
    assert 'signed-secret-output' not in receipt_text
    receipt = _receipts(receipt_path)[0]
    assert receipt['operation'] == 'job.log'
    assert receipt['captured_json'] is False
    assert receipt['request']['platform_job_ids'] == [23053718]


def test_json_group_create_captures_both_group_identifier_names(
    tmp_path: Path,
) -> None:
    env, bohr, receipt_path = _environment(tmp_path)

    result = subprocess.run(
        [str(bohr), 'job_group', 'create', '-n', 'sweep', '-o', 'json'],
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == b'{"id":7247676,"jobGroupId":16377774}'
    receipt = _receipts(receipt_path)[0]
    assert receipt['ids']['group_ids'] == [7247676, 16377774]
    assert receipt['request']['job_name'] == 'sweep'


def test_positional_login_secret_is_redacted(tmp_path: Path) -> None:
    env, bohr, receipt_path = _environment(tmp_path)

    subprocess.run(
        [str(bohr), 'login', 'evo-positional-secret-123456789'],
        input=b'',
        capture_output=True,
        env=env,
        check=False,
    )

    receipt_text = receipt_path.read_text(encoding='utf-8')
    assert 'evo-positional-secret-123456789' not in receipt_text
    assert _receipts(receipt_path)[0]['argv'] == ['login', '<redacted>']


def test_unwritable_receipt_path_does_not_change_real_command_result(
    tmp_path: Path,
) -> None:
    env, bohr, _receipt_path = _environment(tmp_path)
    blocking_file = tmp_path / 'not_a_directory'
    blocking_file.write_text('x', encoding='utf-8')
    env[RECEIPT_PATH_ENV] = str(blocking_file / 'receipt.jsonl')

    result = subprocess.run(
        [str(bohr), 'job_group', 'create', '-o', 'json'],
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == b'{"id":7247676,"jobGroupId":16377774}'
