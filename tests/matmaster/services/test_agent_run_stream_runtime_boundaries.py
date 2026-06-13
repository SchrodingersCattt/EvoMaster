from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from matmaster.types.events import RunResultEvent
from tests.matmaster.services.agent_run_stream_fixtures import (
    _make_cancel_token,
    _patched_service,
)


@pytest.mark.asyncio
async def test_run_agent_passes_workspace_to_bohrium_setup():
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
            workspace="/share/case",
        )

        bohrium_svc = svc._test_bohrium_svc
        call_kwargs = bohrium_svc.run_setup.call_args.kwargs

    assert call_kwargs["workspace"] == "/share/case"
    assert "remote_workdir" not in call_kwargs
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


def test_agent_run_service_no_longer_imports_context_turn_intent_after_exp_cutover() -> (
    None
):
    """Root turn preparation belongs to Exp, not AgentRunService."""
    import src.services.agent_run_service as svc_module

    src = svc_module.__file__
    assert src is not None

    text = Path(src).read_text()
    assert "from src.services.context_turn_intent import" not in text
    assert "build_context_assembler" not in text
