"""Tests for AskQuestionTool and AskQuestionBridge."""

from __future__ import annotations

import asyncio
import json
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

    async def ask(self, **kwargs: Any) -> AskQuestionResponse:
        if self._response is None:
            return {"request_id": "fake", "answers": {}, "annotations": {}}
        return self._response


class _ImmediateReplyQueue:
    """测试用 reply queue，立即返回预设 envelope。"""

    def __init__(self, envelope: str) -> None:
        self._envelope = envelope

    def put_content(self, content: str) -> None:
        self._envelope = content

    def put_cancel(self) -> None:
        self._envelope = ""

    def get(self, timeout: float | None = None) -> str | None:
        return self._envelope


def _exec_ctx() -> ToolExecutionContext:
    return ToolExecutionContext(cancel_token=CancellationToken())


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


class TestAskQuestionToolVisibility:
    def test_hidden_without_bridge(self) -> None:
        tool = AskQuestionTool(bridge=None)
        assert tool.exposed_to_model is False

    def test_visible_with_bridge(self) -> None:
        tool = AskQuestionTool(bridge=_FakeBridge())
        assert tool.exposed_to_model is True


class TestAskQuestionBridge:
    def test_emits_json_serializable_ask_question_event(self) -> None:
        sent_payloads: list[dict[str, Any]] = []
        bridge = AskQuestionBridge(
            session_id="session_1",
            send_cb=sent_payloads.append,
            reply_queue=_ImmediateReplyQueue(
                json.dumps(
                    {
                        "payload": {
                            "answers": {"Q1": "A1"},
                            "annotations": {},
                        }
                    }
                )
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
                metadata={},
            )
        )

        assert response["answers"] == {"Q1": "A1"}
        assert sent_payloads
        assert isinstance(sent_payloads[0]["timestamp"], str)
        json.dumps(sent_payloads[0], ensure_ascii=False)
