"""Poll tests for Bohrium tool with registry-based throttle."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import matmaster.bohrium.client as bohrium_client_module
from matmaster.tools.builtin.bohrium_tool import BohriumTool
from matmaster.tools.builtin.bohrium_tool.registry import JobRegistry
from matmaster.tools.tool_result import ToolResult
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import _patch_bridge


def _make_exec_ctx(registry: JobRegistry | None = None):
    """Build a minimal exec_ctx with runner_state containing a registry."""
    state = SimpleNamespace(
        get=lambda key, default=None: (
            registry if key == "bohrium_job_registry" else default
        ),
        set=lambda key, value: None,
    )
    return SimpleNamespace(
        runner_state=state,
        cancel_token=None,
    )


class TestPollWithRegistry:
    def test_fresh_poll_calls_api_and_returns_next_check(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        registry = JobRegistry()
        registry.register("job-1")

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            return {"data": {"status": 1}}

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_get", fake_get)

        result = asyncio.run(
            tool.execute_with_context(
                {"action": "poll", "job_id": "job-1"},
                _make_exec_ctx(registry),
            )
        )

        assert isinstance(result, ToolResult)
        payload = json.loads(result.content)
        assert payload["status"] == "Running"
        assert payload["next_check_seconds"] == 5
        assert payload.get("cached") is not True
        assert registry.get("job-1").poll_count == 1

    def test_throttled_poll_returns_cached(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        registry = JobRegistry()
        registry.register("job-1")
        api_calls: list[str] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            api_calls.append(path)
            return {"data": {"status": 1}}

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_get", fake_get)

        asyncio.run(
            tool.execute_with_context(
                {"action": "poll", "job_id": "job-1"},
                _make_exec_ctx(registry),
            )
        )
        assert len(api_calls) == 1

        result = asyncio.run(
            tool.execute_with_context(
                {"action": "poll", "job_id": "job-1"},
                _make_exec_ctx(registry),
            )
        )

        assert len(api_calls) == 1
        payload = json.loads(result.content)
        assert payload["cached"] is True
        assert "seconds_until_fresh" in payload

    def test_terminal_status_not_throttled(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        registry = JobRegistry()
        registry.register("job-1")

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            return {"data": {"status": 2}}

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_get", fake_get)

        result = asyncio.run(
            tool.execute_with_context(
                {"action": "poll", "job_id": "job-1"},
                _make_exec_ctx(registry),
            )
        )

        payload = json.loads(result.content)
        assert payload["status"] == "Finished"
        assert "next_check_seconds" not in payload
        assert registry.get("job-1").status == "finished"

    def test_poll_without_registry_still_works(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            return {"data": {"status": 1}}

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_get", fake_get)

        result = asyncio.run(
            tool.execute_with_context(
                {"action": "poll", "job_id": "job-1"},
                _make_exec_ctx(None),
            )
        )

        payload = json.loads(result.content)
        assert payload["status"] == "Running"

    def test_submit_registers_job(self, tmp_path):
        tool = BohriumTool(workdir=tmp_path)
        registry = JobRegistry()

        result = tool._update_registry(
            registry,
            "submit",
            {"job_name": "test-run"},
            ToolResult(
                status="success",
                content=json.dumps({"success": True, "job_id": "job-99"}),
            ),
        )

        assert isinstance(result, ToolResult)
        rec = registry.get("job-99")
        assert rec is not None
        assert rec.status == "submitted"
        assert rec.job_name == "test-run"
