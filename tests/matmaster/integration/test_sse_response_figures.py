from __future__ import annotations

import pytest

from matmaster.integration.sse_handler import SSEHandler
from matmaster.types.events import ResponseFiguresEvent
from matmaster.types.figures import FigureDescriptor


@pytest.mark.asyncio
async def test_response_figures_sse_payload_includes_run_context() -> None:
    sent: list[dict] = []
    handler = SSEHandler(
        send_cb=lambda payload: sent.append(payload),
        session_id='sess-1',
        task_id='task-1',
        invocation_id='inv-1',
        mode='direct',
    )

    await handler.handle(
        ResponseFiguresEvent(
            source='System',
            figures=[
                FigureDescriptor(
                    figure_id='band',
                    asset_url='https://oss.example/band.png',
                    caption='band',
                    importance='primary',
                    placement_hint='sidebar_only',
                    source_tool_call_id='call-band',
                )
            ],
        )
    )

    assert len(sent) == 1
    payload = sent[0]
    assert payload['type'] == 'response_figures'
    assert payload['session_id'] == 'sess-1'
    assert payload['task_id'] == 'task-1'
    assert payload['invocation_id'] == 'inv-1'
    assert payload['spawn_id'] is None
    assert payload['content']['figures'][0]['figure_id'] == 'band'
