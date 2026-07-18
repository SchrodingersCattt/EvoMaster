"""Transparent ``bohr`` launcher that emits narrow evaluation receipts.

The launcher is injected ahead of the real Bohr-CLI binary only for evaluation
questions tagged ``bohr-cli``.  Most commands inherit the caller's standard
streams unchanged.  Short mutating commands with an explicit JSON output mode
and job-description queries are captured, replayed byte-for-byte, and parsed
for allow-listed identifiers and lifecycle fields.

No environment values or full command output are written to the receipt file.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REAL_BIN_ENV = "BOHR_EVAL_REAL_BIN"
RECEIPT_PATH_ENV = "BOHR_EVAL_RECEIPT_PATH"
RECEIPT_SCHEMA = "bohr_cli_receipt_v1"

_JSON_MUTATION_OPERATIONS = frozenset({"job.submit", "job_group.create"})
_JOB_DESCRIPTION_OPERATION = "job.describe"
_COMMAND_NOUNS = frozenset(
    {
        "api",
        "auth",
        "billing",
        "chat",
        "dataset",
        "doctor",
        "file",
        "image",
        "job",
        "job_group",
        "kb",
        "lkm",
        "machine",
        "mentor",
        "node",
        "paper",
        "pdf",
        "project",
        "sandbox",
        "scholar",
        "search",
        "tools",
        "wiki",
    }
)
_GLOBAL_OPTIONS_WITH_VALUES = frozenset(
    {"-o", "--format", "--output", "--output-format", "-q", "--query"}
)
# Commands whose first positional token is free-form user text (a question or
# query), not a subcommand. Their operation is the bare noun; the argument must
# never be embedded in the operation field of a receipt.
_POSITIONAL_ARGUMENT_NOUNS = frozenset({"chat", "mentor", "search"})
_SECRET_FLAGS = frozenset(
    {
        "--access-key",
        "--access_key",
        "--ak",
        "--api-key",
        "--api_key",
        "--authorization",
        "--password",
        "--secret",
        "--token",
    }
)
_INTEGER_RE = re.compile(r"^[1-9][0-9]*$")
_TEMPERATURE_RE = re.compile(r"(?:^|[^A-Za-z0-9_])T\s*=\s*([0-9]+)\b")
_POSSIBLE_AK_RE = re.compile(r"\bevo-[A-Za-z0-9_-]{12,}\b")


def prepare_bohr_cli_audit_environment(
    base_env: dict[str, str],
    *,
    receipt_path: Path,
    shim_dir: Path,
) -> tuple[dict[str, str], bool]:
    """Return a per-task environment with an audited ``bohr`` command.

    The real executable is resolved before the shim directory is prepended, so
    the launcher cannot recursively invoke itself.  Missing Bohr-CLI leaves the
    environment untouched; the task can then surface the normal command-not-found
    failure and scoring will report that no execution receipt was produced.
    """
    env = base_env.copy()
    original_path = base_env.get("PATH", "")
    real_bin = shutil.which("bohr", path=original_path)
    if not real_bin:
        return env, False

    shim_dir.mkdir(parents=True, exist_ok=True)
    launcher = shim_dir / "bohr"
    script_path = Path(__file__).resolve()
    launcher.write_text(
        "#!/bin/sh\n"
        f"exec {shlex.quote(sys.executable)} {shlex.quote(str(script_path))} \"$@\"\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)

    env[REAL_BIN_ENV] = real_bin
    env[RECEIPT_PATH_ENV] = str(receipt_path)
    env["PATH"] = f"{shim_dir}{os.pathsep}{original_path}"
    return env, True


def _normalise_token(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _operation(argv: list[str]) -> str:
    for index, raw_noun in enumerate(argv):
        noun = _normalise_token(raw_noun)
        if noun not in _COMMAND_NOUNS:
            continue
        if noun in _POSITIONAL_ARGUMENT_NOUNS:
            return noun
        cursor = index + 1
        while cursor < len(argv):
            raw_verb = argv[cursor]
            key, separator, _value = raw_verb.partition("=")
            if raw_verb.startswith("-"):
                cursor += 1
                if key in _GLOBAL_OPTIONS_WITH_VALUES and not separator:
                    cursor += 1
                continue
            return f"{noun}.{_normalise_token(raw_verb)}"
        # Noun with no subcommand token (e.g. `bohr doctor`, `bohr pdf --help`):
        # record the bare noun rather than falling through to "unknown".
        return noun
    return "unknown"


def _flag_value(argv: list[str], names: set[str]) -> str | None:
    for index, arg in enumerate(argv):
        key, separator, value = arg.partition("=")
        if key in names and separator:
            return value
        if arg in names and index + 1 < len(argv):
            return argv[index + 1]
    return None


def _explicit_json_output(argv: list[str]) -> bool:
    value = _flag_value(argv, {"-o", "--output", "--output-format", "--format"})
    return isinstance(value, str) and value.strip().lower() == "json"


def _redact_text(value: str) -> str:
    return _POSSIBLE_AK_RE.sub("<redacted>", value)


def _redacted_argv(argv: list[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    auth_index = next(
        (
            index
            for index, arg in enumerate(argv[:3])
            if _normalise_token(arg) in {"login", "auth"}
        ),
        None,
    )
    for index, arg in enumerate(argv):
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        key, separator, _value = arg.partition("=")
        if key.lower() in _SECRET_FLAGS:
            if separator:
                redacted.append(f"{key}=<redacted>")
            else:
                redacted.append(arg)
                redact_next = True
            continue
        if auth_index is not None and index > auth_index and not arg.startswith("-"):
            redacted.append("<redacted>")
            continue
        redacted.append(_redact_text(arg))
    return redacted


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and _INTEGER_RE.fullmatch(value.strip()):
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


def _normalise_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _request_from_json(value: object) -> dict[str, Any]:
    request: dict[str, Any] = {}
    group_ids: set[int] = set()
    temperatures: set[int] = set()
    scalar_keys = {
        "projectid": "project_id",
        "image": "image_address",
        "imageaddress": "image_address",
        "machinetype": "machine_type",
        "command": "command",
        "jobname": "job_name",
    }
    for node in _walk_json(value):
        if not isinstance(node, dict):
            continue
        for raw_key, raw_value in node.items():
            key = _normalise_key(raw_key)
            if key in {"groupid", "jobgroupid", "taskgroupid"}:
                group_id = _positive_int(raw_value)
                if group_id is not None:
                    group_ids.add(group_id)
                continue
            if key in {"temperature", "temperaturek"}:
                temperature = _positive_int(raw_value)
                if temperature is not None:
                    temperatures.add(temperature)
                continue
            target = scalar_keys.get(key)
            if target and target not in request and isinstance(raw_value, (str, int)):
                request[target] = _redact_text(str(raw_value))

    command = request.get("command")
    if isinstance(command, str):
        temperatures.update(int(match) for match in _TEMPERATURE_RE.findall(command))
    if group_ids:
        request["job_group_ids"] = sorted(group_ids)
    if temperatures:
        request["temperatures_k"] = sorted(temperatures)
    return request


def _load_input_request(argv: list[str], *, operation: str) -> dict[str, Any]:
    if operation != "job.submit":
        return {}
    input_name = _flag_value(argv, {"-i", "--input", "--input-file"})
    if not input_name:
        return {}
    input_path = Path(input_name)
    if not input_path.is_absolute():
        input_path = Path.cwd() / input_path
    request: dict[str, Any] = {"input_path": str(Path(input_name))}
    try:
        value = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return request
    request.update(_request_from_json(value))
    return request


def _request_from_argv(argv: list[str], *, operation: str) -> dict[str, Any]:
    request = _load_input_request(argv, operation=operation)
    flag_fields = {
        "project_id": {"--project-id", "--project_id"},
        "image_address": {"--image", "--image-address", "--image_address"},
        "machine_type": {"--machine-type", "--machine_type"},
        "command": {"-c", "--command"},
        "job_name": {"-n", "--job-name", "--job_name", "--name"},
    }
    for field, names in flag_fields.items():
        value = _flag_value(argv, names)
        if value is not None:
            request[field] = _redact_text(value)

    group_value = _flag_value(
        argv,
        {"-g", "--group-id", "--group_id", "--job-group-id", "--job_group_id"},
    )
    group_id = _positive_int(group_value)
    if group_id is not None:
        current = set(request.get("job_group_ids", []))
        current.add(group_id)
        request["job_group_ids"] = sorted(current)

    if operation == "job.describe":
        bohr_job_id = _positive_int(
            _flag_value(argv, {"-i", "--id", "--bohr-job-id", "--bohr_job_id"})
        )
        if bohr_job_id is not None:
            request["bohr_job_ids"] = [bohr_job_id]
    elif operation in {"job.log", "job.download"}:
        platform_job_id = _positive_int(
            _flag_value(argv, {"-j", "--job-id", "--job_id"})
        )
        if platform_job_id is not None:
            request["platform_job_ids"] = [platform_job_id]

    command = request.get("command")
    if isinstance(command, str):
        temperatures = set(request.get("temperatures_k", []))
        temperatures.update(int(match) for match in _TEMPERATURE_RE.findall(command))
        if temperatures:
            request["temperatures_k"] = sorted(temperatures)
    return request


def _response_ids(value: object, *, operation: str) -> dict[str, list[int]]:
    job_ids: set[int] = set()
    bohr_job_ids: set[int] = set()
    platform_job_ids: set[int] = set()
    group_ids: set[int] = set()
    for node in _walk_json(value):
        if not isinstance(node, dict):
            continue
        for raw_key, raw_value in node.items():
            key = _normalise_key(raw_key)
            identifier = _positive_int(raw_value)
            if identifier is None:
                continue
            if key in {"bohrid", "bohrjobid"}:
                bohr_job_ids.add(identifier)
                job_ids.add(identifier)
            elif key == "jobid":
                platform_job_ids.add(identifier)
                job_ids.add(identifier)
            elif key in {"groupid", "jobgroupid", "bohrjobgroupid", "taskgroupid"}:
                group_ids.add(identifier)
            elif key == "id" and operation == "job.submit":
                platform_job_ids.add(identifier)
                job_ids.add(identifier)
            elif key == "id" and operation == "job.describe":
                platform_job_ids.add(identifier)
                job_ids.add(identifier)
            elif key == "id" and operation == "job_group.create":
                group_ids.add(identifier)
    return {
        "job_ids": sorted(job_ids),
        "bohr_job_ids": sorted(bohr_job_ids),
        "platform_job_ids": sorted(platform_job_ids),
        "group_ids": sorted(group_ids),
    }


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value.strip()):
        return int(value)
    return None


def _response_job_state(value: object) -> dict[str, Any]:
    for node in _walk_json(value):
        if not isinstance(node, dict):
            continue
        normalised = {_normalise_key(key): child for key, child in node.items()}
        if not set(normalised) & {"status", "webstatus", "exitcode", "endtime"}:
            continue
        state: dict[str, Any] = {}
        for source, target in (("status", "status"), ("webstatus", "web_status")):
            raw = normalised.get(source)
            if isinstance(raw, (int, str)) and not isinstance(raw, bool):
                state[target] = raw
        exit_code = _integer(normalised.get("exitcode"))
        if exit_code is not None:
            state["exit_code"] = exit_code
        end_time = normalised.get("endtime")
        if isinstance(end_time, str) and end_time.strip():
            state["end_time"] = end_time.strip()
        return state
    return {}


def _append_receipt(receipt: dict[str, Any]) -> None:
    raw_path = os.environ.get(RECEIPT_PATH_ENV, "").strip()
    if not raw_path:
        return
    try:
        path = Path(raw_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = (
            json.dumps(receipt, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError:
        # Evaluation logging must never change the real CLI command's outcome.
        return


def _shell_exit_code(return_code: int) -> int:
    if return_code < 0:
        return 128 + abs(return_code)
    return return_code


def _run_transparent(command: list[str]) -> int:
    process = subprocess.Popen(command)
    try:
        return process.wait()
    except KeyboardInterrupt:
        return process.wait()


def _run_captured(command: list[str]) -> tuple[int, bytes, bytes]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = process.communicate()
    sys.stdout.buffer.write(stdout)
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(stderr)
    sys.stderr.buffer.flush()
    return process.returncode, stdout, stderr


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    real_bin = os.environ.get(REAL_BIN_ENV, "").strip()
    if not real_bin:
        print(f"error: {REAL_BIN_ENV} is not configured", file=sys.stderr)
        return 127

    operation = _operation(args)
    help_requested = any(arg in {"-h", "--help"} for arg in args)
    dry_run = any(
        _normalise_token(arg.partition("=")[0]) == "__dry_run" for arg in args
    )
    captured_json = (
        (
            operation == _JOB_DESCRIPTION_OPERATION
            or (operation in _JSON_MUTATION_OPERATIONS and _explicit_json_output(args))
        )
        and not help_requested
        and not dry_run
    )
    request = _request_from_argv(args, operation=operation)
    command = [real_bin, *args]
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    stdout = b""
    if captured_json:
        exit_code, stdout, _stderr = _run_captured(command)
    else:
        exit_code = _run_transparent(command)
    exit_code = _shell_exit_code(exit_code)

    ids = {"job_ids": [], "group_ids": []}
    job_state: dict[str, Any] = {}
    if captured_json and exit_code == 0:
        try:
            response = json.loads(stdout.decode("utf-8"))
        except ValueError:
            response = None
        if response is not None:
            ids = _response_ids(response, operation=operation)
            if operation == _JOB_DESCRIPTION_OPERATION:
                job_state = _response_job_state(response)

    _append_receipt(
        {
            "schema_version": RECEIPT_SCHEMA,
            "started_at_utc": started_at,
            "duration_ms": max(0, round((time.monotonic() - started) * 1000)),
            "operation": operation,
            "argv": _redacted_argv(args),
            "exit_code": exit_code,
            "ok": exit_code == 0,
            "help_requested": help_requested,
            "dry_run": dry_run,
            "captured_json": captured_json,
            "request": request,
            "ids": ids,
            "job_state": job_state,
        }
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
