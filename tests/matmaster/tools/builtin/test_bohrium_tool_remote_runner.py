from __future__ import annotations

import json
from typing import Any

import pytest

from matmaster.bohrium.errors import BohriumTransferError
from matmaster.tools.builtin.bohrium_tool.remote_runner import (
    REQUIRED_REMOTE_CAPABILITIES,
    SCHEMA_VERSION,
    probe_remote_transfer,
    run_remote_helper,
)


class RunnerSession:
    def __init__(
        self,
        *,
        helper_stdout: str = "",
        helper_exit_code: int = 0,
        version_stdout: str | None = None,
        command_stdout: str | None = None,
        version_exit_code: int | None = None,
        command_exit_code: int | None = None,
    ) -> None:
        self.is_open = True
        self.helper_stdout = helper_stdout
        self.helper_exit_code = helper_exit_code
        self.version_stdout = (
            helper_stdout if version_stdout is None else version_stdout
        )
        self.command_stdout = (
            helper_stdout if command_stdout is None else command_stdout
        )
        self.version_exit_code = (
            helper_exit_code if version_exit_code is None else version_exit_code
        )
        self.command_exit_code = (
            helper_exit_code if command_exit_code is None else command_exit_code
        )
        self.exec_calls: list[str] = []
        self.writes: list[tuple[str, str]] = []

    def exec_bash(
        self,
        command: str,
        timeout=None,
        cancel_token=None,
    ) -> dict[str, Any]:
        del timeout, cancel_token
        self.exec_calls.append(command)
        if command.startswith("command -v "):
            return {
                "stdout": "/usr/bin/python3\nPython 3.12.3\n",
                "stderr": "",
                "exit_code": 0,
                "output": "/usr/bin/python3\nPython 3.12.3\n",
            }
        if command.startswith("mktemp -d "):
            return {
                "stdout": "/tmp/matmaster_bohrium_transfer.ABCD12\n",
                "stderr": "",
                "exit_code": 0,
                "output": "/tmp/matmaster_bohrium_transfer.ABCD12\n",
            }
        if "matmaster_bohrium_transfer.remote version --json" in command:
            return {
                "stdout": self.version_stdout,
                "stderr": "",
                "exit_code": self.version_exit_code,
                "output": self.version_stdout,
            }
        if "-m matmaster_bohrium_transfer.remote" in command:
            return {
                "stdout": self.command_stdout,
                "stderr": "",
                "exit_code": self.command_exit_code,
                "output": self.command_stdout,
            }
        if "remote_transfer_helper.py" in command:
            return {
                "stdout": self.helper_stdout,
                "stderr": "",
                "exit_code": self.helper_exit_code,
                "output": self.helper_stdout,
            }
        return {"stdout": "", "stderr": "", "exit_code": 0, "output": ""}

    def write_file(self, path: str, content: str, encoding: str = "utf-8") -> None:
        del encoding
        self.writes.append((path, content))


def _remote_version_payload(*, capabilities=None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": "1.1",
        "ok": True,
        "package": "matmaster-bohrium-transfer",
        "package_version": "0.1.0",
        "build_id": "0.1.0+test",
        "capabilities": sorted(capabilities or REQUIRED_REMOTE_CAPABILITIES),
    }


def test_run_remote_helper_writes_payload_file_and_cleans_up() -> None:
    session = RunnerSession(
        version_stdout=json.dumps(_remote_version_payload()),
        command_stdout=json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "protocol_version": "1.1",
                "ok": True,
                "oss_key": "prefix/input.zip",
            }
        ),
    )

    result = run_remote_helper(
        session,
        subcommand="upload-submit",
        payload={"token": "secret-token", "input_dir": "/share/input"},
    )

    assert result["oss_key"] == "prefix/input.zip"
    payload_writes = [
        item for item in session.writes if item[0].endswith("payload.json")
    ]
    assert len(payload_writes) == 1
    assert json.loads(payload_writes[0][1])["schema_version"] == SCHEMA_VERSION
    assert any(
        "chmod 700 /tmp/matmaster_bohrium_transfer.ABCD12" in cmd
        for cmd in session.exec_calls
    )
    assert any(
        "chmod 600 /tmp/matmaster_bohrium_transfer.ABCD12/payload.json" in cmd
        for cmd in session.exec_calls
    )
    assert any(
        "rm -rf /tmp/matmaster_bohrium_transfer.ABCD12" in cmd
        for cmd in session.exec_calls
    )
    helper_commands = [
        cmd
        for cmd in session.exec_calls
        if "-m matmaster_bohrium_transfer.remote upload-submit" in cmd
        and "--payload-file" in cmd
    ]
    assert len(helper_commands) == 1
    assert "secret-token" not in helper_commands[0]
    assert not any(
        path.endswith("remote_transfer_helper.py") for path, _ in session.writes
    )


def test_run_remote_helper_rejects_schema_mismatch_and_cleans_up() -> None:
    session = RunnerSession(
        version_stdout=json.dumps(_remote_version_payload()),
        command_stdout=json.dumps(
            {"schema_version": "v0", "protocol_version": "1.0", "ok": True}
        ),
    )

    with pytest.raises(BohriumTransferError, match="schema_version"):
        run_remote_helper(
            session,
            subcommand="upload-submit",
            payload={"input_dir": "/share/input"},
        )

    assert any(
        "rm -rf /tmp/matmaster_bohrium_transfer.ABCD12" in cmd
        for cmd in session.exec_calls
    )


def test_run_remote_helper_rejects_non_json_stdout() -> None:
    session = RunnerSession(
        version_stdout=json.dumps(_remote_version_payload()),
        command_stdout="not json",
        command_exit_code=1,
    )

    with pytest.raises(BohriumTransferError, match="JSON"):
        run_remote_helper(
            session,
            subcommand="download-results",
            payload={"result_dir": "/share/results"},
        )


def test_run_remote_helper_rejects_ok_false_with_redacted_error() -> None:
    session = RunnerSession(
        version_stdout=json.dumps(_remote_version_payload()),
        command_stdout=json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "protocol_version": "1.1",
                "ok": False,
                "error": "failed https://store/api/download/x?token=secret-token",
            }
        ),
        command_exit_code=1,
    )

    with pytest.raises(BohriumTransferError) as exc_info:
        run_remote_helper(
            session,
            subcommand="download-results",
            payload={"result_dir": "/share/results"},
        )

    message = str(exc_info.value)
    assert "secret-token" not in message
    assert "token=<redacted>" in message


def test_run_remote_helper_uses_safe_message_without_leaking_diagnostics() -> None:
    session = RunnerSession(
        version_stdout=json.dumps(_remote_version_payload()),
        command_stdout=json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "protocol_version": "1.1",
                "ok": False,
                "safe_message": "invalid zip archive",
                "diagnostics": {
                    "fallback_attempts": [
                        {"error": "https://store/out.zip?token=secret-token"}
                    ]
                },
            }
        ),
        command_exit_code=1,
    )

    with pytest.raises(BohriumTransferError) as exc_info:
        run_remote_helper(
            session,
            subcommand="download-results",
            payload={"result_dir": "/share/results"},
        )

    message = str(exc_info.value)
    assert "invalid zip archive" in message
    assert "secret-token" not in message
    assert "fallback_attempts" not in message


def test_remote_version_probe_uses_preinstalled_package() -> None:
    session = RunnerSession(helper_stdout=json.dumps(_remote_version_payload()))

    from matmaster.tools.builtin.bohrium_tool.remote_runner import (
        probe_remote_transfer,
    )

    payload = probe_remote_transfer(session)

    assert payload["ok"] is True
    assert any(
        "python3 -m matmaster_bohrium_transfer.remote version --json" in cmd
        for cmd in session.exec_calls
    )


def test_remote_version_probe_rejects_non_json() -> None:
    session = RunnerSession(helper_stdout="not json", helper_exit_code=1)

    with pytest.raises(BohriumTransferError, match="remote transfer version probe"):
        probe_remote_transfer(session)


def test_remote_version_probe_rejects_missing_required_capabilities() -> None:
    session = RunnerSession(
        helper_stdout=json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "protocol_version": "1.1",
                "ok": True,
                "package": "matmaster-bohrium-transfer",
                "package_version": "0.1.0",
                "build_id": "0.1.0+test",
                "capabilities": ["multipart_upload"],
            }
        )
    )

    with pytest.raises(BohriumTransferError) as exc_info:
        probe_remote_transfer(session)

    message = str(exc_info.value)
    assert "remote transfer capability mismatch" in message
    assert "part_content_md5" in message
    assert "matmaster_bohrium_transfer" in message


def test_run_remote_transfer_uses_package_cli_not_source_copy() -> None:
    session = RunnerSession(
        version_stdout=json.dumps(_remote_version_payload()),
        command_stdout=json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "protocol_version": "1.1",
                "ok": True,
                "oss_key": "prefix/input.zip",
            }
        ),
    )

    from matmaster.tools.builtin.bohrium_tool.remote_runner import run_remote_transfer

    result = run_remote_transfer(
        session,
        subcommand="upload-submit",
        payload={"input_dir": "/share/input", "token": "secret-token"},
    )

    assert result["oss_key"] == "prefix/input.zip"
    assert not any(
        path.endswith("remote_transfer_helper.py") for path, _ in session.writes
    )
    assert any(
        "-m matmaster_bohrium_transfer.remote upload-submit --payload-file" in cmd
        for cmd in session.exec_calls
    )
    assert not any("secret-token" in cmd for cmd in session.exec_calls)
