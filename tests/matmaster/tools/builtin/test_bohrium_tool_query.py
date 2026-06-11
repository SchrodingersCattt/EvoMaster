"""Query tests for Bohrium tool (single-shot, no blocking loop)."""

from __future__ import annotations

import asyncio
import json

import matmaster.bohrium.client as bohrium_client_module
from matmaster.tools.builtin.bohrium_tool import BohriumTool
from matmaster.tools.tool_result import ToolResult
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import _patch_bridge


class TestQuerySingleShot:
    def test_query_running_returns_once(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        calls: list[str] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            calls.append(path)
            return {"data": {"status": 1}}  # Running

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_get", fake_get)

        result = asyncio.run(
            tool.execute_with_context({"action": "query", "job_id": "job-1"}, None)
        )

        assert isinstance(result, ToolResult)
        payload = json.loads(result.content)
        assert payload["status"] == "Running"
        # single-shot: exactly one API hit, no blocking retry loop
        assert len(calls) == 1

    def test_query_finished_returns_once(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            return {"data": {"status": 2}}  # Finished

        monkeypatch.delenv("BOHRIUM_USE_SANDBOX", raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, "_get", fake_get)

        result = asyncio.run(
            tool.execute_with_context({"action": "query", "job_id": "job-1"}, None)
        )

        payload = json.loads(result.content)
        assert payload["status"] == "Finished"

    def test_short_polling_loop_removed(self):
        # The blocking short-poll loop and its constants must be gone.
        assert not hasattr(BohriumTool, "_poll_with_short" + "_loop")
        assert not hasattr(BohriumTool, "_POLL_MAX" + "_WAIT")
        assert not hasattr(BohriumTool, "_POLL" + "_INTERVAL")

    def test_query_in_action_enum(self):
        enum = BohriumTool.json_schema["properties"]["action"]["enum"]
        assert "query" in enum
        assert "poll" not in enum
