from __future__ import annotations

import logging

import pytest

import matmaster.bohrium.client as client_module
from matmaster.bohrium.client import (
    add_job,
    confirm_terminal_status,
    create_job,
    get_file_token,
    get_job_detail,
    list_images,
    list_machines,
    mask_secret,
    terminate_job,
)
from matmaster.bohrium.errors import BohriumAPIError
from matmaster.bohrium.types import BohriumContext, BohriumCredentials
from matmaster.bohrium.upload import UploadedArchive
from src.utils.logger import LogContext


def _make_ctx(*, sandbox: bool = True) -> BohriumContext:
    return BohriumContext(
        credentials=BohriumCredentials(
            access_key="ak",
            project_id=42,
            user_id=7,
            user_no="U001",
            base_url="https://openapi.test.dp.tech",
        ),
        sandbox=sandbox,
        credential_source="env",
    )


def test_create_job_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict, bool]] = []

    def fake_post(base_url, path, access_key, payload, *, timeout=30, log_curl=False):
        del base_url, access_key, timeout
        calls.append((path, payload, log_curl))
        return {"code": 0, "data": {"jobId": "job-1"}}

    monkeypatch.setattr("matmaster.bohrium.client._post", fake_post)

    create_job(_make_ctx(sandbox=True), job_name="demo")

    assert calls == [
        ("/openapi/v1/sandbox/job/create", {"projectId": 42, "name": "demo"}, True)
    ]


def test_create_job_non_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict, bool]] = []

    def fake_post(base_url, path, access_key, payload, *, timeout=30, log_curl=False):
        del base_url, access_key, timeout
        calls.append((path, payload, log_curl))
        return {"code": 0, "data": {"jobId": "job-1"}}

    monkeypatch.setattr("matmaster.bohrium.client._post", fake_post)

    create_job(_make_ctx(sandbox=False), job_name="demo")

    assert calls[0][0] == "/openapi/v1/job/create"
    assert calls[0][2] is True


def test_get_job_detail_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(base_url, path, access_key, *, params=None, timeout=30):
        del base_url, access_key, params, timeout
        assert path == "/openapi/v1/sandbox/job/123"
        return {"data": {"status": 1}}

    monkeypatch.setattr("matmaster.bohrium.client._get", fake_get)
    result = get_job_detail(_make_ctx(sandbox=True), job_id="123")
    assert result["status"] == 1


def test_get_job_detail_non_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(base_url, path, access_key, *, params=None, timeout=30):
        del base_url, access_key, params, timeout
        assert path == "/openapi/v1/job/456"
        return {"data": {"status": 2}}

    monkeypatch.setattr("matmaster.bohrium.client._get", fake_get)
    result = get_job_detail(_make_ctx(sandbox=False), job_id=456)
    assert result["status"] == 2


def test_get_file_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(base_url, path, access_key, payload, *, timeout=30):
        del base_url, access_key, timeout
        assert "file/token" in path
        assert payload == {"filePath": "log", "jobId": "1"}
        return {"data": {"host": "h", "path": "p", "token": "t"}}

    monkeypatch.setattr("matmaster.bohrium.client._post", fake_post)
    host, path, token = get_file_token(_make_ctx(), file_path="log", job_id="1")
    assert (host, path, token) == ("h", "p", "t")


def test_terminate_job_sandbox_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_post(base_url, path, access_key, payload, *, timeout=30):
        del base_url, access_key, timeout
        calls.append((path, payload))
        return {"code": 0, "data": {"requested": True}}

    monkeypatch.setattr("matmaster.bohrium.client._post", fake_post)
    result = terminate_job(_make_ctx(sandbox=True), job_id="999")

    assert calls == [("/openapi/v1/sandbox/kill/999", {})]
    assert result == {"requested": True}


def test_terminate_job_rejects_non_sandbox() -> None:
    with pytest.raises(BohriumAPIError, match="sandbox"):
        terminate_job(_make_ctx(sandbox=False), job_id="999")


def test_terminate_job_requires_job_id() -> None:
    with pytest.raises(BohriumAPIError, match="non-empty job_id"):
        terminate_job(_make_ctx(sandbox=True), job_id="")


def test_terminate_job_raises_on_error_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(base_url, path, access_key, payload, *, timeout=30):
        del base_url, path, access_key, payload, timeout
        return {"code": 42, "message": "job not found"}

    monkeypatch.setattr("matmaster.bohrium.client._post", fake_post)
    with pytest.raises(BohriumAPIError, match="kill failed"):
        terminate_job(_make_ctx(sandbox=True), job_id="999")


def test_mask_secret_masks_common_cases() -> None:
    assert mask_secret("") == "(empty)"
    assert mask_secret("abcd") == "a..."
    assert mask_secret("secret-ak") == "secr..."


def test_list_images_non_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    get_calls: list[tuple[str, dict[str, object] | None]] = []

    def fake_get(base_url, path, access_key, *, params=None, timeout=30):
        del base_url, access_key, timeout
        get_calls.append((path, params))
        if path == "/openapi/v2/image/private":
            return {
                "data": {
                    "items": [
                        {
                            "id": 1,
                            "name": "CP2K:2024.1",
                            "description": "CP2K production image",
                            "url": "registry.dp.tech/dptech/cp2k:2024.1",
                        },
                        {"id": 2, "name": "GROMACS", "description": "MD image"},
                    ],
                    "total": 2,
                }
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr("matmaster.bohrium.client._get", fake_get)
    result = list_images(_make_ctx(sandbox=False), keyword="cp2k", max_results=5)

    assert result["success"] is True
    assert result["total_found"] == 1
    assert result["returned"] == 1
    assert result["images"][0]["name"] == "CP2K:2024.1"
    assert result["images"][0]["versions"][0]["version"] == "2024.1"
    assert result["images"][0]["private"] is True
    assert len(get_calls) == 1
    assert get_calls[0][0] == "/openapi/v2/image/private"


def test_list_images_sandbox_uses_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "matmaster.bohrium.client._load_sandbox_catalog",
        lambda: {
            "images": [
                {"name": "CP2K", "description": "CP2K production image"},
                {"name": "GROMACS", "description": "MD image"},
            ]
        },
    )

    result = list_images(_make_ctx(sandbox=True), keyword="cp2k", max_results=5)

    assert result["success"] is True
    assert result["source"] == "sandbox_catalog"
    assert result["total_found"] == 1
    assert result["images"][0]["name"] == "CP2K"


def test_list_images_sandbox_falls_back_when_catalog_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_calls: list[tuple[str, dict[str, object] | None]] = []

    def fake_get(base_url, path, access_key, *, params=None, timeout=30):
        del base_url, access_key, timeout
        get_calls.append((path, params))
        if path == "/openapi/v2/image/private":
            return {
                "data": {
                    "items": [
                        {
                            "id": 1,
                            "name": "CP2K:2024.1",
                            "description": "CP2K production image",
                            "url": "registry.dp.tech/dptech/cp2k:2024.1",
                        }
                    ],
                    "total": 1,
                }
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(
        "matmaster.bohrium.client._load_sandbox_catalog",
        lambda: {"images": []},
    )
    monkeypatch.setattr("matmaster.bohrium.client._get", fake_get)

    result = list_images(_make_ctx(sandbox=True), keyword="cp2k", max_results=5)

    assert result["success"] is True
    assert result["total_found"] == 1
    assert result["returned"] == 1
    assert result["images"][0]["name"] == "CP2K:2024.1"
    assert result["images"][0]["private"] is True
    assert "source" not in result or result["source"] != "sandbox_catalog"
    assert len(get_calls) == 1
    assert get_calls[0][0] == "/openapi/v2/image/private"


def test_list_machines_non_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    get_calls: list[tuple[str, dict[str, object] | None]] = []

    def fake_get(base_url, path, access_key, *, params=None, timeout=30):
        del base_url, access_key, timeout
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

    monkeypatch.setattr("matmaster.bohrium.client._get", fake_get)
    result = list_machines(
        _make_ctx(sandbox=False),
        machine_type="gpu",
        keyword="4090",
        max_results=10,
    )

    assert result["success"] is True
    assert result["type"] == "gpu"
    assert result["total_found"] == 1
    assert result["returned"] == 1
    assert result["machines"][0]["skuEnName"] == "c6_m60_1 * NVIDIA 4090"
    assert get_calls[0][1]["chooseType"] == "gpu"


def test_list_machines_sandbox_uses_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "matmaster.bohrium.client._load_sandbox_catalog",
        lambda: {
            "machines": {
                "gpu": [
                    {
                        "skuEnName": "config_only_gpu",
                        "gpu": "sandbox-gpu",
                        "gpuCoreNum": 1,
                    }
                ]
            }
        },
    )

    result = list_machines(
        _make_ctx(sandbox=True),
        machine_type="gpu",
        keyword="config_only",
        max_results=10,
    )

    assert result["success"] is True
    assert result["returned"] == 1
    assert result["machines"][0]["skuEnName"] == "config_only_gpu"
    assert result["source"] == "sandbox_catalog"


def test_list_machines_sandbox_falls_back_when_catalog_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_calls: list[tuple[str, dict[str, object] | None]] = []

    def fake_get(base_url, path, access_key, *, params=None, timeout=30):
        del base_url, access_key, timeout
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
                    }
                ]
            }
        }

    monkeypatch.setattr(
        "matmaster.bohrium.client._load_sandbox_catalog",
        lambda: {"machines": {"gpu": []}},
    )
    monkeypatch.setattr("matmaster.bohrium.client._get", fake_get)

    result = list_machines(
        _make_ctx(sandbox=True),
        machine_type="gpu",
        keyword="4090",
        max_results=10,
    )

    assert result["success"] is True
    assert result["returned"] == 1
    assert result["machines"][0]["skuEnName"] == "c6_m60_1 * NVIDIA 4090"
    assert "source" not in result or result["source"] != "sandbox_catalog"
    assert get_calls[0][1]["chooseType"] == "gpu"


def test_add_job_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_post(base_url, path, access_key, payload, *, timeout=30, log_curl=False):
        del base_url, access_key, timeout, log_curl
        assert path == "/openapi/v1/sandbox/job/add"
        calls.append(payload)
        return {"code": 0, "data": {"jobId": "job-2", "bohrJobId": "bohr-2"}}

    monkeypatch.setattr("matmaster.bohrium.client._post", fake_post)
    add_job(
        _make_ctx(sandbox=True),
        create_data={"jobId": "create-job-id"},
        upload=UploadedArchive(
            oss_key="key",
            download_url="https://store.example.com/input.zip?token=abc",
        ),
        image="demo:latest",
        cmd="python run.py",
        machine="c32_m128_cpu",
        job_name="demo",
        disk_size=50,
    )
    assert calls[0]["ossPath"] == ["https://store.example.com/input.zip?token=abc"]
    assert calls[0]["projectId"] == 42


class _FakeSpan:
    def __init__(self, name: str) -> None:
        self.name = name
        self.attributes: dict[str, object] = {}
        self.exceptions: list[Exception] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def record_exception(self, exc: Exception) -> None:
        self.exceptions.append(exc)

    def set_status(self, status) -> None:
        self.attributes["otel.status"] = status


class _FakeTracer:
    def __init__(self) -> None:
        self.spans: list[_FakeSpan] = []

    def start_as_current_span(self, name: str) -> _FakeSpan:
        span = _FakeSpan(name)
        self.spans.append(span)
        return span


def test_create_job_emits_trace_span(monkeypatch: pytest.MonkeyPatch) -> None:
    tracer = _FakeTracer()

    def fake_post(base_url, path, access_key, payload, *, timeout=30, log_curl=False):
        del base_url, path, access_key, payload, timeout, log_curl
        return {"code": 0, "data": {"jobId": "create-job-id"}}

    monkeypatch.setattr(client_module, "_TRACER", tracer)
    monkeypatch.setattr("matmaster.bohrium.client._post", fake_post)

    try:
        LogContext.bind("session-1", "task-1")
        create_job(_make_ctx(sandbox=True), job_name="demo")
    finally:
        LogContext.clear()

    assert tracer.spans[0].name == "bohrium.job.create"
    assert tracer.spans[0].attributes["bohrium.openapi.path"] == (
        "/openapi/v1/sandbox/job/create"
    )
    assert tracer.spans[0].attributes["matmaster.session_id"] == "session-1"
    assert tracer.spans[0].attributes["matmaster.task_id"] == "task-1"
    assert tracer.spans[0].attributes["bohrium.job_name"] == "demo"
    assert tracer.spans[0].attributes["bohrium.job_id"] == "create-job-id"


def test_add_job_emits_trace_span(monkeypatch: pytest.MonkeyPatch) -> None:
    tracer = _FakeTracer()

    def fake_post(base_url, path, access_key, payload, *, timeout=30, log_curl=False):
        del base_url, path, access_key, payload, timeout, log_curl
        return {"code": 0, "data": {"jobId": "job-2"}}

    monkeypatch.setattr(client_module, "_TRACER", tracer)
    monkeypatch.setattr("matmaster.bohrium.client._post", fake_post)

    add_job(
        _make_ctx(sandbox=False),
        create_data={"jobId": "create-job-id"},
        upload=UploadedArchive(
            oss_key="store/input.zip",
            download_url="https://store.example.com/input.zip?token=abc",
        ),
        image="demo:latest",
        cmd="python run.py",
        machine="c32_m128_cpu",
        job_name="demo",
        disk_size=50,
    )

    assert tracer.spans[0].name == "bohrium.job.add"
    assert tracer.spans[0].attributes["bohrium.openapi.path"] == ("/openapi/v2/job/add")
    assert tracer.spans[0].attributes["bohrium.created_job_id"] == "create-job-id"
    assert tracer.spans[0].attributes["bohrium.job_id"] == "job-2"


def test_add_job_can_capture_full_http_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracer = _FakeTracer()

    def fake_post(base_url, path, access_key, payload, *, timeout=30, log_curl=False):
        del base_url, path, access_key, payload, timeout, log_curl
        return {"code": 0, "data": {"jobId": "job-2"}}

    monkeypatch.setattr(client_module, "_TRACER", tracer)
    monkeypatch.setattr("matmaster.bohrium.client._post", fake_post)

    add_job(
        _make_ctx(sandbox=True),
        create_data={"jobId": "create-job-id"},
        upload=UploadedArchive(
            oss_key="store/input.zip",
            download_url="https://store.example.com/input.zip?token=abc",
        ),
        image="demo:latest",
        cmd="python run.py",
        machine="c32_m128_cpu",
        job_name="demo",
        disk_size=50,
    )

    span_attrs = tracer.spans[0].attributes
    assert span_attrs["http.request.method"] == "POST"
    assert span_attrs["url.full"] == (
        "https://openapi.test.dp.tech/openapi/v1/sandbox/job/add"
    )
    assert '"accessKey": "ak"' in span_attrs["bohrium.request.headers_json"]
    assert '"ossPath": ["https://store.example.com/input.zip?token=abc"]' in (
        span_attrs["bohrium.request.body_json"]
    )


class _FakeResponse:
    ok = True
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"code": 0, "data": {}}


def test_post_logs_copyable_curl_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fake_requests_post(url, *, headers, json, timeout):
        del url, headers, json, timeout
        return _FakeResponse()

    monkeypatch.setattr(client_module.requests, "post", fake_requests_post)

    with caplog.at_level(logging.INFO, logger=client_module.logger.name):
        client_module._post(
            "https://openapi.test.dp.tech",
            "/openapi/v1/sandbox/job/add",
            "secret-ak",
            {"jobId": "job-1", "cmd": "echo hi"},
            log_curl=True,
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any(client_module.CURL_LOG_PREFIX in msg for msg in messages)
    # The curl line is meant to be copy-pasteable, so it carries the real key.
    assert any("curl -X POST" in msg and "secret-ak" in msg for msg in messages)


def test_post_sends_bohrium_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_headers: dict[str, str] = {}

    class FakeResponse:
        ok = True
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"code": 0, "data": {}}

    def fake_requests_post(url, *, headers, json, timeout):
        del url, json, timeout
        captured_headers.update(headers)
        return FakeResponse()

    monkeypatch.setattr(client_module.requests, "post", fake_requests_post)

    client_module._post(
        "https://openapi.test.dp.tech",
        "/openapi/v1/sandbox/job/add",
        "secret-ak",
        {"jobId": "job-1"},
    )

    assert captured_headers["accessKey"] == "secret-ak"
    assert captured_headers["Content-Type"] == "application/json"


def test_post_does_not_log_curl_by_default(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fake_requests_post(url, *, headers, json, timeout):
        del url, headers, json, timeout
        return _FakeResponse()

    monkeypatch.setattr(client_module.requests, "post", fake_requests_post)

    with caplog.at_level(logging.INFO, logger=client_module.logger.name):
        client_module._post(
            "https://openapi.test.dp.tech",
            "/openapi/v1/sandbox/job/create",
            "secret-ak",
            {"name": "demo"},
        )

    messages = [record.getMessage() for record in caplog.records]
    assert not any(client_module.CURL_LOG_PREFIX in msg for msg in messages)


def test_confirm_terminal_status_retries_failed_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def fake_get_job_detail(ctx, *, job_id):
        del ctx
        calls.append(int(job_id))
        return {"status": 2}

    monkeypatch.setattr(
        "matmaster.bohrium.client.get_job_detail",
        fake_get_job_detail,
    )

    code, name, detail = confirm_terminal_status(
        _make_ctx(),
        job_id=123,
        detail_data={"status": -1},
        attempts=2,
        sleep_seconds=0,
    )

    assert calls == [123]
    assert code == 2
    assert name == "Finished"
    assert detail["status"] == 2
