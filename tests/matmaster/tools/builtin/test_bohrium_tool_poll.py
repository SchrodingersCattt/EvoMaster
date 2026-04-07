"""Poll-focused tests for the builtin Bohrium tool."""

from __future__ import annotations

import asyncio
import json

import matmaster.tools.builtin.bohrium_tool.api as bohrium_api_module
import matmaster.tools.builtin.bohrium_tool.tool as bohrium_tool_module
from matmaster.tools.builtin.bohrium_tool import BohriumTool
from matmaster.tools.tool_result import ToolResult
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import (
    _patch_bridge,
)


class TestBohriumPollExecution:
    def test_poll_running_uses_sandbox_endpoint(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        get_calls: list[str] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            del base_url, access_key, params, timeout
            get_calls.append(path)
            return {'data': {'status': 1}}

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_api_module, '_get', fake_get)

        result = asyncio.run(tool.execute({'action': 'poll', 'job_id': 'job-123'}))

        assert isinstance(result, ToolResult)
        assert result.status == 'success'
        payload = json.loads(result.content)
        assert payload['job_id'] == 'job-123'
        assert payload['status'] == 'Running'
        assert get_calls == ['/openapi/v1/sandbox/job/job-123']

    def test_poll_wait_retries_running_until_timeout(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        get_calls: list[str] = []
        sleep_calls: list[float] = []
        statuses = [1, 1, 1]
        status_index = 0

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            del base_url, access_key, params, timeout
            nonlocal status_index
            get_calls.append(path)
            status = statuses[min(status_index, len(statuses) - 1)]
            status_index += 1
            return {'data': {'status': status}}

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_api_module, '_get', fake_get)
        import time as time_module

        monkeypatch.setattr(bohrium_tool_module, 'time', time_module, raising=False)
        monkeypatch.setattr(bohrium_tool_module.time, 'sleep', sleep_calls.append)

        result = asyncio.run(
            tool.execute(
                {
                    'action': 'poll',
                    'job_id': 'job-running',
                    'wait': True,
                    'max_wait_seconds': 10,
                    'poll_interval_seconds': 5,
                }
            )
        )

        assert result.status == 'success'
        payload = json.loads(result.content)
        assert payload['status'] == 'Running'
        assert 'waited 10s' in payload['message']
        assert get_calls == ['/openapi/v1/sandbox/job/job-running'] * 3
        assert sleep_calls == [5, 5]

    def test_poll_running_without_wait_remains_single_shot(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        get_calls: list[str] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            del base_url, access_key, params, timeout
            get_calls.append(path)
            return {'data': {'status': 1}}

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_api_module, '_get', fake_get)

        result = asyncio.run(tool.execute({'action': 'poll', 'job_id': 'job-789'}))

        assert result.status == 'success'
        payload = json.loads(result.content)
        assert payload['status'] == 'Running'
        assert get_calls == ['/openapi/v1/sandbox/job/job-789']

    def test_poll_finished_returns_guidance_without_downloading(
        self, tmp_path, monkeypatch
    ):
        tool = BohriumTool(workdir=tmp_path)

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            del base_url, access_key, params, timeout
            assert path == '/openapi/v1/sandbox/job/job-finished'
            return {'data': {'status': 2}}

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_api_module, '_get', fake_get)

        result = asyncio.run(tool.execute({'action': 'poll', 'job_id': 'job-finished'}))

        assert result.status == 'success'
        payload = json.loads(result.content)
        assert payload['status'] == 'Finished'
        assert 'action="download"' in payload['message']
        assert 'result_dir' not in payload
        assert 'files' not in payload

    def test_poll_failed_returns_guidance_without_downloading(
        self, tmp_path, monkeypatch
    ):
        tool = BohriumTool(workdir=tmp_path)
        sleep_calls: list[float] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            del base_url, access_key, params, timeout
            return {'data': {'status': -1}}

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_api_module, '_get', fake_get)
        import time as time_module

        monkeypatch.setattr(bohrium_tool_module, 'time', time_module, raising=False)
        monkeypatch.setattr(bohrium_tool_module.time, 'sleep', sleep_calls.append)

        result = asyncio.run(tool.execute({'action': 'poll', 'job_id': 'job-failed'}))

        assert result.status == 'success'
        payload = json.loads(result.content)
        assert payload['status'] == 'Failed'
        assert 'action="download"' in payload['message']
        assert sleep_calls == []

    def test_poll_rejects_result_dir_parameter(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)

        result = asyncio.run(
            tool.execute(
                {
                    'action': 'poll',
                    'job_id': 'job-1',
                    'result_dir': 'results/run_job-1',
                }
            )
        )

        assert result.status == 'error'
        assert 'no longer downloads artifacts' in result.content
        assert 'action="download"' in result.content
