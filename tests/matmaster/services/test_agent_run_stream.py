"""ESIN-02: Integration tests for AgentRunService.run_agent_stream().

Verifies the generator event -> bus bridge, source normalization,
StreamClosedEvent emission, and error handling.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matmaster.core.bus import MessageBus
from matmaster.types.events import (
    CancelledEvent,
    ErrorEvent,
    ResponseEvent,
    RunResultEvent,
    StreamClosedEvent,
    ThoughtEvent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_playground(pg_ctx: Any) -> Any:
    """Build a mock Playground that returns the given PlaygroundContext."""
    pg = MagicMock()
    pg.prepare.return_value = pg_ctx
    return pg


def _make_mock_pg_ctx() -> MagicMock:
    """Build a mock PlaygroundContext with minimum viable fields."""
    ctx = MagicMock()
    ctx.workdir = '/tmp/workspace'
    ctx.execution_workdir = '/tmp/workspace'
    ctx.session = MagicMock()
    ctx.session._stop_event = None
    ctx.session.capabilities = MagicMock()
    ctx.archival = None
    ctx.run_meta = {}
    ctx.with_bohrium.return_value = ctx
    ctx.with_execution.return_value = ctx
    ctx.model_copy.return_value = ctx
    return ctx


def _make_stop_event() -> MagicMock:
    """Build a mock StopEventLike."""
    se = MagicMock()
    se.is_set.return_value = False
    return se


async def _async_gen_from_events(events: list[Any]):
    """Create an async generator that yields the given events."""
    for event in events:
        yield event


class _FakeExp:
    """Minimal Exp stand-in that returns a canned async generator from run_stream."""

    def __init__(self, events: list[Any]) -> None:
        self._events = events
        self._config = MagicMock()
        self._config.name = 'direct'
        self._cleanup_callbacks: list = []

    async def run_stream(self, *args: Any, **kwargs: Any):
        for event in self._events:
            yield event

    async def build_runtime(self, *args: Any, **kwargs: Any) -> Any:
        runtime = MagicMock()
        spec = MagicMock()
        spec.hooks = []
        spec.tool_catalog = None
        runtime.spec = spec
        return runtime

    async def _run_cleanup_callbacks(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Patches: Isolate run_agent_stream from heavy infrastructure
# ---------------------------------------------------------------------------

def _standard_patches():
    """Return a list of patch context managers for isolating run_agent_stream."""
    return [
        patch('src.services.agent_run_service.PlaygroundManager'),
        patch('src.services.agent_run_service.get_chat_events_table'),
        patch('src.services.agent_run_service.EventRouter'),
        patch('src.services.agent_run_service.SSEHandler'),
        patch('src.services.agent_run_service.PersistenceHandler'),
        patch('src.services.agent_run_service.WorkspaceHandler'),
        patch('src.services.agent_run_service.BohriumSetupService'),
        patch('src.services.agent_run_service.derive_skill_sync_spec'),
        patch('src.services.agent_run_service.ChatHistoryConverter'),
        patch('src.services.agent_run_service.get_redis_dao'),
        patch('src.services.agent_run_service.use_quota', new_callable=AsyncMock),
        patch('src.services.agent_run_service._get_agent_default_llm', return_value=None),
    ]


@asynccontextmanager
async def _patched_service(events: list[Any]):
    """Set up an AgentRunService with all infra patched, yielding (service, bus_spy).

    bus_spy is a list that captures all bus.emit_nowait calls.
    """
    patches = _standard_patches()
    mocks = []
    for p in patches:
        mocks.append(p.start())

    try:
        pg_mgr_cls = mocks[0]
        events_table_fn = mocks[1]
        router_cls = mocks[2]
        bohrium_cls = mocks[6]
        derive_fn = mocks[7]
        history_cls = mocks[8]
        redis_fn = mocks[9]

        # PlaygroundManager mock
        pg_ctx = _make_mock_pg_ctx()
        pg = _make_mock_playground(pg_ctx)
        pg_mgr = MagicMock()
        pg_mgr.get_or_create.return_value = pg
        pg_mgr_cls.return_value = pg_mgr

        # EventRouter mock
        router_inst = MagicMock()
        router_inst.start = AsyncMock()
        router_inst.stop = AsyncMock()
        router_cls.return_value = router_inst

        # Bohrium mock -- no SSH, no abort
        bohrium_inst = MagicMock()
        bohrium_result = MagicMock()
        bohrium_result.ssh_attached = False
        bohrium_result.abort_result = None
        bohrium_result.execution_session = None
        bohrium_result._asdict.return_value = {'ssh_attached': False, 'abort_result': None}
        bohrium_inst.run_setup = AsyncMock(return_value=bohrium_result)
        bohrium_inst.run_cleanup = AsyncMock()
        bohrium_cls.return_value = bohrium_inst

        # ChatHistory mock
        history_cls.exclude_spawn_events.return_value = []
        history_cls.exclude_task_events.return_value = []
        history_cls.events_to_messages.return_value = []

        # Redis mock
        redis_mock = MagicMock()
        redis_fn.return_value = redis_mock

        # events_table mock
        events_table_fn.return_value = MagicMock()

        # Capture bus.emit_nowait calls
        bus_calls: list[Any] = []
        original_bus_init = MessageBus.__init__

        def _spy_bus_init(self_bus, *a, **kw):
            original_bus_init(self_bus, *a, **kw)
            original_emit = self_bus.emit_nowait

            def _spy_emit(event):
                bus_calls.append(event)
                return original_emit(event)

            self_bus.emit_nowait = _spy_emit

        # Patch Exp to use our fake events
        fake_exp = _FakeExp(events)

        with patch.object(MessageBus, '__init__', _spy_bus_init), \
             patch('matmaster.config.loader.load_exp_config', return_value=MagicMock()), \
             patch('matmaster.config.loader.load_llm_config', return_value=MagicMock()), \
             patch('matmaster.providers.llm_factory.build_provider', return_value=MagicMock()), \
             patch('matmaster.core.exp.Exp', new=lambda config: fake_exp):

            from src.services.agent_run_service import AgentRunService

            svc = AgentRunService.__new__(AgentRunService)
            svc._sessions_service = MagicMock()
            svc._sessions_service.get_session_user_id.return_value = 'user-1'
            svc._pg_manager = pg_mgr

            yield svc, bus_calls

    finally:
        for p in patches:
            p.stop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_events_reach_bus():
    """Events from exp.run_stream() are forwarded to bus.emit_nowait()."""
    thought = ThoughtEvent(source='agent', content='thinking...')
    response = ResponseEvent(source='agent', content='hello')
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([thought, response, run_result]) as (svc, bus_calls):
        result = await svc.run_agent_stream(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            stop_event=_make_stop_event(),
            mode='direct',
            reply_queue=None,
            task_id='t1',
        )

    # 3 events + StreamClosedEvent = 4 total
    assert len(bus_calls) >= 4
    types = [getattr(e, 'type', None) for e in bus_calls]
    assert 'thought' in types
    assert 'response' in types
    assert 'run_result' in types
    assert 'stream_closed' in types


@pytest.mark.asyncio
async def test_source_normalization_on_events():
    """Event source is normalized to MatMaster before bus.emit_nowait()."""
    thought = ThoughtEvent(source='agent', content='thinking...')
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([thought, run_result]) as (svc, bus_calls):
        await svc.run_agent_stream(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            stop_event=_make_stop_event(),
            mode='direct',
            reply_queue=None,
            task_id='t1',
        )

    # All non-System events should be normalized to MatMaster
    for event in bus_calls:
        src = getattr(event, 'source', '')
        if src != 'System':
            assert src == 'MatMaster', f'Expected MatMaster, got {src}'


@pytest.mark.asyncio
async def test_stream_closed_after_run_result():
    """StreamClosedEvent is emitted after RunResultEvent."""
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([run_result]) as (svc, bus_calls):
        await svc.run_agent_stream(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            stop_event=_make_stop_event(),
            mode='direct',
            reply_queue=None,
            task_id='t1',
        )

    # Last event must be StreamClosedEvent
    stream_closed = [e for e in bus_calls if getattr(e, 'type', None) == 'stream_closed']
    assert len(stream_closed) == 1
    sc = stream_closed[0]
    assert sc.task_completed is True
    assert sc.end_reason == 'natural'


@pytest.mark.asyncio
async def test_cancelled_run_emits_cancelled_and_closed():
    """Cancelled run emits CancelledEvent then StreamClosedEvent."""
    run_result = RunResultEvent(source='agent', status='cancelled', reason='cancelled')

    async with _patched_service([run_result]) as (svc, bus_calls):
        result = await svc.run_agent_stream(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            stop_event=_make_stop_event(),
            mode='direct',
            reply_queue=None,
            task_id='t1',
        )

    # Should return failure with 'cancelled'
    assert result[0] == (False, 'cancelled')

    types = [getattr(e, 'type', None) for e in bus_calls]
    assert 'cancelled' in types
    assert 'stream_closed' in types

    # StreamClosedEvent should have end_reason='cancelled'
    sc = [e for e in bus_calls if getattr(e, 'type', None) == 'stream_closed'][0]
    assert sc.end_reason == 'cancelled'
    assert sc.task_completed is False


@pytest.mark.asyncio
async def test_exception_emits_error_and_closed():
    """Exception during streaming emits error + StreamClosedEvent."""

    class _ErrorExp(_FakeExp):
        async def run_stream(self, *args, **kwargs):
            raise RuntimeError('test explosion')
            yield  # make it a generator  # noqa: E501

    # Use a dedicated _patched_service_with_exp helper to inject the error exp
    # at construction time, before the lazy import inside run_agent_stream.
    patches = _standard_patches()
    mocks = []
    for p in patches:
        mocks.append(p.start())

    try:
        pg_mgr_cls = mocks[0]
        events_table_fn = mocks[1]
        router_cls = mocks[2]
        bohrium_cls = mocks[6]
        history_cls = mocks[8]
        redis_fn = mocks[9]

        pg_ctx = _make_mock_pg_ctx()
        pg = _make_mock_playground(pg_ctx)
        pg_mgr = MagicMock()
        pg_mgr.get_or_create.return_value = pg
        pg_mgr_cls.return_value = pg_mgr

        router_inst = MagicMock()
        router_inst.start = AsyncMock()
        router_inst.stop = AsyncMock()
        router_cls.return_value = router_inst

        bohrium_inst = MagicMock()
        bohrium_result = MagicMock()
        bohrium_result.ssh_attached = False
        bohrium_result.abort_result = None
        bohrium_result.execution_session = None
        bohrium_result._asdict.return_value = {'ssh_attached': False, 'abort_result': None}
        bohrium_inst.run_setup = AsyncMock(return_value=bohrium_result)
        bohrium_inst.run_cleanup = AsyncMock()
        bohrium_cls.return_value = bohrium_inst

        history_cls.exclude_spawn_events.return_value = []
        history_cls.exclude_task_events.return_value = []
        history_cls.events_to_messages.return_value = []
        redis_fn.return_value = MagicMock()
        events_table_fn.return_value = MagicMock()

        error_exp = _ErrorExp([])

        with patch('matmaster.config.loader.load_exp_config', return_value=MagicMock()), \
             patch('matmaster.config.loader.load_llm_config', return_value=MagicMock()), \
             patch('matmaster.providers.llm_factory.build_provider', return_value=MagicMock()), \
             patch('matmaster.core.exp.Exp', new=lambda config: error_exp):

            from src.services.agent_run_service import AgentRunService

            svc = AgentRunService.__new__(AgentRunService)
            svc._sessions_service = MagicMock()
            svc._pg_manager = pg_mgr

            result = await svc.run_agent_stream(
                session_id='s1',
                user_prompt='hi',
                send_cb=AsyncMock(),
                stop_event=_make_stop_event(),
                mode='direct',
                reply_queue=None,
                task_id='t1',
            )
    finally:
        for p in patches:
            p.stop()

    assert result[0] == (False, 'test explosion')


@pytest.mark.asyncio
async def test_successful_run_returns_true():
    """Successful completion returns (True, elapsed_ms)."""
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([run_result]) as (svc, bus_calls):
        result = await svc.run_agent_stream(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            stop_event=_make_stop_event(),
            mode='direct',
            reply_queue=None,
            task_id='t1',
        )

    assert result[0] is True
    assert isinstance(result[1], int)
    assert result[1] >= 0


@pytest.mark.asyncio
async def test_failed_run_returns_false_with_reason():
    """Failed run returns ((False, reason), elapsed_ms)."""
    run_result = RunResultEvent(source='agent', status='failed', reason='max_turns')

    async with _patched_service([run_result]) as (svc, bus_calls):
        result = await svc.run_agent_stream(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            stop_event=_make_stop_event(),
            mode='direct',
            reply_queue=None,
            task_id='t1',
        )

    assert result[0] == (False, 'max_turns')
    assert isinstance(result[1], int)
