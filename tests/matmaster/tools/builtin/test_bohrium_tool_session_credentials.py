"""Session credential tests for the builtin Bohrium tool."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import matmaster.bohrium.client as bohrium_client_module
from matmaster.bohrium.runtime import BohriumRuntimeHandle, attach_runtime
from matmaster.bohrium.types import BohriumCredentials, BohriumExecutionContext
from matmaster.bohrium.upload import UploadedArchive
from matmaster.tools.builtin.bohrium_tool import BohriumTool
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import _patch_bridge


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
        """poll no longer accepts result_dir - directs to download action."""
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
        upload_calls = []

        def fake_post(base_url, path, access_key, payload, timeout=30, log_curl=False):
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

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
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

        assert result.status == "success"
        # Verify session credential was used in API calls.
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
