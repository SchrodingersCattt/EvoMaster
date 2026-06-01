"""Fanout delivery tests for AgentRunService.run_agent()."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from matmaster.types.events import RunResultEvent, ThoughtEvent
from tests.matmaster.services.agent_run_stream_fixtures import (
    _make_cancel_token,
    _patched_service,
)


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

    sse_types = [getattr(e, 'type', None) for e in sse_events]
    assert 'thought' in sse_types
    assert 'run_result' in sse_types
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

    persist_types = [getattr(e, 'type', None) for e in persist_events]
    assert 'thought' in persist_types
    assert 'run_result' in persist_types
    assert 'stream_closed' in persist_types
