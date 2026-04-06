"""Download-focused tests for the builtin Bohrium tool."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import matmaster.tools.builtin.bohrium_tool as bohrium_module
from matmaster.tools.builtin.bohrium_tool import BohriumTool
from matmaster.tools.tool_result import ToolResult
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import (
    FakeRemoteSession,
    _patch_bridge,
)


class TestBohriumDownloadExecution:
    def test_download_requires_result_dir(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)

        result = asyncio.run(
            tool.execute({'action': 'download', 'job_id': 'job-1'})
        )

        assert isinstance(result, ToolResult)
        assert result.status == 'error'
        assert 'Missing required parameter: result_dir' in result.content

    def test_download_rejects_running_job(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            del base_url, access_key, params, timeout
            assert path == '/openapi/v1/sandbox/job/job-running'
            return {'data': {'status': 1}}

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_module, '_get', fake_get)

        result = asyncio.run(
            tool.execute(
                {
                    'action': 'download',
                    'job_id': 'job-running',
                    'result_dir': 'results/run_job-running',
                }
            )
        )

        assert result.status == 'error'
        assert 'only available after terminal status' in result.content

    def test_download_remote_share_without_session_errors(
        self, tmp_path, monkeypatch
    ):
        tool = BohriumTool(workdir=tmp_path)

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)

        result = asyncio.run(
            tool.execute(
                {
                    'action': 'download',
                    'job_id': 'job-1',
                    'result_dir': '/share/results',
                }
            )
        )

        assert result.status == 'error'
        assert 'remote session' in result.content.lower()
