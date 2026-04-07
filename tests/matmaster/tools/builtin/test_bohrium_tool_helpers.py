"""Shared helpers for Bohrium builtin tool tests."""

from __future__ import annotations

import io
import sys
import types
import zipfile

import requests

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
    """Monkeypatch resolve_bohrium_credentials for adapter and Bohrium tool."""
    import matmaster.integration.runtime_bridge.adapters.bohrium as adapter_mod
    import matmaster.tools.builtin.bohrium_tool.tool as tool_mod

    def resolver(session=None, explicit=None):
        return cred or _fake_cred()

    monkeypatch.setattr(adapter_mod, "resolve_bohrium_credentials", resolver)
    monkeypatch.setattr(tool_mod, "resolve_bohrium_credentials", resolver)


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
        self.upload_calls: list[tuple[str, str, object]] = []
        self.upload_error: Exception | None = None

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

    def upload_directory(self, local_dir: str, remote_dir: str, exclude=None) -> None:
        self.upload_calls.append((local_dir, remote_dir, exclude))
        if self.upload_error is not None:
            raise self.upload_error


def _zip_bytes(files: dict[str, str]) -> bytes:
    """Build an in-memory zip for sandbox download tests."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


class _FakeDownloadResponse:
    """Minimal requests-like response used by sandbox download tests."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: dict[str, object] | None = None,
        content: bytes | None = None,
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data or {}
        self._content = content if content is not None else b''
        self.ok = status_code < 400
        self.text = ''

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f'{self.status_code} Client Error')

    def json(self) -> dict[str, object]:
        return self._json_data

    def iter_content(self, chunk_size: int = 8192):
        del chunk_size
        if self._content:
            yield self._content
