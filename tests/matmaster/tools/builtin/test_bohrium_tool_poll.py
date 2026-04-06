"""Poll-focused tests for the builtin Bohrium tool."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import matmaster.tools.builtin.bohrium_tool as bohrium_module
import matmaster.tools.builtin.bohrium_tool._results as bohrium_results_module
from matmaster.tools.builtin.bohrium_tool import BohriumTool
from matmaster.tools.tool_result import ToolResult
from tests.matmaster.tools.builtin.test_bohrium_tool_helpers import (
    _FakeDownloadResponse,
    _patch_bridge,
    _zip_bytes,
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
        monkeypatch.setattr(bohrium_module, '_get', fake_get)

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
        monkeypatch.setattr(bohrium_module, '_get', fake_get)
        import time as time_module

        monkeypatch.setattr(bohrium_module, 'time', time_module, raising=False)
        monkeypatch.setattr(bohrium_module.time, 'sleep', sleep_calls.append)

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

    def test_poll_running_without_wait_remains_single_shot(
        self, tmp_path, monkeypatch
    ):
        tool = BohriumTool(workdir=tmp_path)
        get_calls: list[str] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            del base_url, access_key, params, timeout
            get_calls.append(path)
            return {'data': {'status': 1}}

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_module, '_get', fake_get)

        result = asyncio.run(tool.execute({'action': 'poll', 'job_id': 'job-789'}))

        assert result.status == 'success'
        payload = json.loads(result.content)
        assert payload['status'] == 'Running'
        assert get_calls == ['/openapi/v1/sandbox/job/job-789']

    def test_poll_failed_confirms_before_downloading(self, tmp_path, monkeypatch):
        tool = BohriumTool(workdir=tmp_path)
        get_calls: list[str] = []
        sleep_calls: list[float] = []
        statuses = [
            {
                'status': -1,
                'resultUrl': 'https://store.example/api/download/'
                'prefix/job-999.zip?token=root-token',
            },
            {
                'status': -1,
                'resultUrl': 'https://store.example/api/download/'
                'prefix/job-999.zip?token=root-token',
            },
            {
                'status': -1,
                'resultUrl': 'https://store.example/api/download/'
                'prefix/job-999.zip?token=root-token',
            },
        ]
        status_index = 0
        download_calls: list[tuple[int | str, dict, Path, bool]] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            del base_url, access_key, params, timeout
            nonlocal status_index
            get_calls.append(path)
            detail_data = statuses[min(status_index, len(statuses) - 1)]
            status_index += 1
            return {'data': detail_data}

        def fake_download_results(job_id, detail_data, result_dir, *, ctx, sandbox):
            del ctx
            download_calls.append((job_id, detail_data, result_dir, sandbox))
            return ['log'], 'boom'

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_module, '_get', fake_get)
        import time as time_module

        monkeypatch.setattr(bohrium_module, 'time', time_module, raising=False)
        monkeypatch.setattr(bohrium_module.time, 'sleep', sleep_calls.append)
        monkeypatch.setattr(
            bohrium_module, 'download_bohrium_results', fake_download_results
        )

        result = asyncio.run(tool.execute({'action': 'poll', 'job_id': 'job-999'}))

        assert result.status == 'error'
        payload = json.loads(result.content)
        assert payload['status'] == 'Failed'
        assert payload['result_dir']
        assert payload['files'] == ['log']
        assert payload['log_tail'] == 'boom'
        assert get_calls == ['/openapi/v1/sandbox/job/job-999'] * 3
        assert sleep_calls == [3, 3]
        assert len(download_calls) == 1

    def test_poll_failed_downloads_log_from_file_token_and_result_url_zip(
        self, tmp_path, monkeypatch
    ):
        tool = BohriumTool(workdir=tmp_path)
        get_calls: list[str] = []
        file_token_calls: list[tuple[str, dict[str, str]]] = []
        iterate_calls: list[tuple[str, dict[str, str], dict[str, str]]] = []
        http_get_calls: list[tuple[str, object, object, int, bool]] = []
        sleep_calls: list[int | float] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            del base_url, access_key, params, timeout
            get_calls.append(path)
            return {
                'data': {
                    'status': -1,
                    'resultUrl': 'https://store.example/api/download/'
                    'prefix/job-123.zip?token=root-token',
                }
            }

        def fake_post(base_url, path, access_key, payload, timeout=30):
            del base_url, access_key, timeout
            file_token_calls.append((path, payload))
            return {
                'code': 0,
                'data': {
                    'host': 'https://store.example',
                    'path': 'prefix/log',
                    'token': 'log-token',
                },
            }

        def fake_requests_post(url, *, headers=None, json=None, timeout=30):
            del timeout
            iterate_calls.append((url, headers, json))
            return _FakeDownloadResponse(
                json_data={
                    'code': 0,
                    'data': {
                        'hasNext': False,
                        'objects': [
                            {
                                'path': 'prefix/job-123.zip',
                                'isDir': False,
                            },
                            {
                                'path': 'prefix/log',
                                'isDir': False,
                            },
                        ],
                    },
                }
            )

        def fake_requests_get(
            url, *, headers=None, params=None, timeout=30, stream=False
        ):
            http_get_calls.append((url, headers, params, timeout, stream))
            if url.startswith('https://store.example/api/download/prefix/log'):
                return _FakeDownloadResponse(
                    content=b'traceback: model not found\n',
                )
            if url.startswith('https://store.example/api/download/prefix/job-123.zip'):
                return _FakeDownloadResponse(
                    content=_zip_bytes(
                        {
                            'log': 'traceback: model not found\n',
                            'stderr.txt': 'x',
                        }
                    ),
                )
            if not stream:
                return _FakeDownloadResponse(json_data={'data': {'list': []}})
            return _FakeDownloadResponse(content=b'')

        def fake_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_module, '_get', fake_get)
        monkeypatch.setattr(bohrium_module, '_post', fake_post)
        monkeypatch.setattr(bohrium_results_module, '_post', fake_post)
        monkeypatch.setattr(bohrium_module.requests, 'post', fake_requests_post)
        monkeypatch.setattr(bohrium_module.requests, 'get', fake_requests_get)
        import time as time_module

        monkeypatch.setattr(bohrium_module, 'time', time_module, raising=False)
        monkeypatch.setattr(bohrium_module.time, 'sleep', fake_sleep)

        result = asyncio.run(tool.execute({'action': 'poll', 'job_id': 'job-123'}))

        assert isinstance(result, ToolResult)
        assert result.status == 'error'
        payload = json.loads(result.content)
        assert payload['status'] == 'Failed'
        assert payload['result_dir']
        assert 'traceback: model not found' in payload['log_tail']
        assert 'log' in payload['files']
        assert any(
            url.startswith('https://store.example/api/download/prefix/log')
            for url, _, _, _, _ in http_get_calls
        )
        assert any(
            url.startswith('https://store.example/api/download/prefix/job-123.zip')
            for url, _, _, _, _ in http_get_calls
        )
        assert file_token_calls == [
            (
                '/openapi/v1/sandbox/job/file/token',
                {'filePath': 'log', 'jobId': 'job-123'},
            )
        ]
        assert iterate_calls == [
            (
                'https://store.example/api/iterate',
                {
                    'Authorization': 'Bearer root-token',
                    'Content-Type': 'application/json',
                },
                {'prefix': 'prefix/'},
            )
        ]
        assert get_calls == ['/openapi/v1/sandbox/job/job-123'] * 3
        assert sleep_calls == [3, 3]

    def test_poll_failed_returns_log_when_zip_is_not_ready(
        self, tmp_path, monkeypatch
    ):
        tool = BohriumTool(workdir=tmp_path)
        get_calls: list[str] = []
        file_token_calls: list[tuple[str, dict[str, str]]] = []
        iterate_calls: list[tuple[str, dict[str, str], dict[str, str]]] = []
        http_get_calls: list[tuple[str, object, object, int, bool]] = []
        sleep_calls: list[int | float] = []

        def fake_get(base_url, path, access_key, params=None, timeout=30):
            del base_url, access_key, params, timeout
            get_calls.append(path)
            return {
                'data': {
                    'status': -1,
                    'resultUrl': 'https://store.example/api/download/'
                    'prefix/job-456.zip?token=root-token',
                }
            }

        def fake_post(base_url, path, access_key, payload, timeout=30):
            del base_url, access_key, timeout
            file_token_calls.append((path, payload))
            return {
                'code': 0,
                'data': {
                    'host': 'https://store.example',
                    'path': 'prefix/log',
                    'token': 'log-token',
                },
            }

        def fake_requests_post(url, *, headers=None, json=None, timeout=30):
            del timeout
            iterate_calls.append((url, headers, json))
            return _FakeDownloadResponse(
                json_data={
                    'code': 0,
                    'data': {
                        'hasNext': False,
                        'objects': [
                            {
                                'path': 'prefix/log',
                                'isDir': False,
                            }
                        ],
                    },
                }
            )

        def fake_requests_get(
            url, *, headers=None, params=None, timeout=30, stream=False
        ):
            http_get_calls.append((url, headers, params, timeout, stream))
            if url.startswith('https://store.example/api/download/prefix/log'):
                return _FakeDownloadResponse(
                    content=b'runtime error: missing model\n',
                )
            if url.startswith('https://store.example/api/download/prefix/job-456.zip'):
                return _FakeDownloadResponse(status_code=404)
            return _FakeDownloadResponse(status_code=404)

        monkeypatch.delenv('BOHRIUM_USE_SANDBOX', raising=False)
        _patch_bridge(monkeypatch)
        monkeypatch.setattr(bohrium_module, '_get', fake_get)
        monkeypatch.setattr(bohrium_module, '_post', fake_post)
        monkeypatch.setattr(bohrium_results_module, '_post', fake_post)
        monkeypatch.setattr(bohrium_module.requests, 'post', fake_requests_post)
        monkeypatch.setattr(bohrium_module.requests, 'get', fake_requests_get)
        import time as time_module

        monkeypatch.setattr(bohrium_module, 'time', time_module, raising=False)
        monkeypatch.setattr(bohrium_module.time, 'sleep', sleep_calls.append)

        result = asyncio.run(tool.execute({'action': 'poll', 'job_id': 'job-456'}))

        assert isinstance(result, ToolResult)
        assert result.status == 'error'
        payload = json.loads(result.content)
        assert payload['result_dir']
        assert payload['files'] == ['log']
        assert 'runtime error: missing model' in payload['log_tail']
        assert any(
            url.startswith('https://store.example/api/download/prefix/log')
            for url, _, _, _, _ in http_get_calls
        )
        assert any(
            url.startswith('https://store.example/api/download/prefix/job-456.zip')
            for url, _, _, _, _ in http_get_calls
        )
        assert file_token_calls == [
            (
                '/openapi/v1/sandbox/job/file/token',
                {'filePath': 'log', 'jobId': 'job-456'},
            )
        ]
        assert iterate_calls == [
            (
                'https://store.example/api/iterate',
                {
                    'Authorization': 'Bearer root-token',
                    'Content-Type': 'application/json',
                },
                {'prefix': 'prefix/'},
            )
        ]
        assert get_calls == ['/openapi/v1/sandbox/job/job-456'] * 3
        assert sleep_calls == [3, 3]
