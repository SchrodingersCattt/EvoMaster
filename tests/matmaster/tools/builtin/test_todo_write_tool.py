"""tests/matmaster/tools/builtin/test_todo_write_tool.py"""

import asyncio
import json

from matmaster.tools.builtin.todo_write_tool import TodoWriteTool
from matmaster.tools.tool_result import ToolResult


class TestTodoWriteMetadata:
    def test_name(self, tmp_path):
        tool = TodoWriteTool(workdir=tmp_path)
        assert tool.name == "TodoWrite"

    def test_description_matches_claude_code_style(self, tmp_path):
        tool = TodoWriteTool(workdir=tmp_path)
        assert tool.description == (
            "Update the todo list for the current session. To be used "
            "proactively and often to track progress and pending tasks. Make "
            "sure that at least one task is in_progress at all times. Always "
            "provide both content (imperative) and activeForm (present "
            "continuous) for each task."
        )

    def test_schema_matches_claude_code_shape(self, tmp_path):
        tool = TodoWriteTool(workdir=tmp_path)
        schema = tool.json_schema
        assert schema["additionalProperties"] is False
        assert schema["required"] == ["todos"]

        items = schema["properties"]["todos"]["items"]
        assert set(items["properties"]) == {"content", "status", "activeForm"}
        assert items["required"] == ["content", "status", "activeForm"]
        assert items["additionalProperties"] is False

    def test_prompt_contains_claude_code_guidance(self, tmp_path):
        tool = TodoWriteTool(workdir=tmp_path)
        prompt = tool.prompt()
        assert prompt is not None
        assert "## When to Use This Tool" in prompt
        assert "## When NOT to Use This Tool" in prompt
        assert "activeForm" in prompt
        assert "Exactly ONE task must be in_progress" in prompt


class TestTodoWriteValidation:
    def test_missing_active_form_is_denied(self, tmp_path):
        tool = TodoWriteTool(workdir=tmp_path)
        result = asyncio.run(
            tool.validate_input(
                {
                    "todos": [
                        {
                            "content": "Run tests",
                            "status": "in_progress",
                        }
                    ]
                },
                None,
            )
        )
        assert result is not None
        assert result.decision == "deny"
        assert "activeForm" in result.reason

    def test_zero_in_progress_is_denied(self, tmp_path):
        tool = TodoWriteTool(workdir=tmp_path)
        result = asyncio.run(
            tool.validate_input(
                {
                    "todos": [
                        {
                            "content": "Run tests",
                            "status": "pending",
                            "activeForm": "Running tests",
                        }
                    ]
                },
                None,
            )
        )
        assert result is not None
        assert result.decision == "deny"
        assert "exactly one" in result.reason.lower()

    def test_multiple_in_progress_is_denied(self, tmp_path):
        tool = TodoWriteTool(workdir=tmp_path)
        result = asyncio.run(
            tool.validate_input(
                {
                    "todos": [
                        {
                            "content": "Run tests",
                            "status": "in_progress",
                            "activeForm": "Running tests",
                        },
                        {
                            "content": "Build project",
                            "status": "in_progress",
                            "activeForm": "Building project",
                        },
                    ]
                },
                None,
            )
        )
        assert result is not None
        assert result.decision == "deny"
        assert "exactly one" in result.reason.lower()

    def test_all_completed_is_allowed(self, tmp_path):
        tool = TodoWriteTool(workdir=tmp_path)
        result = asyncio.run(
            tool.validate_input(
                {
                    "todos": [
                        {
                            "content": "Run tests",
                            "status": "completed",
                            "activeForm": "Running tests",
                        }
                    ]
                },
                None,
            )
        )
        assert result is None


class TestTodoWriteExecution:
    def test_create_todos(self, tmp_path):
        tool = TodoWriteTool(workdir=tmp_path)
        todos = [
            {
                "content": "Inspect tool schema",
                "status": "in_progress",
                "activeForm": "Inspecting tool schema",
            },
            {
                "content": "Update tests",
                "status": "pending",
                "activeForm": "Updating tests",
            },
        ]
        result = asyncio.run(tool.execute({"todos": todos}))
        assert isinstance(result, ToolResult)
        assert result.status == "success"
        assert "Todo list updated successfully" in result.content

        data = json.loads((tmp_path / ".todos.json").read_text())
        assert data["todos"] == todos

    def test_full_replacement(self, tmp_path):
        (tmp_path / ".todos.json").write_text(
            json.dumps(
                {
                    "todos": [
                        {
                            "content": "Old task",
                            "status": "in_progress",
                            "activeForm": "Working on old task",
                        }
                    ]
                }
            )
        )
        tool = TodoWriteTool(workdir=tmp_path)
        asyncio.run(
            tool.execute(
                {
                    "todos": [
                        {
                            "content": "New task",
                            "status": "in_progress",
                            "activeForm": "Working on new task",
                        }
                    ]
                }
            )
        )
        data = json.loads((tmp_path / ".todos.json").read_text())
        assert len(data["todos"]) == 1
        assert data["todos"][0]["content"] == "New task"

    def test_all_completed_clears(self, tmp_path):
        tool = TodoWriteTool(workdir=tmp_path)
        asyncio.run(
            tool.execute(
                {
                    "todos": [
                        {
                            "content": "Done",
                            "status": "completed",
                            "activeForm": "Finishing work",
                        }
                    ]
                }
            )
        )
        data = json.loads((tmp_path / ".todos.json").read_text())
        assert len(data["todos"]) == 0

    def test_invalid_status_error(self, tmp_path):
        tool = TodoWriteTool(workdir=tmp_path)
        result = asyncio.run(
            tool.execute(
                {
                    "todos": [
                        {
                            "content": "Bad",
                            "status": "invalid",
                            "activeForm": "Doing bad work",
                        }
                    ]
                }
            )
        )
        assert isinstance(result, ToolResult)
        assert result.status == "error"

    def test_execute_rejects_missing_active_form(self, tmp_path):
        tool = TodoWriteTool(workdir=tmp_path)
        result = asyncio.run(
            tool.execute(
                {
                    "todos": [
                        {
                            "content": "Bad",
                            "status": "in_progress",
                        }
                    ]
                }
            )
        )
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "activeForm" in result.content

    def test_execute_rejects_zero_in_progress(self, tmp_path):
        tool = TodoWriteTool(workdir=tmp_path)
        result = asyncio.run(
            tool.execute(
                {
                    "todos": [
                        {
                            "content": "Bad",
                            "status": "pending",
                            "activeForm": "Doing bad work",
                        }
                    ]
                }
            )
        )
        assert isinstance(result, ToolResult)
        assert result.status == "error"
        assert "exactly one" in result.content.lower()
