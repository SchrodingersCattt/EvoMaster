"""tests/matmaster/tools/builtin/test_todo_write_tool.py"""

import asyncio
import json

from matmaster.tools.builtin.todo_write_tool import TodoWriteTool
from matmaster.tools.tool_result import ToolResult


class TestTodoWriteMetadata:
    def test_name(self, tmp_path):
        tool = TodoWriteTool(workdir=tmp_path)
        assert tool.name == "TodoWrite"


class TestTodoWriteExecution:
    def test_create_todos(self, tmp_path):
        tool = TodoWriteTool(workdir=tmp_path)
        todos = [
            {"id": "1", "content": "First task", "status": "pending"},
            {"id": "2", "content": "Second task", "status": "in_progress"},
        ]
        result = asyncio.run(tool.execute({"todos": todos}))
        assert isinstance(result, ToolResult)
        assert result.status == "success"
        data = json.loads((tmp_path / ".todos.json").read_text())
        assert len(data["todos"]) == 2

    def test_full_replacement(self, tmp_path):
        (tmp_path / ".todos.json").write_text(
            json.dumps(
                {
                    "todos": [
                        {"id": "1", "content": "Old", "status": "pending"},
                    ]
                }
            )
        )
        tool = TodoWriteTool(workdir=tmp_path)
        asyncio.run(
            tool.execute(
                {
                    "todos": [
                        {"id": "2", "content": "New", "status": "pending"},
                    ]
                }
            )
        )
        data = json.loads((tmp_path / ".todos.json").read_text())
        assert len(data["todos"]) == 1
        assert data["todos"][0]["id"] == "2"

    def test_all_completed_clears(self, tmp_path):
        tool = TodoWriteTool(workdir=tmp_path)
        asyncio.run(
            tool.execute(
                {
                    "todos": [
                        {"id": "1", "content": "Done", "status": "completed"},
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
                        {"id": "1", "content": "Bad", "status": "invalid"},
                    ]
                }
            )
        )
        assert isinstance(result, ToolResult)
        assert result.status == "error"
