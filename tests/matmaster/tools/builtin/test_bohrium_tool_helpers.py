"""Shared helpers for Bohrium builtin tool tests."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from types import SimpleNamespace

import requests

from matmaster.bohrium.endpoints import use_sandbox
from matmaster.bohrium.runtime import BohriumRuntimeHandle, attach_runtime
from matmaster.bohrium.types import (
    BohriumContext,
    BohriumCredentials,
    BohriumExecutionContext,
)


@dataclass(frozen=True)
class FakeCredentialSpec:
    access_key: str = "access-key"
    project_id: int = 42
    source: str = "env"
    base_url: str = "https://openapi.test.dp.tech"
    user_id: int | None = 7
    user_no: str = "U001"


def _fake_cred(
    access_key: str = "access-key",
    project_id: int = 42,
    source: str = "env",
    base_url: str = "https://openapi.test.dp.tech",
) -> FakeCredentialSpec:
    """Build a fake Bohrium credential spec for monkeypatching."""
    return FakeCredentialSpec(
        access_key=access_key,
        project_id=project_id,
        source=source,
        base_url=base_url,
    )


def _patch_bridge(monkeypatch, cred: FakeCredentialSpec | None = None):
    """Monkeypatch Bohrium context construction for builtin-tool tests."""
    import matmaster.tools.builtin.bohrium_tool.tool as tool_mod

    fake_cred = cred or _fake_cred()

    def build_ctx(*, session=None, require_project: bool = False):
        ctx = BohriumContext.from_credentials(
            BohriumCredentials(
                access_key=fake_cred.access_key,
                project_id=fake_cred.project_id,
                user_id=fake_cred.user_id,
                user_no=fake_cred.user_no,
                base_url=fake_cred.base_url,
            ),
            sandbox=use_sandbox(),
            source=fake_cred.source,
        )
        if require_project and ctx.credentials.project_id <= 0:
            raise tool_mod.BohriumError(
                "Bohrium project ID unavailable. Provide via session or BOHRIUM_PROJECT_ID."
            )
        return ctx

    monkeypatch.setattr(tool_mod, "build_bohrium_context", build_ctx)


def _install_fake_tiefblue(monkeypatch, upload_calls: list[tuple[str, str, str]]):
    """Install a fake Tiefblue client via _load_tiefblue_client."""

    class FakeTiefblueClient:
        def __init__(self, base_url=None):
            self.base_url = base_url

        def upload_From_file_multi_part(
            self,
            object_key,
            file_path,
            token="",
            progress_bar=False,
            **kwargs,
        ):
            upload_calls.append((object_key, file_path, token))
            assert progress_bar is False
            return None

    monkeypatch.setattr("matmaster.bohrium.upload._tiefblue_cls", None, raising=False)
    monkeypatch.setattr(
        "matmaster.bohrium.upload._load_tiefblue_client",
        lambda: FakeTiefblueClient,
    )


def _fake_submit_post_factory(post_calls: list[tuple[str, dict, str]]):
    """Build a fake submit API handler recording payloads and credentials."""

    def fake_post(base_url, path, access_key, payload, timeout=30, log_curl=False):
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


def test_build_bohrium_context_reads_runtime_handle() -> None:
    from matmaster.bohrium.credentials import build_bohrium_context

    session = SimpleNamespace()
    attach_runtime(
        session,
        BohriumRuntimeHandle(
            credentials=BohriumCredentials(
                access_key="ak",
                project_id=42,
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

    ctx = build_bohrium_context(session=session, require_project=True)

    assert ctx.credentials.project_id == 42


def test_build_bohrium_context_falls_back_to_env(monkeypatch) -> None:
    from matmaster.bohrium.credentials import build_bohrium_context

    monkeypatch.setenv("BOHRIUM_ACCESS_KEY", "env-ak")
    monkeypatch.setenv("BOHRIUM_PROJECT_ID", "9")
    monkeypatch.delenv("BOHRIUM_BASE_URL", raising=False)

    ctx = build_bohrium_context(session=None, require_project=True)

    assert ctx.credentials.access_key == "env-ak"
    assert ctx.credentials.project_id == 9
    assert ctx.credential_source == "env"


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
        default_download: bytes = b"zip-bytes",
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
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
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
        self._content = content if content is not None else b""
        self.ok = status_code < 400
        self.text = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error")

    def json(self) -> dict[str, object]:
        return self._json_data

    def iter_content(self, chunk_size: int = 8192):
        del chunk_size
        if self._content:
            yield self._content
