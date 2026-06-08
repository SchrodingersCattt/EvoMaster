"""Query tests for Bohrium tool (single-shot, no blocking loop)."""

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


class TestQuerySingleShot:
    def test_query_running_returns_once(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        registry = JobRegistry()
        registry.register("job-1")
        calls: list[str] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            calls.append(path)
            return {"data": {"status": 1}}  # Running

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_get", fake_get)

        result = asyncio.run(
            tool.execute_with_context(
                {"action": "query", "job_id": "job-1"},
                _make_exec_ctx(registry),
            )
        )

        assert isinstance(result, ToolResult)
        payload = json.loads(result.content)
        assert payload["status"] == "Running"
        # single-shot: exactly one API hit, no blocking retry loop
        assert len(calls) == 1
        # registry still updated on the normal (non-loop) path
        assert registry.get("job-1").poll_count == 1
        assert registry.get("job-1").status == "running"

    def test_query_finished_returns_once(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        registry = JobRegistry()
        registry.register("job-1")

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            return {"data": {"status": 2}}  # Finished

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_get", fake_get)

        result = asyncio.run(
            tool.execute_with_context(
                {"action": "query", "job_id": "job-1"},
                _make_exec_ctx(registry),
            )
        )

        payload = json.loads(result.content)
        assert payload["status"] == "Finished"
        assert registry.get("job-1").status == "finished"

    def test_query_without_registry_still_works(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            return {"data": {"status": 1}}

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_get", fake_get)

        result = asyncio.run(
            tool.execute_with_context(
                {"action": "query", "job_id": "job-1"},
                _make_exec_ctx(None),
            )
        )

        payload = json.loads(result.content)
        assert payload["status"] == "Running"

    def test_short_polling_loop_removed(self):
        # The blocking short-poll loop and its constants must be gone.
        assert not hasattr(BohriumTool, "_poll_with_short" + "_loop")
        assert not hasattr(BohriumTool, "_POLL_MAX" + "_WAIT")
        assert not hasattr(BohriumTool, "_POLL" + "_INTERVAL")

    def test_query_in_action_enum(self):
        enum = BohriumTool.json_schema["properties"]["action"]["enum"]
        assert "query" in enum
        assert "poll" not in enum

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
