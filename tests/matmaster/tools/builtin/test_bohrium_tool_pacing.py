"""Pacing + meta-signal tests for Bohrium query (in-tool query pacing)."""

from __future__ import annotations

import matmaster.bohrium.client as bohrium_client_module
from matmaster.tools.builtin.bohrium_tool import BohriumTool
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
