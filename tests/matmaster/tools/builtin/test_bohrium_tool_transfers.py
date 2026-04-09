from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from matmaster.bohrium.artifacts import download_job_artifacts
from matmaster.bohrium.types import (
    BohriumContext,
    BohriumCredentials,
)
from matmaster.tools.builtin.bohrium_tool.models import (
    BohriumDownloadTarget,
    BohriumInputSource,
)
from matmaster.tools.builtin.bohrium_tool.transfers import prepare_input_archive, publish_download_target
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import (
    FakeRemoteSession,
    _FakeDownloadResponse,
)


def test_prepare_input_archive_downloads_remote_share_zip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del tmp_path, monkeypatch
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("INPUT", "data")

    session = FakeRemoteSession(downloads={"/tmp/remote.zip": buffer.getvalue()})
    source = BohriumInputSource(
        kind="remote_share_dir",
        raw_path="/share/input",
        resolved_path="/share/input",
    )

    with prepare_input_archive(source, session=session) as zip_path:
        assert zip_path.name == "input.zip"
        assert session.exec_calls
        assert len(session.download_calls) == 1
        assert session.download_calls[0].startswith("/tmp/bohrium_input_")


def test_publish_download_target_uploads_remote_share_and_returns_remote_dir(
    tmp_path: Path,
) -> None:
    session = FakeRemoteSession(is_open=True)
    staging_dir = tmp_path / "download-stage"
    staging_dir.mkdir()
    (staging_dir / "log").write_text("done\n", encoding="utf-8")
    target = BohriumDownloadTarget(
        kind="remote_share_dir",
        raw_path="/share/results",
        resolved_path="/share/results",
        staging_dir=staging_dir,
        publish_mode="staged_upload",
    )

    result_dir = publish_download_target(target, session=session)

    assert result_dir == "/share/results"
    assert session.upload_calls[0][1] == "/share/results"


def test_download_job_artifacts_preserves_sandbox_zip_object_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = BohriumDownloadTarget(
        kind="remote_share_dir",
        raw_path="/share/results",
        resolved_path="/share/results",
        staging_dir=tmp_path / "results",
        publish_mode="staged_upload",
    )
    ctx = BohriumContext(
        credentials=BohriumCredentials(
            access_key="ak",
            project_id=42,
            user_id=None,
            user_no="",
            base_url="https://openapi.test.dp.tech",
        ),
        credential_source="env",
        sandbox=True,
    )

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
        result_dir=target.staging_dir,
        ctx=ctx,
    )

    assert "log" in files
    assert "done" in log_tail


def test_download_job_artifacts_returns_bad_zip_marker_without_crashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = BohriumDownloadTarget(
        kind="local_dir",
        raw_path=str(tmp_path / "results"),
        resolved_path=str(tmp_path / "results"),
        staging_dir=tmp_path / "results",
        publish_mode="direct",
    )
    ctx = BohriumContext(
        credentials=BohriumCredentials(
            access_key="ak",
            project_id=42,
            user_id=None,
            user_no="",
            base_url="https://openapi.test.dp.tech",
        ),
        credential_source="env",
        sandbox=False,
    )
    monkeypatch.setattr(
        "matmaster.bohrium.artifacts.requests.get",
        lambda url, timeout=300, stream=True: _FakeDownloadResponse(
            content=b"not-a-zip"
        ),
    )

    files, log_tail = download_job_artifacts(
        job_id=1,
        detail_data={"resultUrl": "https://store.example/out.zip"},
        result_dir=target.staging_dir,
        ctx=ctx,
    )

    assert files == ["(bad zip: out.zip)"]
    assert log_tail == "(no log file found in result directory)"
