"""tests/matmaster/tools/builtin/test_bohrium_tool.py"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import sys
import types
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

import matmaster.tools.builtin.bohrium_tool as bohrium_module
from matmaster.tools.builtin.bohrium_tool import BohriumTool, _use_sandbox
from matmaster.tools.tool_result import ToolResult
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import (
    FakeRemoteSession,
    _fake_cred,
    _fake_submit_post_factory,
    _install_fake_tiefblue,
    _patch_bridge,
)


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


class _FakeDownloadResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: dict[str, object] | None = None,
        content: bytes | None = None,
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data or {}
        self._content = content if content is not None else b""
        self.ok = status_code < 400
        self.text = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error")

    def json(self) -> dict[str, object]:
        return self._json_data

    def iter_content(self, chunk_size: int = 8192):
        if self._content:
            yield self._content


# ---------------------------------------------------------------------------
# TestBohriumMetadata
# ---------------------------------------------------------------------------


class TestBohriumMetadata:
    def test_name(self):
        assert BohriumTool.name == "Bohrium"

    def test_poll_schema_exposes_wait_fields(self):
        properties = BohriumTool.json_schema["properties"]
        assert properties["wait"]["type"] == "boolean"
        assert properties["max_wait_seconds"]["type"] == "integer"
        assert properties["poll_interval_seconds"]["type"] == "integer"

    def test_prompt_mentions_list_actions(self, tmp_path):
        tool = BohriumTool(workdir=tmp_path)
        prompt = tool.prompt()
        assert prompt is not None
        assert "Bohrium" in prompt
        assert "list_images" in prompt
        assert "list_machines" in prompt

    def test_prompt_mentions_poll_wait_mode(self, tmp_path):
        tool = BohriumTool(workdir=tmp_path)
        prompt = tool.prompt()
        assert prompt is not None
        assert "single query" in prompt
        assert "wait=true" in prompt
        assert "failure confirmation" in prompt


# ---------------------------------------------------------------------------
# TestBohriumSandboxMode
# ---------------------------------------------------------------------------


class TestBohriumSandboxMode:
    def test_use_sandbox_defaults_true(self, monkeypatch):
        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        assert _use_sandbox() is True

    def test_use_sandbox_disabled_with_zero(self, monkeypatch):
        monkeypatch.setenv("BOHRIUM_USE_SANDBOX", "0")
        assert _use_sandbox() is False


# ---------------------------------------------------------------------------
# TestBohriumExecution
# ---------------------------------------------------------------------------


class TestBohriumExecution:
    def test_get_logs_http_error_context(self, monkeypatch, caplog):
        class FakeResponse:
            status_code = 401
            ok = False
            text = '{"code":2000,"error":"AccessKey Invalid"}'

            def raise_for_status(self):
                raise requests.HTTPError(
                    "401 Client Error: Unauthorized for url: "
                    "https://openapi.test.dp.tech/openapi/v1/calc/list"
                )

        monkeypatch.setattr(
            bohrium_module.requests, "get", lambda *args, **kwargs: FakeResponse()
        )

        with caplog.at_level(logging.WARNING):
            with pytest.raises(requests.HTTPError):
                bohrium_module._get(
                    "https://openapi.test.dp.tech",
                    "/openapi/v1/calc/list",
                    "secret-ak",
                )

        messages = [record.getMessage() for record in caplog.records]
        assert any("method=GET" in msg for msg in messages)
        assert any("status=401" in msg for msg in messages)
        assert any("response_body=" in msg for msg in messages)
        assert not any("secret-ak" in msg for msg in messages)

    def test_post_logs_http_error_context(self, monkeypatch, caplog):
        class FakeResponse:
            status_code = 404
            ok = False
            text = "404 page not found"

            def raise_for_status(self):
                raise requests.HTTPError(
                    "404 Client Error: Not Found for url: "
                    "https://open.bohrium.com/openapi/v1/sandbox/job/create"
                )

        monkeypatch.setattr(
            bohrium_module.requests, "post", lambda *args, **kwargs: FakeResponse()
        )

        with caplog.at_level(logging.WARNING):
            with pytest.raises(requests.HTTPError):
                bohrium_module._post(
                    "https://open.bohrium.com",
                    "/openapi/v1/sandbox/job/create",
                    "secret-ak",
                    {"projectId": 1, "name": "probe"},
                )

        messages = [record.getMessage() for record in caplog.records]
        assert any("method=POST" in msg for msg in messages)
        assert any("status=404" in msg for msg in messages)
        assert any("response_body=404 page not found" in msg for msg in messages)
        assert not any("secret-ak" in msg for msg in messages)

    def test_submit_logs_resolved_context(self, tmp_path, monkeypatch, caplog):
        input_dir = tmp_path / "inputs"
        input_dir.mkdir()
        (input_dir / "INPUT").write_text("data", encoding="utf-8")

        tool = BohriumTool(workdir=tmp_path)
        post_calls: list[tuple[str, dict, str]] = []
        upload_calls: list[tuple[str, str, dict]] = []

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(
            monkeypatch,
            _fake_cred(
                access_key="secret-access-key",
                project_id=42,
                source="session",
                base_url="https://openapi.test.dp.tech",
            ),
        )
        monkeypatch.setattr(
            bohrium_module, "_post", _fake_submit_post_factory(post_calls)
        )

        with monkeypatch.context() as m:
            _install_fake_tiefblue(m, upload_calls)
            with caplog.at_level(logging.INFO):
                result = asyncio.run(
                    tool.execute(
                        {
                            "action": "submit",
                            "input_dir": "inputs",
                            "image": "test:latest",
                            "cmd": "echo hi",
                        }
                    )
                )

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        messages = [record.getMessage() for record in caplog.records]
        assert any("action=submit" in msg for msg in messages)
        assert any("source=session" in msg for msg in messages)
        assert any("base_url=https://openapi.test.dp.tech" in msg for msg in messages)
        assert any("sandbox=True" in msg for msg in messages)
        assert any("access_key=secr..." in msg for msg in messages)
        assert not any("secret-access-key" in msg for msg in messages)

    def test_submit_remote_share_without_session_errors(self, tmp_path, monkeypatch):
        _patch_bridge(monkeypatch)
        tool = BohriumTool(workdir=tmp_path)

        result = asyncio.run(
            tool.execute(
                {
                    "action": "submit",
                    "input_dir": "/share/Pd111_submit",
                    "image": "registry.dp.tech/dptech/abacus:LTSv3.10.1",
                    "cmd": "mpirun -np 16 abacus",
                }
            )
        )

        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "remote session" in result.content.lower()

    def test_submit_relative_input_dir_resolves_under_workdir(
        self, tmp_path, monkeypatch
    ):
        input_dir = tmp_path / "inputs"
        input_dir.mkdir()
        (input_dir / "INPUT").write_text("data", encoding="utf-8")

        tool = BohriumTool(workdir=tmp_path)
        post_calls: list[tuple[str, dict, str]] = []
        upload_calls: list[tuple[str, str, dict]] = []

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(
            bohrium_module, "_post", _fake_submit_post_factory(post_calls)
        )

        with monkeypatch.context() as m:
            _install_fake_tiefblue(m, upload_calls)
            result = asyncio.run(
                tool.execute(
                    {
                        "action": "submit",
                        "input_dir": "inputs",
                        "image": "test:latest",
                        "cmd": "echo hi",
                    }
                )
            )

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert upload_calls
        assert upload_calls[0][0].endswith("input.zip")
        assert post_calls[1][1]["cmd"].endswith("> log 2>&1")

    def test_submit_remote_share_with_session_downloads_bundle(
        self, tmp_path, monkeypatch
    ):
        session = FakeRemoteSession(
            existing_paths={"/share/Pd111_submit"},
        )
        tool = BohriumTool(session=session, workdir=tmp_path)
        post_calls: list[tuple[str, dict, str]] = []
        upload_calls: list[tuple[str, str, dict]] = []

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(
            bohrium_module, "_post", _fake_submit_post_factory(post_calls)
        )

        with monkeypatch.context() as m:
            _install_fake_tiefblue(m, upload_calls)
            result = asyncio.run(
                tool.execute(
                    {
                        "action": "submit",
                        "input_dir": "/share/Pd111_submit",
                        "image": "test:latest",
                        "cmd": "echo hi",
                    }
                )
            )

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert session.exec_calls
        assert session.download_calls
        assert upload_calls
        assert post_calls[1][1]["cmd"].endswith("> log 2>&1")

    def test_submit_delegates_path_classification_to_resolve_output_path(
        self, tmp_path, monkeypatch
    ):
        input_dir = tmp_path / "inputs"
        input_dir.mkdir()
        (input_dir / "INPUT").write_text("data", encoding="utf-8")

        tool = BohriumTool(workdir=tmp_path)
        post_calls: list[tuple[str, dict, str]] = []
        upload_calls: list[tuple[str, str, dict]] = []

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(
            bohrium_module, "_post", _fake_submit_post_factory(post_calls)
        )

        with (
            monkeypatch.context() as m,
            patch(
                "matmaster.tools.builtin.bohrium_tool.resolve_output_path",
                create=True,
            ) as mock_resolve,
        ):
            mock_resolve.return_value = SimpleNamespace(
                kind="local_abs",
                normalized_path=str(input_dir),
                requires_remote_session=False,
            )
            _install_fake_tiefblue(m, upload_calls)
            result = asyncio.run(
                tool.execute(
                    {
                        "action": "submit",
                        "input_dir": str(input_dir),
                        "image": "test:latest",
                        "cmd": "echo hi",
                    }
                )
            )

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        mock_resolve.assert_called_once()

    def test_remote_input_dir_missing_directory_surfaces_remote_error(
        self, tmp_path, monkeypatch
    ):
        session = FakeRemoteSession()
        tool = BohriumTool(session=session, workdir=tmp_path)

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)

        result = asyncio.run(
            tool.execute(
                {
                    "action": "submit",
                    "input_dir": "/share/missing",
                    "image": "test:latest",
                    "cmd": "echo hi",
                }
            )
        )

        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "remote input_dir not found" in result.content.lower()

    def test_remote_input_dir_packaging_failure_surfaces_stderr(
        self, tmp_path, monkeypatch
    ):
        session = FakeRemoteSession(
            existing_paths={"/share/Pd111_submit"},
            exec_result={
                "stdout": "",
                "stderr": "python3: command not found",
                "exit_code": 127,
                "working_dir": "/share",
                "output": "python3: command not found",
            },
        )
        tool = BohriumTool(session=session, workdir=tmp_path)

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)

        result = asyncio.run(
            tool.execute(
                {
                    "action": "submit",
                    "input_dir": "/share/Pd111_submit",
                    "image": "test:latest",
                    "cmd": "echo hi",
                }
            )
        )

        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "failed to package remote input_dir".lower() in result.content.lower()
        assert "python3: command not found" in result.content

    def test_submit_file_path_instead_of_directory_errors(self, tmp_path, monkeypatch):
        file_path = tmp_path / "INPUT"
        file_path.write_text("data", encoding="utf-8")
        tool = BohriumTool(workdir=tmp_path)

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)

        result = asyncio.run(
            tool.execute(
                {
                    "action": "submit",
                    "input_dir": str(file_path),
                    "image": "test:latest",
                    "cmd": "echo hi",
                }
            )
        )

        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "not a directory" in result.content.lower()

    def test_remote_temp_archive_cleanup_is_attempted(self, tmp_path, monkeypatch):
        session = FakeRemoteSession(
            existing_paths={"/share/Pd111_submit"},
            exec_result={
                "stdout": "",
                "stderr": "zip failure",
                "exit_code": 1,
                "working_dir": "/share",
                "output": "zip failure",
            },
        )
        tool = BohriumTool(session=session, workdir=tmp_path)

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)

        result = asyncio.run(
            tool.execute(
                {
                    "action": "submit",
                    "input_dir": "/share/Pd111_submit",
                    "image": "test:latest",
                    "cmd": "echo hi",
                }
            )
        )

        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert any(
            cmd.startswith("rm -f /tmp/bohrium_input_") for cmd in session.exec_calls
        )

    def test_submit_remote_share_with_closed_session_errors(
        self, tmp_path, monkeypatch
    ):
        session = FakeRemoteSession(is_open=False)
        tool = BohriumTool(session=session, workdir=tmp_path)

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)

        result = asyncio.run(
            tool.execute(
                {
                    "action": "submit",
                    "input_dir": "/share/Pd111_submit",
                    "image": "test:latest",
                    "cmd": "echo hi",
                }
            )
        )

        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "open remote session" in result.content.lower()

    def test_submit_personal_path_treated_as_remote_share(self, tmp_path, monkeypatch):
        session = FakeRemoteSession(existing_paths={"/personal/inputs"})
        tool = BohriumTool(session=session, workdir=tmp_path)
        post_calls: list[tuple[str, dict, str]] = []
        upload_calls: list[tuple[str, str, dict]] = []

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(
            bohrium_module, "_post", _fake_submit_post_factory(post_calls)
        )

        with monkeypatch.context() as m:
            _install_fake_tiefblue(m, upload_calls)
            result = asyncio.run(
                tool.execute(
                    {
                        "action": "submit",
                        "input_dir": "/personal/inputs",
                        "image": "test:latest",
                        "cmd": "echo hi",
                    }
                )
            )

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert session.download_calls
        assert upload_calls

    def test_submit_empty_directory_still_uploads_input_zip(
        self, tmp_path, monkeypatch
    ):
        input_dir = tmp_path / "empty-inputs"
        input_dir.mkdir()

        tool = BohriumTool(workdir=tmp_path)
        post_calls: list[tuple[str, dict, str]] = []
        upload_calls: list[tuple[str, str, dict]] = []

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(
            bohrium_module, "_post", _fake_submit_post_factory(post_calls)
        )

        with monkeypatch.context() as m:
            _install_fake_tiefblue(m, upload_calls)
            result = asyncio.run(
                tool.execute(
                    {
                        "action": "submit",
                        "input_dir": str(input_dir),
                        "image": "test:latest",
                        "cmd": "echo hi",
                    }
                )
            )

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert upload_calls
        assert upload_calls[0][0].endswith("input.zip")

    def test_unknown_action_error(self, tmp_path):
        tool = BohriumTool(workdir=tmp_path)
        result = asyncio.run(tool.execute({"action": "unknown"}))
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "Unknown action" in result.content

    def test_submit_defaults_to_sandbox_and_appends_log(self, tmp_path, monkeypatch):
        input_dir = tmp_path / "inputs"
        input_dir.mkdir()
        (input_dir / "input.inp").write_text("&CONTROL\n", encoding="utf-8")

        tool = BohriumTool(workdir=tmp_path)
        post_calls: list[tuple[str, dict]] = []
        upload_calls: list[tuple[str, str, dict]] = []

        def fake_post(base_url, path, access_key, payload, timeout=30):
            post_calls.append((path, payload))
            if path == "/openapi/v1/sandbox/job/create":
                return {
                    "code": 0,
                    "data": {
                        "storePath": "sandbox/jobs/run-1/",
                        "storeHost": "https://store.example.com",
                        "token": "token-123",
                        "jobId": "create-job-id",
                    },
                }
            if path == "/openapi/v1/sandbox/job/add":
                return {
                    "code": 0,
                    "data": {
                        "jobId": "job-123",
                        "bohrJobId": "bohr-456",
                    },
                }
            raise AssertionError(f"unexpected path: {path}")

        class FakeTiefblueClient:
            def __init__(self, *, base_url):
                self.base_url = base_url

            def upload_from_file_multi_part(
                self,
                *,
                object_key,
                file_path,
                custom_headers,
                progress_bar,
            ):
                upload_calls.append((object_key, file_path, custom_headers))
                assert progress_bar is False
                return {}

        sdk_module = types.ModuleType("bohrium_open_sdk")
        opensdk_module = types.ModuleType("bohrium_open_sdk.opensdk")
        tiefblue_module = types.ModuleType("bohrium_open_sdk.opensdk._tiefblue_client")
        tiefblue_module.Tiefblue = FakeTiefblueClient
        sdk_module.opensdk = opensdk_module
        opensdk_module._tiefblue_client = tiefblue_module

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_module, "_post", fake_post)

        with monkeypatch.context() as m:
            m.setitem(sys.modules, "bohrium_open_sdk", sdk_module)
            m.setitem(sys.modules, "bohrium_open_sdk.opensdk", opensdk_module)
            m.setitem(
                sys.modules,
                "bohrium_open_sdk.opensdk._tiefblue_client",
                tiefblue_module,
            )
            result = asyncio.run(
                tool.execute(
                    {
                        "action": "submit",
                        "input_dir": str(input_dir),
                        "image": "registry.dp.tech/dptech/cp2k:2024.1",
                        "cmd": "cp2k.popt -i input.inp",
                    }
                )
            )

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        payload = json.loads(result.content)
        assert payload["job_id"] == "job-123"
        assert payload["bohr_job_id"] == "bohr-456"
        assert payload["use_sandbox"] is True
        assert [path for path, _ in post_calls] == [
            "/openapi/v1/sandbox/job/create",
            "/openapi/v1/sandbox/job/add",
        ]
        assert post_calls[1][1]["cmd"].endswith("> log 2>&1")
        assert post_calls[1][1]["jobId"] == "create-job-id"
        assert post_calls[1][1]["ossPath"][0].startswith(
            "https://store.example.com/api/download/"
        )
        assert upload_calls
        assert upload_calls[0][0].endswith("input.zip")
        assert upload_calls[0][2]["Authorization"] == "Bearer token-123"

    def test_poll_running_uses_sandbox_endpoint(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        get_calls: list[str] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            get_calls.append(path)
            return {"data": {"status": 1}}

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_module, "_get", fake_get)

        result = asyncio.run(tool.execute({"action": "poll", "job_id": "job-123"}))

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        payload = json.loads(result.content)
        assert payload["job_id"] == "job-123"
        assert payload["status"] == "Running"
        assert get_calls == ["/openapi/v1/sandbox/job/job-123"]

    def test_poll_wait_retries_running_until_timeout(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        get_calls: list[str] = []
        sleep_calls: list[float] = []
        statuses = [1, 1, 1]
        status_index = 0

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            nonlocal status_index
            get_calls.append(path)
            status = statuses[min(status_index, len(statuses) - 1)]
            status_index += 1
            return {"data": {"status": status}}

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_module, "_get", fake_get)
        import time as time_module

        monkeypatch.setattr(bohrium_module, "time", time_module, raising=False)
        monkeypatch.setattr(bohrium_module.time, "sleep", sleep_calls.append)

        result = asyncio.run(
            tool.execute(
                {
                    "action": "poll",
                    "job_id": "job-running",
                    "wait": True,
                    "max_wait_seconds": 10,
                    "poll_interval_seconds": 5,
                }
            )
        )

        assert result.status == "success"
        payload = json.loads(result.content)
        assert payload["status"] == "Running"
        assert "waited 10s" in payload["message"]
        assert get_calls == ["/openapi/v1/sandbox/job/job-running"] * 3
        assert sleep_calls == [5, 5]

    def test_poll_running_without_wait_remains_single_shot(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        get_calls: list[str] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            get_calls.append(path)
            return {"data": {"status": 1}}

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_module, "_get", fake_get)

        result = asyncio.run(tool.execute({"action": "poll", "job_id": "job-789"}))

        assert result.status == "success"
        payload = json.loads(result.content)
        assert payload["status"] == "Running"
        assert get_calls == ["/openapi/v1/sandbox/job/job-789"]

    def test_poll_failed_confirms_before_downloading(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        get_calls: list[str] = []
        sleep_calls: list[float] = []
        statuses = [
            {
                "status": -1,
                "resultUrl": "https://store.example/api/download/"
                "prefix/job-999.zip?token=root-token",
            },
            {
                "status": -1,
                "resultUrl": "https://store.example/api/download/"
                "prefix/job-999.zip?token=root-token",
            },
            {
                "status": -1,
                "resultUrl": "https://store.example/api/download/"
                "prefix/job-999.zip?token=root-token",
            },
        ]
        status_index = 0
        download_calls: list[tuple[int | str, dict, Path, bool]] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            nonlocal status_index
            get_calls.append(path)
            detail_data = statuses[min(status_index, len(statuses) - 1)]
            status_index += 1
            return {"data": detail_data}

        def fake_download_results(job_id, detail_data, result_dir, *, ctx, sandbox):
            download_calls.append((job_id, detail_data, result_dir, sandbox))
            return ["log"], "boom"

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_module, "_get", fake_get)
        import time as time_module

        monkeypatch.setattr(bohrium_module, "time", time_module, raising=False)
        monkeypatch.setattr(bohrium_module.time, "sleep", sleep_calls.append)
        monkeypatch.setattr(tool, "_download_results", fake_download_results)

        result = asyncio.run(tool.execute({"action": "poll", "job_id": "job-999"}))

        assert result.status == "error"
        payload = json.loads(result.content)
        assert payload["status"] == "Failed"
        assert payload["result_dir"]
        assert payload["files"] == ["log"]
        assert payload["log_tail"] == "boom"
        assert get_calls == ["/openapi/v1/sandbox/job/job-999"] * 3
        assert sleep_calls == [3, 3]
        assert len(download_calls) == 1

    def test_poll_failed_downloads_log_from_file_token_and_result_url_zip(
        self, tmp_path, monkeypatch
    ):
        tool = BohriumTool(workdir=tmp_path)
        get_calls: list[str] = []
        file_token_calls: list[tuple[str, dict[str, str]]] = []
        iterate_calls: list[tuple[str, dict[str, str], dict[str, str]]] = []
        http_get_calls: list[tuple[str, object, object, int, bool]] = []
        sleep_calls: list[int | float] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            get_calls.append(path)
            return {
                "data": {
                    "status": -1,
                    "resultUrl": "https://store.example/api/download/"
                    "prefix/job-123.zip?token=root-token",
                }
            }

        def fake_post(base_url, path, access_key, payload, timeout=30):
            file_token_calls.append((path, payload))
            return {
                "code": 0,
                "data": {
                    "host": "https://store.example",
                    "path": "prefix/log",
                    "token": "log-token",
                },
            }

        def fake_requests_post(url, *, headers=None, json=None, timeout=30):
            iterate_calls.append((url, headers, json))
            return _FakeDownloadResponse(
                json_data={
                    "code": 0,
                    "data": {
                        "hasNext": False,
                        "objects": [
                            {
                                "path": "prefix/job-123.zip",
                                "isDir": False,
                            },
                            {
                                "path": "prefix/log",
                                "isDir": False,
                            },
                        ],
                    },
                }
            )

        def fake_requests_get(
            url, *, headers=None, params=None, timeout=30, stream=False
        ):
            http_get_calls.append((url, headers, params, timeout, stream))
            if url.startswith("https://store.example/api/download/prefix/log"):
                return _FakeDownloadResponse(
                    content=b"traceback: model not found\n",
                )
            if url.startswith("https://store.example/api/download/prefix/job-123.zip"):
                return _FakeDownloadResponse(
                    content=_zip_bytes(
                        {
                            "log": "traceback: model not found\n",
                            "stderr.txt": "x",
                        }
                    ),
                )
            if not stream:
                return _FakeDownloadResponse(json_data={"data": {"list": []}})
            return _FakeDownloadResponse(content=b"")

        def fake_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_module, "_get", fake_get)
        monkeypatch.setattr(bohrium_module, "_post", fake_post)
        monkeypatch.setattr(bohrium_module.requests, "post", fake_requests_post)
        monkeypatch.setattr(bohrium_module.requests, "get", fake_requests_get)
        import time as time_module

        monkeypatch.setattr(bohrium_module, "time", time_module, raising=False)
        monkeypatch.setattr(bohrium_module.time, "sleep", fake_sleep)

        result = asyncio.run(tool.execute({"action": "poll", "job_id": "job-123"}))

        assert isinstance(result, ToolResult)
        assert result.status == "error"
        payload = json.loads(result.content)
        assert payload["status"] == "Failed"
        assert payload["result_dir"]
        assert "traceback: model not found" in payload["log_tail"]
        assert "log" in payload["files"]
        assert any(
            url.startswith("https://store.example/api/download/prefix/log")
            for url, _, _, _, _ in http_get_calls
        )
        assert any(
            url.startswith("https://store.example/api/download/prefix/job-123.zip")
            for url, _, _, _, _ in http_get_calls
        )
        assert file_token_calls == [
            (
                "/openapi/v1/sandbox/job/file/token",
                {"filePath": "log", "jobId": "job-123"},
            )
        ]
        assert iterate_calls == [
            (
                "https://store.example/api/iterate",
                {
                    "Authorization": "Bearer root-token",
                    "Content-Type": "application/json",
                },
                {"prefix": "prefix/"},
            )
        ]
        assert get_calls == ["/openapi/v1/sandbox/job/job-123"] * 3
        assert sleep_calls == [3, 3]

    def test_poll_failed_returns_log_when_zip_is_not_ready(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        get_calls: list[str] = []
        file_token_calls: list[tuple[str, dict[str, str]]] = []
        iterate_calls: list[tuple[str, dict[str, str], dict[str, str]]] = []
        http_get_calls: list[tuple[str, object, object, int, bool]] = []
        sleep_calls: list[int | float] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            get_calls.append(path)
            return {
                "data": {
                    "status": -1,
                    "resultUrl": "https://store.example/api/download/"
                    "prefix/job-456.zip?token=root-token",
                }
            }

        def fake_post(base_url, path, access_key, payload, timeout=30):
            file_token_calls.append((path, payload))
            return {
                "code": 0,
                "data": {
                    "host": "https://store.example",
                    "path": "prefix/log",
                    "token": "log-token",
                },
            }

        def fake_requests_post(url, *, headers=None, json=None, timeout=30):
            iterate_calls.append((url, headers, json))
            return _FakeDownloadResponse(
                json_data={
                    "code": 0,
                    "data": {
                        "hasNext": False,
                        "objects": [
                            {
                                "path": "prefix/log",
                                "isDir": False,
                            }
                        ],
                    },
                }
            )

        def fake_requests_get(
            url, *, headers=None, params=None, timeout=30, stream=False
        ):
            http_get_calls.append((url, headers, params, timeout, stream))
            if url.startswith("https://store.example/api/download/prefix/log"):
                return _FakeDownloadResponse(
                    content=b"runtime error: missing model\n",
                )
            if url.startswith("https://store.example/api/download/prefix/job-456.zip"):
                return _FakeDownloadResponse(status_code=404)
            return _FakeDownloadResponse(status_code=404)

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_module, "_get", fake_get)
        monkeypatch.setattr(bohrium_module, "_post", fake_post)
        monkeypatch.setattr(bohrium_module.requests, "post", fake_requests_post)
        monkeypatch.setattr(bohrium_module.requests, "get", fake_requests_get)
        import time as time_module

        monkeypatch.setattr(bohrium_module, "time", time_module, raising=False)
        monkeypatch.setattr(bohrium_module.time, "sleep", sleep_calls.append)

        result = asyncio.run(tool.execute({"action": "poll", "job_id": "job-456"}))

        assert isinstance(result, ToolResult)
        assert result.status == "error"
        payload = json.loads(result.content)
        assert payload["result_dir"]
        assert payload["files"] == ["log"]
        assert "runtime error: missing model" in payload["log_tail"]
        assert any(
            url.startswith("https://store.example/api/download/prefix/log")
            for url, _, _, _, _ in http_get_calls
        )
        assert any(
            url.startswith("https://store.example/api/download/prefix/job-456.zip")
            for url, _, _, _, _ in http_get_calls
        )
        assert file_token_calls == [
            (
                "/openapi/v1/sandbox/job/file/token",
                {"filePath": "log", "jobId": "job-456"},
            )
        ]
        assert iterate_calls == [
            (
                "https://store.example/api/iterate",
                {
                    "Authorization": "Bearer root-token",
                    "Content-Type": "application/json",
                },
                {"prefix": "prefix/"},
            )
        ]
        assert get_calls == ["/openapi/v1/sandbox/job/job-456"] * 3
        assert sleep_calls == [3, 3]

    def test_list_images_filters_and_returns_versions(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        get_calls: list[tuple[str, dict | None]] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            get_calls.append((path, params))
            if path == "/openapi/v2/image/public":
                return {
                    "data": {
                        "items": [
                            {
                                "id": 1,
                                "name": "CP2K",
                                "description": "CP2K production image",
                            },
                            {"id": 2, "name": "GROMACS", "description": "MD image"},
                        ]
                    }
                }
            if path == "/openapi/v2/image/public/1/version":
                return {
                    "data": {
                        "items": [
                            {
                                "url": "registry.dp.tech/dptech/cp2k:2024.1",
                                "version": "2024.1",
                                "resourceType": "CPU",
                            }
                        ]
                    }
                }
            raise AssertionError(f"unexpected path: {path}")

        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_module, "_get", fake_get)

        result = asyncio.run(
            tool.execute({"action": "list_images", "keyword": "cp2k", "max_results": 5})
        )

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        payload = json.loads(result.content)
        assert payload["total_found"] == 1
        assert payload["returned"] == 1
        assert payload["images"][0]["name"] == "CP2K"
        assert payload["images"][0]["versions"][0]["version"] == "2024.1"
        assert get_calls[0][0] == "/openapi/v2/image/public"
        assert get_calls[1][0] == "/openapi/v2/image/public/1/version"

    def test_list_machines_filters_by_type_and_keyword(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        get_calls: list[tuple[str, dict | None]] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            get_calls.append((path, params))
            assert path == "/openapi/v1/calc/list"
            return {
                "data": {
                    "items": [
                        {
                            "skuEnName": "c6_m60_1 * NVIDIA 4090",
                            "gpu": "4090",
                            "gpuCoreNum": 1,
                            "hasStock": True,
                        },
                        {
                            "skuEnName": "c32_m128_cpu",
                            "cpuCoreNum": 32,
                            "memory": 128,
                            "hasStock": True,
                        },
                    ]
                }
            }

        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_module, "_get", fake_get)

        result = asyncio.run(
            tool.execute(
                {
                    "action": "list_machines",
                    "machine_type": "gpu",
                    "keyword": "4090",
                    "max_results": 10,
                }
            )
        )

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        payload = json.loads(result.content)
        assert payload["type"] == "gpu"
        assert payload["total_found"] == 1
        assert payload["returned"] == 1
        assert payload["machines"][0]["skuEnName"] == "c6_m60_1 * NVIDIA 4090"
        assert get_calls[0][1]["chooseType"] == "gpu"


# ---------------------------------------------------------------------------
# TestBohriumSessionCredentials
# ---------------------------------------------------------------------------


class TestBohriumSessionCredentials:
    """Tests for session-backed credential resolution."""

    def test_poll_uses_session_credentials_when_env_missing(
        self, tmp_path, monkeypatch
    ):
        """BohriumTool should work with session credentials even without env vars."""
        monkeypatch.delenv("BOHRIUM_ACCESS_KEY", raising=False)
        monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)

        session = SimpleNamespace(
            _bohrium_credentials={"access_key": "session-ak", "project_id": 42},
            is_open=True,
        )
        tool = BohriumTool(session=session, workdir=tmp_path)

        get_calls = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            get_calls.append((path, access_key))
            return {"data": {"status": 1}}  # Running

        monkeypatch.setattr(bohrium_module, "_get", fake_get)
        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)

        result = asyncio.run(tool.execute({"action": "poll", "job_id": "job-1"}))
        assert result.status == "success"
        assert get_calls[0][1] == "session-ak"  # Used session credential, not env

    def test_poll_remote_share_without_session_errors(self, tmp_path, monkeypatch):
        """poll with /share/ result_dir and no session should error."""
        monkeypatch.delenv("BOHRIUM_ACCESS_KEY", raising=False)
        monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)

        # Provide env credentials so we pass the credential check,
        # but no session -- so path policy rejects the remote path
        _patch_bridge(monkeypatch)
        tool = BohriumTool(workdir=tmp_path)

        result = asyncio.run(
            tool.execute(
                {"action": "poll", "job_id": "job-1", "result_dir": "/share/out"}
            )
        )
        assert result.status == "error"
        assert "remote session" in result.content.lower()

    def test_submit_uses_session_credentials_when_env_missing(
        self, tmp_path, monkeypatch
    ):
        """submit should work with session credentials."""
        monkeypatch.delenv("BOHRIUM_ACCESS_KEY", raising=False)
        monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)

        input_dir = tmp_path / "inputs"
        input_dir.mkdir()
        (input_dir / "input.inp").write_text("data", encoding="utf-8")

        session = SimpleNamespace(
            _bohrium_credentials={"access_key": "session-ak", "project_id": 42},
            is_open=True,
        )
        tool = BohriumTool(session=session, workdir=tmp_path)

        post_calls = []

        def fake_post(base_url, path, access_key, payload, timeout=30):
            post_calls.append((path, access_key))
            if "create" in path:
                return {
                    "code": 0,
                    "data": {
                        "storePath": "p/",
                        "storeHost": "https://s.com",
                        "token": "t",
                        "jobId": "j1",
                    },
                }
            return {"code": 0, "data": {"jobId": "j2", "bohrJobId": "b2"}}

        class FakeTiefblueClient:
            def __init__(self, *, base_url):
                pass

            def upload_from_file_multi_part(self, **kwargs):
                return {}

        sdk_module = types.ModuleType("bohrium_open_sdk")
        opensdk_module = types.ModuleType("bohrium_open_sdk.opensdk")
        tiefblue_module = types.ModuleType("bohrium_open_sdk.opensdk._tiefblue_client")
        tiefblue_module.Tiefblue = FakeTiefblueClient
        sdk_module.opensdk = opensdk_module
        opensdk_module._tiefblue_client = tiefblue_module

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        monkeypatch.setattr(bohrium_module, "_post", fake_post)

        with monkeypatch.context() as m:
            m.setitem(sys.modules, "bohrium_open_sdk", sdk_module)
            m.setitem(sys.modules, "bohrium_open_sdk.opensdk", opensdk_module)
            m.setitem(
                sys.modules,
                "bohrium_open_sdk.opensdk._tiefblue_client",
                tiefblue_module,
            )
            result = asyncio.run(
                tool.execute(
                    {
                        "action": "submit",
                        "input_dir": str(input_dir),
                        "image": "registry.dp.tech/dptech/cp2k:2024.1",
                        "cmd": "cp2k.popt -i input.inp",
                    }
                )
            )

        assert result.status == "success"
        # Verify session credential was used in API calls
        assert post_calls[0][1] == "session-ak"
        assert post_calls[1][1] == "session-ak"

    def test_no_credentials_returns_error(self, tmp_path, monkeypatch):
        """No session and no env vars should return a credential error."""
        monkeypatch.delenv("BOHRIUM_ACCESS_KEY", raising=False)
        monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)
        monkeypatch.delenv("BOHRIUM_BASE_URL", raising=False)

        tool = BohriumTool(workdir=tmp_path)
        result = asyncio.run(tool.execute({"action": "poll", "job_id": "job-1"}))
        assert result.status == "error"
        assert (
            "credential" in result.content.lower()
            or "unavailable" in result.content.lower()
        )

    def test_session_none_falls_back_to_env(self, tmp_path, monkeypatch):
        """session=None should fall back to env credentials via bridge."""
        monkeypatch.setenv("BOHRIUM_ACCESS_KEY", "env-ak")
        monkeypatch.setenv("BOHRIUM_PROJECT_ID", "99")
        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)

        tool = BohriumTool(session=None, workdir=tmp_path)

        get_calls = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            get_calls.append((path, access_key))
            return {"data": {"status": 1}}

        monkeypatch.setattr(bohrium_module, "_get", fake_get)

        result = asyncio.run(tool.execute({"action": "poll", "job_id": "job-1"}))
        assert result.status == "success"
        assert get_calls[0][1] == "env-ak"
