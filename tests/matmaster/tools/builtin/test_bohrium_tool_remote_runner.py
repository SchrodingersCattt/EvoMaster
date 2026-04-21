from __future__ import annotations

import json
from typing import Any

import pytest

from matmaster.bohrium.errors import BohriumTransferError
from matmaster.tools.builtin.bohrium_tool.remote_runner import (
    SCHEMA_VERSION,
    run_remote_helper,
)


class RunnerSession:
    def __init__(self, *, helper_stdout: str, helper_exit_code: int = 0) -> None:
        self.is_open = True
        self.helper_stdout = helper_stdout
        self.helper_exit_code = helper_exit_code
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


def test_run_remote_helper_writes_payload_file_and_cleans_up() -> None:
    session = RunnerSession(
        helper_stdout=json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "oss_key": "prefix/input.zip",
            }
        )
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
        if "remote_transfer_helper.py" in cmd and "--payload-file" in cmd
    ]
    assert len(helper_commands) == 1
    assert "secret-token" not in helper_commands[0]


def test_run_remote_helper_rejects_schema_mismatch_and_cleans_up() -> None:
    session = RunnerSession(
        helper_stdout=json.dumps(
            {"schema_version": "v0", "ok": True, "oss_key": "prefix/input.zip"}
        )
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
    session = RunnerSession(helper_stdout="not json")

    with pytest.raises(BohriumTransferError, match="JSON"):
        run_remote_helper(
            session,
            subcommand="download-results",
            payload={"result_dir": "/share/results"},
        )


def test_run_remote_helper_rejects_ok_false_with_redacted_error() -> None:
    session = RunnerSession(
        helper_stdout=json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "ok": False,
                "error": "failed https://store/api/download/x?token=secret-token",
            }
        )
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
