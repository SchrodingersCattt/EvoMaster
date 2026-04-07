"""Tests for matmaster.integration.runtime_bridge credential bridge primitives."""

from __future__ import annotations

from types import SimpleNamespace

from matmaster.integration import runtime_bridge
from matmaster.integration.runtime_bridge import (
    build_service_env,
    resolve_service_credentials,
)


def _session_with_bohrium():
    return SimpleNamespace(
        _bohrium_credentials={
            "access_key": "session-ak",
            "project_id": 42,
            "user_id": 7,
            "user_no": "U001",
        }
    )


def test_bohrium_resolver_prefers_explicit_over_session_and_env(monkeypatch):
    monkeypatch.setenv("BOHRIUM_ACCESS_KEY", "env-ak")
    monkeypatch.setenv("BOHRIUM_BASE_URL", "https://test.dp.tech/")
    session = _session_with_bohrium()

    cred = resolve_service_credentials(
        "bohrium",
        session=session,
        explicit={"access_key": "explicit-ak", "project_id": 99},
    )

    assert cred.source == "explicit"
    assert cred.values["access_key"] == "explicit-ak"
    assert cred.values["project_id"] == 99
    assert cred.values["base_url"] == "https://test.dp.tech"


def test_bohrium_env_projection_uses_resolved_values(monkeypatch):
    monkeypatch.setenv("BOHRIUM_BASE_URL", "https://test.dp.tech/")
    session = _session_with_bohrium()

    env = build_service_env("bohrium", session=session)

    assert env["BOHRIUM_ACCESS_KEY"] == "session-ak"
    assert env["BOHRIUM_PROJECT_ID"] == "42"
    assert env["BOHRIUM_USER_ID"] == "7"
    assert env["BOHRIUM_USER_NO"] == "U001"
    assert env["BOHRIUM_BASE_URL"] == "https://test.dp.tech"


def test_bohrium_resolver_uses_service_env_default_host(monkeypatch):
    monkeypatch.delenv("BOHRIUM_BASE_URL", raising=False)
    monkeypatch.setenv("SERVICE_ENV", "uat")
    session = _session_with_bohrium()

    cred = resolve_service_credentials("bohrium", session=session, explicit=None)

    assert cred.source == "session"
    assert cred.values["base_url"] == "https://openapi.uat.dp.tech"


def test_bohrium_resolver_returns_source_none_without_credentials(monkeypatch):
    monkeypatch.delenv("BOHRIUM_ACCESS_KEY", raising=False)
    monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)
    monkeypatch.delenv("BOHRIUM_USER_ID", raising=False)
    monkeypatch.delenv("BOHRIUM_BASE_URL", raising=False)

    cred = resolve_service_credentials("bohrium", session=None, explicit=None)

    assert cred.source == "none"
    assert cred.values == {}


def test_runtime_bridge_keeps_env_projection_but_drops_path_resolution():
    assert hasattr(runtime_bridge, "build_service_env")
    assert hasattr(runtime_bridge, "resolve_service_credentials")
    assert not hasattr(runtime_bridge, "resolve_output_path")
    assert not hasattr(runtime_bridge, "OutputPathDecision")
