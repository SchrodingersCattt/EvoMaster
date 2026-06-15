from __future__ import annotations

from types import SimpleNamespace

import pytest

from matmaster.bohrium.runtime import (
    BohriumRuntimeHandle,
    attach_runtime,
    detach_runtime,
    get_runtime,
    require_runtime,
)
from matmaster.bohrium.types import BohriumCredentials, BohriumExecutionContext
from matmaster.calculation_runtimes.types import SubmissionRequest


def _runtime() -> BohriumRuntimeHandle:
    credentials = BohriumCredentials(
        access_key="ak",
        project_id=42,
        user_id=7,
        user_no="U001",
        base_url="https://openapi.test.dp.tech",
    )
    execution = BohriumExecutionContext(
        session_type="ssh",
        execution_workdir="/share",
        remote_workspace_root="/share",
        remote_project_root="/share/.matmaster",
        node_id=8,
        node_ip="10.0.0.8",
        ssh_attached=True,
    )
    return BohriumRuntimeHandle(credentials=credentials, execution=execution)


def test_attach_and_require_runtime_round_trip() -> None:
    session = SimpleNamespace()
    runtime = _runtime()

    attach_runtime(session, runtime)

    assert get_runtime(session) is runtime
    assert require_runtime(session) is runtime


def test_detach_runtime_clears_session() -> None:
    session = SimpleNamespace()
    attach_runtime(session, _runtime())

    detach_runtime(session)

    assert get_runtime(session) is None


def test_build_env_projects_runtime_credentials() -> None:
    env = _runtime().build_env()
    assert env["BOHRIUM_ACCESS_KEY"] == "ak"
    assert env["BOHRIUM_PROJECT_ID"] == "42"
    assert env["BOHRIUM_BASE_URL"] == "https://openapi.test.dp.tech"


def test_build_env_includes_trace_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACE_EXPORTER_ENDPOINT", "trace.example.com:10010")
    monkeypatch.setenv("TRACE_INSTANCE_ID", "test-instance")
    monkeypatch.setenv("TRACE_PROJECT", "trace-project")
    monkeypatch.setenv("TRACE_AK", "trace-ak")
    monkeypatch.setenv("TRACE_SK", "trace-sk")
    monkeypatch.setenv("TRACE_LOGSTORE", "trace-logstore")

    env = _runtime().build_env()

    assert env["TRACE_EXPORTER_ENDPOINT"] == "trace.example.com:10010"
    assert env["TRACE_INSTANCE_ID"] == "test-instance"
    assert env["TRACE_PROJECT"] == "trace-project"
    assert env["TRACE_AK"] == "trace-ak"
    assert env["TRACE_SK"] == "trace-sk"
    assert env["TRACE_LOGSTORE"] == "trace-logstore"


def test_build_submission_injects_dispatcher_credentials() -> None:
    submission = _runtime().build_submission(
        SubmissionRequest(
            executor_template={
                "type": "dispatcher",
                "machine": {
                    "remote_profile": {
                        "machine_type": "c2_m8_cpu",
                        "image_address": "repo/image:latest",
                    }
                },
            },
            needs_storage=True,
            submission_mode="async",
        )
    )

    assert submission.executor["machine"]["remote_profile"]["access_key"] == "ak"
    assert submission.storage["plugin"]["project_id"] == 42


def test_build_submission_injects_dispatcher_trace_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRACE_EXPORTER_ENDPOINT", "trace.example.com:10010")
    monkeypatch.setenv("TRACE_INSTANCE_ID", "test-instance")

    submission = _runtime().build_submission(
        SubmissionRequest(
            executor_template={
                "type": "dispatcher",
                "resources": {"envs": {"EXISTING": "1"}},
                "machine": {
                    "remote_profile": {
                        "machine_type": "c2_m8_cpu",
                        "image_address": "repo/image:latest",
                    }
                },
            },
            needs_storage=False,
            submission_mode="async",
        )
    )

    envs = submission.executor["resources"]["envs"]
    assert envs["EXISTING"] == "1"
    assert envs["TRACE_EXPORTER_ENDPOINT"] == "trace.example.com:10010"
    assert envs["TRACE_INSTANCE_ID"] == "test-instance"


def test_require_runtime_raises_for_missing_runtime() -> None:
    with pytest.raises(RuntimeError):
        require_runtime(SimpleNamespace())


def test_snapshot_maps_execution_fields_explicitly() -> None:
    snap = _runtime().snapshot()

    assert snap.session_type == "ssh"
    assert snap.execution_workdir == "/share"


def test_materialize_input_path_uploads_local_files(tmp_path, monkeypatch) -> None:
    input_file = tmp_path / "input.in"
    input_file.write_text("data", encoding="utf-8")

    monkeypatch.setattr(
        "matmaster.bohrium.paths.upload_file_to_oss",
        lambda path, workspace_root, **kwargs: f"https://oss/{path.name}",
    )

    url = _runtime().materialize_input_path(
        str(input_file),
        workspace_root=tmp_path,
        session=None,
    )

    assert url == "https://oss/input.in"


def test_materialize_input_path_downloads_remote_files_before_upload(
    tmp_path, monkeypatch
) -> None:
    session = SimpleNamespace(
        is_file=lambda path: True,
        download=lambda path: b"remote-data",
    )

    monkeypatch.setattr(
        "matmaster.bohrium.paths.upload_file_to_oss",
        lambda path, workspace_root, **kwargs: f"https://oss/{kwargs['object_basename']}",
    )

    url = _runtime().materialize_input_path(
        "inputs/job.in",
        workspace_root=tmp_path,
        session=session,
    )

    assert url == "https://oss/job.in"
