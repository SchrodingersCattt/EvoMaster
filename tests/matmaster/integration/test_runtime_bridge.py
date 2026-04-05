"""Tests for matmaster.integration.runtime_bridge -- credential bridge primitives.

Covers:
- Bohrium credential resolution precedence (explicit > session > env > none)
- Env projection from resolved credentials
- Output path classification (relative / local_abs / remote_share)
"""

from __future__ import annotations

from types import SimpleNamespace

from matmaster.integration.runtime_bridge import (
    build_service_env,
    resolve_output_path,
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


def test_bohrium_resolver_returns_source_none_without_credentials(monkeypatch):
    monkeypatch.delenv("BOHRIUM_ACCESS_KEY", raising=False)
    monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)
    monkeypatch.delenv("BOHRIUM_USER_ID", raising=False)
    monkeypatch.delenv("BOHRIUM_BASE_URL", raising=False)

    cred = resolve_service_credentials("bohrium", session=None, explicit=None)

    assert cred.source == "none"
    assert cred.values == {}


def test_relative_path_resolves_under_execution_workdir():
    decision = resolve_output_path(
        raw_path="results/run_1",
        execution_workdir="/workspace/run",
        session=None,
    )

    assert decision.kind == "relative"
    assert decision.normalized_path == "/workspace/run/results/run_1"
    assert decision.requires_remote_session is False


def test_local_absolute_path_stays_local():
    decision = resolve_output_path(
        raw_path="/tmp/results/run_1",
        execution_workdir="/workspace/run",
        session=None,
    )

    assert decision.kind == "local_abs"
    assert decision.normalized_path == "/tmp/results/run_1"
    assert decision.requires_remote_session is False


def test_remote_share_path_requires_remote_session():
    decision = resolve_output_path(
        raw_path="/share/NiCoCr_relax",
        execution_workdir="/workspace",
        session=None,
    )

    assert decision.kind == "remote_share"
    assert decision.requires_remote_session is True


def test_remote_share_path_is_allowed_with_remote_session():
    session = SimpleNamespace(is_open=True)
    decision = resolve_output_path(
        raw_path="/share/NiCoCr_relax",
        execution_workdir="/share",
        session=session,
    )

    assert decision.kind == "remote_share"
    assert decision.normalized_path == "/share/NiCoCr_relax"
    assert decision.requires_remote_session is False
