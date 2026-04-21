from __future__ import annotations

from pathlib import Path

import pytest

from matmaster.bohrium.artifacts import download_job_artifacts
from matmaster.bohrium.types import BohriumContext, BohriumCredentials


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
            "resultUrl": "https://store.example/api/download/prefix/job-1.zip?token=root-token"
        },
        result_dir=result_dir,
        ctx=ctx,
    )

    assert "log" in files
    assert "done" in log_tail
    assert captured["sandbox"] is True
    assert captured["detail_data"]["resultUrl"].endswith("token=root-token")


def test_download_job_artifacts_returns_bad_zip_marker_without_crashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result_dir = tmp_path / "results"
    ctx = _make_ctx(sandbox=False)

    def fake_run_download_results_payload(payload):
        return {
            "files": ["(bad zip: out.zip)"],
            "log_tail": "(no log file found in result directory)",
        }

    monkeypatch.setattr(
        "matmaster.bohrium.artifacts.run_download_results_payload",
        fake_run_download_results_payload,
    )

    files, log_tail = download_job_artifacts(
        job_id=1,
        detail_data={"resultUrl": "https://store.example/out.zip"},
        result_dir=result_dir,
        ctx=ctx,
    )

    assert files == ["(bad zip: out.zip)"]
    assert log_tail == "(no log file found in result directory)"


def test_download_job_artifacts_delegates_to_transfer_package(tmp_path, monkeypatch):
    from matmaster.bohrium.artifacts import download_job_artifacts

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

    def fake_get_file_token(ctx, *, file_path, bohr_job_id):
        return "https://store.example", "prefix/log", "log-token"

    def fake_run_download_results_payload(payload):
        captured.update(payload)
        return {
            "ok": True,
            "files": ["log"],
            "log_tail": "done",
            "result_dir": str(tmp_path / "results"),
        }

    monkeypatch.setattr(
        "matmaster.bohrium.artifacts.get_file_token", fake_get_file_token
    )
    monkeypatch.setattr(
        "matmaster.bohrium.artifacts.run_download_results_payload",
        fake_run_download_results_payload,
    )

    files, log_tail = download_job_artifacts(
        job_id="job-55",
        detail_data={
            "resultUrl": "https://store.example/api/download/prefix/job-55.zip?token=t"
        },
        result_dir=tmp_path / "results",
        ctx=ctx,
    )

    assert files == ["log"]
    assert log_tail == "done"
    assert captured["job_id"] == "job-55"
    assert captured["sandbox"] is True
    assert captured["sandbox_log_file"] == {
        "host": "https://store.example",
        "path": "prefix/log",
        "token": "log-token",
    }
