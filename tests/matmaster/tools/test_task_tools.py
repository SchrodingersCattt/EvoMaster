"""Tests for TaskStore and 5 TaskTools (TaskCreate/Get/List/Update/Complete).

TDD RED phase: tests written before implementation.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from matmaster.tools.builtin.task._store import TaskStore
from matmaster.tools.builtin.task import (
    TaskCreateTool,
    TaskCompleteTool,
    TaskGetTool,
    TaskListTool,
    TaskUpdateTool,
)
from matmaster.tools.tool_registry import Tool


# ---------------------------------------------------------------------------
# TaskStore tests
# ---------------------------------------------------------------------------


class TestTaskStore:
    """TaskStore CRUD + persistence tests."""

    def test_create_returns_task_with_required_fields(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        task = store.create("Build the widget")
        assert "id" in task
        assert len(task["id"]) == 8
        assert task["description"] == "Build the widget"
        assert task["status"] == "open"
        assert "created_at" in task
        assert "updated_at" in task

    def test_get_existing_task(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        created = store.create("Test task")
        fetched = store.get(created["id"])
        assert fetched is not None
        assert fetched["id"] == created["id"]
        assert fetched["description"] == "Test task"

    def test_get_nonexistent_task_returns_none(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        assert store.get("nonexist") is None

    def test_list_all_returns_all_tasks(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        store.create("Task A")
        store.create("Task B")
        tasks = store.list_all()
        assert len(tasks) == 2
        descriptions = {t["description"] for t in tasks}
        assert descriptions == {"Task A", "Task B"}

    def test_list_all_empty(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        assert store.list_all() == []

    def test_update_description(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        created = store.create("Old desc")
        updated = store.update(created["id"], description="New desc")
        assert updated is not None
        assert updated["description"] == "New desc"
        assert updated["updated_at"] >= created["updated_at"]

    def test_update_status(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        created = store.create("Task")
        updated = store.update(created["id"], status="in_progress")
        assert updated is not None
        assert updated["status"] == "in_progress"

    def test_update_nonexistent_returns_none(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        assert store.update("nonexist", description="x") is None

    def test_complete_sets_status_completed(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        created = store.create("Finish this")
        completed = store.complete(created["id"])
        assert completed is not None
        assert completed["status"] == "completed"

    def test_complete_nonexistent_returns_none(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        assert store.complete("nonexist") is None

    def test_persistence_across_instances(self, tmp_path: Path) -> None:
        store1 = TaskStore(tmp_path)
        task = store1.create("Persistent task")

        store2 = TaskStore(tmp_path)
        fetched = store2.get(task["id"])
        assert fetched is not None
        assert fetched["description"] == "Persistent task"

    def test_data_stored_in_tasks_json(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        store.create("Check file")
        tasks_file = tmp_path / ".tasks.json"
        assert tasks_file.exists()
        data = json.loads(tasks_file.read_text())
        assert "tasks" in data

    def test_lock_exists(self, tmp_path: Path) -> None:
        """TaskStore has a threading.Lock for concurrency protection."""
        assert hasattr(TaskStore, "_lock")
        assert isinstance(TaskStore._lock, type(threading.Lock()))


# ---------------------------------------------------------------------------
# TaskCreateTool tests
# ---------------------------------------------------------------------------


class TestTaskCreateTool:
    def test_name(self) -> None:
        tool = TaskCreateTool(workdir=Path("/tmp"))
        assert tool.name == "task_create"

    def test_satisfies_tool_protocol(self) -> None:
        tool = TaskCreateTool(workdir=Path("/tmp"))
        assert isinstance(tool, Tool)

    def test_task_create(self, tmp_path: Path) -> None:
        tool = TaskCreateTool(workdir=tmp_path)
        result = tool.execute({"description": "New task"})
        parsed = json.loads(result)
        assert parsed["description"] == "New task"
        assert parsed["status"] == "open"
        assert "id" in parsed

    def test_workdir_none(self) -> None:
        tool = TaskCreateTool(workdir=None)
        result = tool.execute({"description": "Should fail"})
        assert "Error" in result
        assert "workdir" in result


# ---------------------------------------------------------------------------
# TaskGetTool tests
# ---------------------------------------------------------------------------


class TestTaskGetTool:
    def test_name(self) -> None:
        tool = TaskGetTool(workdir=Path("/tmp"))
        assert tool.name == "task_get"

    def test_satisfies_tool_protocol(self) -> None:
        tool = TaskGetTool(workdir=Path("/tmp"))
        assert isinstance(tool, Tool)

    def test_task_get_existing(self, tmp_path: Path) -> None:
        # Create a task first
        create_tool = TaskCreateTool(workdir=tmp_path)
        create_result = json.loads(create_tool.execute({"description": "Find me"}))
        task_id = create_result["id"]

        tool = TaskGetTool(workdir=tmp_path)
        result = tool.execute({"task_id": task_id})
        parsed = json.loads(result)
        assert parsed["description"] == "Find me"

    def test_task_get_nonexistent(self, tmp_path: Path) -> None:
        tool = TaskGetTool(workdir=tmp_path)
        result = tool.execute({"task_id": "nope1234"})
        assert "Task not found" in result
        assert "nope1234" in result

    def test_workdir_none(self) -> None:
        tool = TaskGetTool(workdir=None)
        result = tool.execute({"task_id": "abc"})
        assert "Error" in result


# ---------------------------------------------------------------------------
# TaskListTool tests
# ---------------------------------------------------------------------------


class TestTaskListTool:
    def test_name(self) -> None:
        tool = TaskListTool(workdir=Path("/tmp"))
        assert tool.name == "task_list"

    def test_satisfies_tool_protocol(self) -> None:
        tool = TaskListTool(workdir=Path("/tmp"))
        assert isinstance(tool, Tool)

    def test_task_list_with_tasks(self, tmp_path: Path) -> None:
        create_tool = TaskCreateTool(workdir=tmp_path)
        create_tool.execute({"description": "Task 1"})
        create_tool.execute({"description": "Task 2"})

        tool = TaskListTool(workdir=tmp_path)
        result = tool.execute({})
        parsed = json.loads(result)
        assert len(parsed) == 2

    def test_task_list_empty(self, tmp_path: Path) -> None:
        tool = TaskListTool(workdir=tmp_path)
        result = tool.execute({})
        assert result == "[]"

    def test_workdir_none(self) -> None:
        tool = TaskListTool(workdir=None)
        result = tool.execute({})
        assert "Error" in result


# ---------------------------------------------------------------------------
# TaskUpdateTool tests
# ---------------------------------------------------------------------------


class TestTaskUpdateTool:
    def test_name(self) -> None:
        tool = TaskUpdateTool(workdir=Path("/tmp"))
        assert tool.name == "task_update"

    def test_satisfies_tool_protocol(self) -> None:
        tool = TaskUpdateTool(workdir=Path("/tmp"))
        assert isinstance(tool, Tool)

    def test_task_update(self, tmp_path: Path) -> None:
        create_tool = TaskCreateTool(workdir=tmp_path)
        created = json.loads(create_tool.execute({"description": "Original"}))

        tool = TaskUpdateTool(workdir=tmp_path)
        result = tool.execute({
            "task_id": created["id"],
            "description": "Updated",
            "status": "in_progress",
        })
        parsed = json.loads(result)
        assert parsed["description"] == "Updated"
        assert parsed["status"] == "in_progress"

    def test_task_update_nonexistent(self, tmp_path: Path) -> None:
        tool = TaskUpdateTool(workdir=tmp_path)
        result = tool.execute({"task_id": "nope", "description": "x"})
        assert "not found" in result.lower() or "Task not found" in result

    def test_workdir_none(self) -> None:
        tool = TaskUpdateTool(workdir=None)
        result = tool.execute({"task_id": "abc"})
        assert "Error" in result


# ---------------------------------------------------------------------------
# TaskCompleteTool tests
# ---------------------------------------------------------------------------


class TestTaskCompleteTool:
    def test_name(self) -> None:
        tool = TaskCompleteTool(workdir=Path("/tmp"))
        assert tool.name == "task_complete"

    def test_satisfies_tool_protocol(self) -> None:
        tool = TaskCompleteTool(workdir=Path("/tmp"))
        assert isinstance(tool, Tool)

    def test_task_complete(self, tmp_path: Path) -> None:
        create_tool = TaskCreateTool(workdir=tmp_path)
        created = json.loads(create_tool.execute({"description": "Complete me"}))

        tool = TaskCompleteTool(workdir=tmp_path)
        result = tool.execute({"task_id": created["id"]})
        parsed = json.loads(result)
        assert parsed["status"] == "completed"

    def test_task_complete_nonexistent(self, tmp_path: Path) -> None:
        tool = TaskCompleteTool(workdir=tmp_path)
        result = tool.execute({"task_id": "nope"})
        assert "not found" in result.lower() or "Task not found" in result

    def test_workdir_none(self) -> None:
        tool = TaskCompleteTool(workdir=None)
        result = tool.execute({"task_id": "abc"})
        assert "Error" in result
