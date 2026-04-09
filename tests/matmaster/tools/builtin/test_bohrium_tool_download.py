"""Download-focused tests for the builtin Bohrium tool."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import matmaster.bohrium.artifacts as bohrium_artifacts_module
import matmaster.bohrium.client as bohrium_client_module
import matmaster.tools.builtin.bohrium_tool.tool as bohrium_tool_module
from matmaster.tools.builtin.bohrium_tool import BohriumTool
from matmaster.tools.tool_result import ToolResult
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import (
    FakeRemoteSession,
    _FakeDownloadResponse,
    _patch_bridge,
)


class TestBohriumDownloadExecution:
    def test_download_requires_result_dir(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)

        result = asyncio.run(tool.execute({'action': 'download', 'job_id': 'job-1'}))

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
        monkeypatch.setattr(bohrium_client_module, '_get', fake_get)

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

    def test_download_remote_share_without_session_errors(self, tmp_path, monkeypatch):
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

        def fake_download_artifacts(*, job_id, detail_data, result_dir, ctx):
            del job_id, detail_data, ctx
            result_dir.mkdir(parents=True, exist_ok=True)
            (result_dir / 'log').write_text('done\n', encoding='utf-8')
            return ['log'], 'done\n'

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, '_get', fake_get)
        monkeypatch.setattr(
            bohrium_tool_module, 'download_job_artifacts', fake_download_artifacts
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

    def test_download_unexpected_transfer_error_returns_tool_result(
        self, tmp_path, monkeypatch
    ):
        tool = BohriumTool(workdir=tmp_path)

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            del base_url, path, access_key, params, timeout
            return {'data': {'status': 2}}

        def fake_download_artifacts(*, job_id, detail_data, result_dir, ctx):
            del job_id, detail_data, result_dir, ctx
            raise RuntimeError('boom')

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, '_get', fake_get)
        monkeypatch.setattr(
            bohrium_tool_module, 'download_job_artifacts', fake_download_artifacts
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

        assert result.status == 'error'
        assert result.content == 'Download failed: boom'

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

        def fake_download_artifacts(*, job_id, detail_data, result_dir, ctx):
            del job_id, detail_data, ctx
            result_dir.mkdir(parents=True, exist_ok=True)
            (result_dir / 'log').write_text('boom\n', encoding='utf-8')
            return ['log'], 'boom\n'

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, '_get', fake_get)
        monkeypatch.setattr(
            bohrium_tool_module, 'download_job_artifacts', fake_download_artifacts
        )
        import time as time_module

        monkeypatch.setattr(bohrium_tool_module, 'time', time_module, raising=False)
        monkeypatch.setattr(bohrium_tool_module.time, 'sleep', sleep_calls.append)

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
        assert sleep_calls == []

    def test_download_remote_share_stages_and_uploads(self, tmp_path, monkeypatch):
        session = FakeRemoteSession(is_open=True)
        tool = BohriumTool(session=session, workdir=tmp_path)

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            del base_url, access_key, params, timeout
            return {'data': {'status': 2}}

        def fake_download_artifacts(*, job_id, detail_data, result_dir, ctx):
            del job_id, detail_data, ctx
            result_dir.mkdir(parents=True, exist_ok=True)
            (result_dir / 'log').write_text('done\n', encoding='utf-8')
            return ['log'], 'done\n'

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, '_get', fake_get)
        monkeypatch.setattr(
            bohrium_tool_module, 'download_job_artifacts', fake_download_artifacts
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

        def fake_download_artifacts(*, job_id, detail_data, result_dir, ctx):
            del job_id, detail_data, ctx
            result_dir.mkdir(parents=True, exist_ok=True)
            (result_dir / 'log').write_text('done\n', encoding='utf-8')
            return ['log'], 'done\n'

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, '_get', fake_get)
        monkeypatch.setattr(
            bohrium_tool_module, 'download_job_artifacts', fake_download_artifacts
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
        monkeypatch.setattr(bohrium_client_module, '_get', fake_get)

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

    def test_download_failed_falls_back_to_individual_objects_when_zip_missing(
        self, tmp_path, monkeypatch
    ):
        tool = BohriumTool(workdir=tmp_path)
        http_get_calls: list[str] = []
        sleep_calls: list[float] = []

        statuses = [
            {
                'status': -1,
                'resultUrl': 'https://store.example/api/download/'
                'prefix/job-789.zip?token=root-token',
            },
            {
                'status': -1,
                'resultUrl': 'https://store.example/api/download/'
                'prefix/job-789.zip?token=root-token',
            },
            {
                'status': -1,
                'resultUrl': 'https://store.example/api/download/'
                'prefix/job-789.zip?token=root-token',
            },
        ]
        idx = 0

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            del base_url, access_key, params, timeout
            nonlocal idx
            detail = statuses[min(idx, len(statuses) - 1)]
            idx += 1
            return {'data': detail}

        def fake_requests_post(url, *, headers=None, json=None, timeout=30):
            del headers, json, timeout
            if url == 'https://openapi.test.dp.tech/openapi/v1/sandbox/job/file/token':
                return _FakeDownloadResponse(
                    json_data={
                        'code': 0,
                        'data': {
                            'host': 'https://store.example',
                            'path': 'prefix/log',
                            'token': 'log-token',
                        },
                    }
                )
            assert url == 'https://store.example/api/iterate'
            return _FakeDownloadResponse(
                json_data={
                    'code': 0,
                    'data': {
                        'hasNext': False,
                        'objects': [
                            {'path': 'prefix/log', 'isDir': False},
                            {'path': 'prefix/run_bader.sh', 'isDir': False},
                            {'path': 'prefix/OUT.ABACUS/warning.log', 'isDir': False},
                            {'path': 'prefix/bader_bin', 'isDir': False},
                        ],
                    },
                }
            )

        def fake_requests_get(
            url, *, headers=None, params=None, timeout=30, stream=False
        ):
            del headers, params, timeout
            http_get_calls.append(url)
            if url.startswith('https://store.example/api/download/prefix/log'):
                return _FakeDownloadResponse(content=b'job failed\n')
            if url.startswith('https://store.example/api/download/prefix/job-789.zip'):
                return _FakeDownloadResponse(status_code=404)
            if url.startswith('https://store.example/api/download/prefix/run_bader.sh'):
                return _FakeDownloadResponse(content=b'#!/bin/bash\necho run\n')
            if url.startswith(
                'https://store.example/api/download/prefix/OUT.ABACUS/warning.log'
            ):
                return _FakeDownloadResponse(content=b'warning details\n')
            if url.startswith('https://store.example/api/download/prefix/bader_bin'):
                return _FakeDownloadResponse(content=b'binary')
            return _FakeDownloadResponse(status_code=404)

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_client_module, '_get', fake_get)
        monkeypatch.setattr(
            bohrium_artifacts_module.requests, 'post', fake_requests_post
        )
        monkeypatch.setattr(bohrium_artifacts_module.requests, 'get', fake_requests_get)
        import time as time_module

        monkeypatch.setattr(bohrium_tool_module, 'time', time_module, raising=False)
        monkeypatch.setattr(bohrium_tool_module.time, 'sleep', sleep_calls.append)

        result = asyncio.run(
            tool.execute(
                {
                    'action': 'download',
                    'job_id': 'job-789',
                    'result_dir': str(tmp_path / 'results'),
                }
            )
        )

        assert result.status == 'error'
        payload = json.loads(result.content)
        result_dir = Path(payload['result_dir'])
        assert payload['files'] == [
            'log',
            'run_bader.sh',
            'OUT.ABACUS/warning.log',
            'bader_bin',
        ]
        assert (
            (result_dir / 'run_bader.sh')
            .read_text(encoding='utf-8')
            .startswith('#!/bin/bash')
        )
        assert (result_dir / 'OUT.ABACUS' / 'warning.log').read_text(
            encoding='utf-8'
        ) == 'warning details\n'
        assert (result_dir / 'bader_bin').read_bytes() == b'binary'
        assert sleep_calls == []
