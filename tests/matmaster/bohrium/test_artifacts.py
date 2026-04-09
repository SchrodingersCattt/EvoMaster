from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from matmaster.bohrium.artifacts import download_job_artifacts
from matmaster.bohrium.types import BohriumContext, BohriumCredentials
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import (
    _FakeDownloadResponse,
)


def _make_ctx(*, sandbox: bool) -> BohriumContext:
    return BohriumContext(
        credentials=BohriumCredentials(
            access_key="ak",
            project_id=42,
            user_id=None,
            user_no="",
            base_url="https://openapi.test.dp.tech",
        ),
        sandbox=sandbox,
        credential_source="env",
    )


def test_download_job_artifacts_preserves_sandbox_zip_object_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result_dir = tmp_path / "results"
    ctx = _make_ctx(sandbox=True)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("log", "done\n")

    monkeypatch.setattr(
        "matmaster.bohrium.artifacts.requests.get",
        lambda url, timeout=300, stream=True: _FakeDownloadResponse(
            content=buffer.getvalue()
        ),
    )
    monkeypatch.setattr(
        "matmaster.bohrium.artifacts.requests.post",
        lambda *args, **kwargs: _FakeDownloadResponse(
            json_data={
                "code": 0,
                "data": {
                    "objects": [{"path": "prefix/job-1.zip", "isDir": False}],
                    "hasNext": False,
                    "nextToken": "",
                },
            }
        ),
    )

    files, log_tail = download_job_artifacts(
        job_id="job-1",
        detail_data={
            "resultUrl": "https://store.example/api/download/prefix/job-1.zip?token=root-token"
        },
        result_dir=result_dir,
        ctx=ctx,
    )

    assert "log" in files
    assert "done" in log_tail


def test_download_job_artifacts_returns_bad_zip_marker_without_crashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result_dir = tmp_path / "results"
    ctx = _make_ctx(sandbox=False)

    monkeypatch.setattr(
        "matmaster.bohrium.artifacts.requests.get",
        lambda url, timeout=300, stream=True: _FakeDownloadResponse(
            content=b"not-a-zip"
        ),
    )

    files, log_tail = download_job_artifacts(
        job_id=1,
        detail_data={"resultUrl": "https://store.example/out.zip"},
        result_dir=result_dir,
        ctx=ctx,
    )

    assert files == ["(bad zip: out.zip)"]
    assert log_tail == "(no log file found in result directory)"
