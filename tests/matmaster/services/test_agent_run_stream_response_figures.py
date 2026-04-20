from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from matmaster.types.cancellation import CancellationController
from matmaster.types.events import ResponseEvent, RunResultEvent, ToolResultEvent
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
    assert sse_types.index('tool_result') < sse_types.index('response_figures')
    assert sse_types.index('response_figures') < sse_types.index('run_result')


@pytest.mark.asyncio
async def test_planner_child_tool_result_figures_promote_to_parent_response_figures():
    child_tool_result = ToolResultEvent(
        source='MatMaster:direct',
        spawn_id='child-1',
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
                }
            ]
        },
    )
    child_response = ResponseEvent(
        source='MatMaster:direct',
        spawn_id='child-1',
        content='见 [[fig:band]]',
        stream_state='streaming',
        stream_id='child-response',
    )
    root_run_result = RunResultEvent(
        source='MatMaster',
        status='completed',
        reason='natural',
        final_content='done',
    )

    async def events(ctx):
        await ctx.run_meta['event_sink'](child_tool_result)
        await ctx.run_meta['event_sink'](child_response)
        yield root_run_result

    async with _patched_service(events) as (svc, sse_events, _):
        controller = CancellationController()
        await svc.run_agent(
            session_id='sess-1',
            user_prompt='planner delegates figure task',
            send_cb=AsyncMock(),
            cancel_token=controller.token,
            mode='planner',
            task_id='task-1',
            invocation_id='inv-1',
        )

    sse_types = [getattr(evt, 'type', None) for evt in sse_events]
    assert sse_types.index('tool_result') < sse_types.index('response_figures')
    assert sse_types.index('response_figures') < sse_types.index('response')

    response_figures = next(
        evt for evt in sse_events if getattr(evt, 'type', None) == 'response_figures'
    )
    assert response_figures.spawn_id is None
    assert [fig.figure_id for fig in response_figures.figures] == ['band']


@pytest.mark.asyncio
async def test_run_agent_ignores_spawned_tool_result_figures_yielded_on_parent_stream():
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
    assert response_figures_idx < run_result_indices[0]
    assert response_figures_idx < run_result_indices[-1]


@pytest.mark.asyncio
async def test_run_agent_emits_response_figures_immediately_after_parent_tool_result():
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
                }
            ]
        },
    )
    response = ResponseEvent(
        source='MatMaster',
        content='见 [[fig:band]]',
        stream_state='streaming',
        stream_id='resp-1',
    )
    run_result = RunResultEvent(
        source='MatMaster',
        status='completed',
        reason='natural',
        final_content='answer',
    )

    async with _patched_service([tool_result, response, run_result]) as (
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
            invocation_id='inv-1',
        )

    sse_types = [getattr(evt, 'type', None) for evt in sse_events]
    assert sse_types.index('tool_result') < sse_types.index('response_figures')
    assert sse_types.index('response_figures') < sse_types.index('response')
    assert sse_types.index('response_figures') < sse_types.index('run_result')


@pytest.mark.asyncio
async def test_run_agent_emits_complete_response_figure_snapshots_after_each_tool_result():
    first_tool_result = ToolResultEvent(
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
                }
            ]
        },
    )
    second_tool_result = ToolResultEvent(
        source='MatMaster',
        call_id='call-dos',
        tool_name='Bash',
        result='done',
        payload={
            'figures': [
                {
                    'figure_id': 'dos',
                    'asset_url': 'https://oss.example/dos.png',
                    'caption': 'dos',
                    'importance': 'secondary',
                    'placement_hint': 'sidebar_only',
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

    async with _patched_service(
        [first_tool_result, second_tool_result, run_result]
    ) as (svc, sse_events, _):
        controller = CancellationController()
        await svc.run_agent(
            session_id='sess-1',
            user_prompt='show band and dos',
            send_cb=AsyncMock(),
            cancel_token=controller.token,
            mode='direct',
            task_id='task-1',
            invocation_id='inv-1',
        )

    figure_events = [
        event
        for event in sse_events
        if getattr(event, 'type', None) == 'response_figures'
    ]
    assert len(figure_events) == 2
    assert [fig.figure_id for fig in figure_events[0].figures] == ['band']
    assert [fig.figure_id for fig in figure_events[1].figures] == ['band', 'dos']


@pytest.mark.asyncio
async def test_run_agent_final_flush_retries_uncommitted_response_figures_snapshot():
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

    from matmaster.integration.fanout import RunEventFanout

    real_checked_dispatch = RunEventFanout.dispatch_and_wait_persistence
    failed_once = False

    async def flaky_checked_dispatch(self, event):
        nonlocal failed_once
        if getattr(event, 'type', None) == 'response_figures' and not failed_once:
            failed_once = True
            return False
        return await real_checked_dispatch(self, event)

    async with _patched_service([tool_result, run_result]) as (svc, sse_events, _):
        controller = CancellationController()
        with patch.object(
            RunEventFanout,
            'dispatch_and_wait_persistence',
            flaky_checked_dispatch,
        ):
            await svc.run_agent(
                session_id='sess-1',
                user_prompt='show band structure',
                send_cb=AsyncMock(),
                cancel_token=controller.token,
                mode='direct',
                task_id='task-1',
                invocation_id='inv-1',
            )

    sse_types = [getattr(evt, 'type', None) for evt in sse_events]
    assert failed_once is True
    assert sse_types.count('response_figures') == 1
    assert sse_types.index('response_figures') < sse_types.index('run_result')


@pytest.mark.asyncio
async def test_run_agent_flushes_persistence_before_response_figures_snapshot():
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

    from matmaster.integration.fanout import RunEventFanout

    calls: list[tuple[str, str | None]] = []
    real_dispatch = RunEventFanout.dispatch
    real_flush = RunEventFanout.flush_persistence_barrier
    real_checked_dispatch = RunEventFanout.dispatch_and_wait_persistence

    async def tracing_dispatch(self, event):
        calls.append(('dispatch', getattr(event, 'type', None)))
        return await real_dispatch(self, event)

    async def tracing_flush(self):
        calls.append(('flush', None))
        return await real_flush(self)

    async def tracing_checked_dispatch(self, event):
        calls.append(('checked', getattr(event, 'type', None)))
        return await real_checked_dispatch(self, event)

    async with _patched_service([tool_result, run_result]) as (svc, _sse_events, _):
        controller = CancellationController()
        with (
            patch.object(RunEventFanout, 'dispatch', tracing_dispatch),
            patch.object(
                RunEventFanout,
                'flush_persistence_barrier',
                tracing_flush,
            ),
            patch.object(
                RunEventFanout,
                'dispatch_and_wait_persistence',
                tracing_checked_dispatch,
            ),
        ):
            await svc.run_agent(
                session_id='sess-1',
                user_prompt='show band structure',
                send_cb=AsyncMock(),
                cancel_token=controller.token,
                mode='direct',
                task_id='task-1',
                invocation_id='inv-1',
            )

    tool_idx = calls.index(('dispatch', 'tool_result'))
    flush_idx = next(
        idx
        for idx, call in enumerate(calls)
        if idx > tool_idx and call == ('flush', None)
    )
    figures_idx = calls.index(('checked', 'response_figures'))
    run_result_idx = calls.index(('dispatch', 'run_result'))
    assert tool_idx < flush_idx < figures_idx < run_result_idx
