"""tests/matmaster/tools/builtin/test_bohrium_tool.py"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

import matmaster.bohrium.client as bohrium_client_module
import matmaster.tools.builtin.bohrium_tool as bohrium_module
import matmaster.tools.builtin.bohrium_tool.tool as bohrium_tool_module
import matmaster.tools.builtin.bohrium_tool.transfers as bohrium_transfers_module
from matmaster.bohrium.endpoints import use_sandbox
from matmaster.bohrium.errors import BohriumError, BohriumTransferError
from matmaster.bohrium.upload import UploadedArchive
from matmaster.tools.builtin.bohrium_tool import BohriumTool
from matmaster.tools.builtin.bohrium_tool.models import BohriumSubmittedJob
from matmaster.tools.tool_compiler import ToolCompiler
from matmaster.tools.tool_result import ToolResult
from matmaster.types.topology import RuntimeTopology, ToolPlane
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import (
    FakeRemoteSession,
    _fake_cred,
    _fake_submit_post_factory,
    _patch_bridge,
)


def _install_fake_sdk_free_upload(monkeypatch, upload_calls: list) -> None:
    def fake_upload(*, create_data, zip_path, manifest_root=None):
        del manifest_root
        store_path = str(create_data["storePath"]).strip()
        if not store_path.endswith("/"):
            store_path += "/"
        store_host = str(create_data["storeHost"]).rstrip("/")
        token = str(create_data["token"]).strip()
        oss_key = f"{store_path}input.zip"
        upload_calls.append((oss_key, str(zip_path), token))
        return UploadedArchive(
            oss_key=oss_key,
            download_url=(
                f"{store_host}/api/download/{oss_key}?token={token}"
                "&Response-Content-Type=application/octet-stream"
            ),
        )

    monkeypatch.setattr(
        "matmaster.bohrium.upload._upload_input_archive_sdk_free",
        fake_upload,
    )


# ---------------------------------------------------------------------------
# TestBohriumMetadata
# ---------------------------------------------------------------------------


class TestBohriumMetadata:
    def test_name(self):
        assert BohriumTool.name == "Bohrium"

    def test_module_is_loaded_from_bohrium_tool_package(self):
        module_path = Path(bohrium_module.__file__)
        assert module_path.name == "__init__.py"
        assert module_path.parent.name == "bohrium_tool"

    def test_poll_schema_has_no_wait_fields(self):
        properties = BohriumTool.json_schema["properties"]
        assert "wait" not in properties
        assert "max_wait_seconds" not in properties
        assert "poll_interval_seconds" not in properties

    def test_prompt_mentions_list_actions(self, tmp_path):
        tool = BohriumTool(workdir=tmp_path)
        prompt = tool.prompt()
        assert prompt is not None
        assert "skill" in prompt
        assert "image" in prompt
        assert "machine" in prompt
        assert "submit" in prompt
        assert "query" in prompt

    def test_schema_exposes_download_action(self):
        properties = BohriumTool.json_schema["properties"]
        assert "download" in properties["action"]["enum"]
        assert properties["result_dir"]["description"].endswith("(download)")

    def test_schema_exposes_kill_action(self):
        properties = BohriumTool.json_schema["properties"]
        assert "kill" in properties["action"]["enum"]
        assert "kill" in properties["job_id"]["description"]

    def test_capabilities_include_download(self):
        assert BohriumTool.capabilities == frozenset(
            {"bohrium.submit", "bohrium.query", "bohrium.download", "bohrium.kill"}
        )

    def test_prompt_mentions_query_and_download_modes(self, tmp_path):
        tool = BohriumTool(workdir=tmp_path)
        prompt = tool.prompt()
        assert prompt is not None
        assert "**download**" in prompt or "download artifacts" in prompt
        assert "kill" in prompt


# ---------------------------------------------------------------------------
# TestBohriumSandboxMode
# ---------------------------------------------------------------------------


class TestBohriumSandboxMode:
    def test_use_sandbox_defaults_true(self, monkeypatch):
        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        assert use_sandbox() is True

    def test_use_sandbox_disabled_with_zero(self, monkeypatch):
        monkeypatch.setenv("BOHRIUM_USE_SANDBOX", "0")
        assert use_sandbox() is False


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
            bohrium_client_module.requests,
            "get",
            lambda *args, **kwargs: FakeResponse(),
        )

        with caplog.at_level(logging.WARNING):
            with pytest.raises(requests.HTTPError):
                bohrium_client_module._get(
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
            bohrium_client_module.requests,
            "post",
            lambda *args, **kwargs: FakeResponse(),
        )

        with caplog.at_level(logging.WARNING):
            with pytest.raises(requests.HTTPError):
                bohrium_client_module._post(
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
            bohrium_client_module, "_post", _fake_submit_post_factory(post_calls)
        )

        with monkeypatch.context() as m:
            _install_fake_sdk_free_upload(m, upload_calls)
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
        # The job/add curl log intentionally carries the real accessKey so it
        # is directly copy-pasteable; every other log line must keep it masked.
        non_curl_messages = [
            msg for msg in messages if bohrium_client_module.CURL_LOG_PREFIX not in msg
        ]
        assert not any("secret-access-key" in msg for msg in non_curl_messages)

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

    def test_submit_unexpected_context_error_returns_tool_result(
        self, tmp_path, monkeypatch
    ):
        tool = BohriumTool(workdir=tmp_path)

        with patch.object(
            BohriumTool,
            "_build_context",
            side_effect=RuntimeError("boom"),
        ):
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
        assert result.status == "error"
        assert result.content == "Submit failed: boom"

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
            bohrium_client_module, "_post", _fake_submit_post_factory(post_calls)
        )

        with monkeypatch.context() as m:
            _install_fake_sdk_free_upload(m, upload_calls)
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

    def test_submit_remote_share_with_session_uses_direct_remote_upload(
        self, tmp_path, monkeypatch
    ):
        session = FakeRemoteSession(
            existing_paths={"/share/Pd111_submit"},
        )
        tool = BohriumTool(session=session, workdir=tmp_path)
        post_calls: list[tuple[str, dict, str]] = []
        helper_calls: list[tuple[str, dict]] = []

        def fake_remote_helper(session_arg, *, subcommand, payload, timeout=3600):
            del timeout
            assert session_arg is session
            helper_calls.append((subcommand, payload))
            return {
                "schema_version": "v1",
                "ok": True,
                "oss_key": "sandbox/jobs/run-1/input.zip",
            }

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(
            bohrium_client_module, "_post", _fake_submit_post_factory(post_calls)
        )
        monkeypatch.setattr(
            bohrium_transfers_module,
            "run_remote_transfer",
            fake_remote_helper,
        )

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
        assert session.download_calls == []
        assert helper_calls[0][0] == "upload-submit"
        helper_payload = helper_calls[0][1]
        assert helper_payload["transfer_id"].startswith("submit-")
        assert helper_payload | {"transfer_id": "<dynamic>"} == {
            "transfer_id": "<dynamic>",
            "input_dir": "/share/Pd111_submit",
            "store_host": "https://store.example.com",
            "store_path": "sandbox/jobs/run-1/",
            "token": "token-123",
            "object_name": "input.zip",
        }
        assert post_calls[1][1]["cmd"].endswith("> log 2>&1")
        assert post_calls[1][1]["ossPath"][0].startswith(
            "https://store.example.com/api/download/"
            "sandbox%2Fjobs%2Frun-1%2Finput.zip"
        )

    def test_submit_delegates_input_path_resolution_to_bohrium_paths(
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
            bohrium_client_module, "_post", _fake_submit_post_factory(post_calls)
        )

        with (
            monkeypatch.context() as m,
            patch(
                "matmaster.tools.builtin.bohrium_tool.tool.resolve_input_source",
                create=True,
            ) as mock_resolve_input,
        ):
            mock_resolve_input.return_value = SimpleNamespace(
                kind="local_dir",
                raw_path=str(input_dir),
                resolved_path=str(input_dir),
            )
            _install_fake_sdk_free_upload(m, upload_calls)
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
        mock_resolve_input.assert_called_once()

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

    def test_remote_input_dir_upload_failure_prevents_job_add(
        self, tmp_path, monkeypatch
    ):
        session = FakeRemoteSession(
            existing_paths={"/share/Pd111_submit"},
        )
        tool = BohriumTool(session=session, workdir=tmp_path)
        post_calls: list[tuple[str, dict, str]] = []

        def fake_remote_helper(session_arg, *, subcommand, payload, timeout=3600):
            del session_arg, subcommand, payload, timeout
            raise BohriumTransferError("remote helper failed: python3 not found")

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(
            bohrium_client_module, "_post", _fake_submit_post_factory(post_calls)
        )
        monkeypatch.setattr(
            bohrium_transfers_module,
            "run_remote_transfer",
            fake_remote_helper,
        )

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
        assert "compute job was not submitted" in result.content
        assert "created_job_ref=create-job-id" in result.content
        assert "python3 not found" in result.content
        assert [path for path, _, _ in post_calls] == ["/openapi/v1/sandbox/job/create"]

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
        helper_calls: list[tuple[str, dict]] = []

        def fake_remote_helper(session_arg, *, subcommand, payload, timeout=3600):
            del session_arg, timeout
            helper_calls.append((subcommand, payload))
            return {
                "schema_version": "v1",
                "ok": True,
                "oss_key": "sandbox/jobs/run-1/input.zip",
            }

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(
            bohrium_client_module, "_post", _fake_submit_post_factory(post_calls)
        )
        monkeypatch.setattr(
            bohrium_transfers_module,
            "run_remote_transfer",
            fake_remote_helper,
        )

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
        assert session.download_calls == []
        assert helper_calls[0][1]["input_dir"] == "/personal/inputs"

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
            bohrium_client_module, "_post", _fake_submit_post_factory(post_calls)
        )

        with monkeypatch.context() as m:
            _install_fake_sdk_free_upload(m, upload_calls)
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

    def test_submit_optout_normalizes_before_runtime(
        self, tmp_path, monkeypatch
    ):
        tool = BohriumTool(workdir=tmp_path)
        captured: dict[str, object] = {}

        _patch_bridge(monkeypatch)

        def fake_submit_job_via_runtime(**kwargs):
            captured.update(kwargs)
            return BohriumSubmittedJob(job_id="job-123", raw_add_response={})

        monkeypatch.setattr(
            bohrium_tool_module,
            "submit_job_via_runtime",
            fake_submit_job_via_runtime,
        )

        result = tool._submit(
            {
                "action": "submit",
                "input_dir": "inputs",
                "image": "test:latest",
                "cmd": "run",
            }
        )

        assert result.status == "success"
        assert captured["cmd"] == "run > log 2>&1"
        assert captured["machine"] == "c32_m128_cpu"
        assert captured["job_name"] == "matmaster-job"
        assert captured["disk_size"] == 50
        assert result.meta["submit_execution_audit"]["job_id"] == "job-123"

    def test_submit_optout_rejects_oversized_args_before_runtime(
        self, tmp_path, monkeypatch
    ):
        tool = BohriumTool(workdir=tmp_path)
        called = False

        def fake_submit_job_via_runtime(**kwargs):
            nonlocal called
            called = True
            return BohriumSubmittedJob(job_id="job-123", raw_add_response={})

        monkeypatch.setattr(
            bohrium_tool_module,
            "submit_job_via_runtime",
            fake_submit_job_via_runtime,
        )

        result = tool._submit(
            {
                "action": "submit",
                "input_dir": "inputs",
                "image": "test:latest",
                "cmd": "x" * 9000,
            }
        )

        assert result.status == "error"
        assert "too long" in result.content.lower()
        assert called is False

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

        def fake_post(base_url, path, access_key, payload, timeout=30, log_curl=False):
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

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_post", fake_post)
        _install_fake_sdk_free_upload(monkeypatch, upload_calls)

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
        assert payload == {
            "success": True,
            "job_id": "job-123",
            "status": "Submitted",
            "use_sandbox": True,
        }
        assert "bohr_job_id" not in payload
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
        assert upload_calls[0][2] == "token-123"

    def test_submit_job_via_runtime_returns_named_job_model(
        self, tmp_path, monkeypatch
    ):
        input_dir = tmp_path / "inputs"
        input_dir.mkdir()
        (input_dir / "input.inp").write_text("&CONTROL\n", encoding="utf-8")

        post_calls: list[tuple[str, dict]] = []
        upload_calls: list[tuple[str, str, dict]] = []

        def fake_post(base_url, path, access_key, payload, timeout=30, log_curl=False):
            del base_url, access_key, timeout
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

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_post", fake_post)
        _install_fake_sdk_free_upload(monkeypatch, upload_calls)

        submitted = bohrium_tool_module.submit_job_via_runtime(
            input_dir=str(input_dir),
            image="registry.dp.tech/dptech/cp2k:2024.1",
            cmd="cp2k.popt -i input.inp > log 2>&1",
            machine="c64_m256_cpu",
            job_name="matmaster-job",
            disk_size=50,
            workdir=tmp_path,
            session=None,
        )

        assert submitted.job_id == "job-123"
        assert submitted.raw_add_response == {
            "jobId": "job-123",
            "bohrJobId": "bohr-456",
        }
        assert not isinstance(submitted, tuple)

    def test_submit_job_via_runtime_defensive_on_unnormalized_cmd(
        self, tmp_path, monkeypatch
    ):
        input_dir = tmp_path / "inputs"
        input_dir.mkdir()
        (input_dir / "input.inp").write_text("&CONTROL\n", encoding="utf-8")

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(
            bohrium_client_module, "_post", _fake_submit_post_factory([])
        )
        _install_fake_sdk_free_upload(monkeypatch, [])

        with pytest.raises(BohriumError, match="cmd not normalized"):
            bohrium_tool_module.submit_job_via_runtime(
                input_dir=str(input_dir),
                image="registry.dp.tech/dptech/cp2k:2024.1",
                cmd="cp2k.popt -i input.inp",
                machine="c64_m256_cpu",
                job_name="matmaster-job",
                disk_size=50,
                workdir=tmp_path,
                session=None,
            )

    def test_compiled_bohrium_instance_carries_provider(self, tmp_path):
        topology = RuntimeTopology(
            session_kind="local",
            control_root=str(tmp_path),
            workspace_root=str(tmp_path),
            active_planes=frozenset(ToolPlane),
        )

        instance = ToolCompiler().compile(
            BohriumTool(workdir=tmp_path),
            topology,
            source="builtin",
        )

        assert instance.submit_review_provider is BohriumTool.submit_review_provider

    def test_poll_live_log_uses_canonical_job_id(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        calls: list[dict] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            del base_url, access_key, params, timeout
            assert path == "/openapi/v1/sandbox/job/job-123"
            return {"data": {"status": 1}}

        def fake_post(base_url, path, access_key, payload, timeout=30, log_curl=False):
            del base_url, access_key, timeout
            assert path == "/openapi/v1/sandbox/job/file/token"
            calls.append(payload)
            return {
                "code": 0,
                "data": {
                    "host": "https://store.example",
                    "path": "prefix/log",
                    "token": "log-token",
                },
            }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b"line1\nline2\n"

        def fake_urlopen(req, timeout=5):
            del req, timeout
            return FakeResponse()

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_get", fake_get)
        monkeypatch.setattr(bohrium_client_module, "_post", fake_post)
        import urllib.request

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        result = asyncio.run(tool.execute({"action": "query", "job_id": "job-123"}))

        assert result.status == "success"
        payload = json.loads(result.content)
        assert payload["job_id"] == "job-123"
        assert payload["log_tail"] == "line1\nline2"
        assert calls == [{"filePath": "log", "jobId": "job-123"}]

    def test_list_images_filters_and_returns_versions(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        calls: list[tuple[str, str, int, bool]] = []

        def fake_list_images(ctx, *, keyword, max_results):
            calls.append(
                (ctx.credentials.access_key, keyword, max_results, ctx.sandbox)
            )
            return {
                "success": True,
                "keyword": keyword,
                "total_found": 1,
                "returned": 1,
                "images": [
                    {
                        "id": 1,
                        "name": "CP2K",
                        "versions": [{"version": "2024.1"}],
                    }
                ],
            }

        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_tool_module, "list_images", fake_list_images)

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
        assert calls == [("access-key", "cp2k", 5, True)]

    def test_list_machines_filters_by_type_and_keyword(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        calls: list[tuple[str, str, str, int, bool]] = []

        def fake_list_machines(ctx, *, machine_type, keyword, max_results):
            calls.append(
                (
                    ctx.credentials.access_key,
                    machine_type,
                    keyword,
                    max_results,
                    ctx.sandbox,
                )
            )
            return {
                "success": True,
                "type": machine_type,
                "keyword": keyword,
                "total_found": 1,
                "returned": 1,
                "machines": [{"skuEnName": "c6_m60_1 * NVIDIA 4090"}],
            }

        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_tool_module, "list_machines", fake_list_machines)

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
        assert calls == [("access-key", "gpu", "4090", 10, True)]


class TestBohriumKillAction:
    """Tests for the kill action wiring (client helper + registry update)."""

    def test_kill_sandbox_requests_termination(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BOHRIUM_USE_SANDBOX", "1")
        _patch_bridge(monkeypatch)
        tool = BohriumTool(workdir=tmp_path)

        calls: list[tuple[str, dict]] = []

        def fake_post(base_url, path, access_key, payload, *, timeout=30):
            del base_url, access_key, timeout
            calls.append((path, payload))
            return {"code": 0, "data": {"accepted": True}}

        monkeypatch.setattr(bohrium_client_module, "_post", fake_post)
        result = asyncio.run(tool.execute({"action": "kill", "job_id": "abc-123"}))

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert calls == [("/openapi/v1/sandbox/kill/abc-123", {})]
        payload = json.loads(result.content)
        assert payload["status"] == "Terminating"
        assert "query" in payload["message"]

    def test_kill_rejects_non_sandbox(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BOHRIUM_USE_SANDBOX", "0")
        _patch_bridge(monkeypatch)
        tool = BohriumTool(workdir=tmp_path)

        result = asyncio.run(tool.execute({"action": "kill", "job_id": "999"}))
        assert result.status == "error"
        assert "sandbox" in result.content.lower()

    def test_kill_missing_job_id_returns_error(self, tmp_path):
        tool = BohriumTool(workdir=tmp_path)
        result = asyncio.run(tool.execute({"action": "kill"}))
        assert result.status == "error"
        assert "job_id" in result.content
