"""Run pipeline outcome tests: verify run_agent success/failure semantics per path.

计价化后扣费迁移到 tools-server（evo 不再调用 use_quota），本文件改为校验 run_agent
的成功/失败语义与生命周期事件：成功路径返回成功、cancel/error/invalid_finish 返回失败，
并发出对应的 run_result / stream_closed / cancelled / error 事件。

All external dependencies mocked per D-10.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matmaster.core.playground import ExecutionEnvironment
from matmaster.types.cancellation import CancellationController
from matmaster.types.messages import LLMResponse, StreamChunk
from matmaster.types.run_metadata import RunMetadata
from tests.conftest import ProviderProtocolAttrs

# ── Mock LLM providers for different outcomes ────────


class _SuccessLLM(ProviderProtocolAttrs):
    """Mock LLM: natural finish."""

    async def __aenter__(self) -> _SuccessLLM:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content='success', finish_reason='stop')

    async def chat_stream(
        self, messages, tools=None, *, timeout=None
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content='success', finish_reason='stop')


class _InvalidFinishLLM(ProviderProtocolAttrs):
    """Mock LLM: streams content but ends with a non-committable finish reason."""

    async def __aenter__(self) -> _InvalidFinishLLM:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content='partial', finish_reason='length')

    async def chat_stream(
        self, messages, tools=None, *, timeout=None
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content='partial')
        yield StreamChunk(finish_reason='length')


class _EmptyStopLLM(ProviderProtocolAttrs):
    """Mock LLM: clean stop with no user-visible content or tool calls."""

    stream_timeout = 10.0
    max_retries = 1
    retry_delay = 0.0

    async def __aenter__(self) -> _EmptyStopLLM:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content=None, finish_reason='stop')

    async def chat_stream(
        self, messages, tools=None, *, timeout=None
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(finish_reason='stop')


class _SentinelStopLLM(ProviderProtocolAttrs):
    """Mock LLM: returns an empty-value sentinel as the whole answer."""

    stream_timeout = 10.0
    max_retries = 1
    retry_delay = 0.0

    async def __aenter__(self) -> _SentinelStopLLM:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content='none', finish_reason='stop')

    async def chat_stream(
        self, messages, tools=None, *, timeout=None
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content='none')
        yield StreamChunk(finish_reason='stop')


class _ErrorLLM(ProviderProtocolAttrs):
    """Mock LLM: raises exception."""

    async def __aenter__(self) -> _ErrorLLM:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass

    async def chat(self, messages, tools=None) -> LLMResponse:
        raise RuntimeError('LLM error')

    async def chat_stream(
        self, messages, tools=None, *, timeout=None
    ) -> AsyncIterator[StreamChunk]:
        raise RuntimeError('LLM error during streaming')
        yield  # make it an async generator


def _async_collect(payloads: list) -> Callable:
    """Return an async send_cb that appends payloads to a list."""

    async def _cb(payload: Any) -> None:
        payloads.append(payload)

    return _cb


def _make_environment(tmp_path: Path) -> ExecutionEnvironment:
    return ExecutionEnvironment(
        workdir=tmp_path / 'workspace',
        session_type='local',
        cache_area=tmp_path / 'cache',
        metadata=RunMetadata(run_dir=str(tmp_path), task_id='test'),
    )


def _make_cancel_token(*, cancelled: bool = False):
    controller = CancellationController()
    if cancelled:
        controller.cancel()
    return controller.token


def _build_patched_service(mock_llm, mock_sessions_svc=None, mock_environment=None):
    """Build an AgentRunService with standard mocks applied."""
    AgentRunService = pytest.importorskip(
        "src.services.agent_run_service",
        reason="src not available (isolation test)",
    ).AgentRunService

    if mock_sessions_svc is None:
        mock_sessions_svc = MagicMock()
        mock_sessions_svc.get_session_user_id.return_value = 'user-123'

    svc = AgentRunService(sessions_service=mock_sessions_svc)
    # mock_llm stored for _run_agent to wire up via build_provider_bundle patch
    svc._test_mock_llm = mock_llm

    mock_pg = MagicMock()
    if mock_environment is not None:
        mock_pg.prepare.return_value = mock_environment
    mock_pg.config_path = Path('config/config.yaml')
    mock_pg.session = None

    return svc, mock_pg


def _run_agent(
    svc,
    mock_pg,
    cancel_token=None,
    send_cb=None,
    *,
    return_result: bool = False,
):
    """Run agent with standard patches.

    默认返回 run_agent 是否成功（结果首元素为 True）；``return_result=True`` 时直接返回
    run_agent 的完整结果元组，供需要校验失败原因/耗时的用例使用。
    """
    with (
        patch.object(svc._pg_manager, 'get_or_create', return_value=mock_pg),
        patch(
            'src.services.agent_run_bohrium_stage.BohriumSetupService'
        ) as mock_bohrium_cls,
        patch('src.services.agent_run_service.get_chat_events_table') as mock_events_fn,
        patch('src.services.agent_run_service.get_redis_dao') as mock_redis_fn,
        patch(
            'matmaster.providers.llm_factory.build_provider_bundle',
            return_value=SimpleNamespace(
                provider=svc._test_mock_llm,
                model="test-model",
                model_profile="test-profile",
                model_route="test-route",
                provider_name="test-provider",
                model_family="test-family",
                context_limit=345_000,
                context_limit_source="profile",
            ),
        ),
        patch('matmaster.config.loader.load_llm_config', return_value=MagicMock()),
    ):
        mock_bohrium_result = MagicMock()
        mock_bohrium_result.ssh_attached = False
        mock_bohrium_result.abort_result = None
        mock_bohrium_result.execution_session = None
        mock_bohrium_result.execution_workdir = None
        mock_bohrium_result.session_type = None
        mock_bohrium_result._asdict.return_value = {
            'ssh_attached': False,
            'abort_result': None,
            'execution_session': None,
            'execution_workdir': None,
            'session_type': None,
        }
        mock_bohrium_svc = mock_bohrium_cls.return_value
        mock_bohrium_svc.load_credentials.return_value = ({}, None, 'org-1')
        mock_bohrium_svc.setup.return_value = mock_bohrium_result
        mock_bohrium_svc.run_setup = AsyncMock(return_value=mock_bohrium_result)
        mock_bohrium_svc.run_cleanup = AsyncMock()

        mock_events_table = MagicMock()
        mock_events_table.get_session_events.return_value = []
        mock_events_table.get_history_checkpoints.return_value = []
        mock_events_table.has_user_turn_context.return_value = False
        mock_events_table.get_session_user_query_events.return_value = []
        mock_events_table.query_context_events.return_value = []
        mock_events_table.get_recent_context_anchor_events.return_value = []
        mock_events_table.query_user_turn_context_by_invocation.return_value = None
        mock_events_table.add_event.return_value = True
        mock_events_fn.return_value = mock_events_table

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        result = asyncio.run(
            svc.run_agent(
                session_id='sess-q',
                user_prompt='quota test',
                send_cb=send_cb or AsyncMock(),
                cancel_token=cancel_token or _make_cancel_token(),
                mode='direct',
                task_id='task-q',
                invocation_id='inv-task-q',
            )
        )

    if return_result:
        return result
    return result[0] is True


# ── QUAL-05: Quota pipeline tests ────────────────────


class TestRunSucceedsOnSuccess:
    """Verify run_agent reports success when the kernel completes naturally."""

    def test_run_succeeds_on_success(self, tmp_path: Path) -> None:
        """Verify run_agent reports success when the kernel completes naturally."""
        environment = _make_environment(tmp_path)
        mock_llm = _SuccessLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_environment=environment)

        called = _run_agent(svc, mock_pg)
        assert called, 'run should succeed on success path'

    def test_run_result_event_is_sent_on_success(self, tmp_path: Path) -> None:
        """Verify run_agent emits run_result and stream_closed on success."""
        environment = _make_environment(tmp_path)
        mock_llm = _SuccessLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_environment=environment)
        payloads: list[dict[str, Any]] = []

        _run_agent(
            svc,
            mock_pg,
            send_cb=_async_collect(payloads),
        )

        run_result_payload = next(
            (payload for payload in payloads if payload.get('type') == 'run_result'),
            None,
        )
        assert run_result_payload is not None
        assert run_result_payload['content']['status'] == 'completed'
        assert run_result_payload['content']['reason'] == 'natural'
        assert run_result_payload['content']['content'] == 'success'
        assert 'status' not in run_result_payload
        assert 'reason' not in run_result_payload
        assert 'final_content' not in run_result_payload
        assert run_result_payload['source'] == 'MatMaster'

        stream_closed_payload = next(
            (payload for payload in payloads if payload.get('type') == 'stream_closed'),
            None,
        )
        assert stream_closed_payload is not None
        assert stream_closed_payload['source'] == 'System'
        assert stream_closed_payload['task_completed'] is True
        assert stream_closed_payload['end_reason'] == 'natural'
        payload_types = [payload.get('type') for payload in payloads]
        assert payload_types.index('run_result') < payload_types.index('stream_closed')

    def test_success_emits_run_result_before_stream_closed(
        self, tmp_path: Path
    ) -> None:
        """run_result is emitted before stream_closed on natural finish.

        Post-kernel ResponseEvent (stream_state=None) was removed because
        streaming chunks already deliver content; the duplicate caused
        double-render.  Only run_result → stream_closed ordering is verified.
        """
        environment = _make_environment(tmp_path)
        mock_llm = _SuccessLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_environment=environment)
        payloads: list[dict[str, Any]] = []

        _run_agent(
            svc,
            mock_pg,
            send_cb=_async_collect(payloads),
        )

        payload_types = [payload.get('type') for payload in payloads]
        assert 'run_result' in payload_types
        assert 'stream_closed' in payload_types
        assert payload_types.index('run_result') < payload_types.index('stream_closed')


class TestRunFailsOnCancel:
    """Verify run_agent reports failure when the task is cancelled."""

    def test_run_fails_on_cancel(self, tmp_path: Path) -> None:
        """Verify run_agent reports failure when the task is cancelled."""
        environment = _make_environment(tmp_path)
        mock_llm = _SuccessLLM()  # LLM would succeed, but the token is pre-cancelled
        svc, mock_pg = _build_patched_service(mock_llm, mock_environment=environment)

        # Pre-cancel before run -> kernel returns cancelled immediately
        cancel_token = _make_cancel_token(cancelled=True)

        called = _run_agent(svc, mock_pg, cancel_token=cancel_token)
        assert not called, 'run should not succeed on cancel'

    def test_cancelled_run_emits_stream_closed_event(self, tmp_path: Path) -> None:
        """Verify cancelled runs still emit stream_closed for frontend stream closure."""
        environment = _make_environment(tmp_path)
        mock_llm = _SuccessLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_environment=environment)
        payloads: list[dict[str, Any]] = []

        cancel_token = _make_cancel_token(cancelled=True)

        _run_agent(
            svc,
            mock_pg,
            cancel_token=cancel_token,
            send_cb=_async_collect(payloads),
        )

        assert any(payload.get('type') == 'cancelled' for payload in payloads)
        stream_closed_payload = next(
            (payload for payload in payloads if payload.get('type') == 'stream_closed'),
            None,
        )
        assert stream_closed_payload is not None
        assert stream_closed_payload['task_completed'] is False
        assert stream_closed_payload['end_reason'] == 'cancelled'
        payload_types = [payload.get('type') for payload in payloads]
        assert payload_types.index('cancelled') < payload_types.index('stream_closed')

    def test_cancelled_run_returns_failure_result(self, tmp_path: Path) -> None:
        """Verify cancelled runs return a failure result for Worker notifications."""
        environment = _make_environment(tmp_path)
        mock_llm = _SuccessLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_environment=environment)

        cancel_token = _make_cancel_token(cancelled=True)

        result = _run_agent(
            svc,
            mock_pg,
            cancel_token=cancel_token,
            return_result=True,
        )

        assert isinstance(result, tuple)
        assert result[0] == (False, 'cancelled')
        assert isinstance(result[1], int)
        assert result[1] >= 0


class TestRunFailsOnError:
    """Verify run_agent reports failure when the kernel raises / finishes invalid."""

    def test_run_fails_on_error(self, tmp_path: Path) -> None:
        """Verify run_agent reports failure when the kernel raises exception."""
        environment = _make_environment(tmp_path)
        mock_llm = _ErrorLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_environment=environment)

        called = _run_agent(svc, mock_pg)
        assert not called, 'run should not succeed on error'

    def test_error_run_emits_stream_closed_event(self, tmp_path: Path) -> None:
        """Verify error runs emit stream_closed after the error event."""
        environment = _make_environment(tmp_path)
        mock_llm = _ErrorLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_environment=environment)
        payloads: list[dict[str, Any]] = []

        _run_agent(
            svc,
            mock_pg,
            send_cb=_async_collect(payloads),
        )

        assert any(payload.get('type') == 'error' for payload in payloads)
        stream_closed_payload = next(
            (payload for payload in payloads if payload.get('type') == 'stream_closed'),
            None,
        )
        assert stream_closed_payload is not None
        assert stream_closed_payload['task_completed'] is False
        assert stream_closed_payload['end_reason'] == 'error'
        assert stream_closed_payload['treat_as_failure'] is True
        payload_types = [payload.get('type') for payload in payloads]
        assert payload_types.index('error') < payload_types.index('stream_closed')

    def test_error_run_returns_failure_result(self, tmp_path: Path) -> None:
        """Verify exception paths return failure so Worker won't notify success."""
        environment = _make_environment(tmp_path)
        mock_llm = _ErrorLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_environment=environment)

        result = _run_agent(
            svc,
            mock_pg,
            return_result=True,
        )

        assert isinstance(result, tuple)
        assert result[0][0] is False
        assert 'LLM error' in result[0][1]
        assert isinstance(result[1], int)
        assert result[1] >= 0

    def test_run_fails_on_invalid_finish(self, tmp_path: Path) -> None:
        """Verify run_agent reports failure when run_result validation fails."""
        environment = _make_environment(tmp_path)
        mock_llm = _InvalidFinishLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_environment=environment)

        called = _run_agent(svc, mock_pg)
        assert not called, 'run should not succeed on invalid finish'

    def test_invalid_finish_emits_error_and_stream_closed_event(
        self, tmp_path: Path
    ) -> None:
        """Verify invalid finishes emit a visible error before closing the stream."""
        environment = _make_environment(tmp_path)
        mock_llm = _InvalidFinishLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_environment=environment)
        payloads: list[dict[str, Any]] = []

        _run_agent(
            svc,
            mock_pg,
            send_cb=_async_collect(payloads),
        )

        run_result_payload = next(
            (payload for payload in payloads if payload.get('type') == 'run_result'),
            None,
        )
        assert run_result_payload is not None
        assert run_result_payload['content']['status'] == 'failed'
        assert run_result_payload['content']['reason'] == 'invalid_finish'
        assert run_result_payload['content']['finish_detail']['kind'] == (
            'output_length_exceeded'
        )
        assert (
            run_result_payload['content']['finish_detail']['provider_finish_reason']
            == 'length'
        )
        error_payload = next(
            (payload for payload in payloads if payload.get('type') == 'error'),
            None,
        )
        assert error_payload is not None
        assert error_payload['source'] == 'System'
        assert '输出 token 上限截断' in error_payload['content']['message']
        stream_closed_payload = next(
            (payload for payload in payloads if payload.get('type') == 'stream_closed'),
            None,
        )
        assert stream_closed_payload is not None
        assert stream_closed_payload['task_completed'] is False
        assert stream_closed_payload['end_reason'] == 'invalid_finish'
        assert stream_closed_payload['treat_as_failure'] is True
        payload_types = [payload.get('type') for payload in payloads]
        assert payload_types.index('run_result') < payload_types.index('error')
        assert payload_types.index('error') < payload_types.index('stream_closed')

    def test_invalid_finish_returns_failure_result(self, tmp_path: Path) -> None:
        """Verify failed finish states return failure for Worker status updates."""
        environment = _make_environment(tmp_path)
        mock_llm = _InvalidFinishLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_environment=environment)

        result = _run_agent(
            svc,
            mock_pg,
            return_result=True,
        )

        assert isinstance(result, tuple)
        assert result[0] == (False, 'invalid_finish')
        assert isinstance(result[1], int)
        assert result[1] >= 0

    def test_run_fails_on_empty_stop_invalid_finish(self, tmp_path: Path) -> None:
        """Verify empty stop finish validation failure reports run failure."""
        environment = _make_environment(tmp_path)
        mock_llm = _EmptyStopLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_environment=environment)

        called = _run_agent(svc, mock_pg)
        assert not called, 'run should not succeed on empty stop'

    def test_empty_stop_invalid_finish_emits_error_and_stream_closed_event(
        self, tmp_path: Path
    ) -> None:
        """Verify empty stop finishes use the public invalid_finish stream shape."""
        environment = _make_environment(tmp_path)
        mock_llm = _EmptyStopLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_environment=environment)
        payloads: list[dict[str, Any]] = []

        _run_agent(
            svc,
            mock_pg,
            send_cb=_async_collect(payloads),
        )

        run_result_payload = next(
            (payload for payload in payloads if payload.get('type') == 'run_result'),
            None,
        )
        assert run_result_payload is not None
        assert run_result_payload['content']['status'] == 'failed'
        assert run_result_payload['content']['reason'] == 'invalid_finish'
        assert run_result_payload['content']['content'] == ''
        assert 'final_content' not in run_result_payload
        assert run_result_payload['content']['finish_detail']['kind'] == (
            'empty_response'
        )
        error_payload = next(
            (payload for payload in payloads if payload.get('type') == 'error'),
            None,
        )
        assert error_payload is not None
        assert error_payload['source'] == 'System'
        assert '没有返回可见最终回答' in error_payload['content']['message']
        stream_closed_payload = next(
            (payload for payload in payloads if payload.get('type') == 'stream_closed'),
            None,
        )
        assert stream_closed_payload is not None
        assert stream_closed_payload['task_completed'] is False
        assert stream_closed_payload['end_reason'] == 'invalid_finish'
        assert stream_closed_payload['treat_as_failure'] is True
        payload_types = [payload.get('type') for payload in payloads]
        assert payload_types.index('run_result') < payload_types.index('error')
        assert payload_types.index('error') < payload_types.index('stream_closed')

    def test_empty_stop_invalid_finish_returns_failure_result(
        self, tmp_path: Path
    ) -> None:
        """Verify empty stop invalid_finish returns failure for Worker status."""
        environment = _make_environment(tmp_path)
        mock_llm = _EmptyStopLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_environment=environment)

        result = _run_agent(
            svc,
            mock_pg,
            return_result=True,
        )

        assert isinstance(result, tuple)
        assert result[0] == (False, 'invalid_finish')
        assert isinstance(result[1], int)
        assert result[1] >= 0

    def test_run_fails_on_sentinel_stop_invalid_finish(self, tmp_path: Path) -> None:
        """Verify empty-value sentinel finish validation reports run failure."""
        environment = _make_environment(tmp_path)
        mock_llm = _SentinelStopLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_environment=environment)

        called = _run_agent(svc, mock_pg)
        assert not called, 'run should not succeed on sentinel stop'

    def test_sentinel_stop_invalid_finish_emits_error_and_stream_closed_event(
        self, tmp_path: Path
    ) -> None:
        """Verify sentinel answers use the public invalid_finish stream shape."""
        environment = _make_environment(tmp_path)
        mock_llm = _SentinelStopLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_environment=environment)
        payloads: list[dict[str, Any]] = []

        _run_agent(
            svc,
            mock_pg,
            send_cb=_async_collect(payloads),
        )

        run_result_payload = next(
            (payload for payload in payloads if payload.get('type') == 'run_result'),
            None,
        )
        assert run_result_payload is not None
        assert run_result_payload['content']['status'] == 'failed'
        assert run_result_payload['content']['reason'] == 'invalid_finish'
        assert run_result_payload['content']['content'] == ''
        assert 'final_content' not in run_result_payload
        error_payload = next(
            (payload for payload in payloads if payload.get('type') == 'error'),
            None,
        )
        assert error_payload is not None
        assert error_payload['source'] == 'System'
        assert error_payload['content']['message']
        stream_closed_payload = next(
            (payload for payload in payloads if payload.get('type') == 'stream_closed'),
            None,
        )
        assert stream_closed_payload is not None
        assert stream_closed_payload['task_completed'] is False
        assert stream_closed_payload['end_reason'] == 'invalid_finish'
        assert stream_closed_payload['treat_as_failure'] is True
        payload_types = [payload.get('type') for payload in payloads]
        assert payload_types.index('run_result') < payload_types.index('error')
        assert payload_types.index('error') < payload_types.index('stream_closed')


class TestRunSucceedsOnNaturalFinish:
    """Verify run_agent reports success on a natural finish (post-billing migration)."""

    def test_run_succeeds_on_natural_finish(self, tmp_path: Path) -> None:
        """Natural finish -> run_agent returns success (result first element is True)."""
        environment = _make_environment(tmp_path)
        mock_llm = _SuccessLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_environment=environment)

        result = _run_agent(svc, mock_pg, return_result=True)

        assert isinstance(result, tuple)
        assert result[0] is True
