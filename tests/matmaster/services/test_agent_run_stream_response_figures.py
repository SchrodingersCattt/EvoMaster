from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from matmaster.types.cancellation import CancellationController
from matmaster.types.events import RunResultEvent, ToolResultEvent
from tests.matmaster.services.test_agent_run_stream import _patched_service


@pytest.mark.asyncio
async def test_run_agent_emits_response_figures_before_run_result() -> None:
    tool_result = ToolResultEvent(
        source='MatMaster',
        call_id='call-band',
        tool_name='Bash',
        result='done',
        payload={
            'figures': [
                {
                    'figure_id': 'band',
                    'asset_url': 'https://oss.example/band.png',
                    'caption': 'band',
                    'importance': 'primary',
                    'placement_hint': 'sidebar_only',
                    'source_tool_call_id': 'call-band',
                }
            ]
        },
    )
    run_result = RunResultEvent(
        source='MatMaster',
        status='completed',
        reason='natural',
        final_content='answer',
    )

    async with _patched_service([tool_result, run_result]) as (svc, sse_events, _):
        controller = CancellationController()
        await svc.run_agent(
            session_id='sess-1',
            user_prompt='show band structure',
            send_cb=AsyncMock(),
            cancel_token=controller.token,
            mode='direct',
            task_id='task-1',
        )

    sse_types = [getattr(evt, 'type', None) for evt in sse_events]
    assert 'response_figures' in sse_types
    assert sse_types.index('response_figures') < sse_types.index('run_result')


@pytest.mark.asyncio
async def test_run_agent_ignores_subagent_tool_result_figures_in_parent_response():
    child_tool_result = ToolResultEvent(
        source='MatMaster:direct',
        spawn_id='sub-1',
        call_id='call-band',
        tool_name='Bash',
        result='done',
        payload={
            'figures': [
                {
                    'figure_id': 'band',
                    'asset_url': 'https://oss.example/band.png',
                    'caption': 'band',
                    'importance': 'primary',
                    'placement_hint': 'sidebar_only',
                    'source_tool_call_id': 'call-band',
                }
            ]
        },
    )
    run_result = RunResultEvent(
        source='MatMaster',
        status='completed',
        reason='natural',
        final_content='answer',
    )

    async with _patched_service([child_tool_result, run_result]) as (
        svc,
        sse_events,
        _,
    ):
        controller = CancellationController()
        await svc.run_agent(
            session_id='sess-1',
            user_prompt='show band structure',
            send_cb=AsyncMock(),
            cancel_token=controller.token,
            mode='direct',
            task_id='task-1',
        )

    sse_types = [getattr(evt, 'type', None) for evt in sse_events]
    assert 'response_figures' not in sse_types


@pytest.mark.asyncio
async def test_run_agent_only_emits_response_figures_on_root_run_result():
    parent_tool_result = ToolResultEvent(
        source='MatMaster',
        call_id='call-band',
        tool_name='Bash',
        result='done',
        payload={
            'figures': [
                {
                    'figure_id': 'band',
                    'asset_url': 'https://oss.example/band.png',
                    'caption': 'band',
                    'importance': 'primary',
                    'placement_hint': 'sidebar_only',
                    'source_tool_call_id': 'call-band',
                }
            ]
        },
    )
    child_run_result = RunResultEvent(
        source='MatMaster:direct',
        spawn_id='sub-1',
        status='completed',
        reason='natural',
        final_content='child answer',
    )
    parent_run_result = RunResultEvent(
        source='MatMaster',
        status='completed',
        reason='natural',
        final_content='parent answer',
    )

    async with _patched_service(
        [parent_tool_result, child_run_result, parent_run_result]
    ) as (svc, sse_events, _):
        controller = CancellationController()
        await svc.run_agent(
            session_id='sess-1',
            user_prompt='show band structure',
            send_cb=AsyncMock(),
            cancel_token=controller.token,
            mode='direct',
            task_id='task-1',
        )

    sse_types = [getattr(evt, 'type', None) for evt in sse_events]
    run_result_indices = [
        idx for idx, event_type in enumerate(sse_types) if event_type == 'run_result'
    ]
    assert sse_types.count('response_figures') == 1
    response_figures_idx = sse_types.index('response_figures')
    assert response_figures_idx > run_result_indices[0]
    assert response_figures_idx < run_result_indices[-1]
