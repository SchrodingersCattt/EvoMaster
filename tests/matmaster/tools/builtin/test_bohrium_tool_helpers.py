"""Shared helpers for Bohrium builtin tool tests."""

from __future__ import annotations

import sys
import types

from matmaster.integration.runtime_bridge.models import ResolvedCredential


def _fake_cred(
    access_key: str = "access-key",
    project_id: int = 42,
    source: str = "env",
    base_url: str = "https://openapi.test.dp.tech",
) -> ResolvedCredential:
    """Build a ResolvedCredential for monkeypatching."""
    return ResolvedCredential(
        service="bohrium",
        source=source,
        values={
            "access_key": access_key,
            "project_id": project_id,
            "base_url": base_url,
        },
    )


def _patch_bridge(monkeypatch, cred: ResolvedCredential | None = None):
    """Monkeypatch resolve_bohrium_credentials at the adapter module."""
    import matmaster.integration.runtime_bridge.adapters.bohrium as adapter_mod

    monkeypatch.setattr(
        adapter_mod,
        "resolve_bohrium_credentials",
        lambda session=None, explicit=None: cred or _fake_cred(),
    )


def _install_fake_tiefblue(monkeypatch, upload_calls: list[tuple[str, str, dict]]):
    """Install a fake Tiefblue SDK module into sys.modules."""

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

    monkeypatch.setitem(sys.modules, "bohrium_open_sdk", sdk_module)
    monkeypatch.setitem(sys.modules, "bohrium_open_sdk.opensdk", opensdk_module)
    monkeypatch.setitem(
        sys.modules,
        "bohrium_open_sdk.opensdk._tiefblue_client",
        tiefblue_module,
    )


def _fake_submit_post_factory(post_calls: list[tuple[str, dict, str]]):
    """Build a fake submit API handler recording payloads and credentials."""

    def fake_post(base_url, path, access_key, payload, timeout=30):
        post_calls.append((path, payload, access_key))
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

    return fake_post


class FakeRemoteSession:
    """Minimal fake remote session for Bohrium submit tests."""

    def __init__(
        self,
        *,
        existing_paths: set[str] | None = None,
        file_paths: set[str] | None = None,
        downloads: dict[str, bytes] | None = None,
        is_open: bool = True,
        exec_result: dict | None = None,
        default_download: bytes = b'zip-bytes',
    ):
        self._existing_paths = existing_paths or set()
        self._file_paths = file_paths or set()
        self._downloads = downloads or {}
        self._default_download = default_download
        self.is_open = is_open
        self.exec_calls: list[str] = []
        self.download_calls: list[str] = []

        self._exec_result = exec_result or {
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
            "working_dir": "/share",
            "output": "",
        }

    def path_exists(self, path: str) -> bool:
        return path in self._existing_paths

    def is_file(self, path: str) -> bool:
        return path in self._file_paths

    def exec_bash(self, command: str, timeout=None, cancel_token=None) -> dict:
        self.exec_calls.append(command)
        return dict(self._exec_result)

    def download(self, path: str, timeout=None) -> bytes:
        self.download_calls.append(path)
        if path in self._downloads:
            return self._downloads[path]
        return self._default_download
