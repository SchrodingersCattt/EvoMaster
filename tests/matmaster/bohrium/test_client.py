from __future__ import annotations

import pytest

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
    calls: list[tuple[str, dict]] = []

    def fake_post(base_url, path, access_key, payload, *, timeout=30):
        del base_url, access_key, timeout
        calls.append((path, payload))
        return {"code": 0, "data": {"jobId": "job-1"}}

    monkeypatch.setattr("matmaster.bohrium.client._post", fake_post)

    create_job(_make_ctx(sandbox=True), job_name="demo")

    assert calls == [
        ("/openapi/v1/sandbox/job/create", {"projectId": 42, "name": "demo"})
    ]


def test_create_job_non_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_post(base_url, path, access_key, payload, *, timeout=30):
        del base_url, access_key, timeout
        calls.append((path, payload))
        return {"code": 0, "data": {"jobId": "job-1"}}

    monkeypatch.setattr("matmaster.bohrium.client._post", fake_post)

    create_job(_make_ctx(sandbox=False), job_name="demo")

    assert calls[0][0] == "/openapi/v1/job/create"


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
    host, path, token = get_file_token(_make_ctx(), file_path="log", bohr_job_id="1")
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
        if path == "/openapi/v2/image/public":
            return {
                "data": {
                    "items": [
                        {
                            "id": 1,
                            "name": "CP2K",
                            "description": "CP2K production image",
                        },
                        {"id": 2, "name": "GROMACS", "description": "MD image"},
                    ]
                }
            }
        if path == "/openapi/v2/image/private":
            return {"data": {"items": []}}
        if path == "/openapi/v2/image/public/1/version":
            return {
                "data": {
                    "items": [
                        {
                            "url": "registry.dp.tech/dptech/cp2k:2024.1",
                            "version": "2024.1",
                            "resourceType": "CPU",
                        }
                    ]
                }
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr("matmaster.bohrium.client._get", fake_get)
    result = list_images(_make_ctx(sandbox=False), keyword="cp2k", max_results=5)

    assert result["success"] is True
    assert result["total_found"] == 1
    assert result["returned"] == 1
    assert result["images"][0]["name"] == "CP2K"
    assert result["images"][0]["versions"][0]["version"] == "2024.1"
    assert get_calls[0][0] == "/openapi/v2/image/public"
    assert get_calls[1][0] == "/openapi/v2/image/private"
    assert get_calls[2][0] == "/openapi/v2/image/public/1/version"


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
        if path == "/openapi/v2/image/public":
            return {
                "data": {
                    "items": [
                        {
                            "id": 1,
                            "name": "CP2K",
                            "description": "CP2K production image",
                        }
                    ]
                }
            }
        if path == "/openapi/v2/image/public/1/version":
            return {
                "data": {
                    "items": [
                        {
                            "url": "registry.dp.tech/dptech/cp2k:2024.1",
                            "version": "2024.1",
                            "resourceType": "CPU",
                        }
                    ]
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
    assert result["images"][0]["name"] == "CP2K"
    assert "source" not in result or result["source"] != "sandbox_catalog"
    assert get_calls[0][0] == "/openapi/v2/image/public"
    assert get_calls[1][0] == "/openapi/v2/image/public/1/version"


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

    def fake_post(base_url, path, access_key, payload, *, timeout=30):
        del base_url, access_key, timeout
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
