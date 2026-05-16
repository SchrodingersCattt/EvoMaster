from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from matmaster.types.events import RunResultEvent, ToolResultEvent

from .test_agent_run_stream_fixtures import (
    _ImmediateReplyQueue,
    _make_cancel_token,
    _patched_service,
)


@pytest.mark.asyncio
async def test_ask_question_bridge_events_go_through_fanout_and_persistence():
    async def ask_then_finish(ctx):
        await ctx.interaction_bridge.ask(
            request_id="aq_1",
            questions=[
                {
                    "question": "Q1",
                    "header": "H1",
                    "options": [
                        {"label": "A1", "description": "desc"},
                        {"label": "A2", "description": "desc"},
                    ],
                    "allow_freeform": True,
                    "multi_select": False,
                }
            ],
            metadata={"scene": "test"},
        )
        yield RunResultEvent(source="agent", status="completed", reason="natural")

    send_cb = MagicMock()
    reply_queue = _ImmediateReplyQueue(
        json.dumps(
            {
                "payload": {
                    "request_id": "aq_1",
                    "answers": {"Q1": "A1"},
                    "annotations": {},
                }
            }
        )
    )

    async with _patched_service(ask_then_finish) as (svc, _, persist_events):
        with patch(
            "src.services.agent_run_service.RedisReplyQueue",
            return_value=reply_queue,
        ):
            await svc.run_agent(
                session_id="s1",
                user_prompt="hi",
                send_cb=send_cb,
                cancel_token=_make_cancel_token(),
                mode="direct",
                task_id="t1",
                invocation_id="inv-1",
            )

    payload = send_cb.call_args_list[0].args[0]
    assert payload["type"] == "ask_question"
    assert payload["session_id"] == "s1"
    assert payload["task_id"] == "t1"
    assert payload["invocation_id"] == "inv-1"
    assert payload["content"]["request_id"] == "aq_1"
    assert payload["content"]["metadata"] == {"scene": "test"}

    persisted = [
        event
        for event in persist_events
        if getattr(event, "type", None) == "ask_question"
    ]
    assert len(persisted) == 1
    assert persisted[0].request_id == "aq_1"


@pytest.mark.asyncio
async def test_ask_question_tool_result_reaches_sse_before_run_result():
    async def ask_then_emit_tool_result(ctx):
        await ctx.interaction_bridge.ask(
            request_id="aq_1",
            questions=[
                {
                    "question": "Q1",
                    "header": "H1",
                    "options": [
                        {"label": "A1", "description": "desc"},
                        {"label": "A2", "description": "desc"},
                    ],
                    "allow_freeform": True,
                    "multi_select": False,
                }
            ],
            metadata={"scene": "stream-order"},
        )
        yield ToolResultEvent(
            source="agent",
            call_id="call_aq_1",
            tool_name="AskQuestion",
            result='"Q1"="A1"',
            status="success",
            payload={
                "request_id": "aq_1",
                "answers": {"Q1": "A1"},
                "annotations": {},
            },
        )
        yield RunResultEvent(source="agent", status="completed", reason="natural")

    send_cb = MagicMock()
    reply_queue = _ImmediateReplyQueue(
        json.dumps(
            {
                "payload": {
                    "request_id": "aq_1",
                    "answers": {"Q1": "A1"},
                    "annotations": {},
                }
            }
        )
    )

    async with _patched_service(ask_then_emit_tool_result) as (svc, _, __):
        with patch(
            "src.services.agent_run_service.RedisReplyQueue",
            return_value=reply_queue,
        ):
            await svc.run_agent(
                session_id="s1",
                user_prompt="hi",
                send_cb=send_cb,
                cancel_token=_make_cancel_token(),
                mode="direct",
                task_id="t1",
                invocation_id="inv-1",
            )

    payloads = [call.args[0] for call in send_cb.call_args_list]
    payload_types = [payload["type"] for payload in payloads]

    ask_idx = payload_types.index("ask_question")
    tool_result_idx = payload_types.index("tool_result")
    run_result_idx = payload_types.index("run_result")
    stream_closed_idx = payload_types.index("stream_closed")

    assert ask_idx < tool_result_idx < run_result_idx < stream_closed_idx
    assert payloads[tool_result_idx]["content"]["id"] == "call_aq_1"
    assert payloads[tool_result_idx]["content"]["name"] == "AskQuestion"
    assert payloads[tool_result_idx]["content"]["result"] == '"Q1"="A1"'
