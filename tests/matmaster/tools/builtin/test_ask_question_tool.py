"""Tests for AskQuestionTool and AskQuestionBridge."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from typing import Any

from matmaster.integration.interaction_bridge import (
    AskQuestionBridge,
    AskQuestionResponse,
)
from matmaster.tools.builtin.ask_question_tool import AskQuestionTool
from matmaster.types.cancellation import CancellationToken
from matmaster.types.tool_spec import ToolExecutionContext


class _FakeBridge:
    """测试用 bridge，ask() 直接返回预设的 response。"""

    def __init__(self, response: AskQuestionResponse | None = None) -> None:
        self._response = response
        self.last_kwargs: dict[str, Any] | None = None

    async def ask(self, **kwargs: Any) -> AskQuestionResponse:
        self.last_kwargs = kwargs
        if self._response is None:
            return {"request_id": "fake", "answers": {}, "annotations": {}}
        return self._response


class _QueueReplyQueue:
    """测试用 reply queue，按顺序返回预设值。"""

    def __init__(self, values: list[str | None]) -> None:
        self.values = list(values)

    def put_content(self, content: str) -> None:
        self.values.append(content)

    def put_cancel(self) -> None:
        self.values.append(None)

    def get(self, timeout: float | None = None) -> str | None:
        if not self.values:
            raise queue.Empty
        return self.values.pop(0)


def _exec_ctx() -> ToolExecutionContext:
    return ToolExecutionContext(cancel_token=CancellationToken())


def test_ask_question_declares_interaction_exclusive_resource_claim() -> None:
    claims = AskQuestionTool.resource_claims
    assert len(claims) == 1
    assert claims[0].resource == "interaction"
    assert claims[0].mode == "exclusive"


class TestAskQuestionToolValidation:
    def test_validate_rejects_duplicate_question_texts(self) -> None:
        tool = AskQuestionTool(bridge=_FakeBridge())
        decision = asyncio.run(
            tool.validate_input(
                {
                    "questions": [
                        {
                            "question": "Which library should we use?",
                            "header": "Library",
                            "options": [
                                {
                                    "label": "Pydantic",
                                    "description": "Runtime validation",
                                },
                                {"label": "dataclasses", "description": "Stdlib only"},
                            ],
                        },
                        {
                            "question": "Which library should we use?",
                            "header": "Again",
                            "options": [
                                {"label": "A", "description": "a"},
                                {"label": "B", "description": "b"},
                            ],
                        },
                    ]
                }
            )
        )
        assert decision.decision == "deny"

    def test_validate_allows_unique_questions(self) -> None:
        tool = AskQuestionTool(bridge=_FakeBridge())
        decision = asyncio.run(
            tool.validate_input(
                {
                    "questions": [
                        {
                            "question": "Q1",
                            "header": "H1",
                            "options": [
                                {"label": "A", "description": "a"},
                                {"label": "B", "description": "b"},
                            ],
                        },
                    ]
                }
            )
        )
        assert decision is None


class TestAskQuestionToolExecution:
    def test_execute_with_context_returns_model_readable_summary(self) -> None:
        tool = AskQuestionTool(
            bridge=_FakeBridge(
                {
                    "request_id": "aq_1",
                    "answers": {
                        "Which library should we use?": "Pydantic (Recommended)"
                    },
                    "annotations": {
                        "Which library should we use?": {
                            "notes": "Need runtime validation"
                        }
                    },
                }
            )
        )
        result = asyncio.run(
            tool.execute_with_context(
                {
                    "questions": [
                        {
                            "question": "Which library should we use?",
                            "header": "Library",
                            "options": [
                                {
                                    "label": "Pydantic (Recommended)",
                                    "description": "Runtime validation",
                                },
                                {
                                    "label": "dataclasses",
                                    "description": "Stdlib only",
                                },
                            ],
                        }
                    ]
                },
                _exec_ctx(),
            )
        )
        assert (
            '"Which library should we use?"="Pydantic (Recommended)"' in result.content
        )
        assert "user notes: Need runtime validation" in result.content

    def test_execute_without_bridge_returns_error(self) -> None:
        tool = AskQuestionTool(bridge=None)
        result = asyncio.run(
            tool.execute_with_context(
                {"questions": [{"question": "Q", "header": "H", "options": []}]},
                _exec_ctx(),
            )
        )
        assert result.status == "error"
        assert "no interaction bridge" in result.content

    def test_execute_accepts_prompt_multi_select_alias(self) -> None:
        bridge = _FakeBridge()
        tool = AskQuestionTool(bridge=bridge)

        asyncio.run(
            tool.execute_with_context(
                {
                    "questions": [
                        {
                            "question": "Q",
                            "header": "H",
                            "options": [
                                {"label": "A", "description": "a"},
                                {"label": "B", "description": "b"},
                            ],
                            "multiSelect": True,
                        }
                    ]
                },
                _exec_ctx(),
            )
        )

        assert (
            "multiSelect"
            in AskQuestionTool.json_schema["properties"]["questions"]["items"][
                "properties"
            ]
        )
        assert bridge.last_kwargs is not None
        assert bridge.last_kwargs["questions"][0]["multi_select"] is True


class TestAskQuestionToolVisibility:
    def test_prompt_contains_key_usage_guidance(self) -> None:
        tool = AskQuestionTool(bridge=_FakeBridge())
        prompt = tool.prompt()

        assert prompt
        assert "ask the user questions during execution" in prompt
        assert '"Other"' in prompt
        assert "multiSelect: true" in prompt
        assert "(Recommended)" in prompt
        assert "Planner mode note" in prompt
        assert "Do NOT use this tool" in prompt

    def test_hidden_without_bridge(self) -> None:
        tool = AskQuestionTool(bridge=None)
        assert tool.exposed_to_model is False

    def test_visible_with_bridge(self) -> None:
        tool = AskQuestionTool(bridge=_FakeBridge())
        assert tool.exposed_to_model is True


class TestAskQuestionBridge:
    def test_emits_typed_events_through_async_sink(self) -> None:
        sent_events: list[Any] = []

        async def event_sink(event: Any) -> None:
            sent_events.append(event)

        bridge = AskQuestionBridge(
            session_id="session_1",
            event_sink=event_sink,
            reply_queue=_QueueReplyQueue(
                [
                    json.dumps(
                        {
                            "payload": {
                                "request_id": "aq_1",
                                "answers": {"Q1": "A1"},
                                "annotations": {},
                            }
                        }
                    )
                ]
            ),
        )

        response = asyncio.run(
            bridge.ask(
                request_id="aq_1",
                questions=[
                    {
                        "question": "Q1",
                        "header": "H1",
                        "options": [
                            {"label": "A1", "description": "desc"},
                            {"label": "A2", "description": "desc"},
                        ],
                    }
                ],
                metadata={"scene": "unit"},
            )
        )

        assert response["answers"] == {"Q1": "A1"}
        assert len(sent_events) == 1
        assert sent_events[0].type == "ask_question"
        assert sent_events[0].request_id == "aq_1"
        assert sent_events[0].metadata == {"scene": "unit"}

    def test_timeout_event_is_emitted_from_event_loop_thread(self) -> None:
        sent_events: list[Any] = []

        async def event_sink(event: Any) -> None:
            sent_events.append(event)

        bridge = AskQuestionBridge(
            session_id="session_1",
            event_sink=event_sink,
            reply_queue=_QueueReplyQueue([]),
            timeout_seconds=1,
        )

        try:
            asyncio.run(
                bridge.ask(
                    request_id="aq_timeout",
                    questions=[],
                    metadata=None,
                )
            )
        except TimeoutError:
            pass
        else:
            raise AssertionError("expected TimeoutError")

        assert [event.type for event in sent_events] == [
            "ask_question",
            "ask_question_timeout",
        ]
        assert sent_events[1].request_id == "aq_timeout"

    def test_cancel_sentinel_does_not_emit_timeout(self) -> None:
        sent_events: list[Any] = []

        async def event_sink(event: Any) -> None:
            sent_events.append(event)

        bridge = AskQuestionBridge(
            session_id="session_1",
            event_sink=event_sink,
            reply_queue=_QueueReplyQueue([None]),
        )

        try:
            asyncio.run(bridge.ask(request_id="aq_cancel", questions=[], metadata=None))
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("expected CancelledError")

        assert [event.type for event in sent_events] == ["ask_question"]

    def test_reply_request_id_mismatch_is_hard_error(self) -> None:
        async def event_sink(event: Any) -> None:
            return None

        bridge = AskQuestionBridge(
            session_id="session_1",
            event_sink=event_sink,
            reply_queue=_QueueReplyQueue(
                [
                    json.dumps(
                        {
                            "payload": {
                                "request_id": "aq_other",
                                "answers": {"Q1": "A1"},
                                "annotations": {},
                            }
                        }
                    )
                ]
            ),
        )

        try:
            asyncio.run(bridge.ask(request_id="aq_1", questions=[], metadata=None))
        except RuntimeError as exc:
            assert "request_id mismatch" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")

    def test_concurrent_asks_are_serialized(self) -> None:
        sent_events: list[Any] = []
        first_reply_allowed = threading.Event()

        class _BlockingQueue:
            def __init__(self) -> None:
                self.calls = 0

            def put_content(self, content: str) -> None:
                return None

            def put_cancel(self) -> None:
                return None

            def get(self, timeout: float | None = None) -> str | None:
                self.calls += 1
                if self.calls == 1:
                    while not first_reply_allowed.is_set():
                        time.sleep(0.001)
                    return json.dumps(
                        {
                            "payload": {
                                "request_id": "aq_1",
                                "answers": {},
                                "annotations": {},
                            }
                        }
                    )
                return json.dumps(
                    {
                        "payload": {
                            "request_id": "aq_2",
                            "answers": {},
                            "annotations": {},
                        }
                    }
                )

        async def event_sink(event: Any) -> None:
            sent_events.append(event)

        async def run_two() -> None:
            bridge = AskQuestionBridge(
                session_id="session_1",
                event_sink=event_sink,
                reply_queue=_BlockingQueue(),
            )
            t1 = asyncio.create_task(
                bridge.ask(request_id="aq_1", questions=[], metadata=None)
            )
            await asyncio.sleep(0)
            t2 = asyncio.create_task(
                bridge.ask(request_id="aq_2", questions=[], metadata=None)
            )
            await asyncio.sleep(0.05)
            assert [event.request_id for event in sent_events] == ["aq_1"]
            first_reply_allowed.set()
            await asyncio.gather(t1, t2)

        asyncio.run(run_two())
        assert [event.request_id for event in sent_events] == ["aq_1", "aq_2"]
