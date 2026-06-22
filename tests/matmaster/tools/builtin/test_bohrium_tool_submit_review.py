from __future__ import annotations

import pytest

import matmaster.bohrium.client as bohrium_client_module
import matmaster.tools.builtin.bohrium_tool.tool as bohrium_tool_module
from matmaster.bohrium.errors import BohriumError
from matmaster.tools.builtin.bohrium_tool import BohriumTool
from matmaster.tools.builtin.bohrium_tool.models import BohriumSubmittedJob
from matmaster.tools.tool_compiler import ToolCompiler
from matmaster.types.topology import RuntimeTopology, ToolPlane
from tests.matmaster.tools.builtin.test_bohrium_tool import (
    _install_fake_sdk_free_upload,
)
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import (
    _fake_submit_post_factory,
    _patch_bridge,
)


def test_submit_optout_normalizes_before_runtime(tmp_path, monkeypatch):
    tool = BohriumTool(workdir=tmp_path)
    captured: dict[str, object] = {}

    _patch_bridge(monkeypatch)

    def fake_submit_job_via_runtime(**kwargs):
        captured.update(kwargs)
        return BohriumSubmittedJob(job_id="job-123", raw_add_response={})

    monkeypatch.setattr(
        bohrium_tool_module,
        "submit_job_via_runtime",
        fake_submit_job_via_runtime,
    )

    result = tool._submit(
        {
            "action": "submit",
            "input_dir": "inputs",
            "image": "test:latest",
            "cmd": "run",
        }
    )

    assert result.status == "success"
    assert captured["cmd"] == "run > log 2>&1"
    assert captured["machine"] == "c32_m128_cpu"
    assert captured["job_name"] == "matmaster-job"
    assert captured["disk_size"] == 50
    assert result.meta["submit_execution_audit"]["job_id"] == "job-123"


def test_submit_optout_rejects_oversized_args_before_runtime(tmp_path, monkeypatch):
    tool = BohriumTool(workdir=tmp_path)
    called = False

    def fake_submit_job_via_runtime(**kwargs):
        nonlocal called
        called = True
        return BohriumSubmittedJob(job_id="job-123", raw_add_response={})

    monkeypatch.setattr(
        bohrium_tool_module,
        "submit_job_via_runtime",
        fake_submit_job_via_runtime,
    )

    result = tool._submit(
        {
            "action": "submit",
            "input_dir": "inputs",
            "image": "test:latest",
            "cmd": "x" * 9000,
        }
    )

    assert result.status == "error"
    assert "too long" in result.content.lower()
    assert called is False


def test_submit_job_via_runtime_defensive_on_unnormalized_cmd(tmp_path, monkeypatch):
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    (input_dir / "input.inp").write_text("&CONTROL\n", encoding="utf-8")

    monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
    _patch_bridge(monkeypatch)
    monkeypatch.setattr(bohrium_client_module, "_post", _fake_submit_post_factory([]))
    _install_fake_sdk_free_upload(monkeypatch, [])

    with pytest.raises(BohriumError, match="cmd not normalized"):
        bohrium_tool_module.submit_job_via_runtime(
            input_dir=str(input_dir),
            image="registry.dp.tech/dptech/cp2k:2024.1",
            cmd="cp2k.popt -i input.inp",
            machine="c64_m256_cpu",
            job_name="matmaster-job",
            disk_size=50,
            workdir=tmp_path,
            session=None,
        )


def test_compiled_bohrium_instance_carries_provider(tmp_path):
    topology = RuntimeTopology(
        session_kind="local",
        control_root=str(tmp_path),
        workspace_root=str(tmp_path),
        active_planes=frozenset(ToolPlane),
    )

    instance = ToolCompiler().compile(
        BohriumTool(workdir=tmp_path),
        topology,
        source="builtin",
    )

    assert instance.submit_review_provider is BohriumTool.submit_review_provider
