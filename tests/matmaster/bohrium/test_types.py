from __future__ import annotations

from matmaster.bohrium.endpoints import get_bohrium_base_url, get_bohrium_service_env
from matmaster.bohrium.types import BohriumCredentials, BohriumRuntimeSnapshot


def test_bohrium_credentials_normalize_raw_mapping() -> None:
    cred = BohriumCredentials.from_mapping(
        {
            "access_key": "  ak-123  ",
            "project_id": "42",
            "user_id": "7",
            "user_no": "  U001  ",
            "base_url": "https://openapi.test.dp.tech/",
        }
    )

    assert cred.access_key == "ak-123"
    assert cred.project_id == 42
    assert cred.user_id == 7
    assert cred.user_no == "U001"
    assert cred.base_url == "https://openapi.test.dp.tech"


def test_runtime_snapshot_is_plain_data() -> None:
    snap = BohriumRuntimeSnapshot(
        session_type="ssh",
        execution_workdir="/share",
        remote_workspace_root="/share",
        remote_project_root="/share/.matmaster",
        node_id=12,
        node_ip="10.0.0.8",
        ssh_attached=True,
    )

    assert snap.model_dump()["node_id"] == 12


def test_bohrium_base_url_prefers_explicit_env(monkeypatch) -> None:
    monkeypatch.setenv("BOHRIUM_BASE_URL", "https://openapi.custom.dp.tech/")
    assert get_bohrium_base_url() == "https://openapi.custom.dp.tech"


def test_bohrium_base_url_keeps_existing_runtime_prod_default(monkeypatch) -> None:
    monkeypatch.delenv("BOHRIUM_BASE_URL", raising=False)
    monkeypatch.setenv("SERVICE_ENV", "prod")
    assert get_bohrium_base_url() == "https://openapi.dp.tech"


def test_bohrium_service_env_defaults_to_test(monkeypatch) -> None:
    monkeypatch.delenv("SERVICE_ENV", raising=False)
    assert get_bohrium_service_env() == "test"
