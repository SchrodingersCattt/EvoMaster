"""Interaction-oriented GPT-style tools."""

from __future__ import annotations

from typing import Any, ClassVar

from ..base import BaseTool
from ..models import ToolResult


class TodoWriteTool(BaseTool):
    """Atomic todo list replacement, matching the reference contract."""

    name: ClassVar[str] = "TodoWrite"
    description: ClassVar[str] = (
        "Create and manage a structured task list for the current session. "
        "Replaces the full todo list atomically and keeps at most one item in_progress."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "The updated todo list.",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "minLength": 1},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                        },
                        "activeForm": {"type": "string", "minLength": 1},
                    },
                    "required": ["content", "status", "activeForm"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["todos"],
        "additionalProperties": False,
    }

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        todos = [dict(item) for item in arguments["todos"]]
        in_progress = [item for item in todos if item["status"] == "in_progress"]
        if len(in_progress) > 1:
            return ToolResult.error(
                "Error: TodoWrite allows at most one item with status='in_progress'."
            )

        if todos and all(item["status"] == "completed" for item in todos):
            self.context.todo_store.clear()
        else:
            self.context.todo_store.replace(todos)

        return ToolResult.ok(
            "Todo list updated successfully.",
            new_todos=todos,
            stored_todos=self.context.todo_store.current_items(),
        )


class SkillTool(BaseTool):
    """Minimal backend-only skill dispatcher."""

    name: ClassVar[str] = "Skill"
    description: ClassVar[str] = (
        "Execute a named skill within the current conversation. "
        "Skills expand domain-specific instructions or run a registered backend handler."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "skill": {"type": "string", "description": "Skill name, with or without leading slash."},
            "args": {"type": "string", "description": "Optional skill arguments."},
        },
        "required": ["skill"],
        "additionalProperties": False,
    }

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        skill_name = arguments["skill"].lstrip("/")
        args = str(arguments.get("args", "") or "")
        definition = self.context.skill_registry.get(skill_name)
        if definition is None:
            return ToolResult.error(f"Error: unknown skill '{skill_name}'.")

        if definition.runner is not None:
            result = definition.runner(args, self.context)
            if isinstance(result, ToolResult):
                return result
            return ToolResult.ok(str(result), skill=skill_name, args=args)

        content = definition.content
        if args:
            content += f"\n\nArgs: {args}"
        return ToolResult.ok(content, skill=skill_name, args=args, mode=definition.mode)


class AskUserQuestionTool(BaseTool):
    """Backend-only structured question contract."""

    name: ClassVar[str] = "AskUserQuestion"
    description: ClassVar[str] = (
        "Ask the user structured multiple-choice questions. "
        "Without answers the tool returns awaiting_input and echoes the question payload."
    )
    defer_loading: ClassVar[bool] = True
    search_hint: ClassVar[str] = "structured multiple choice user clarification"
    input_schema: ClassVar[dict[str, Any]] = {
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
                        "multiSelect": {"type": "boolean", "default": False},
                    },
                    "required": ["question", "header", "options"],
                    "additionalProperties": False,
                },
            },
            "answers": {"type": "object", "additionalProperties": {"type": "string"}},
            "annotations": {"type": "object"},
            "metadata": {"type": "object"},
        },
        "required": ["questions"],
        "additionalProperties": False,
    }

    def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        questions = arguments["questions"]
        self._validate_questions(questions)

        answers = arguments.get("answers")
        annotations = arguments.get("annotations") or {}
        metadata = arguments.get("metadata") or {}
        if not answers:
            return ToolResult.awaiting_input(
                "User input required to answer the structured questions.",
                questions=questions,
                annotations=annotations,
                metadata=metadata,
            )

        lines = []
        for question in questions:
            label = question["question"]
            answer = answers.get(label, "")
            lines.append(f"{label} -> {answer}")
        return ToolResult.ok(
            "\n".join(lines),
            questions=questions,
            answers=answers,
            annotations=annotations,
            metadata=metadata,
        )

    @staticmethod
    def _validate_questions(questions: list[dict[str, Any]]) -> None:
        seen_questions: set[str] = set()
        for question in questions:
            text = question["question"]
            if text in seen_questions:
                raise ValueError("question text must be unique")
            seen_questions.add(text)

            labels = [option["label"] for option in question["options"]]
            if len(labels) != len(set(labels)):
                raise ValueError(f"option labels must be unique for question '{text}'")


