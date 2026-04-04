"""tests/matmaster/tools/builtin/test_bohrium_tool.py"""

from __future__ import annotations

import asyncio
import json
import sys
import types

import matmaster.tools.builtin.bohrium_tool as bohrium_module
from matmaster.tools.builtin.bohrium_tool import BohriumTool, _use_sandbox
from matmaster.tools.tool_result import ToolResult


class TestBohriumMetadata:
    def test_name(self):
        assert BohriumTool.name == "Bohrium"

    def test_prompt_mentions_list_actions(self, tmp_path):
        tool = BohriumTool(workdir=tmp_path)
        prompt = tool.prompt()
        assert prompt is not None
        assert "Bohrium" in prompt
        assert "list_images" in prompt
        assert "list_machines" in prompt


class TestBohriumSandboxMode:
    def test_use_sandbox_defaults_true(self, monkeypatch):
        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        assert _use_sandbox() is True

    def test_use_sandbox_disabled_with_zero(self, monkeypatch):
        monkeypatch.setenv("BOHRIUM_USE_SANDBOX", "0")
        assert _use_sandbox() is False


class TestBohriumExecution:
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
        monkeypatch.setattr(
            bohrium_module,
            "_resolve_env",
            lambda: ("access-key", 42, "https://open.bohrium.com"),
        )
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
        monkeypatch.setattr(
            bohrium_module,
            "_resolve_env",
            lambda: ("access-key", 42, "https://open.bohrium.com"),
        )
        monkeypatch.setattr(bohrium_module, "_get", fake_get)

        result = asyncio.run(tool.execute({"action": "poll", "job_id": "job-123"}))

        assert isinstance(result, ToolResult)
        assert result.status == "success"
        payload = json.loads(result.content)
        assert payload["job_id"] == "job-123"
        assert payload["status"] == "Running"
        assert get_calls == ["/openapi/v1/sandbox/job/job-123"]

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

        monkeypatch.setattr(
            bohrium_module,
            "_resolve_env",
            lambda: ("access-key", 42, "https://open.bohrium.com"),
        )
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

        monkeypatch.setattr(
            bohrium_module,
            "_resolve_env",
            lambda: ("access-key", 42, "https://open.bohrium.com"),
        )
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
