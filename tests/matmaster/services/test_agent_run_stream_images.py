from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matmaster.context.sources.turn_input import TurnInput
from matmaster.types.events import RunResultEvent
from matmaster.types.messages import ImageContentPart
from matmaster.types.run_metadata import RunMetadata
from tests.matmaster.services.agent_run_stream_fixtures import (
    _make_cancel_token,
    _patched_service,
)


@pytest.mark.asyncio
async def test_run_agent_builds_turn_input_from_images_without_image_metadata_key():
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        image_service = MagicMock()
        image_service.ensure_vision_supported.return_value = MagicMock(
            vision_detail="high"
        )
        with patch(
            "src.services.agent_run_service.get_image_input_service",
            return_value=image_service,
        ):
            ok, _elapsed, _usage = await svc.run_agent(
                session_id="sess-images",
                user_prompt="看图",
                images=["https://oss.example.com/chat/a.png"],
                send_cb=AsyncMock(),
                cancel_token=_make_cancel_token(),
                mode="direct",
                task_id="task-images",
                invocation_id="inv-images",
            )

    assert ok is True
    assert "current_user_images" not in RunMetadata.model_fields
    turn_input = svc._test_fake_exp.last_ctx.request.turn_input
    assert isinstance(turn_input, TurnInput)
    assert turn_input.images == ("https://oss.example.com/chat/a.png",)
    assert turn_input.attachments.image_detail == "high"
    assert turn_input.attachments.images_as_parts() == (
        ImageContentPart(url="https://oss.example.com/chat/a.png", detail="high"),
    )


@pytest.mark.asyncio
async def test_run_agent_enriches_existing_turn_input_images_with_detail():
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")
    turn_input = TurnInput.from_values(
        user_text="看图",
        images=["https://oss.example.com/chat/a.png"],
        pre_turn_history_event_id=12,
    )

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        image_service = MagicMock()
        image_service.ensure_vision_supported.return_value = MagicMock(
            vision_detail="high"
        )
        with patch(
            "src.services.agent_run_service.get_image_input_service",
            return_value=image_service,
        ):
            ok, _elapsed, _usage = await svc.run_agent(
                session_id="sess-images",
                user_prompt="看图",
                images=["https://oss.example.com/chat/a.png"],
                turn_input=turn_input,
                send_cb=AsyncMock(),
                cancel_token=_make_cancel_token(),
                mode="direct",
                task_id="task-images",
                invocation_id="inv-images",
            )

    assert ok is True
    enriched = svc._test_fake_exp.last_ctx.request.turn_input
    assert isinstance(enriched, TurnInput)
    assert enriched.images == ("https://oss.example.com/chat/a.png",)
    assert enriched.pre_turn_history_event_id == 12
    assert enriched.attachments.image_detail == "high"
    assert enriched.attachments.images_as_parts() == (
        ImageContentPart(url="https://oss.example.com/chat/a.png", detail="high"),
    )


@pytest.mark.asyncio
async def test_run_agent_validates_images_from_turn_input_without_top_level_images():
    run_result = RunResultEvent(source="agent", status="completed", reason="natural")
    turn_input = TurnInput.from_values(
        user_text="看图",
        images=["https://oss.example.com/chat/a.png"],
    )

    async with _patched_service([run_result]) as (svc, _sse, _persist):
        image_service = MagicMock()
        image_service.ensure_vision_supported.return_value = MagicMock(
            vision_detail="high"
        )
        with patch(
            "src.services.agent_run_service.get_image_input_service",
            return_value=image_service,
        ):
            ok, _elapsed, _usage = await svc.run_agent(
                session_id="sess-images",
                user_prompt="看图",
                turn_input=turn_input,
                send_cb=AsyncMock(),
                cancel_token=_make_cancel_token(),
                mode="direct",
                task_id="task-images",
                invocation_id="inv-images",
            )

    assert ok is True
    image_service.ensure_vision_supported.assert_called_once()
    enriched = svc._test_fake_exp.last_ctx.request.turn_input
    assert enriched.images == ("https://oss.example.com/chat/a.png",)
    assert enriched.attachments.image_detail == "high"
