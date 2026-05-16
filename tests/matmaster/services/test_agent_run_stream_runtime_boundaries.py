from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from matmaster.types.events import RunResultEvent
from tests.matmaster.services.test_agent_run_stream_fixtures import (
    _make_cancel_token,
    _patched_service,
)


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


def test_history_wiring_attachment_text_equivalent_to_manifests_shim() -> None:
    """Phase 2C: context source output must stay byte-equivalent to shim."""
    from matmaster.context.sources.attachments import (
        format_entries_text,
        scan_legacy_attachment_entries,
    )
    from matmaster.manifests.attachment import (
        build_available_attachments,
        format_available_attachments,
    )

    query_events: list[dict] = [
        {
            "id": 10,
            "source": "User",
            "type": "query",
            "content": "first turn user text",
            "files": ["/tmp/a.txt"],
            "images": ["/tmp/b.png"],
            "workspace_paths": ["/workspace/note.md"],
        },
        {
            "id": 12,
            "source": "User",
            "type": "query",
            "content": "second turn",
            "files": ["/tmp/c.csv"],
        },
    ]

    manifest_text = format_available_attachments(
        build_available_attachments(query_events)
    )
    source_text = format_entries_text(scan_legacy_attachment_entries(query_events))

    assert manifest_text == source_text
    assert "[Available attachments]" in source_text
    assert "/tmp/c.csv" in source_text


def test_agent_run_service_imports_resolve_turn_context_intent_after_cutover() -> None:
    """Phase 2C cutover smoke check for the Stage 5b import boundary."""
    import src.services.agent_run_service as svc_module

    src = svc_module.__file__
    assert src is not None

    text = Path(src).read_text()
    assert "from src.services.context_turn_intent import" in text
    assert "from matmaster.context.assembly import" in text
