"""DBUS-03: Integration tests for AgentRunService.run_agent() (single entrypoint).

Verifies the generator event -> fanout dispatch, source normalization,
StreamClosedEvent emission, error handling, and worker-mode send_cb
live delivery through SSEHandler.

After Plan 02 collapse, run_agent_stream() no longer exists;
all tests exercise run_agent() exclusively.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from matmaster.context.ports import SessionEventQuery
from matmaster.types.cancellation import CancellationController
from matmaster.types.events import (
    ResponseEvent,
    RunResultEvent,
    ThoughtEvent,
)
from matmaster.types.figures import FigureUploadConfig
from matmaster.types.run_metadata import RunMetadata
from src.services.image_input_service import ImageInputService
from tests.matmaster.services.agent_run_stream_fixtures import (
    _FakeExp,
    _make_cancel_token,
    _make_mock_environment,
    _make_mock_playground,
    _make_mock_session,
    _patched_service,
    _standard_patches,
)

# ---------------------------------------------------------------------------
# Tests: All via run_agent() -- no run_agent_stream() alias
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_agent_stream_method_does_not_exist():
    """After Plan 02, run_agent_stream() must not exist on AgentRunService."""
    from src.services.agent_run_service import AgentRunService

    assert not hasattr(
        AgentRunService, 'run_agent_stream'
    ), "run_agent_stream() should be removed; run_agent() is the sole entrypoint"


@pytest.mark.asyncio
async def test_run_agent_signature_does_not_accept_reply_queue():
    """Unused confirmation queue plumbing should not remain in run_agent()."""
    from src.services.agent_run_service import AgentRunService

    params = inspect.signature(AgentRunService.run_agent).parameters
    assert 'reply_queue' not in params, (
        "run_agent() should not accept reply_queue; "
        "confirmation queue plumbing is no longer used"
    )


def test_run_agent_signature_accepts_byok_runtime_kwargs():
    from src.services.agent_run_service import AgentRunService

    params = inspect.signature(AgentRunService.run_agent).parameters

    for name in (
        "byok_profile",
        "byok_config_id",
        "byok_config_version",
        "billing_mode",
    ):
        assert name in params


@pytest.mark.asyncio
async def test_run_agent_injects_cancel_token_into_session_and_exp():
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')
    cancel_token = _make_cancel_token()

    async with _patched_service([run_result]) as (svc, _, __):
        await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            cancel_token=cancel_token,
            mode='direct',
            task_id='t1',
            invocation_id='inv-cancel-token',
        )

    assert svc._test_session._cancel_token is cancel_token
    assert svc._test_fake_exp.last_run_kwargs is not None
    assert svc._test_fake_exp.last_run_kwargs['cancel_token'] is cancel_token


@pytest.mark.asyncio
async def test_run_agent_injects_child_event_forward_sink_into_runtime_ports():
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        ok, _elapsed, _usage = await svc.run_agent(
            session_id='sess-1',
            user_prompt='hello',
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='task-1',
            invocation_id='inv-child-sink',
        )

    assert ok is True
    ports = svc._test_fake_exp.last_ctx.request.ports
    injected = ports.child_event_forward_sink
    assert callable(injected)
    assert svc._test_fake_exp.last_ctx.environment.metadata.task_id == 'task-1'


@pytest.mark.asyncio
async def test_run_agent_passes_session_id_to_playground_prepare():
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        ok, _elapsed, _usage = await svc.run_agent(
            session_id='sess-explicit',
            user_prompt='hello',
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='task-1',
            invocation_id='inv-prepare-session',
        )

    assert ok is True
    prepare_call = svc._test_playground.prepare.call_args
    assert prepare_call.args[0].task_id == "task-1"
    assert prepare_call.kwargs["session_id"] == "sess-explicit"
    assert "task_id" not in prepare_call.kwargs
    assert "session_id" not in RunMetadata.model_fields
    assert svc._test_fake_exp.last_ctx.environment.session_id == "sess-explicit"


@pytest.mark.asyncio
async def test_run_agent_injects_figure_upload_via_runtime_ports():
    run_result = RunResultEvent(
        source='MatMaster',
        status='completed',
        reason='natural',
        final_content='done',
    )

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        controller = CancellationController()
        await svc.run_agent(
            session_id='sess-1',
            user_prompt='make a plot',
            send_cb=lambda payload: None,
            cancel_token=controller.token,
            mode='direct',
            task_id='task-1',
            invocation_id='inv-figure-config',
        )

    ctx = svc._test_fake_exp.last_ctx
    figure_cfg = ctx.request.ports.figure_upload.config
    assert figure_cfg is not None
    assert isinstance(figure_cfg, FigureUploadConfig)
    assert figure_cfg.session_id == 'sess-1'
    assert figure_cfg.task_id == 'task-1'
    assert callable(figure_cfg.upload_bytes)
    assert "figure_upload_config" not in RunMetadata.model_fields
    assert ctx.request.ports.child_event_forward_sink is not None
    assert ctx.request.ports.compaction.history is not None


@pytest.mark.asyncio
async def test_run_agent_injects_turn_input_into_request():
    from matmaster.context.sources.turn_input import TurnInput

    run_result = RunResultEvent(source="agent", status="completed", reason="natural")
    turn_input = TurnInput.from_values(
        user_text="current prompt",
        files=["https://oss.example.com/chat/current.cif"],
        pre_turn_history_event_id=21,
    )

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        ok, _elapsed, _usage = await svc.run_agent(
            session_id="sess-1",
            user_prompt="current prompt",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-1",
            invocation_id="inv-current-input",
            turn_input=turn_input,
        )

    assert ok is True
    assert svc._test_fake_exp.last_ctx.request.turn_input == turn_input


@pytest.mark.asyncio
async def test_agent_run_service_keeps_compaction_history_without_attachment_run_meta():
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([run_result]) as (svc, _, __):
        svc._test_events_table.get_session_user_query_events.return_value = [
            {
                "id": 1,
                "source": "User",
                "type": "query",
                "content": "upload",
                "files": ["https://oss.example.com/chat/data.csv"],
            }
        ]
        svc._test_events_table.get_latest_scope_event_id.return_value = 25

        await svc.run_agent(
            session_id='sess-attachments',
            user_prompt='hi',
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='task-1',
            invocation_id='inv-attachments',
        )

    svc._test_fake_exp.last_ctx.environment.metadata
    assert "attachment_manifest" not in RunMetadata.model_fields
    history = svc._test_fake_exp.last_ctx.request.ports.compaction.history
    assert history is not None
    assert callable(history.query_events)
    assert history.query_events()[0]["files"] == [
        "https://oss.example.com/chat/data.csv"
    ]
    assert callable(history.all_events)
    assert callable(history.latest_checkpoint_covered_until_event_id)
    assert history.latest_scope_event_id() == 25
    await history.load_events(
        SessionEventQuery(
            session_id="sess-attachments",
            spawn_id=None,
            until_event_id=10,
            event_types=("query",),
        )
    )
    svc._test_events_table.get_latest_scope_event_id.assert_called_with(
        "sess-attachments",
        None,
    )
    svc._test_events_table.query_context_events.assert_called_with(
        session_id="sess-attachments",
        spawn_id=None,
        until_event_id=10,
        event_types=("query",),
        limit=None,
        order="asc",
    )
    assert callable(
        svc._test_fake_exp.last_ctx.request.ports.compaction.pre_compaction_barrier
    )


@pytest.mark.asyncio
async def test_run_agent_uses_model_history_restore_service_and_injects_spawn_aware_checkpoint_factory():
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')
    restored_history = [MagicMock(name='restored_message')]
    checkpoint_sink = AsyncMock(name='checkpoint_sink')

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        with (
            patch(
                'src.services.agent_run_history_wiring.ModelHistoryRestoreService',
                create=True,
            ) as restore_cls,
            patch(
                'src.services.agent_run_service.HistoryCheckpointService',
                create=True,
            ) as checkpoint_cls,
        ):
            restore_inst = MagicMock()
            restore_inst.restore_history.return_value = restored_history
            restore_cls.return_value = restore_inst

            checkpoint_inst = MagicMock()
            checkpoint_inst.build_checkpoint_sink.return_value = checkpoint_sink
            checkpoint_cls.return_value = checkpoint_inst

            ok, _elapsed, _usage = await svc.run_agent(
                session_id='sess-1',
                user_prompt='hello',
                send_cb=AsyncMock(),
                cancel_token=_make_cancel_token(),
                mode='direct',
                task_id='task-1',
                invocation_id='inv-1',
            )

        assert ok is True
        restore_cls.assert_called_once_with(svc._test_events_table)
        restore_inst.restore_history.assert_called_once_with(
            session_id='sess-1',
            spawn_id=None,
            task_id='task-1',
            raw_limit=ANY,
        )
        assert svc._test_fake_exp.last_run_kwargs is not None
        assert svc._test_fake_exp.last_run_kwargs['history'] == restored_history

        checkpoint_cls.assert_called_once_with(svc._test_events_table)
        checkpoint_sink_factory = (
            svc._test_fake_exp.last_ctx.request.ports.compaction.checkpoint_sink_factory
        )
        assert callable(checkpoint_sink_factory)

        built_sink = checkpoint_sink_factory(spawn_id='spawn-child-1')

        checkpoint_inst.build_checkpoint_sink.assert_called_once_with(
            fanout=ANY,
            session_id='sess-1',
            task_id='task-1',
            invocation_id='inv-1',
            spawn_id='spawn-child-1',
        )
        assert built_sink is checkpoint_sink


def test_run_agent_injects_bohrium_rebuild_events_into_request():
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')
    rebuild_events = [
        {
            'action': 'submit',
            'job_id': 'job-1',
            'job_name': 'alpha',
            'status': 'Submitted',
            'cached': False,
        }
    ]

    async def _run() -> tuple[Any, Any]:
        async with _patched_service([run_result]) as (svc, _sse, _persist):
            svc._test_events_table.get_bohrium_events.return_value = rebuild_events

            ok, _elapsed, _usage = await svc.run_agent(
                session_id='sess-1',
                user_prompt='hello',
                send_cb=AsyncMock(),
                cancel_token=_make_cancel_token(),
                mode='direct',
                task_id='task-1',
                invocation_id='inv-bohrium-rebuild',
            )
            return svc, ok

    svc, ok = asyncio.run(_run())

    assert ok is True
    svc._test_events_table.get_bohrium_events.assert_called_once_with('sess-1')
    assert svc._test_fake_exp.last_ctx.request.bohrium_rebuild_events == tuple(
        rebuild_events
    )


@pytest.mark.asyncio
async def test_stream_events_reach_handlers_via_fanout():
    """Events from exp.run_stream() are dispatched through RunEventFanout to handlers."""
    thought = ThoughtEvent(source='agent', content='thinking...')
    response = ResponseEvent(source='agent', content='hello')
    run_result = RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service([thought, response, run_result]) as (
        svc,
        sse_events,
        persist_events,
    ):
        await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='t1',
            invocation_id='inv-stream-events',
        )

    # SSE handler should receive: thought + response + run_result + StreamClosedEvent = 4+
    sse_types = [getattr(e, 'type', None) for e in sse_events]
    assert 'thought' in sse_types
    assert 'response' in sse_types
    assert 'run_result' in sse_types
    assert 'stream_closed' in sse_types


@pytest.mark.asyncio
async def test_child_event_sink_reaches_sse_and_persistence():
    async def child_then_parent(ctx):
        await ctx.request.ports.child_event_forward_sink(
            ResponseEvent(
                source='MatMaster:direct',
                spawn_id='childdeadbeef123',
                content='child answer',
            )
        )
        yield RunResultEvent(source='agent', status='completed', reason='natural')

    async with _patched_service(child_then_parent) as (
        svc,
        sse_events,
        persist_events,
    ):
        await svc.run_agent(
            session_id='s1',
            user_prompt='hi',
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode='direct',
            task_id='t1',
            invocation_id='inv-child-events',
        )

    assert any(
        getattr(event, 'spawn_id', None) == 'childdeadbeef123' for event in sse_events
    )
    assert any(
        getattr(event, 'spawn_id', None) == 'childdeadbeef123'
        for event in persist_events
    )


@pytest.mark.asyncio
async def test_run_agent_does_not_store_callback_ports_in_metadata():
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        await svc.run_agent(
            session_id="session-1",
            user_prompt="hello",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-1",
            invocation_id="inv-1",
        )

    field_names = set(RunMetadata.model_fields)
    forbidden = {
        "event_sink",
        "checkpoint_sink_factory",
        "get_query_events",
        "get_all_events",
        "get_latest_checkpoint_covered_until_event_id",
        "pre_compaction_barrier",
        "figure_upload_config",
    }
    assert forbidden.isdisjoint(field_names)


@pytest.mark.asyncio
async def test_run_agent_injects_user_turn_context_writer_and_raw_turn_input():
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        svc._test_session.read_file.return_value = "Prefer concise answers."
        svc._test_events_table.get_recent_context_anchor_events.return_value = []
        svc._test_events_table.query_user_turn_context_by_invocation.return_value = None

        ok, _elapsed, _usage = await svc.run_agent(
            session_id="sess-1",
            user_prompt="first question",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-1",
            invocation_id="inv-1",
        )

    assert ok is True
    request = svc._test_fake_exp.last_ctx.request
    assert request.invocation_id == "inv-1"
    assert request.ports.user_turn_context_writer is not None
    assert request.user_instructions.hash.startswith("sha256:")
    assert svc._test_fake_exp.last_task == "first question"
    assert svc._test_events_table.add_event.call_args_list == []


@pytest.mark.asyncio
async def test_run_agent_user_turn_context_records_full_provider_facing_with_attachments():
    from matmaster.context.sources.turn_input import TurnInput

    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        svc._test_session.read_file.return_value = "Use SI units."
        svc._test_events_table.get_recent_context_anchor_events.return_value = []
        svc._test_events_table.query_user_turn_context_by_invocation.return_value = None
        svc._test_events_table.get_session_user_query_events.return_value = [
            {
                "id": 10,
                "source": "User",
                "type": "query",
                "content": "Compare FeO vs Fe2O3 from these files",
                "files": [
                    "https://oss.example.com/input/feo.cif",
                    "https://oss.example.com/input/fe2o3.cif",
                ],
                "images": ["https://oss.example.com/input/struct1.png"],
                "workspace_paths": ["/workspace/notes.md"],
            }
        ]
        turn_input = TurnInput.from_values(
            user_text="Compare FeO vs Fe2O3 from these files",
            files=[
                "https://oss.example.com/input/feo.cif",
                "https://oss.example.com/input/fe2o3.cif",
            ],
            images=["https://oss.example.com/input/struct1.png"],
            workspace_paths=["/workspace/notes.md"],
        )
        image_service = MagicMock()
        image_service.resolve_image_detail.return_value = None
        image_service.enrich_turn_input_images.side_effect = (
            ImageInputService().enrich_turn_input_images
        )

        with patch(
            "src.services.agent_run_service.get_image_input_service",
            return_value=image_service,
        ):
            ok, _elapsed, _usage = await svc.run_agent(
                session_id="sess-1",
                user_prompt="Compare FeO vs Fe2O3 from these files",
                images=["https://oss.example.com/input/struct1.png"],
                turn_input=turn_input,
                send_cb=AsyncMock(),
                cancel_token=_make_cancel_token(),
                mode="direct",
                task_id="task-att",
                invocation_id="inv-att",
            )

    assert ok is True
    request = svc._test_fake_exp.last_ctx.request
    assert request.ports.user_turn_context_writer is not None
    assert request.turn_input.files == (
        "https://oss.example.com/input/feo.cif",
        "https://oss.example.com/input/fe2o3.cif",
    )
    assert request.turn_input.workspace_paths == ("/workspace/notes.md",)
    assert request.turn_input.images == ("https://oss.example.com/input/struct1.png",)
    assert svc._test_fake_exp.last_task == "Compare FeO vs Fe2O3 from these files"


@pytest.mark.asyncio
async def test_run_agent_leaves_active_skill_resolution_to_exp_without_hot_cache():
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        svc._test_events_table.query_context_events.return_value = [
            {
                "id": 1,
                "type": "skill_hit",
                "source": "MatMaster",
                "content": {"skill_name": "mlip"},
            }
        ]

        ok, _elapsed, _usage = await svc.run_agent(
            session_id="sess-1",
            user_prompt="hello",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-1",
            invocation_id="inv-active-skills",
        )

    assert ok is True
    assert not hasattr(svc, "_active_skills")
    assert svc._test_fake_exp.last_ctx.request.active_skills == frozenset()
    assert svc._test_fake_exp.last_ctx.request.ports.compaction.history is not None


@pytest.mark.asyncio
async def test_run_agent_writes_continuation_when_instruction_hash_matches():
    from src.services.user_turn_context_service import hash_user_instructions

    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        svc._test_session.read_file.return_value = "Stable preference."
        svc._test_events_table.query_context_events.return_value = [
            {
                "id": 1,
                "type": "user_turn_context",
                "source": "MatMaster",
                "content": {
                    "kind": "anchor",
                    "user_instructions_hash": hash_user_instructions(
                        "Stable preference."
                    ),
                },
            }
        ]
        svc._test_events_table.query_user_turn_context_by_invocation.return_value = None

        ok, _elapsed, _usage = await svc.run_agent(
            session_id="sess-1",
            user_prompt="follow up",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-2",
            invocation_id="inv-2",
        )

    assert ok is True
    assert (
        svc._test_fake_exp.last_ctx.request.ports.user_turn_context_writer is not None
    )
    assert svc._test_fake_exp.last_task == "follow up"


@pytest.mark.asyncio
async def test_run_agent_uses_byok_profile_identity_and_skips_model_quota():
    from matmaster.config.llm import LLMProfileConfig

    run_result = RunResultEvent(source="agent", status="completed", reason="natural")
    byok_profile = LLMProfileConfig(
        provider="openai",
        model="model-a",
        api_key="sk-test",
        base_url="https://api.example.com/v1",
    )
    byok_provider = MagicMock()

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        with patch(
            "matmaster.providers.llm_factory.build_provider_from_profile",
            return_value=byok_provider,
        ) as build_from_profile:
            ok, _elapsed, _usage = await svc.run_agent(
                session_id="sess-1",
                user_prompt="hello",
                send_cb=AsyncMock(),
                cancel_token=_make_cancel_token(),
                mode="direct",
                task_id="task-1",
                invocation_id="inv-byok",
                byok_profile=byok_profile,
                byok_config_id=12,
                byok_config_version=3,
                billing_mode="byok",
            )

    assert ok is True
    build_from_profile.assert_called_once_with(byok_profile, byok_profile.model)
    request = svc._test_fake_exp.last_ctx.request
    assert request.llm_provider is byok_provider
    assert request.llm_model == byok_profile.model
    assert request.llm_model_profile == "byok:12"
    assert request.llm_model_route is None


@pytest.mark.asyncio
async def test_preset_model_success_wraps_provider_for_platform_billing():
    from src.services import agent_run_service as mod

    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        ok, _elapsed, _usage = await svc.run_agent(
            session_id="sess-1",
            user_prompt="hello",
            send_cb=AsyncMock(),
            cancel_token=_make_cancel_token(),
            mode="direct",
            task_id="task-1",
            invocation_id="inv-platform-quota",
            model_override="claude-sonnet-4-6",
        )

    assert ok is True
    request = svc._test_fake_exp.last_ctx.request
    assert isinstance(request.llm_provider, mod.BillingLLMProvider)
    assert request.llm_model == "test-model"


@pytest.mark.asyncio
async def test_run_agent_idempotent_skip_when_user_turn_context_already_exists():
    from matmaster.context.ports import hash_user_instructions
    from matmaster.context.user_turn_context import (
        DEFAULT_TURN_TRANSFORM,
        USER_CONTEXT_RENDER_VERSION,
        USER_TURN_CONTEXT_SCHEMA_VERSION,
    )
    from matmaster.types.messages import UserMessage

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

        ok, _elapsed, _usage = await svc.run_agent(
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
    assert svc._test_fake_exp.last_task == "first question"


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
        events_table.query_context_events.return_value = []
        events_table.get_bohrium_events.return_value = []
        events_table_fn.return_value = events_table

        error_exp = _ErrorExp([])

        with (
            patch('matmaster.config.loader.load_exp_config', return_value=MagicMock()),
            patch('matmaster.config.loader.load_llm_config', return_value=MagicMock()),
            patch(
                'matmaster.providers.llm_factory.build_provider_bundle',
                return_value=SimpleNamespace(
                    provider=MagicMock(),
                    model="test-model",
                    model_profile="test-profile",
                    model_route="test-route",
                ),
            ),
            patch('matmaster.core.exp.Exp', new=lambda config: error_exp),
        ):

            from src.services.agent_run_service import AgentRunService

            svc = AgentRunService.__new__(AgentRunService)
            svc._sessions_service = MagicMock()
            svc._pg_manager = pg_mgr

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
