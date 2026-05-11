from __future__ import annotations

import json
import os
import shlex
from typing import Any

from matmaster_bohrium_transfer.security import redact_secrets
from matmaster_bohrium_transfer.version import PROTOCOL_VERSION, SCHEMA_VERSION

from matmaster.bohrium.errors import BohriumTransferError

REMOTE_PROTOCOL_MAJOR = "1"
REQUIRED_REMOTE_CAPABILITIES = {
    "transfer_id_path_isolation",
    "strict_business_code",
    "single_retry_budget",
    "streaming_part_upload",
    "part_content_md5",
    "manifest_resume_v2",
    "download_sha256",
    "download_zip_verify",
}


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
            "remote transfer version probe failed: " f"{redact_secrets(detail)}"
        )
    payload = _parse_json_stdout(stdout, purpose="remote transfer version probe")
    protocol = str(payload.get("protocol_version") or "")
    if protocol.split(".", 1)[0] != REMOTE_PROTOCOL_MAJOR:
        raise BohriumTransferError(
            "remote transfer protocol mismatch: "
            f"expected major {REMOTE_PROTOCOL_MAJOR}, got {protocol!r}"
        )
    capabilities = {str(item) for item in payload.get("capabilities") or []}
    missing = sorted(REQUIRED_REMOTE_CAPABILITIES - capabilities)
    if missing:
        package = str(payload.get("package") or "matmaster-bohrium-transfer")
        version = str(payload.get("package_version") or "unknown")
        build_id = str(
            payload.get("build_id") or payload.get("git_commit") or "unknown"
        )
        raise BohriumTransferError(
            "remote transfer capability mismatch: "
            f"package={package} version={version} build={build_id} "
            f"missing={','.join(missing)}; update the Bohrium remote image so "
            "matmaster_bohrium_transfer includes protocol 1.1 capabilities"
        )
    return payload


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
        payload_with_schema.setdefault("protocol_version", PROTOCOL_VERSION)
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
    return run_remote_transfer(
        session,
        subcommand=subcommand,
        payload=payload,
        timeout=timeout,
    )
