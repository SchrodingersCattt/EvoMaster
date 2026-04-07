from __future__ import annotations

import pytest

from matmaster.tools.builtin.bohrium_tool.api import (
    add_job,
    create_job,
)
from matmaster.tools.builtin.bohrium_tool.models import BohriumContext
from matmaster.tools.builtin.bohrium_tool.open_sdk import UploadedArchive


def test_create_job_uses_sandbox_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_post(base_url, path, access_key, payload, timeout=30):
        del base_url, access_key, timeout
        calls.append((path, payload))
        return {"code": 0, "data": {"jobId": "job-1"}}

    monkeypatch.setattr(
        "matmaster.tools.builtin.bohrium_tool.api._post",
        fake_post,
    )
    ctx = BohriumContext(
        access_key="ak",
        project_id=42,
        base_url="https://openapi.test.dp.tech",
        credential_source="env",
        sandbox=True,
    )

    create_job(ctx, job_name="demo")

    assert calls == [("/openapi/v1/sandbox/job/create", {"projectId": 42, "name": "demo"})]


def test_add_job_uses_uploaded_download_url_for_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def fake_post(base_url, path, access_key, payload, timeout=30):
        del base_url, access_key, timeout
        assert path == "/openapi/v1/sandbox/job/add"
        calls.append(payload)
        return {"code": 0, "data": {"jobId": "job-2", "bohrJobId": "bohr-2"}}

    monkeypatch.setattr(
        "matmaster.tools.builtin.bohrium_tool.api._post",
        fake_post,
    )
    ctx = BohriumContext(
        access_key="ak",
        project_id=42,
        base_url="https://openapi.test.dp.tech",
        credential_source="env",
        sandbox=True,
    )

    add_job(
        ctx,
        create_data={"jobId": "job-create"},
        upload=UploadedArchive(
            oss_key="sandbox/jobs/run-1/input.zip",
            download_url="https://store.example.com/api/download/input.zip?token=abc",
        ),
        image="demo:latest",
        cmd="python run.py > log 2>&1",
        machine="c32_m128_cpu",
        job_name="demo",
        disk_size=50,
    )

    assert calls[0]["ossPath"] == [
        "https://store.example.com/api/download/input.zip?token=abc"
    ]
