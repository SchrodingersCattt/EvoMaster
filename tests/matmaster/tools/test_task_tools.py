"""Tests for TaskStore and 5 TaskTools (TaskCreate/Get/List/Update/Complete).

All operations are subtask-level. Parent task status is always auto-derived.
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
        task = store.create("Build the widget", ["Step A", "Step B"])
        assert "id" in task
        assert len(task["id"]) == 8
        assert task["description"] == "Build the widget"
        assert task["status"] == "open"
        assert len(task["subtasks"]) == 2
        assert task["subtasks"][0] == {"description": "Step A", "status": "open"}
        assert "created_at" in task
        assert "updated_at" in task

    def test_get_existing_task(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        created = store.create("Test task", ["Sub 1"])
        fetched = store.get(created["id"])
        assert fetched is not None
        assert fetched["id"] == created["id"]
        assert fetched["description"] == "Test task"

    def test_get_nonexistent_task_returns_none(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        assert store.get("nonexist") is None

    def test_list_all_returns_all_tasks(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        store.create("Task A", ["Sub 1"])
        store.create("Task B", ["Sub 1"])
        tasks = store.list_all()
        assert len(tasks) == 2
        descriptions = {t["description"] for t in tasks}
        assert descriptions == {"Task A", "Task B"}

    def test_list_all_empty(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        assert store.list_all() == []

    def test_update_subtask_status(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        task = store.create("Parent", ["A", "B", "C"])
        updated = store.update_subtask(task["id"], 1, "completed")
        assert updated is not None
        assert updated["subtasks"][1]["status"] == "completed"
        assert updated["subtasks"][0]["status"] == "open"
        assert updated["status"] == "in_progress"

    def test_update_subtask_all_completed_derives_parent(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        task = store.create("Parent", ["A", "B"])
        store.update_subtask(task["id"], 0, "completed")
        updated = store.update_subtask(task["id"], 1, "completed")
        assert updated is not None
        assert updated["status"] == "completed"

    def test_update_subtask_invalid_index_returns_none(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        task = store.create("Parent", ["A"])
        assert store.update_subtask(task["id"], 5, "completed") is None
        assert store.update_subtask(task["id"], -1, "completed") is None

    def test_update_subtask_nonexistent_task_returns_none(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        assert store.update_subtask("nope", 0, "completed") is None

    def test_complete_subtask(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        task = store.create("Parent", ["A", "B"])
        result = store.complete_subtask(task["id"], 0)
        assert result is not None
        assert result["subtasks"][0]["status"] == "completed"
        assert result["subtasks"][1]["status"] == "open"
        assert result["status"] == "in_progress"

    def test_complete_all_subtasks_completes_parent(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        task = store.create("Parent", ["A", "B"])
        store.complete_subtask(task["id"], 0)
        result = store.complete_subtask(task["id"], 1)
        assert result is not None
        assert result["status"] == "completed"

    def test_derive_status_all_open(self, tmp_path: Path) -> None:
        subtasks = [{"status": "open"}, {"status": "open"}]
        assert TaskStore._derive_status(subtasks) == "open"

    def test_derive_status_mixed(self, tmp_path: Path) -> None:
        subtasks = [{"status": "completed"}, {"status": "open"}]
        assert TaskStore._derive_status(subtasks) == "in_progress"

    def test_derive_status_all_completed(self, tmp_path: Path) -> None:
        subtasks = [{"status": "completed"}, {"status": "completed"}]
        assert TaskStore._derive_status(subtasks) == "completed"

    def test_persistence_across_instances(self, tmp_path: Path) -> None:
        store1 = TaskStore(tmp_path)
        task = store1.create("Persistent task", ["Sub 1"])

        store2 = TaskStore(tmp_path)
        fetched = store2.get(task["id"])
        assert fetched is not None
        assert fetched["description"] == "Persistent task"

    def test_data_stored_in_tasks_json(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path)
        store.create("Check file", ["Sub 1"])
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

    def test_task_create_with_subtasks(self, tmp_path: Path) -> None:
        tool = TaskCreateTool(workdir=tmp_path)
        result = tool.execute({
            "description": "Multi-step task",
            "tasks": ["Step 1", "Step 2", "Step 3"],
        })
        parsed = json.loads(result)
        assert parsed["description"] == "Multi-step task"
        assert len(parsed["subtasks"]) == 3
        assert parsed["subtasks"][0]["description"] == "Step 1"
        assert parsed["subtasks"][0]["status"] == "open"
        assert parsed["status"] == "open"

    def test_workdir_none(self) -> None:
        tool = TaskCreateTool(workdir=None)
        result = tool.execute({"description": "Fail", "tasks": ["A"]})
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
        create_tool = TaskCreateTool(workdir=tmp_path)
        create_result = json.loads(create_tool.execute({
            "description": "Find me",
            "tasks": ["Sub 1"],
        }))
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
        create_tool.execute({"description": "Task 1", "tasks": ["A"]})
        create_tool.execute({"description": "Task 2", "tasks": ["B"]})

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

    def test_task_update_subtask(self, tmp_path: Path) -> None:
        create_tool = TaskCreateTool(workdir=tmp_path)
        created = json.loads(create_tool.execute({
            "description": "Parent",
            "tasks": ["A", "B", "C"],
        }))

        tool = TaskUpdateTool(workdir=tmp_path)
        result = tool.execute({
            "task_id": created["id"],
            "subtask_index": 1,
            "status": "completed",
        })
        parsed = json.loads(result)
        assert parsed["subtasks"][1]["status"] == "completed"
        assert parsed["subtasks"][0]["status"] == "open"
        assert parsed["status"] == "in_progress"

    def test_task_update_subtask_invalid_index(self, tmp_path: Path) -> None:
        create_tool = TaskCreateTool(workdir=tmp_path)
        created = json.loads(create_tool.execute({
            "description": "Parent",
            "tasks": ["A"],
        }))

        tool = TaskUpdateTool(workdir=tmp_path)
        result = tool.execute({
            "task_id": created["id"],
            "subtask_index": 5,
            "status": "completed",
        })
        assert "not found" in result.lower()

    def test_task_update_nonexistent(self, tmp_path: Path) -> None:
        tool = TaskUpdateTool(workdir=tmp_path)
        result = tool.execute({
            "task_id": "nope",
            "subtask_index": 0,
            "status": "in_progress",
        })
        assert "not found" in result.lower()

    def test_workdir_none(self) -> None:
        tool = TaskUpdateTool(workdir=None)
        result = tool.execute({
            "task_id": "abc",
            "subtask_index": 0,
            "status": "in_progress",
        })
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

    def test_task_complete_single_subtask(self, tmp_path: Path) -> None:
        create_tool = TaskCreateTool(workdir=tmp_path)
        created = json.loads(create_tool.execute({
            "description": "Parent",
            "tasks": ["A", "B"],
        }))

        tool = TaskCompleteTool(workdir=tmp_path)
        result = tool.execute({"task_id": created["id"], "subtask_index": 0})
        parsed = json.loads(result)
        assert parsed["subtasks"][0]["status"] == "completed"
        assert parsed["subtasks"][1]["status"] == "open"
        assert parsed["status"] == "in_progress"

    def test_task_complete_all_subtasks_completes_parent(self, tmp_path: Path) -> None:
        create_tool = TaskCreateTool(workdir=tmp_path)
        created = json.loads(create_tool.execute({
            "description": "Parent",
            "tasks": ["A", "B"],
        }))

        tool = TaskCompleteTool(workdir=tmp_path)
        tool.execute({"task_id": created["id"], "subtask_index": 0})
        result = tool.execute({"task_id": created["id"], "subtask_index": 1})
        parsed = json.loads(result)
        assert parsed["status"] == "completed"
        assert all(s["status"] == "completed" for s in parsed["subtasks"])

    def test_task_complete_nonexistent(self, tmp_path: Path) -> None:
        tool = TaskCompleteTool(workdir=tmp_path)
        result = tool.execute({"task_id": "nope", "subtask_index": 0})
        assert "not found" in result.lower()

    def test_workdir_none(self) -> None:
        tool = TaskCompleteTool(workdir=None)
        result = tool.execute({"task_id": "abc", "subtask_index": 0})
        assert "Error" in result
