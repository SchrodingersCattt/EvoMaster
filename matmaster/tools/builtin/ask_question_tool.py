"""matmaster/tools/builtin/ask_question_tool.py

AskQuestion builtin tool — 结构化多选问答，语义对齐 Claude Code AskQuestion。
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_result import ToolResult
from matmaster.types import InteractionTimeoutEvent
from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ResourceClaim, ToolExecutionContext

ASK_QUESTION_USAGE_PROMPT = '''Use this tool when you need to ask the user questions during execution. This allows you to:
1. Gather user preferences or requirements
2. Clarify ambiguous instructions
3. Get decisions on implementation choices as you work
4. Offer choices to the user about what direction to take.

Usage notes:
- Users will always be able to select "Other" to provide custom text input
- Use multiSelect: true to allow multiple answers to be selected for a question
- If you recommend a specific option, make that the first option in the list and add "(Recommended)" at the end of the label

Planner mode note: In planner mode, use this tool to clarify requirements or choose between approaches BEFORE finalizing your plan. Do NOT use this tool to ask "Is my plan ready?" or "Should I proceed?"
'''


class AskQuestionTool(BuiltinTool):
    name: ClassVar[str] = "AskQuestion"
    description: ClassVar[str] = (
        "Asks the user structured multiple-choice questions to clarify ambiguity, "
        "gather preferences, make decisions, or collect missing requirements during execution."
    )
    usage_prompt: ClassVar[str] = ASK_QUESTION_USAGE_PROMPT
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "header": {"type": "string"},
                        "options": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 4,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "description": {"type": "string"},
                                    "preview": {"type": "string"},
                                },
                                "required": ["label", "description"],
                                "additionalProperties": False,
                            },
                        },
                        "multi_select": {"type": "boolean"},
                        "multiSelect": {"type": "boolean"},
                        "allow_freeform": {"type": "boolean"},
                    },
                    "required": ["question", "header", "options"],
                    "additionalProperties": False,
                },
            },
            "metadata": {
                "type": "object",
                "properties": {"source": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        "required": ["questions"],
        "additionalProperties": False,
    }
    effect_level: ClassVar[str] = "none"
    resource_claims: ClassVar[tuple[ResourceClaim, ...]] = (
        ResourceClaim(resource="interaction", mode="exclusive"),
    )

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Any | None = None,
        bridge: Any | None = None,
    ) -> None:
        super().__init__(session=session, workdir=workdir)
        self._bridge = bridge

        # 同 AgentTool 模式：bridge 缺失时实例级遮蔽 ClassVar，
        # tool_compiler 读 tool.exposed_to_model 时优先命中实例属性
        if bridge is None:
            self.exposed_to_model = False

    def prompt(self, ctx: Any | None = None) -> str:
        return self.usage_prompt

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        """AskQuestion 走 execute_with_context；不支持 sync 调用路径。"""
        raise NotImplementedError(
            "AskQuestionTool requires execute_with_context; direct _execute is not supported"
        )

    async def validate_input(
        self,
        arguments: dict[str, Any],
        runner_state: ToolRunnerState | None = None,
    ) -> ToolDecision | None:
        questions = arguments.get("questions") or []
        texts = [q.get("question", "") for q in questions]
        if len(texts) != len(set(texts)):
            return ToolDecision(
                decision="deny",
                reason="Duplicate question texts are not allowed",
            )
        return None

    async def execute_with_context(
        self,
        arguments: dict[str, Any],
        exec_ctx: ToolExecutionContext | None,
    ) -> str | ToolResult:
        if self._bridge is None:
            return ToolResult(
                status="error",
                content="AskQuestion is not available in this session (no interaction bridge)",
            )

        normalized_questions = self._normalize_questions(arguments["questions"])
        request_id = f"aq_{uuid.uuid4().hex[:12]}"
        request_payload = {
            "questions": normalized_questions,
            "metadata": arguments.get("metadata") or {},
            "origin": "tool:AskQuestion",
            "preview_format": "markdown",
        }

        try:
            reply_payload = await self._bridge.request(
                kind="ask_question",
                request_id=request_id,
                payload=request_payload,
            )
        except TimeoutError:
            await self._bridge.emit(
                InteractionTimeoutEvent(
                    source="System", kind="ask_question", request_id=request_id
                )
            )
            raise

        content = self._render_answer_summary(
            reply_payload.get("answers") or {},
            reply_payload.get("annotations") or {},
        )
        return ToolResult(
            status="success",
            content=content,
            payload={
                "request_id": request_id,
                "questions": normalized_questions,
                "answers": reply_payload.get("answers") or {},
                "annotations": reply_payload.get("annotations") or {},
            },
        )

    @staticmethod
    def _normalize_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """确保每个 question 有完整的字段结构。"""
        result = []
        for q in questions:
            result.append(
                {
                    "question": q["question"],
                    "header": q.get("header", q["question"]),
                    "options": q.get("options", []),
                    # Prompt uses Claude-style camelCase; SSE payload keeps snake_case.
                    "multi_select": q.get("multi_select", q.get("multiSelect", False)),
                    "allow_freeform": q.get("allow_freeform", False),
                }
            )
        return result

    @staticmethod
    def _render_answer_summary(
        answers: dict[str, str],
        annotations: dict[str, dict[str, str]],
    ) -> str:
        """渲染模型可读的回答摘要。"""
        parts = []
        for question, answer in answers.items():
            line = f'"{question}"="{answer}"'
            ann = annotations.get(question)
            if ann:
                for key, value in ann.items():
                    line += f"\n  user {key}: {value}"
            parts.append(line)
        return "\n".join(parts)
