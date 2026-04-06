"""matmaster/tools/builtin/ask_question_tool.py

AskQuestion builtin tool — 结构化多选问答，语义对齐 Claude Code AskQuestion。
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from matmaster.tools.builtin.base import BuiltinTool
from matmaster.tools.tool_result import ToolResult
from matmaster.types.tool_decision import ToolDecision
from matmaster.types.tool_runner_state import ToolRunnerState
from matmaster.types.tool_spec import ToolExecutionContext


class AskQuestionTool(BuiltinTool):
    name: ClassVar[str] = "AskQuestion"
    description: ClassVar[str] = (
        "Asks the user structured multiple-choice questions to clarify ambiguity, "
        "gather preferences, make decisions, or collect missing requirements during execution."
    )
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

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Any | None = None,
        bridge: Any | None = None,
        session_id: str = "",
        task_id: str = "",
        invocation_id: str | None = None,
    ) -> None:
        super().__init__(session=session, workdir=workdir)
        self._bridge = bridge
        self._session_id = session_id
        self._task_id = task_id
        self._invocation_id = invocation_id

        # 同 AgentTool 模式：bridge 缺失时实例级遮蔽 ClassVar，
        # tool_compiler 读 tool.exposed_to_model 时优先命中实例属性
        if bridge is None:
            self.exposed_to_model = False

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        """AskQuestion 必须走 execute_with_context（需要 cancel_token），不支持无上下文调用。"""
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

        response = await self._bridge.ask(
            session_id=self._session_id,
            task_id=self._task_id,
            invocation_id=self._invocation_id,
            request_id=request_id,
            questions=normalized_questions,
            metadata=arguments.get("metadata"),
            cancel_token=exec_ctx.cancel_token if exec_ctx else None,
        )

        content = self._render_answer_summary(
            response["answers"],
            response.get("annotations") or {},
        )
        return ToolResult(
            status="success",
            content=content,
            payload={
                "request_id": response["request_id"],
                "questions": normalized_questions,
                "answers": response["answers"],
                "annotations": response.get("annotations") or {},
            },
        )

    @staticmethod
    def _normalize_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """确保每个 question 有完整的字段结构。"""
        result = []
        for q in questions:
            result.append({
                "question": q["question"],
                "header": q.get("header", q["question"]),
                "options": q.get("options", []),
                "multi_select": q.get("multi_select", False),
            })
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
