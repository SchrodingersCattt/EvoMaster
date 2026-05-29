"""Terminal and return-value tests for AgentRunService.run_agent()."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matmaster.types.events import RunResultEvent, ThoughtEvent
from tests.matmaster.services.test_agent_run_stream import (
    _FakeExp,
    _make_cancel_token,
    _make_mock_environment,
    _make_mock_playground,
    _make_mock_session,
    _patched_service,
    _standard_patches,
)


@pytest.mark.asyncio
async def test_run_agent_idempotent_skip_when_user_turn_context_already_exists():
    from matmaster.types.messages import UserMessage
    from src.services.user_turn_context_service import (
        DEFAULT_TURN_TRANSFORM,
        USER_CONTEXT_RENDER_VERSION,
        USER_TURN_CONTEXT_SCHEMA_VERSION,
        hash_user_instructions,
    )

    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        svc._test_session.read_file.return_value = "Use SI units."
        svc._test_events_table.get_recent_context_anchor_events.return_value = []

        instructions_hash = hash_user_instructions("Use SI units.")
        rendered_content = (
            "<user_instructions>\nUse SI units.\n</user_instructions>"
            "\n\n"
            "<current_instruction>\nfirst question\n</current_instruction>"
        )
        existing_payload = {
            "schema_version": USER_TURN_CONTEXT_SCHEMA_VERSION,
            "kind": "anchor",
            "message": UserMessage(content=rendered_content).model_dump(mode="json"),
            "user_instructions_hash": instructions_hash,
            "transform": DEFAULT_TURN_TRANSFORM,
            "render_version": USER_CONTEXT_RENDER_VERSION,
        }
        svc._test_events_table.query_user_turn_context_by_invocation.return_value = {
            "id": 99,
            "type": "user_turn_context",
            "invocation_id": "inv-1",
            "content": existing_payload,
        }

        ok, _elapsed = await svc.run_agent(
            session_id="sess-1",
            user_prompt="first question",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-1",
            invocation_id="inv-1",
        )

    assert ok is True
    utc_calls = [
        call
        for call in svc._test_events_table.add_event.call_args_list
        if call.args[2] == "user_turn_context"
    ]
    assert utc_calls == []
    assert svc._test_fake_exp.last_task is not None
    assert "Use SI units." in svc._test_fake_exp.last_task


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
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='t1',
            invocation_id='inv-source-normalization',
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
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='t1',
            invocation_id='inv-stream-closed',
        )

    stream_closed = [
        e for e in sse_events if getattr(e, 'type', None) == 'stream_closed'
    ]
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
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='t1',
            invocation_id='inv-cancelled',
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
        history_restore_cls = mocks[6]
        redis_fn = mocks[7]

        environment = _make_mock_environment(_make_mock_session())
        pg = _make_mock_playground(environment)
        pg_mgr = MagicMock()
        pg_mgr.get_or_create.return_value = pg
        pg_mgr_cls.return_value = pg_mgr

        # SSE handler mock
        sse_received: list[Any] = []
        sse_inst = MagicMock()
        sse_inst.handle = AsyncMock(
            side_effect=lambda event: sse_received.append(event)
        )
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
        bohrium_result.runtime_snapshot = None
        bohrium_result.execution_session = None
        bohrium_result.session_type = None
        bohrium_result._asdict.return_value = {
            'ssh_attached': False,
            'abort_result': None,
        }
        bohrium_inst.run_setup = AsyncMock(return_value=bohrium_result)
        bohrium_inst.run_cleanup = AsyncMock()
        bohrium_cls.return_value = bohrium_inst

        history_restore_inst = MagicMock()
        history_restore_inst.restore_history.return_value = []
        history_restore_cls.return_value = history_restore_inst
        redis_fn.return_value = MagicMock()
        events_table = MagicMock()
        events_table.get_recent_context_anchor_events.return_value = []
        events_table.query_user_turn_context_by_invocation.return_value = None
        events_table.add_event.return_value = True
        events_table.get_session_user_query_events.return_value = []
        events_table.get_bohrium_events.return_value = []
        events_table_fn.return_value = events_table

        error_exp = _ErrorExp([])

        with (
            patch('matmaster.config.loader.load_exp_config', return_value=MagicMock()),
            patch('matmaster.config.loader.load_llm_config', return_value=MagicMock()),
            patch(
                'matmaster.providers.llm_factory.build_provider',
                return_value=MagicMock(),
            ),
            patch('matmaster.core.exp.Exp', new=lambda config: error_exp),
        ):

            from src.services.agent_run_service import AgentRunService

            svc = AgentRunService.__new__(AgentRunService)
            svc._sessions_service = MagicMock()
            svc._pg_manager = pg_mgr
            svc._active_skills = {}

            result = await svc.run_agent(
                session_id='s1',
                user_prompt='hi',
                send_cb=AsyncMock(),
                cancel_token=_make_cancel_token(),
                mode='direct',
                task_id='t1',
                invocation_id='inv-exception',
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
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='t1',
            invocation_id='inv-success',
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
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='t1',
            invocation_id='inv-failed',
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
        await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='t1',
            invocation_id='inv-worker-send',
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
        await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='t1',
            invocation_id='inv-persistence',
        )

    # Persistence handler should receive all events including terminal
    persist_types = [getattr(e, 'type', None) for e in persist_events]
    assert 'thought' in persist_types
    assert 'run_result' in persist_types
    assert 'stream_closed' in persist_types


@pytest.mark.asyncio
async def test_run_agent_passes_remote_workdir_to_bohrium_setup():
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        await svc.run_agent(
            session_id="sess-1",
            user_prompt="hello",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-1",
            invocation_id="inv-remote-workdir",
            remote_workdir="/share/case",
        )

        bohrium_svc = svc._test_bohrium_svc
        call_kwargs = bohrium_svc.run_setup.call_args.kwargs

    assert call_kwargs["remote_workdir"] == "/share/case"
    assert call_kwargs["bohrium_required"] is True


@pytest.mark.asyncio
async def test_run_agent_runs_bohrium_cleanup_after_success():
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        await svc.run_agent(
            session_id="s1",
            user_prompt="hi",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="t1",
            invocation_id="inv-cleanup",
        )

        bohrium_svc = svc._test_bohrium_svc
        pg_for_run = svc._pg_manager.get_or_create.return_value

    bohrium_svc.run_cleanup.assert_awaited_once_with(
        session_id="s1",
        pg_for_run=pg_for_run,
        ssh_attached=False,
    )
