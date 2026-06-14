"""Pacing + meta-signal tests for Bohrium query (in-tool query pacing)."""

from __future__ import annotations

import asyncio

import matmaster.bohrium.client as bohrium_client_module
import matmaster.tools.builtin.bohrium_tool.tool as tmod
from matmaster.tools.builtin.bohrium_tool import BohriumTool
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ToolExecutionContext
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import _patch_bridge


class TestBohriumQueryMeta:
    def test_query_running_emits_meta(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(
            bohrium_client_module, "_get", lambda *a, **k: {"data": {"status": 1}}
        )  # Running

        result = tool._query({"job_id": "job-1"})

        assert result.status == "success"
        assert result.meta["bohrium_running"] is True
        assert result.meta["bohrium_status_code"] == 1

    def test_query_finished_emits_meta(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(
            bohrium_client_module, "_get", lambda *a, **k: {"data": {"status": 2}}
        )  # Finished (SUCCESS_CODE)

        result = tool._query({"job_id": "job-1"})

        assert result.status == "success"
        assert result.meta["bohrium_running"] is False
        assert result.meta["bohrium_status_code"] == 2


class TestBohriumQueryPacing:
    def test_first_query_runs_immediately_repeat_running_waits(
        self, tmp_path, monkeypatch
    ):
        tool = BohriumTool(workdir=tmp_path)
        calls: list[str] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            calls.append(path)
            return {"data": {"status": 1}}  # Running

        slept: list[float] = []

        async def fake_sleep(delay):
            slept.append(delay)

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_get", fake_get)
        monkeypatch.setattr(tmod.asyncio, "sleep", fake_sleep)

        state = ToolRunnerState()
        ctx = ToolExecutionContext(runner_state=state)

        # First query: immediate, no sleep, one platform hit.
        asyncio.run(tool.execute_with_context({"action": "query", "job_id": "J"}, ctx))
        assert slept == []
        assert len(calls) == 1

        # Second query while still Running: waits up to min interval, then hits again.
        asyncio.run(tool.execute_with_context({"action": "query", "job_id": "J"}, ctx))
        assert len(slept) == 1
        assert 0 < slept[0] <= 30.0
        assert len(calls) == 2

    def test_repeat_query_after_terminal_does_not_wait(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)

        slept: list[float] = []

        async def fake_sleep(delay):
            slept.append(delay)

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(
            bohrium_client_module, "_get", lambda *a, **k: {"data": {"status": 2}}
        )  # Finished
        monkeypatch.setattr(tmod.asyncio, "sleep", fake_sleep)

        state = ToolRunnerState()
        ctx = ToolExecutionContext(runner_state=state)

        asyncio.run(tool.execute_with_context({"action": "query", "job_id": "J"}, ctx))
        asyncio.run(tool.execute_with_context({"action": "query", "job_id": "J"}, ctx))
        assert slept == []

    def test_query_error_does_not_record_running_pacing(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)

        slept: list[float] = []

        async def fake_sleep(delay):
            slept.append(delay)

        def boom(base_url, path, access_key, params=None, timeout=30):
            raise RuntimeError("api down")

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_get", boom)
        monkeypatch.setattr(tmod.asyncio, "sleep", fake_sleep)

        state = ToolRunnerState()
        ctx = ToolExecutionContext(runner_state=state)

        res1 = asyncio.run(
            tool.execute_with_context({"action": "query", "job_id": "J"}, ctx)
        )
        res2 = asyncio.run(
            tool.execute_with_context({"action": "query", "job_id": "J"}, ctx)
        )

        assert res1.status == "error"
        assert res2.status == "error"
        assert slept == []
        pacing = state.get(BohriumTool._QUERY_PACING_STATE_KEY, {})
        assert "J" not in pacing

    def test_query_without_runner_state_is_single_shot(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        calls: list[str] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            calls.append(path)
            return {"data": {"status": 1}}  # Running

        slept: list[float] = []

        async def fake_sleep(delay):
            slept.append(delay)

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_get", fake_get)
        monkeypatch.setattr(tmod.asyncio, "sleep", fake_sleep)

        # exec_ctx=None -> no runner_state -> degrade to base single-shot behavior.
        asyncio.run(tool.execute_with_context({"action": "query", "job_id": "J"}, None))
        asyncio.run(tool.execute_with_context({"action": "query", "job_id": "J"}, None))

        assert slept == []
        assert len(calls) == 2
