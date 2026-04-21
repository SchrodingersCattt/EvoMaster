from __future__ import annotations

import json
import logging
import os
import shlex
from pathlib import Path
from typing import Any

from matmaster.bohrium.errors import BohriumTransferError
from matmaster_bohrium_transfer.security import redact_secrets
from matmaster_bohrium_transfer.version import SCHEMA_VERSION

logger = logging.getLogger(__name__)
REMOTE_PROTOCOL_MAJOR = "1"


def _helper_source() -> str:
    import matmaster.bohrium.remote_transfer_helper as helper_module

    helper_file = Path(helper_module.__file__ or "")
    if not helper_file.exists():
        raise BohriumTransferError("remote transfer helper source not found")
    return helper_file.read_text(encoding="utf-8")


def _run_checked(session, command: str, *, purpose: str, timeout: int = 30) -> dict:
    result = session.exec_bash(command, timeout=timeout)
    if result.get("exit_code") != 0:
        detail = str(
            result.get("stderr")
            or result.get("stdout")
            or result.get("output")
            or "unknown error"
        ).strip()
        raise BohriumTransferError(f"{purpose} failed: {redact_secrets(detail)}")
    return result


def _remote_python_binary() -> str:
    return (os.environ.get("BOHRIUM_REMOTE_HELPER_PYTHON") or "python3").strip()


def _remote_transfer_python_binary() -> str:
    return (
        os.environ.get("BOHRIUM_TRANSFER_REMOTE_PYTHON")
        or os.environ.get("BOHRIUM_REMOTE_HELPER_PYTHON")
        or "python3"
    ).strip()


def _parse_json_stdout(stdout: str, *, purpose: str) -> dict[str, Any]:
    try:
        parsed = json.loads(stdout.strip())
    except json.JSONDecodeError as exc:
        raise BohriumTransferError(
            f"{purpose} stdout is not JSON: {redact_secrets(stdout)}"
        ) from exc
    if not isinstance(parsed, dict):
        raise BohriumTransferError(f"{purpose} JSON output must be an object")
    if parsed.get("schema_version") != SCHEMA_VERSION:
        raise BohriumTransferError(
            f"{purpose} schema_version mismatch: "
            f"expected {SCHEMA_VERSION}, got {parsed.get('schema_version')!r}"
        )
    return parsed


def probe_remote_transfer(session) -> dict[str, Any]:
    python_binary = _remote_transfer_python_binary()
    quoted_python = shlex.quote(python_binary)
    command = f"{quoted_python} -m matmaster_bohrium_transfer.remote version --json"
    result = session.exec_bash(command, timeout=30)
    stdout = str(result.get("stdout") or "").strip()
    if result.get("exit_code") != 0:
        detail = stdout or result.get("stderr") or result.get("output") or ""
        raise BohriumTransferError(
            "remote transfer version probe failed: "
            f"{redact_secrets(detail)}"
        )
    payload = _parse_json_stdout(stdout, purpose="remote transfer version probe")
    protocol = str(payload.get("protocol_version") or "")
    if protocol.split(".", 1)[0] != REMOTE_PROTOCOL_MAJOR:
        raise BohriumTransferError(
            "remote transfer protocol mismatch: "
            f"expected major {REMOTE_PROTOCOL_MAJOR}, got {protocol!r}"
        )
    return payload


def _parse_helper_output(stdout: str) -> dict[str, Any]:
    try:
        parsed = json.loads(stdout.strip())
    except json.JSONDecodeError as exc:
        raise BohriumTransferError(
            f"remote helper stdout is not JSON: {redact_secrets(stdout)}"
        ) from exc
    if not isinstance(parsed, dict):
        raise BohriumTransferError("remote helper JSON output must be an object")
    if parsed.get("schema_version") != SCHEMA_VERSION:
        raise BohriumTransferError(
            "remote helper schema_version mismatch: "
            f"expected {SCHEMA_VERSION}, got {parsed.get('schema_version')!r}"
        )
    if parsed.get("ok") is False:
        raise BohriumTransferError(
            f"remote helper failed: {redact_secrets(parsed.get('error', 'unknown'))}"
        )
    return parsed


def _run_legacy_remote_helper(
    session,
    *,
    subcommand: str,
    payload: dict[str, Any],
    timeout: int = 3600,
) -> dict[str, Any]:
    if session is None or not getattr(session, "is_open", False):
        raise BohriumTransferError("remote helper requires an open remote session")

    python_binary = _remote_python_binary()
    quoted_python = shlex.quote(python_binary)
    probe = _run_checked(
        session,
        f"command -v {quoted_python} && {quoted_python} --version 2>&1",
        purpose="remote Python probe",
        timeout=15,
    )
    python_diag = str(probe.get("output") or probe.get("stdout") or "").strip()

    temp_dir = ""
    try:
        mktemp = _run_checked(
            session,
            "mktemp -d /tmp/matmaster_bohrium_transfer.XXXXXX",
            purpose="remote temp directory creation",
            timeout=15,
        )
        temp_lines = str(mktemp.get("stdout") or "").strip().splitlines()
        temp_dir = temp_lines[-1] if temp_lines else ""
        if not temp_dir:
            raise BohriumTransferError("remote mktemp returned an empty path")
        q_temp_dir = shlex.quote(temp_dir)
        _run_checked(
            session,
            f"chmod 700 {q_temp_dir}",
            purpose="remote temp directory permission setup",
            timeout=15,
        )

        payload_path = f"{temp_dir}/payload.json"
        helper_path = f"{temp_dir}/remote_transfer_helper.py"
        q_payload_path = shlex.quote(payload_path)
        q_helper_path = shlex.quote(helper_path)
        _run_checked(
            session,
            f": > {q_payload_path} && chmod 600 {q_payload_path}",
            purpose="remote payload pre-create",
            timeout=15,
        )
        payload_with_schema = dict(payload)
        payload_with_schema["schema_version"] = SCHEMA_VERSION
        session.write_file(
            payload_path,
            json.dumps(payload_with_schema, ensure_ascii=False),
        )
        _run_checked(
            session,
            f"chmod 600 {q_payload_path}",
            purpose="remote payload permission setup",
            timeout=15,
        )

        session.write_file(helper_path, _helper_source())
        _run_checked(
            session,
            f"chmod 700 {q_helper_path}",
            purpose="remote helper permission setup",
            timeout=15,
        )

        helper_cmd = (
            f"{quoted_python} {q_helper_path} {shlex.quote(subcommand)} "
            f"--payload-file {q_payload_path}"
        )
        result = session.exec_bash(helper_cmd, timeout=timeout)
        stdout = str(result.get("stdout") or "").strip()
        if result.get("exit_code") != 0:
            if stdout:
                parsed = _parse_helper_output(stdout)
                raise BohriumTransferError(
                    f"remote helper failed with exit code {result.get('exit_code')}: "
                    f"{redact_secrets(parsed.get('error', 'unknown'))}"
                )
            detail = result.get("stderr") or result.get("output") or "unknown error"
            raise BohriumTransferError(
                "remote helper failed with exit code "
                f"{result.get('exit_code')}: {redact_secrets(detail)}"
            )
        parsed = _parse_helper_output(stdout)
        parsed.setdefault("remote_python_version", python_diag)
        parsed.setdefault("remote_helper_temp_dir", temp_dir)
        return parsed
    finally:
        if temp_dir:
            cleanup = session.exec_bash(f"rm -rf {shlex.quote(temp_dir)}", timeout=30)
            if cleanup.get("exit_code") != 0:
                logger.warning(
                    "Failed to clean remote helper temp dir %s: %s",
                    temp_dir,
                    cleanup.get("stderr") or cleanup.get("output"),
                )


def _parse_remote_transfer_result(result: dict, *, purpose: str) -> dict[str, Any]:
    stdout = str(result.get("stdout") or "").strip()
    exit_code = int(result.get("exit_code") or 0)
    if stdout:
        try:
            parsed = _parse_json_stdout(stdout, purpose=purpose)
        except BohriumTransferError:
            if exit_code == 0:
                raise
            detail = result.get("stderr") or result.get("output") or stdout
            raise BohriumTransferError(
                f"{purpose} failed without JSON: {redact_secrets(detail)}"
            )
        if exit_code != 0 or parsed.get("ok") is False:
            safe = parsed.get("safe_message") or parsed.get("error") or "unknown error"
            raise BohriumTransferError(f"{purpose} failed: {redact_secrets(safe)}")
        return parsed
    if exit_code != 0:
        detail = result.get("stderr") or result.get("output") or "empty stdout"
        raise BohriumTransferError(
            f"{purpose} failed without JSON: {redact_secrets(detail)}"
        )
    raise BohriumTransferError(f"{purpose} produced empty stdout")


def run_remote_transfer(
    session,
    *,
    subcommand: str,
    payload: dict[str, Any],
    timeout: int = 3600,
) -> dict[str, Any]:
    if session is None or not getattr(session, "is_open", False):
        raise BohriumTransferError("remote transfer requires an open remote session")

    probe_remote_transfer(session)
    python_binary = _remote_transfer_python_binary()
    quoted_python = shlex.quote(python_binary)
    temp_dir = ""
    try:
        mktemp = _run_checked(
            session,
            "mktemp -d /tmp/matmaster_bohrium_transfer.XXXXXX",
            purpose="remote temp directory creation",
            timeout=15,
        )
        temp_dir = str(mktemp.get("stdout") or "").strip().splitlines()[-1]
        q_temp_dir = shlex.quote(temp_dir)
        _run_checked(
            session,
            f"chmod 700 {q_temp_dir}",
            purpose="remote temp directory permission setup",
            timeout=15,
        )
        payload_path = f"{temp_dir}/payload.json"
        q_payload_path = shlex.quote(payload_path)
        payload_with_schema = dict(payload)
        payload_with_schema.setdefault("schema_version", SCHEMA_VERSION)
        payload_with_schema.setdefault("protocol_version", "1.0")
        _run_checked(
            session,
            f"umask 077; : > {q_payload_path}",
            purpose="remote payload secure create",
            timeout=15,
        )
        session.write_file(
            payload_path,
            json.dumps(payload_with_schema, ensure_ascii=False),
        )
        _run_checked(
            session,
            f"chmod 600 {q_payload_path}",
            purpose="remote payload permission verification",
            timeout=15,
        )
        command = (
            f"{quoted_python} -m matmaster_bohrium_transfer.remote "
            f"{shlex.quote(subcommand)} --payload-file {q_payload_path}"
        )
        result = session.exec_bash(command, timeout=timeout)
        parsed = _parse_remote_transfer_result(
            result,
            purpose=f"remote transfer {subcommand}",
        )
        parsed.setdefault("remote_helper_temp_dir", temp_dir)
        return parsed
    finally:
        if temp_dir:
            session.exec_bash(f"rm -rf {shlex.quote(temp_dir)}", timeout=30)


def run_remote_helper(
    session,
    *,
    subcommand: str,
    payload: dict[str, Any],
    timeout: int = 3600,
) -> dict[str, Any]:
    if os.environ.get("BOHRIUM_TRANSFER_USE_LEGACY") == "1":
        return _run_legacy_remote_helper(
            session,
            subcommand=subcommand,
            payload=payload,
            timeout=timeout,
        )
    return run_remote_transfer(
        session,
        subcommand=subcommand,
        payload=payload,
        timeout=timeout,
    )
