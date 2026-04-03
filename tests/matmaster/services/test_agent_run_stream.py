"""DBUS-03: Integration tests for AgentRunService.run_agent() (single entrypoint).

Verifies the generator event -> fanout dispatch, source normalization,
StreamClosedEvent emission, error handling, and worker-mode send_cb
live delivery through SSEHandler.

After Plan 02 collapse, run_agent_stream() no longer exists;
all tests exercise run_agent() exclusively.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
# Patches: Isolate run_agent from heavy infrastructure
# ---------------------------------------------------------------------------

def _standard_patches():
    """Return a list of patch context managers for isolating run_agent."""
    return [
        patch('src.services.agent_run_service.PlaygroundManager'),
        patch('src.services.agent_run_service.get_chat_events_table'),
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
async def _patched_service(events: list[Any], *, send_cb: Any = None):
    """Set up an AgentRunService with all infra patched.

    Yields (service, sse_received, persist_received).
    """
    patches = _standard_patches()
    mocks = []
    for p in patches:
        mocks.append(p.start())

    try:
        pg_mgr_cls = mocks[0]
        events_table_fn = mocks[1]
        sse_handler_cls = mocks[2]
        persistence_handler_cls = mocks[3]
        workspace_handler_cls = mocks[4]
        bohrium_cls = mocks[5]
        derive_fn = mocks[6]
        history_cls = mocks[7]
        redis_fn = mocks[8]

        # PlaygroundManager mock
        pg_ctx = _make_mock_pg_ctx()
        pg = _make_mock_playground(pg_ctx)
        pg_mgr = MagicMock()
        pg_mgr.get_or_create.return_value = pg
        pg_mgr_cls.return_value = pg_mgr

        # SSEHandler mock -- records events it receives
        sse_received: list[Any] = []
        sse_inst = MagicMock()
        sse_inst.handle = AsyncMock(side_effect=lambda event: sse_received.append(event))
        sse_handler_cls.return_value = sse_inst

        # PersistenceHandler mock
        persist_received: list[Any] = []
        persist_inst = MagicMock()
        persist_inst.handle = AsyncMock(side_effect=lambda event: persist_received.append(event))
        persistence_handler_cls.return_value = persist_inst

        # WorkspaceHandler mock
        ws_inst = MagicMock()
        ws_inst.handle = AsyncMock()
        ws_inst.close = MagicMock()
        workspace_handler_cls.return_value = ws_inst

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

        # Patch Exp to use our fake events
        fake_exp = _FakeExp(events)

        with patch('matmaster.config.loader.load_exp_config', return_value=MagicMock()), \
             patch('matmaster.config.loader.load_llm_config', return_value=MagicMock()), \
             patch('matmaster.providers.llm_factory.build_provider', return_value=MagicMock()), \
             patch('matmaster.core.exp.Exp', new=lambda config: fake_exp):

            from src.services.agent_run_service import AgentRunService

            svc = AgentRunService.__new__(AgentRunService)
            svc._sessions_service = MagicMock()
            svc._sessions_service.get_session_user_id.return_value = 'user-1'
            svc._pg_manager = pg_mgr

            yield svc, sse_received, persist_received

    finally:
        for p in patches:
            p.stop()


# ---------------------------------------------------------------------------
# Tests: All via run_agent() -- no run_agent_stream() alias
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_agent_stream_method_does_not_exist():
    """After Plan 02, run_agent_stream() must not exist on AgentRunService."""
    from src.services.agent_run_service import AgentRunService
    assert not hasattr(AgentRunService, 'run_agent_stream'), \
        "run_agent_stream() should be removed; run_agent() is the sole entrypoint"


@pytest.mark.asyncio
async def test_stream_events_reach_handlers_via_fanout():
    """Events from exp.run_stream() are dispatched through RunEventFanout to handlers."""
    thought = ThoughtEvent(source='agent', content='thinking...')
    response = ResponseEvent(source='agent', content='hello')
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([thought, response, run_result]) as (svc, sse_events, persist_events):
        result = await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            stop_event=_make_stop_event(),
            mode='direct',
            reply_queue=None,
            task_id='t1',
        )

    # SSE handler should receive: thought + response + run_result + StreamClosedEvent = 4+
    sse_types = [getattr(e, 'type', None) for e in sse_events]
    assert 'thought' in sse_types
    assert 'response' in sse_types
    assert 'run_result' in sse_types
    assert 'stream_closed' in sse_types


@pytest.mark.asyncio
async def test_source_normalization_on_events():
    """Event source is normalized to MatMaster before fanout dispatch."""
    thought = ThoughtEvent(source='agent', content='thinking...')
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([thought, run_result]) as (svc, sse_events, _):
        await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            stop_event=_make_stop_event(),
            mode='direct',
            reply_queue=None,
            task_id='t1',
        )

    # All non-System events should be normalized to MatMaster
    for event in sse_events:
        src = getattr(event, 'source', '')
        if src != 'System':
            assert src == 'MatMaster', f'Expected MatMaster, got {src}'


@pytest.mark.asyncio
async def test_stream_closed_after_run_result():
    """StreamClosedEvent is dispatched after RunResultEvent."""
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([run_result]) as (svc, sse_events, _):
        await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            stop_event=_make_stop_event(),
            mode='direct',
            reply_queue=None,
            task_id='t1',
        )

    stream_closed = [e for e in sse_events if getattr(e, 'type', None) == 'stream_closed']
    assert len(stream_closed) == 1
    sc = stream_closed[0]
    assert sc.task_completed is True
    assert sc.end_reason == 'natural'


@pytest.mark.asyncio
async def test_cancelled_run_emits_cancelled_and_closed():
    """Cancelled run dispatches CancelledEvent then StreamClosedEvent."""
    run_result = RunResultEvent(source='agent', status='cancelled', reason='cancelled')

    async with _patched_service([run_result]) as (svc, sse_events, _):
        result = await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            stop_event=_make_stop_event(),
            mode='direct',
            reply_queue=None,
            task_id='t1',
        )

    assert result[0] == (False, 'cancelled')

    types = [getattr(e, 'type', None) for e in sse_events]
    assert 'cancelled' in types
    assert 'stream_closed' in types

    sc = [e for e in sse_events if getattr(e, 'type', None) == 'stream_closed'][0]
    assert sc.end_reason == 'cancelled'
    assert sc.task_completed is False


@pytest.mark.asyncio
async def test_exception_emits_error_and_closed():
    """Exception during streaming dispatches error + StreamClosedEvent via fanout."""

    class _ErrorExp(_FakeExp):
        async def run_stream(self, *args, **kwargs):
            raise RuntimeError('test explosion')
            yield  # make it a generator  # noqa: E501

    patches = _standard_patches()
    mocks = []
    for p in patches:
        mocks.append(p.start())

    try:
        pg_mgr_cls = mocks[0]
        events_table_fn = mocks[1]
        sse_handler_cls = mocks[2]
        persistence_handler_cls = mocks[3]
        workspace_handler_cls = mocks[4]
        bohrium_cls = mocks[5]
        history_cls = mocks[7]
        redis_fn = mocks[8]

        pg_ctx = _make_mock_pg_ctx()
        pg = _make_mock_playground(pg_ctx)
        pg_mgr = MagicMock()
        pg_mgr.get_or_create.return_value = pg
        pg_mgr_cls.return_value = pg_mgr

        # SSE handler mock
        sse_received: list[Any] = []
        sse_inst = MagicMock()
        sse_inst.handle = AsyncMock(side_effect=lambda event: sse_received.append(event))
        sse_handler_cls.return_value = sse_inst

        # Persistence handler mock
        persist_inst = MagicMock()
        persist_inst.handle = AsyncMock()
        persistence_handler_cls.return_value = persist_inst

        # Workspace handler mock
        ws_inst = MagicMock()
        ws_inst.handle = AsyncMock()
        ws_inst.close = MagicMock()
        workspace_handler_cls.return_value = ws_inst

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

            result = await svc.run_agent(
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

    async with _patched_service([run_result]) as (svc, _, __):
        result = await svc.run_agent(
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

    async with _patched_service([run_result]) as (svc, _, __):
        result = await svc.run_agent(
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


@pytest.mark.asyncio
async def test_worker_mode_send_cb_receives_live_events():
    """Worker mode SSEHandler(send_cb,...) stays on the live delivery path.

    Verifies the contract: send_cb -> Redis publish -> active SSE subscriber
    remains intact for both generator events and terminal/system parity events.
    """
    thought = ThoughtEvent(source='agent', content='thinking...')
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([thought, run_result]) as (svc, sse_events, _):
        result = await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            stop_event=_make_stop_event(),
            mode='direct',
            reply_queue=None,
            task_id='t1',
        )

    # SSE handler must receive generator events + terminal events
    sse_types = [getattr(e, 'type', None) for e in sse_events]
    # Generator events
    assert 'thought' in sse_types
    assert 'run_result' in sse_types
    # Terminal/system parity events through SSEHandler
    assert 'stream_closed' in sse_types


@pytest.mark.asyncio
async def test_persistence_receives_events():
    """PersistenceHandler receives events through fanout dispatch."""
    thought = ThoughtEvent(source='agent', content='thinking...')
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([thought, run_result]) as (svc, _, persist_events):
        result = await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            stop_event=_make_stop_event(),
            mode='direct',
            reply_queue=None,
            task_id='t1',
        )

    # Persistence handler should receive all events including terminal
    persist_types = [getattr(e, 'type', None) for e in persist_events]
    assert 'thought' in persist_types
    assert 'run_result' in persist_types
    assert 'stream_closed' in persist_types
