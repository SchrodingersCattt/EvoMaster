from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from matmaster.types.events import RunResultEvent
from matmaster.types.figures import FigureUploadConfig
from tests.matmaster.services.agent_run_stream_fixtures import (
    _make_cancel_token,
    _patched_service,
)


@pytest.mark.asyncio
async def test_figure_upload_port_is_set_and_survives_history_wiring() -> None:
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        await svc.run_agent(
            session_id="sess-1",
            user_prompt="make a plot",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-1",
            invocation_id="inv-figure-boundary",
        )

    ctx = svc._test_fake_exp.last_ctx

    assert ctx.runtime_ports.figure_upload.config is not None
    assert isinstance(ctx.runtime_ports.figure_upload.config, FigureUploadConfig)
    assert ctx.run_meta.get("figure_upload_config") is None
    assert ctx.runtime_ports.child_event_forward_sink is not None
    assert ctx.runtime_ports.compaction.history is not None
    assert ctx.runtime_ports.compaction.checkpoint_sink_factory is not None
