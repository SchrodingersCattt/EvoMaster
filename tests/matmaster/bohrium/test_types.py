from __future__ import annotations

from types import SimpleNamespace

import pytest

from matmaster.bohrium.credentials import build_bohrium_context
from matmaster.bohrium.endpoints import (
    get_bohrium_base_url,
    get_bohrium_service_env,
    use_sandbox,
)
from matmaster.bohrium.errors import BohriumCredentialError
from matmaster.bohrium.runtime import BohriumRuntimeHandle, attach_runtime
from matmaster.bohrium.types import (
    BohriumContext,
    BohriumCredentials,
    BohriumExecutionContext,
    BohriumRuntimeSnapshot,
)


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


def test_bohrium_context_from_credentials() -> None:
    cred = BohriumCredentials(
        access_key="ak",
        project_id=42,
        user_id=7,
        user_no="U001",
        base_url="https://openapi.test.dp.tech",
    )
    ctx = BohriumContext.from_credentials(cred, sandbox=False)
    assert ctx.credentials.access_key == "ak"
    assert ctx.credentials.project_id == 42
    assert ctx.sandbox is False
    assert ctx.credential_source == "runtime"


def test_bohrium_context_rejects_empty_access_key() -> None:
    cred = BohriumCredentials(
        access_key="",
        project_id=42,
        user_id=None,
        user_no="",
        base_url="https://openapi.test.dp.tech",
    )
    with pytest.raises(BohriumCredentialError):
        BohriumContext.from_credentials(cred, sandbox=False)


def test_use_sandbox_defaults_true(monkeypatch) -> None:
    monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
    assert use_sandbox() is True


def test_use_sandbox_respects_env(monkeypatch) -> None:
    monkeypatch.setenv("BOHRIUM_USE_SANDBOX", "0")
    assert use_sandbox() is False


def test_build_bohrium_context_from_env(monkeypatch) -> None:
    monkeypatch.setenv("BOHRIUM_ACCESS_KEY", "env-ak")
    monkeypatch.setenv("BOHRIUM_PROJECT_ID", "9")
    monkeypatch.delenv("BOHRIUM_BASE_URL", raising=False)
    monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)

    ctx = build_bohrium_context(session=None, require_project=True)

    assert ctx.credentials.access_key == "env-ak"
    assert ctx.credentials.project_id == 9
    assert ctx.credential_source == "env"
    assert ctx.sandbox is True


def test_build_bohrium_context_from_runtime() -> None:
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
    assert ctx.credential_source == "runtime"
