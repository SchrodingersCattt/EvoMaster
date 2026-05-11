from __future__ import annotations

from pathlib import Path

import pytest
from matmaster_bohrium_transfer.errors import TransferError

from matmaster.bohrium.artifacts import download_job_artifacts
from matmaster.bohrium.types import (
    BohriumContext,
    BohriumCredentials,
)
from matmaster.tools.builtin.bohrium_tool.models import (
    BohriumDownloadTarget,
    BohriumInputSource,
)
from matmaster.tools.builtin.bohrium_tool.transfers import (
    prepare_input_archive,
    publish_download_target,
    upload_input_source,
)
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import FakeRemoteSession


def test_prepare_input_archive_rejects_remote_share_zip_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del tmp_path, monkeypatch
    session = FakeRemoteSession()
    source = BohriumInputSource(
        kind="remote_share_dir",
        raw_path="/share/input",
        resolved_path="/share/input",
    )

    with pytest.raises(Exception, match="direct remote upload"):
        with prepare_input_archive(source, session=session):
            pass


def test_upload_input_source_uses_remote_helper_without_session_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeRemoteSession()
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

    monkeypatch.setattr(
        "matmaster.tools.builtin.bohrium_tool.transfers.run_remote_transfer",
        fake_remote_helper,
    )
    source = BohriumInputSource(
        kind="remote_share_dir",
        raw_path="/share/input",
        resolved_path="/share/input",
    )

    upload = upload_input_source(
        source,
        create_data={
            "storePath": "sandbox/jobs/run-1/",
            "storeHost": "https://store.example.com",
            "token": "token-123",
        },
        session=session,
    )

    assert upload.oss_key == "sandbox/jobs/run-1/input.zip"
    assert upload.download_url.startswith("https://store.example.com/api/download/")
    assert session.download_calls == []
    assert helper_calls[0][0] == "upload-submit"
    assert helper_calls[0][1]["input_dir"] == "/share/input"


def test_upload_input_source_adds_transfer_id_and_encodes_download_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeRemoteSession()
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

    monkeypatch.setattr(
        "matmaster.tools.builtin.bohrium_tool.transfers.run_remote_transfer",
        fake_remote_helper,
    )
    source = BohriumInputSource(
        kind="remote_share_dir",
        raw_path="/share/input",
        resolved_path="/share/input",
    )

    upload = upload_input_source(
        source,
        create_data={
            "storePath": "sandbox/jobs/run-1/",
            "storeHost": "https://store.example.com",
            "token": "a+b/c==",
        },
        session=session,
    )

    helper_payload = helper_calls[0][1]
    assert helper_payload["transfer_id"]
    assert helper_payload["transfer_id"].startswith("submit-")
    assert "a+b/c==" not in upload.download_url
    assert "token=a%2Bb%2Fc%3D%3D" in upload.download_url


def test_publish_download_target_remote_direct_does_not_upload(
    tmp_path: Path,
) -> None:
    session = FakeRemoteSession(is_open=True)
    target = BohriumDownloadTarget(
        kind="remote_share_dir",
        raw_path="/share/results",
        resolved_path="/share/results",
        staging_dir=Path("/share/results"),
        publish_mode="remote_direct",
    )

    result_dir = publish_download_target(target, session=session)

    assert result_dir == "/share/results"
    assert session.upload_calls == []


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

    captured: dict = {}

    def fake_run_download_results_payload(payload):
        captured.update(payload)
        return {"files": ["log"], "log_tail": "done\n"}

    monkeypatch.setattr(
        "matmaster.bohrium.artifacts.run_download_results_payload",
        fake_run_download_results_payload,
    )

    files, log_tail = download_job_artifacts(
        job_id="job-1",
        detail_data={
            "resultUrl": (
                "https://store.example/api/download/"
                "prefix/job-1.zip?token=root-token"
            )
        },
        result_dir=target.staging_dir,
        ctx=ctx,
    )

    assert "log" in files
    assert "done" in log_tail
    assert captured["sandbox"] is True
    assert captured["detail_data"]["resultUrl"].endswith("token=root-token")


def test_download_job_artifacts_treats_bad_zip_as_transfer_failure(
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

    def fake_run_download_results_payload(payload):
        del payload
        raise TransferError("download_verify", "invalid zip archive")

    monkeypatch.setattr(
        "matmaster.bohrium.artifacts.run_download_results_payload",
        fake_run_download_results_payload,
    )

    with pytest.raises(TransferError, match="invalid zip"):
        download_job_artifacts(
            job_id=1,
            detail_data={"resultUrl": "https://store.example/out.zip"},
            result_dir=target.staging_dir,
            ctx=ctx,
        )
