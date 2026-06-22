"""Tests for AskQuestionTool."""

from __future__ import annotations

import asyncio
from typing import Any

from matmaster.tools.builtin.ask_question_tool import AskQuestionTool
from matmaster.types.cancellation import CancellationToken
from matmaster.types.events import InteractionTimeoutEvent
from matmaster.types.tool_spec import ToolExecutionContext


class _FakeBridge:
    """测试用 bridge，request() 直接返回预设的 reply payload。"""

    def __init__(
        self,
        response: dict[str, Any] | None = None,
        exc: BaseException | None = None,
    ) -> None:
        self._response = response
        self._exc = exc
        self.last_kwargs: dict[str, Any] | None = None
        self.emitted: list[Any] = []
        self._event_sink = self._record_event

    async def request(self, **kwargs: Any) -> dict[str, Any]:
        self.last_kwargs = kwargs
        if self._exc is not None:
            raise self._exc
        return self._response or {"answers": {}, "annotations": {}}

    async def _record_event(self, event: Any) -> None:
        self.emitted.append(event)

    async def emit(self, event: Any) -> None:
        await self._record_event(event)


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
        assert tool._bridge.last_kwargs["kind"] == "ask_question"
        assert tool._bridge.last_kwargs["payload"]["origin"] == "tool:AskQuestion"
        assert result.payload["request_id"] == tool._bridge.last_kwargs["request_id"]

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
        assert bridge.last_kwargs["payload"]["questions"][0]["multi_select"] is True

    def test_timeout_emits_interaction_timeout_event(self) -> None:
        bridge = _FakeBridge(exc=TimeoutError("timed out"))
        tool = AskQuestionTool(bridge=bridge)

        try:
            asyncio.run(
                tool.execute_with_context(
                    {"questions": [{"question": "Q", "header": "H", "options": []}]},
                    _exec_ctx(),
                )
            )
        except TimeoutError:
            pass
        else:
            raise AssertionError("expected TimeoutError")

        assert len(bridge.emitted) == 1
        assert isinstance(bridge.emitted[0], InteractionTimeoutEvent)
        assert bridge.emitted[0].kind == "ask_question"
        assert bridge.emitted[0].request_id == bridge.last_kwargs["request_id"]

    def test_cancel_propagates_without_timeout_event(self) -> None:
        bridge = _FakeBridge(exc=asyncio.CancelledError("cancelled"))
        tool = AskQuestionTool(bridge=bridge)

        try:
            asyncio.run(
                tool.execute_with_context(
                    {"questions": [{"question": "Q", "header": "H", "options": []}]},
                    _exec_ctx(),
                )
            )
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("expected CancelledError")

        assert bridge.emitted == []


class TestAskQuestionToolVisibility:
    def test_prompt_contains_key_usage_guidance(self) -> None:
        tool = AskQuestionTool(bridge=_FakeBridge())
        prompt = tool.prompt()

        assert prompt
        assert '"Other"' in prompt
        assert "multiSelect: true" in prompt
        assert "(Recommended)" in prompt

    def test_hidden_without_bridge(self) -> None:
        tool = AskQuestionTool(bridge=None)
        assert tool.exposed_to_model is False

    def test_visible_with_bridge(self) -> None:
        tool = AskQuestionTool(bridge=_FakeBridge())
        assert tool.exposed_to_model is True
