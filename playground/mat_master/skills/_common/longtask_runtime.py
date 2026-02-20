"""Shared runtime helpers for resumable long-task skills.

This module defines:
- Durable state file handling (`state.json`)
- Append-only event logging (`events.jsonl`)
- Standard status envelope emission for upstream parsers
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STATUS_RUNNING = "running"
STATUS_NEEDS_INPUT = "needs_input"
STATUS_RETRYABLE_ERROR = "retryable_error"
STATUS_FATAL_ERROR = "fatal_error"
STATUS_COMPLETED = "completed"

VALID_STATUSES = {
    STATUS_RUNNING,
    STATUS_NEEDS_INPUT,
    STATUS_RETRYABLE_ERROR,
    STATUS_FATAL_ERROR,
    STATUS_COMPLETED,
}

# Prefix used so upstream code can reliably parse structured status from script output.
RESULT_PREFIX = "LONGTASK_RESULT_JSON:"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    _ensure_parent(path)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def init_or_load_state(
    *,
    state_path: Path,
    task_type: str,
    stage: str,
    resume: bool,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load existing state or initialize a new one.

    Args:
        state_path: State file path.
        task_type: Logical task type, e.g. "manuscript" or "lit_data_table".
        stage: Current stage name.
        resume: If True, load existing state when available.
        extra: Extra fields to merge into state.
    """
    if resume and state_path.exists():
        state = read_json(state_path, default={})
        if not isinstance(state, dict):
            state = {}
    else:
        state = {
            "task_type": task_type,
            "created_at": now_iso(),
            "attempts": 0,
        }

    state.setdefault("task_type", task_type)
    state.setdefault("created_at", now_iso())
    state["updated_at"] = now_iso()
    state["stage"] = stage
    state["attempts"] = int(state.get("attempts", 0)) + 1

    if extra:
        state.update(extra)

    write_json(state_path, state)
    return state


def append_event(
    *,
    events_path: Path,
    status: str,
    stage: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    _ensure_parent(events_path)
    event: dict[str, Any] = {
        "ts": now_iso(),
        "status": status,
        "stage": stage,
        "message": message,
    }
    if payload:
        event["payload"] = payload
    with events_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False))
        f.write("\n")


def build_result(
    *,
    status: str,
    stage: str,
    message: str,
    result_path: Path | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    result: dict[str, Any] = {
        "status": status,
        "stage": stage,
        "message": message,
        "ts": now_iso(),
    }
    if payload:
        result["payload"] = payload
    if result_path is not None:
        write_json(result_path, result)
        result["result_file"] = str(result_path)
    return result


def emit_result(result: dict[str, Any]) -> None:
    """Print standard one-line status envelope for upstream parsing."""
    print(f"{RESULT_PREFIX} {json.dumps(result, ensure_ascii=False)}")


def parse_prefixed_result_line(text: str) -> dict[str, Any] | None:
    """Parse a `LONGTASK_RESULT_JSON:` line from script output text."""
    if not text:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith(RESULT_PREFIX):
            continue
        raw = line[len(RESULT_PREFIX) :].strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None
