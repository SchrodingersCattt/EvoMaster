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
from matmaster.bohrium.errors import BohriumTransferError
from matmaster.bohrium.runtime import BohriumRuntimeHandle, attach_runtime
from matmaster.bohrium.types import BohriumCredentials, BohriumExecutionContext
from matmaster.tools.builtin.bohrium_tool import BohriumTool
from matmaster.tools.tool_result import ToolResult
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import (
    FakeRemoteSession,
    _fake_cred,
    _fake_submit_post_factory,
    _install_fake_tiefblue,
    _patch_bridge,
)


def _session_with_runtime(
    *,
    access_key: str = "session-ak",
    project_id: int = 42,
) -> SimpleNamespace:
    session = SimpleNamespace(is_open=True)
    attach_runtime(
        session,
        BohriumRuntimeHandle(
            credentials=BohriumCredentials(
                access_key=access_key,
                project_id=project_id,
                user_id=7,
                user_no="U001",
                base_url="https://openapi.test.dp.tech",
            ),
            execution=BohriumExecutionContext(
                session_type="ssh",
                execution_workdir="/share",
                remote_workspace_root="/share",
                remote_project_root="/share/.matmaster",
                node_id=1,
                node_ip="10.0.0.1",
                ssh_attached=True,
            ),
            execution_session=session,
        ),
    )
    return session


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
        assert "poll" in prompt

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

    def test_prompt_mentions_poll_and_download_modes(self, tmp_path):
        tool = BohriumTool(workdir=tmp_path)
        prompt = tool.prompt()
        assert prompt is not None
        assert "single-shot" in prompt
        assert "**download**" in prompt or "download artifacts" in prompt
        assert "does not download artifacts" in prompt
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
            "run_remote_helper",
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
        assert helper_calls == [
            (
                "upload-submit",
                {
                    "input_dir": "/share/Pd111_submit",
                    "store_host": "https://store.example.com",
                    "store_path": "sandbox/jobs/run-1/",
                    "token": "token-123",
                    "object_name": "input.zip",
                },
            )
        ]
        assert post_calls[1][1]["cmd"].endswith("> log 2>&1")
        assert post_calls[1][1]["ossPath"][0].startswith(
            "https://store.example.com/api/download/sandbox/jobs/run-1/input.zip"
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
            "run_remote_helper",
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
            "run_remote_helper",
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
            def __init__(self, base_url=None):
                self.base_url = base_url

            def upload_From_file_multi_part(
                self, object_key, file_path, token="", progress_bar=False, **kw
            ):
                upload_calls.append((object_key, file_path, token))
                assert progress_bar is False
                return None

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_post", fake_post)
        monkeypatch.setattr(
            "matmaster.bohrium.upload._tiefblue_cls", None, raising=False
        )
        monkeypatch.setattr(
            "matmaster.bohrium.upload._load_tiefblue_client",
            lambda: FakeTiefblueClient,
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
        assert upload_calls[0][2] == "token-123"

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

        session = _session_with_runtime()
        tool = BohriumTool(session=session, workdir=tmp_path)

        get_calls = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            get_calls.append((path, access_key))
            return {"data": {"status": 1}}  # Running

        monkeypatch.setattr(bohrium_client_module, "_get", fake_get)
        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)

        result = asyncio.run(tool.execute({"action": "poll", "job_id": "job-1"}))
        assert result.status == "success"
        assert get_calls[0][1] == "session-ak"  # Used session credential, not env

    def test_poll_rejects_result_dir_parameter(self, tmp_path, monkeypatch):
        """poll no longer accepts result_dir — directs to download action."""
        monkeypatch.delenv("BOHRIUM_ACCESS_KEY", raising=False)
        monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)

        _patch_bridge(monkeypatch)
        tool = BohriumTool(workdir=tmp_path)

        result = asyncio.run(
            tool.execute(
                {"action": "poll", "job_id": "job-1", "result_dir": "/share/out"}
            )
        )
        assert result.status == "error"
        assert "no longer downloads artifacts" in result.content
        assert 'action="download"' in result.content

    def test_submit_uses_session_credentials_when_env_missing(
        self, tmp_path, monkeypatch
    ):
        """submit should work with session credentials."""
        monkeypatch.delenv("BOHRIUM_ACCESS_KEY", raising=False)
        monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)

        input_dir = tmp_path / "inputs"
        input_dir.mkdir()
        (input_dir / "input.inp").write_text("data", encoding="utf-8")

        session = _session_with_runtime()
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
            def __init__(self, base_url=None):
                pass

            def upload_From_file_multi_part(self, *args, **kwargs):
                return None

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        monkeypatch.setattr(bohrium_client_module, "_post", fake_post)
        monkeypatch.setattr(
            "matmaster.bohrium.upload._tiefblue_cls", None, raising=False
        )
        monkeypatch.setattr(
            "matmaster.bohrium.upload._load_tiefblue_client",
            lambda: FakeTiefblueClient,
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

        monkeypatch.setattr(bohrium_client_module, "_get", fake_get)

        result = asyncio.run(tool.execute({"action": "poll", "job_id": "job-1"}))
        assert result.status == "success"
        assert get_calls[0][1] == "env-ak"


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
        assert "poll" in payload["message"]

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
