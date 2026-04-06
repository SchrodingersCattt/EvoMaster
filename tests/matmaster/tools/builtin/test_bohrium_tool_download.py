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

    def test_download_finished_job_returns_files(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            del base_url, access_key, params, timeout
            return {'data': {'status': 2, 'resultUrl': 'https://store.example/out.zip'}}

        def fake_download_results(job_id, detail_data, result_dir, *, ctx, sandbox):
            del job_id, detail_data, ctx, sandbox
            result_dir.mkdir(parents=True, exist_ok=True)
            (result_dir / 'log').write_text('done\n', encoding='utf-8')
            return ['log'], 'done\n'

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_module, '_get', fake_get)
        monkeypatch.setattr(
            bohrium_module, 'download_bohrium_results', fake_download_results
        )

        result = asyncio.run(
            tool.execute(
                {
                    'action': 'download',
                    'job_id': 'job-finished',
                    'result_dir': str(tmp_path / 'results'),
                }
            )
        )

        assert result.status == 'success'
        payload = json.loads(result.content)
        assert payload['status'] == 'Finished'
        assert payload['files'] == ['log']
        assert payload['log_tail'] == 'done\n'

    def test_download_failed_job_returns_error_with_artifacts(
        self, tmp_path, monkeypatch
    ):
        tool = BohriumTool(workdir=tmp_path)
        sleep_calls: list[float] = []
        statuses = [{'status': -1}, {'status': -1}, {'status': -1}]
        idx = 0

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            del base_url, access_key, params, timeout
            nonlocal idx
            payload = statuses[min(idx, len(statuses) - 1)]
            idx += 1
            return {'data': payload}

        def fake_download_results(job_id, detail_data, result_dir, *, ctx, sandbox):
            del job_id, detail_data, ctx, sandbox
            result_dir.mkdir(parents=True, exist_ok=True)
            (result_dir / 'log').write_text('boom\n', encoding='utf-8')
            return ['log'], 'boom\n'

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_module, '_get', fake_get)
        monkeypatch.setattr(
            bohrium_module, 'download_bohrium_results', fake_download_results
        )
        import time as time_module

        monkeypatch.setattr(bohrium_module, 'time', time_module, raising=False)
        monkeypatch.setattr(bohrium_module.time, 'sleep', sleep_calls.append)

        result = asyncio.run(
            tool.execute(
                {
                    'action': 'download',
                    'job_id': 'job-failed',
                    'result_dir': str(tmp_path / 'results'),
                }
            )
        )

        assert result.status == 'error'
        payload = json.loads(result.content)
        assert payload['status'] == 'Failed'
        assert payload['files'] == ['log']
        assert payload['log_tail'] == 'boom\n'
        assert sleep_calls == [3, 3]

    def test_download_remote_share_stages_and_uploads(self, tmp_path, monkeypatch):
        session = FakeRemoteSession(is_open=True)
        tool = BohriumTool(session=session, workdir=tmp_path)

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            del base_url, access_key, params, timeout
            return {'data': {'status': 2}}

        def fake_download_results(job_id, detail_data, result_dir, *, ctx, sandbox):
            del job_id, detail_data, ctx, sandbox
            result_dir.mkdir(parents=True, exist_ok=True)
            (result_dir / 'log').write_text('done\n', encoding='utf-8')
            return ['log'], 'done\n'

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_module, '_get', fake_get)
        monkeypatch.setattr(
            bohrium_module, 'download_bohrium_results', fake_download_results
        )

        result = asyncio.run(
            tool.execute(
                {
                    'action': 'download',
                    'job_id': 'job-remote',
                    'result_dir': '/share/remote/results',
                }
            )
        )

        assert result.status == 'success'
        payload = json.loads(result.content)
        assert payload['result_dir'] == '/share/remote/results'
        assert session.upload_calls
        local_dir, remote_dir, exclude = session.upload_calls[0]
        assert local_dir != '/share/remote/results'
        assert remote_dir == '/share/remote/results'
        assert exclude is None

    def test_download_remote_share_upload_failure_returns_local_staging_path(
        self, tmp_path, monkeypatch
    ):
        session = FakeRemoteSession(is_open=True)
        session.upload_error = RuntimeError('upload failed')
        tool = BohriumTool(session=session, workdir=tmp_path)

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            del base_url, access_key, params, timeout
            return {'data': {'status': 2}}

        def fake_download_results(job_id, detail_data, result_dir, *, ctx, sandbox):
            del job_id, detail_data, ctx, sandbox
            result_dir.mkdir(parents=True, exist_ok=True)
            (result_dir / 'log').write_text('done\n', encoding='utf-8')
            return ['log'], 'done\n'

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_module, '_get', fake_get)
        monkeypatch.setattr(
            bohrium_module, 'download_bohrium_results', fake_download_results
        )

        result = asyncio.run(
            tool.execute(
                {
                    'action': 'download',
                    'job_id': 'job-remote-fallback',
                    'result_dir': '/share/remote/results',
                }
            )
        )

        assert result.status == 'success'
        payload = json.loads(result.content)
        assert payload['result_dir'] != '/share/remote/results'
        assert payload['result_dir'].startswith('/')
        assert session.upload_calls

    def test_download_unknown_status_returns_error(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            del base_url, access_key, params, timeout
            return {'data': {'status': 99}}

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_module, '_get', fake_get)

        result = asyncio.run(
            tool.execute(
                {
                    'action': 'download',
                    'job_id': 'job-unknown',
                    'result_dir': str(tmp_path / 'results'),
                }
            )
        )

        assert result.status == 'error'
        assert 'Unexpected job status' in result.content
        assert 'code=99' in result.content
