from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matmaster.config.exp import ExpConfig
from matmaster.context.ports import UserInstructions
from matmaster.context.sources.turn_input import TurnInput
from matmaster.core.playground import PlaygroundContext
from matmaster.sessions.local import LocalSession
from matmaster.types.cancellation import CancellationController
from matmaster.types.messages import LLMResponse, StreamChunk


class RecordingVisionProvider:
    stream_timeout = 10.0
    max_retries = 1
    retry_delay = 0.0

    def __init__(self) -> None:
        self.seen_messages: list[list[dict[str, Any]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def chat(self, messages, tools=None):
        return LLMResponse(content="done", finish_reason="stop")

    async def chat_stream(self, messages, tools=None, *, timeout=None):
        self.seen_messages.append(messages)
        yield StreamChunk(content="done")
        yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 5})


def _make_events_table() -> MagicMock:
    table = MagicMock()
    table.get_recent_context_anchor_events.return_value = []
    table.query_user_turn_context_by_invocation.return_value = None
    table.add_event.return_value = True
    table.get_session_user_query_events.return_value = []
    table.query_context_events.return_value = []
    table.get_bohrium_events.return_value = []
    table.get_latest_scope_event_id.return_value = 0
    table.get_history_checkpoints.return_value = []
    table.get_session_events.return_value = []
    table.has_user_turn_context.return_value = False
    return table


@pytest.mark.asyncio
async def test_images_flow_from_service_to_kernel_user_message(tmp_path: Path) -> None:
    from src.services.agent_run_service import AgentRunService

    provider = RecordingVisionProvider()
    session = LocalSession(workspace_path=tmp_path / "workspace")
    pg_ctx = PlaygroundContext(
        workdir=tmp_path / "workspace",
        session_type="local",
        session_id="sess-images",
        cache_area=tmp_path / "cache",
        execution_workdir=str(tmp_path / "workspace"),
        session=session,
    )
    playground = MagicMock()
    playground.prepare.return_value = pg_ctx

    pg_manager = MagicMock()
    pg_manager.get_or_create.return_value = playground
    pg_manager.validate_startup.return_value = None

    sessions_service = MagicMock()
    sessions_service.get_session_user_id.return_value = "user-1"

    llm_config = MagicMock()
    llm_config.resolve_route.return_value = SimpleNamespace(profile_key="vision")
    vision_profile = SimpleNamespace(vision_detail="high")
    llm_config.get_profile.return_value = vision_profile

    image_service = MagicMock()
    image_service.ensure_vision_supported.return_value = vision_profile

    bohrium_service = MagicMock()
    bohrium_service.run_cleanup = AsyncMock()
    stage_result = SimpleNamespace(
        pg_ctx=pg_ctx,
        bohrium_svc=bohrium_service,
        abort_result=None,
        ssh_attached=False,
        user_instructions=UserInstructions(text="", hash="", truncated=False),
    )

    svc = AgentRunService.__new__(AgentRunService)
    svc._sessions_service = sessions_service
    svc._pg_manager = pg_manager
    svc._active_skills = {}

    with (
        patch("src.services.agent_run_service.get_chat_events_table") as events_fn,
        patch("src.services.agent_run_service.SSEHandler") as sse_cls,
        patch("src.services.agent_run_service.PersistenceHandler") as persist_cls,
        patch(
            "src.services.agent_run_service.run_bohrium_stage",
            new=AsyncMock(return_value=stage_result),
        ),
        patch("matmaster.config.loader.load_llm_config", return_value=llm_config),
        patch(
            "matmaster.config.loader.load_exp_config",
            return_value=ExpConfig(
                name="direct",
                tools={"builtin": []},
                skills={"enabled": False},
            ),
        ),
        patch(
            "matmaster.providers.llm_factory.build_provider",
            return_value=provider,
        ),
        patch(
            "src.services.agent_run_service.get_image_input_service",
            return_value=image_service,
        ),
        patch(
            "src.services.agent_run_service._get_agent_default_llm",
            return_value=None,
        ),
        patch("src.services.agent_run_service.use_quota", new_callable=AsyncMock),
    ):
        events_fn.return_value = _make_events_table()
        sse_cls.return_value.handle = AsyncMock()
        persist_cls.return_value.handle = AsyncMock()

        turn_input = TurnInput.from_payload(
            TurnInput.from_values(
                user_text="看图",
                images=["https://oss.example.com/chat/a.png"],
            ).to_payload()
        )
        assert turn_input is not None
        assert turn_input.attachments.image_detail is None

        ok, _elapsed, _usage = await svc.run_agent(
            session_id="sess-images",
            user_prompt="看图",
            images=["https://oss.example.com/chat/a.png"],
            turn_input=turn_input,
            send_cb=AsyncMock(),
            cancel_token=CancellationController().token,
            mode="direct",
            task_id="task-images",
            invocation_id="inv-images",
        )

    assert ok is True
    user_message = provider.seen_messages[-1][-1]
    assert user_message["role"] == "user"
    assert {
        "type": "image_url",
        "image_url": {
            "url": "https://oss.example.com/chat/a.png",
            "detail": "high",
        },
    } in user_message["content"]
