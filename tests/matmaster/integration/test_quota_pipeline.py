"""Quota pipeline tests: verify use_quota deduction logic for all paths.

QUAL-05: use_quota on success, skip on failure, skip on cancel,
run_agent mode handling.

All external dependencies mocked per D-10.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from matmaster.types.context import PlaygroundContext
from matmaster.types.messages import LLMResponse, StreamChunk

# ── Mock LLM providers for different outcomes ────────


class _SuccessLLM:
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


class _InvalidFinishLLM:
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


class _ErrorLLM:
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


def _make_ctx(tmp_path: Path) -> PlaygroundContext:
    return PlaygroundContext(
        workdir=tmp_path / 'workspace',
        session_type='local',
        cache_area=tmp_path / 'cache',
        run_meta={'run_dir': str(tmp_path), 'task_id': 'test'},
    )


def _build_patched_service(mock_llm, mock_sessions_svc=None, mock_pg_ctx=None):
    """Build an AgentRunService with standard mocks applied."""
    from src.services.agent_run_service import AgentRunService

    if mock_sessions_svc is None:
        mock_sessions_svc = MagicMock()
        mock_sessions_svc.get_session_user_id.return_value = 'user-123'

    svc = AgentRunService(sessions_service=mock_sessions_svc)
    # mock_llm stored for _run_with_quota_mock to wire up via build_provider patch
    svc._test_mock_llm = mock_llm

    mock_pg = MagicMock()
    if mock_pg_ctx is not None:
        mock_pg.prepare.return_value = mock_pg_ctx
    mock_pg.config_path = Path('configs/mat_master/config.yaml')
    mock_pg.session = None

    return svc, mock_pg


def _run_with_quota_mock(
    svc,
    mock_pg,
    use_quota_mock,
    stop_event=None,
    send_cb=None,
    *,
    return_result: bool = False,
):
    """Run agent with standard patches and return whether use_quota was called."""
    with (
        patch.object(svc._pg_manager, 'get_or_create', return_value=mock_pg),
        patch('src.services.agent_run_service.BohriumSetupService') as mock_bohrium_cls,
        patch('src.services.agent_run_service.get_chat_events_table') as mock_events_fn,
        patch('src.services.agent_run_service.get_redis_dao') as mock_redis_fn,
        patch('src.services.agent_run_service.use_quota', use_quota_mock),
        patch(
            'matmaster.providers.llm_factory.build_provider',
            return_value=svc._test_mock_llm,
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
        mock_events_fn.return_value = mock_events_table

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        result = asyncio.run(
            svc.run_agent(
                session_id='sess-q',
                user_prompt='quota test',
                send_cb=send_cb or AsyncMock(),
                stop_event=stop_event or threading.Event(),
                mode='direct',
                reply_queue=None,
                task_id='task-q',
            )
        )

    if return_result:
        return result
    return use_quota_mock.called


# ── QUAL-05: Quota pipeline tests ────────────────────


class TestQuotaDeductedOnSuccess:
    """Verify use_quota called when kernel completes successfully."""

    def test_quota_deducted_on_success(self, tmp_path: Path) -> None:
        """Verify use_quota called when kernel completes successfully."""
        pg_ctx = _make_ctx(tmp_path)
        mock_llm = _SuccessLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_pg_ctx=pg_ctx)

        async def mock_use_quota(uid):
            pass

        use_quota_mock = MagicMock(side_effect=mock_use_quota)
        called = _run_with_quota_mock(svc, mock_pg, use_quota_mock)
        assert called, 'use_quota should be called on success'

    def test_run_result_event_is_sent_on_success(self, tmp_path: Path) -> None:
        """Verify run_agent emits run_result and stream_closed on success."""
        pg_ctx = _make_ctx(tmp_path)
        mock_llm = _SuccessLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_pg_ctx=pg_ctx)
        payloads: list[dict[str, Any]] = []

        async def mock_use_quota(uid):
            pass

        use_quota_mock = MagicMock(side_effect=mock_use_quota)
        _run_with_quota_mock(
            svc,
            mock_pg,
            use_quota_mock,
            send_cb=_async_collect(payloads),
        )

        run_result_payload = next(
            (payload for payload in payloads if payload.get('type') == 'run_result'),
            None,
        )
        assert run_result_payload is not None
        assert run_result_payload['status'] == 'completed'
        assert run_result_payload['reason'] == 'natural'
        assert run_result_payload['final_content'] == 'success'
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

        Note: post-kernel ResponseEvent was removed (910f537) because streaming
        chunks already deliver content; the duplicate caused double-render.
        """
        pg_ctx = _make_ctx(tmp_path)
        mock_llm = _SuccessLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_pg_ctx=pg_ctx)
        payloads: list[dict[str, Any]] = []

        async def mock_use_quota(uid):
            pass

        use_quota_mock = MagicMock(side_effect=mock_use_quota)
        _run_with_quota_mock(
            svc,
            mock_pg,
            use_quota_mock,
            send_cb=_async_collect(payloads),
        )

        response_payload = next(
            (
                payload
                for payload in payloads
                if payload.get('type') == 'response'
                and payload.get('stream_state') is None
            ),
            None,
        )
        assert response_payload is not None
        assert response_payload['content'] == 'success'
        assert response_payload['source'] == 'MatMaster'

        payload_types = [payload.get('type') for payload in payloads]
        assert payload_types.index('response') < payload_types.index('run_result')
        assert payload_types.index('run_result') < payload_types.index('stream_closed')


class TestQuotaNotDeductedOnCancel:
    """Verify use_quota NOT called when task is cancelled."""

    def test_quota_not_deducted_on_cancel(self, tmp_path: Path) -> None:
        """Verify use_quota NOT called when task is cancelled."""
        pg_ctx = _make_ctx(tmp_path)
        mock_llm = _SuccessLLM()  # LLM would succeed, but stop_event is set
        svc, mock_pg = _build_patched_service(mock_llm, mock_pg_ctx=pg_ctx)

        # Set stop_event before run -> kernel returns cancelled immediately
        stop_event = threading.Event()
        stop_event.set()

        async def mock_use_quota(uid):
            pass

        use_quota_mock = MagicMock(side_effect=mock_use_quota)
        called = _run_with_quota_mock(
            svc, mock_pg, use_quota_mock, stop_event=stop_event
        )
        assert not called, 'use_quota should NOT be called on cancel'

    def test_cancelled_run_emits_stream_closed_event(self, tmp_path: Path) -> None:
        """Verify cancelled runs still emit stream_closed for frontend stream closure."""
        pg_ctx = _make_ctx(tmp_path)
        mock_llm = _SuccessLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_pg_ctx=pg_ctx)
        payloads: list[dict[str, Any]] = []

        stop_event = threading.Event()
        stop_event.set()

        async def mock_use_quota(uid):
            pass

        use_quota_mock = MagicMock(side_effect=mock_use_quota)
        _run_with_quota_mock(
            svc,
            mock_pg,
            use_quota_mock,
            stop_event=stop_event,
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
        pg_ctx = _make_ctx(tmp_path)
        mock_llm = _SuccessLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_pg_ctx=pg_ctx)

        stop_event = threading.Event()
        stop_event.set()

        async def mock_use_quota(uid):
            pass

        use_quota_mock = MagicMock(side_effect=mock_use_quota)
        result = _run_with_quota_mock(
            svc,
            mock_pg,
            use_quota_mock,
            stop_event=stop_event,
            return_result=True,
        )

        assert isinstance(result, tuple)
        assert result[0] == (False, 'cancelled')
        assert isinstance(result[1], int)
        assert result[1] >= 0


class TestQuotaNotDeductedOnError:
    """Verify use_quota NOT called when kernel raises exception."""

    def test_quota_not_deducted_on_error(self, tmp_path: Path) -> None:
        """Verify use_quota NOT called when kernel raises exception."""
        pg_ctx = _make_ctx(tmp_path)
        mock_llm = _ErrorLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_pg_ctx=pg_ctx)

        async def mock_use_quota(uid):
            pass

        use_quota_mock = MagicMock(side_effect=mock_use_quota)
        called = _run_with_quota_mock(svc, mock_pg, use_quota_mock)
        assert not called, 'use_quota should NOT be called on error'

    def test_error_run_emits_stream_closed_event(self, tmp_path: Path) -> None:
        """Verify error runs emit stream_closed after the error event."""
        pg_ctx = _make_ctx(tmp_path)
        mock_llm = _ErrorLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_pg_ctx=pg_ctx)
        payloads: list[dict[str, Any]] = []

        async def mock_use_quota(uid):
            pass

        use_quota_mock = MagicMock(side_effect=mock_use_quota)
        _run_with_quota_mock(
            svc,
            mock_pg,
            use_quota_mock,
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
        pg_ctx = _make_ctx(tmp_path)
        mock_llm = _ErrorLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_pg_ctx=pg_ctx)

        async def mock_use_quota(uid):
            pass

        use_quota_mock = MagicMock(side_effect=mock_use_quota)
        result = _run_with_quota_mock(
            svc,
            mock_pg,
            use_quota_mock,
            return_result=True,
        )

        assert isinstance(result, tuple)
        assert result[0][0] is False
        assert 'LLM error' in result[0][1]
        assert isinstance(result[1], int)
        assert result[1] >= 0

    def test_quota_not_deducted_on_invalid_finish(self, tmp_path: Path) -> None:
        """Verify use_quota NOT called when run_result validation fails."""
        pg_ctx = _make_ctx(tmp_path)
        mock_llm = _InvalidFinishLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_pg_ctx=pg_ctx)

        async def mock_use_quota(uid):
            pass

        use_quota_mock = MagicMock(side_effect=mock_use_quota)
        called = _run_with_quota_mock(svc, mock_pg, use_quota_mock)
        assert not called, 'use_quota should NOT be called on invalid finish'

    def test_invalid_finish_emits_stream_closed_event(self, tmp_path: Path) -> None:
        """Verify invalid finishes still close the stream."""
        pg_ctx = _make_ctx(tmp_path)
        mock_llm = _InvalidFinishLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_pg_ctx=pg_ctx)
        payloads: list[dict[str, Any]] = []

        async def mock_use_quota(uid):
            pass

        use_quota_mock = MagicMock(side_effect=mock_use_quota)
        _run_with_quota_mock(
            svc,
            mock_pg,
            use_quota_mock,
            send_cb=_async_collect(payloads),
        )

        run_result_payload = next(
            (payload for payload in payloads if payload.get('type') == 'run_result'),
            None,
        )
        assert run_result_payload is not None
        assert run_result_payload['status'] == 'failed'
        assert run_result_payload['reason'] == 'invalid_finish'
        stream_closed_payload = next(
            (payload for payload in payloads if payload.get('type') == 'stream_closed'),
            None,
        )
        assert stream_closed_payload is not None
        assert stream_closed_payload['task_completed'] is False
        assert stream_closed_payload['end_reason'] == 'invalid_finish'
        assert stream_closed_payload['treat_as_failure'] is True
        payload_types = [payload.get('type') for payload in payloads]
        assert payload_types.index('run_result') < payload_types.index('stream_closed')

    def test_invalid_finish_returns_failure_result(self, tmp_path: Path) -> None:
        """Verify failed finish states return failure for Worker status updates."""
        pg_ctx = _make_ctx(tmp_path)
        mock_llm = _InvalidFinishLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_pg_ctx=pg_ctx)

        async def mock_use_quota(uid):
            pass

        use_quota_mock = MagicMock(side_effect=mock_use_quota)
        result = _run_with_quota_mock(
            svc,
            mock_pg,
            use_quota_mock,
            return_result=True,
        )

        assert isinstance(result, tuple)
        assert result[0] == (False, 'invalid_finish')
        assert isinstance(result[1], int)
        assert result[1] >= 0


class TestQuotaDeduction:
    """Verify use_quota called via native await."""

    def test_quota_deduction(self, tmp_path: Path) -> None:
        """Verify use_quota called via native await."""
        pg_ctx = _make_ctx(tmp_path)
        mock_llm = _SuccessLLM()
        svc, mock_pg = _build_patched_service(mock_llm, mock_pg_ctx=pg_ctx)

        use_quota_calls = []

        async def mock_use_quota(uid):
            use_quota_calls.append(uid)

        use_quota_mock = MagicMock(side_effect=mock_use_quota)
        _run_with_quota_mock(svc, mock_pg, use_quota_mock)

        assert len(use_quota_calls) == 1
        assert use_quota_calls[0] == 'user-123'
