"""AskUserQuestion tool -- CC-style structured multi-choice questioning.

Renders structured questions with options for the user to select.
In CC, this produces UI components (cards, preview panels).
In matmaster, the questions/answers flow through the message bus as events.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from .base import BuiltinTool, ToolResult


class AskUserQuestionTool(BuiltinTool):
    """Structured multi-choice questioning with optional preview."""

    name: ClassVar[str] = "AskUserQuestion"
    description: ClassVar[str] = (
        "Ask the user structured questions during execution.\n\n"
        "Use this to:\n"
        "1. Gather user preferences or requirements\n"
        "2. Clarify ambiguous instructions\n"
        "3. Get decisions on implementation choices\n"
        "4. Offer choices about what direction to take\n\n"
        "Usage notes:\n"
        '- Users can always select "Other" for custom input\n'
        "- Use multiSelect: true to allow multiple answers\n"
        '- Add "(Recommended)" to the first option label if you recommend it\n'
        "- Use preview field for comparing code snippets, mockups, or configs"
    )
    json_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "description": "Questions to ask (1-4)",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The complete question. Should end with '?'.",
                        },
                        "header": {
                            "type": "string",
                            "description": "Short label displayed as chip/tag (max 12 chars).",
                        },
                        "options": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 4,
                            "description": (
                                "Available choices (2-4). "
                                '"Other" is added automatically.'
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {
                                        "type": "string",
                                        "description": "Display text (1-5 words)",
                                    },
                                    "description": {
                                        "type": "string",
                                        "description": "Explanation of this option",
                                    },
                                    "preview": {
                                        "type": "string",
                                        "description": (
                                            "Optional preview content (markdown). "
                                            "For mockups, code snippets, or comparisons."
                                        ),
                                    },
                                },
                                "required": ["label", "description"],
                                "additionalProperties": False,
                            },
                        },
                        "multiSelect": {
                            "type": "boolean",
                            "default": False,
                            "description": "Allow multiple answers",
                        },
                    },
                    "required": ["question", "header", "options"],
                    "additionalProperties": False,
                },
            },
            "answers": {
                "type": "object",
                "description": (
                    "User answers (filled by the UI/frontend, not by the model). "
                    "Keyed by question text."
                ),
                "additionalProperties": {"type": "string"},
            },
            "annotations": {
                "type": "object",
                "description": "Optional per-question annotations from the user.",
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "preview": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                },
            },
            "metadata": {
                "type": "object",
                "description": "Optional metadata for tracking.",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Identifier for the question source",
                    },
                },
            },
        },
        "required": ["questions"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        *,
        session: Any | None = None,
        workdir: Any | None = None,
        answer_callback: Any | None = None,
    ) -> None:
        """
        Args:
            answer_callback: Callable that receives questions and returns
                answers. If None, returns questions for the caller to handle.
        """
        super().__init__(session=session, workdir=workdir)
        self._answer_callback = answer_callback

    def _execute(self, arguments: dict[str, Any]) -> str | ToolResult:
        questions = arguments.get("questions", [])
        answers = arguments.get("answers")

        if not questions:
            return "Error: at least one question is required"

        # Validate questions
        seen_questions: set[str] = set()
        for q in questions:
            text = q.get("question", "")
            if text in seen_questions:
                return f"Error: duplicate question: {text}"
            seen_questions.add(text)

            options = q.get("options", [])
            seen_labels: set[str] = set()
            for opt in options:
                label = opt.get("label", "")
                if label in seen_labels:
                    return f"Error: duplicate label '{label}' in question '{text}'"
                seen_labels.add(label)

        # If answers already provided (UI round-trip), return them
        if answers:
            return ToolResult.ok(
                self._format_answers(questions, answers),
                questions=[q["question"] for q in questions],
                answers=answers,
            )

        # Format questions for text-based interaction
        return ToolResult(
            status="pending",
            content=self._format_questions(questions),
            payload={
                "type": "ask_user_question",
                "questions": questions,
            },
        )

    @staticmethod
    def _format_questions(questions: list[dict[str, Any]]) -> str:
        """Format questions for text display."""
        parts: list[str] = []
        for i, q in enumerate(questions, 1):
            header = q.get("header", "")
            text = q.get("question", "")
            multi = q.get("multiSelect", False)

            parts.append(f"[{header}] {text}")
            if multi:
                parts.append("  (Select multiple)")

            for j, opt in enumerate(q.get("options", []), 1):
                label = opt.get("label", "")
                desc = opt.get("description", "")
                parts.append(f"  {j}. {label} -- {desc}")

            parts.append("  (Other: type your answer)")
            parts.append("")

        return "\n".join(parts)

    @staticmethod
    def _format_answers(
        questions: list[dict[str, Any]], answers: dict[str, str]
    ) -> str:
        """Format answered questions for display."""
        parts: list[str] = []
        for q in questions:
            text = q.get("question", "")
            answer = answers.get(text, "(no answer)")
            header = q.get("header", "")
            parts.append(f"[{header}] {text}\n  Answer: {answer}")
        return "\n".join(parts)
