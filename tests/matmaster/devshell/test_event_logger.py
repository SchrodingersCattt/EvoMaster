"""Tests for EventLogger JSONL persistence."""
from __future__ import annotations

import json
from pathlib import Path

from matmaster.types.events import (
    AssistantStateEvent,
    ThoughtEvent,
    ToolCallEvent,
    ToolResultEvent,
    RunResultEvent,
)


class TestEventLogger:
    def test_writes_jsonl(self, tmp_path: Path) -> None:
        from matmaster.devshell.event_logger import EventLogger

        log_file = tmp_path / "events.jsonl"
        logger = EventLogger(log_file, run_id="run-001")

        logger.log_event(ToolCallEvent(
            source="test", call_id="tc-1", tool_name="bash",
            arguments={"command": "ls"},
        ))
        logger.log_event(ToolResultEvent(
            source="test", call_id="tc-1", tool_name="bash",
            result="file1.py",
        ))
        logger.close()

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2

        rec1 = json.loads(lines[0])
        assert rec1["type"] == "tool_call"
        assert rec1["tool"] == "bash"
        assert rec1["run_id"] == "run-001"

        rec2 = json.loads(lines[1])
        assert rec2["type"] == "tool_result"
        assert rec2["tool"] == "bash"

    def test_thought_streaming_merged(self, tmp_path: Path) -> None:
        from matmaster.devshell.event_logger import EventLogger

        log_file = tmp_path / "events.jsonl"
        logger = EventLogger(log_file, run_id="run-001")

        logger.log_event(ThoughtEvent(source="test", content="", stream_state="start", stream_id="s1"))
        logger.log_event(ThoughtEvent(source="test", content="Hel", stream_state="streaming", stream_id="s1"))
        logger.log_event(ThoughtEvent(source="test", content="lo", stream_state="streaming", stream_id="s1"))
        logger.log_event(ThoughtEvent(source="test", content="", stream_state="end", stream_id="s1"))
        logger.close()

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1  # Merged into single record
        rec = json.loads(lines[0])
        assert rec["type"] == "thought"
        assert rec["content"] == "Hello"
        assert "duration_ms" in rec
        assert isinstance(rec["duration_ms"], (int, float))
        assert rec["duration_ms"] >= 0
        assert "ts_start" in rec

    def test_skips_assistant_state(self, tmp_path: Path) -> None:
        from matmaster.devshell.event_logger import EventLogger

        log_file = tmp_path / "events.jsonl"
        logger = EventLogger(log_file, run_id="run-001")

        logger.log_event(AssistantStateEvent(source="test", state={}))
        logger.close()

        assert not log_file.exists() or log_file.read_text().strip() == ""

    def test_run_result_event(self, tmp_path: Path) -> None:
        from matmaster.devshell.event_logger import EventLogger

        log_file = tmp_path / "events.jsonl"
        logger = EventLogger(log_file, run_id="run-002")

        logger.log_event(RunResultEvent(
            source="test", status="completed", reason="done",
        ))
        logger.close()

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["type"] == "run_result"
        assert rec["status"] == "completed"
        assert rec["run_id"] == "run-002"

    def test_non_streaming_thought(self, tmp_path: Path) -> None:
        from matmaster.devshell.event_logger import EventLogger

        log_file = tmp_path / "events.jsonl"
        logger = EventLogger(log_file, run_id="run-001")

        logger.log_event(ThoughtEvent(source="test", content="direct thought"))
        logger.close()

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["type"] == "thought"
        assert rec["content"] == "direct thought"

    def test_set_run_id(self, tmp_path: Path) -> None:
        from matmaster.devshell.event_logger import EventLogger

        log_file = tmp_path / "events.jsonl"
        logger = EventLogger(log_file, run_id="run-001")

        logger.log_event(ToolCallEvent(
            source="test", call_id="tc-1", tool_name="bash",
            arguments={"command": "ls"},
        ))
        logger.set_run_id("run-002")
        logger.log_event(ToolCallEvent(
            source="test", call_id="tc-2", tool_name="bash",
            arguments={"command": "pwd"},
        ))
        logger.close()

        lines = log_file.read_text().strip().split("\n")
        rec1 = json.loads(lines[0])
        rec2 = json.loads(lines[1])
        assert rec1["run_id"] == "run-001"
        assert rec2["run_id"] == "run-002"
