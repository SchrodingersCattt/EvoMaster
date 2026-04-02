"""Mat Master runner and answer extraction for evaluation.

Uses the matmaster Playground/Exp/Kernel stack directly (no evomaster
dependency). Mirrors the DevRunner pattern: build_runtime → kernel.run
with asyncio.run() bridging sync/async.
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from matmaster.config.loader import load_exp_config, load_llm_config
from matmaster.core.exp import Exp
from matmaster.providers.llm_factory import build_provider
from matmaster.sessions.local import LocalSession
from matmaster.types.context import PlaygroundContext
from matmaster.types.messages import AssistantMessage

from .schemas import ModeLiteral

logger = logging.getLogger(__name__)


def run_mat_task(
    *,
    prompt: str,
    mode: ModeLiteral,
    task_id: str,
    run_dir: Path,
    mat_config_path: Path,
) -> dict[str, Any]:
    """Run one Mat Master evaluation task and extract answer text.

    Args:
        prompt: The user task prompt.
        mode: 'direct' or 'planner'.
        task_id: Unique identifier for this evaluation run.
        run_dir: Root directory for storing run artifacts.
        mat_config_path: Path to LLM config YAML (config/llm_config.yaml).
    """
    # 1. Load LLM config and build provider
    llm_config = load_llm_config(mat_config_path)
    llm_provider = build_provider(llm_config)

    # 2. Load exp config (mode maps to exp name)
    exp_config = load_exp_config(mode)

    # 3. Create workspace and session
    workspace = run_dir / "workspaces" / task_id
    workspace.mkdir(parents=True, exist_ok=True)
    cache_area = workspace / ".cache"
    cache_area.mkdir(parents=True, exist_ok=True)

    session = LocalSession(workspace_path=workspace)
    session.open()

    # 4. Build PlaygroundContext
    pg_ctx = PlaygroundContext(
        workdir=workspace,
        session_type="local",
        cache_area=cache_area,
        session=session,
        llm_provider=llm_provider,
        llm_config=llm_config,
        run_meta={"source": "evaluation", "task_id": task_id, "mode": mode},
    )

    # 5. Build runtime and run kernel
    exp = Exp(exp_config)
    t0 = time.monotonic()

    async def _run() -> Any:
        try:
            runtime = await exp.build_runtime(pg_ctx)
            return await runtime.kernel.run(runtime.spec, prompt)
        finally:
            await exp._run_cleanup_callbacks()

    try:
        kernel_run_result = asyncio.run(_run())
    finally:
        try:
            session.close()
        except Exception:
            pass
    duration_ms = int((time.monotonic() - t0) * 1000)

    # 6. Extract answer and tool calls from kernel result
    result_obj = kernel_run_result.result
    answer = result_obj.final_content or ""
    tool_calls = _extract_tool_calls_from_messages(kernel_run_result.messages)

    # 7. Fallback: try trajectory file if answer is empty
    trajectory_path = _guess_trajectory_file(run_dir=run_dir, task_id=task_id)
    if not answer and trajectory_path is not None and trajectory_path.exists():
        answer = extract_answer_from_trajectory_file(trajectory_path, task_id=task_id)
    if not tool_calls and trajectory_path is not None and trajectory_path.exists():
        tool_calls = extract_tool_calls_from_trajectory_file(
            trajectory_path, task_id=task_id
        )

    return {
        "task_id": task_id,
        "mode": mode,
        "answer": answer,
        "tool_calls": tool_calls,
        "result": {
            "status": result_obj.status,
            "reason": result_obj.reason,
            "num_turns": result_obj.num_turns,
            "usage": result_obj.usage,
        },
        "trajectory_path": str(trajectory_path) if trajectory_path else "",
        "status": result_obj.status,
        "duration_ms": duration_ms,
    }


def _extract_tool_calls_from_messages(
    messages: list[Any],
) -> list[dict[str, Any]]:
    """Extract tool call records from the kernel message transcript."""
    records: list[dict[str, Any]] = []
    _SKIP_TOOLS = {"finish", "peek_file"}
    step = 0
    for msg in messages:
        if not isinstance(msg, AssistantMessage):
            continue
        step += 1
        if not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            if tc.name in _SKIP_TOOLS:
                continue
            records.append(
                {
                    "step": step,
                    "tool_name": tc.name,
                    "tool_args": tc.arguments,
                    "success": True,
                }
            )
    return records


def _guess_trajectory_file(*, run_dir: Path, task_id: str) -> Path | None:
    candidates = [
        run_dir / "trajectories" / task_id / "trajectory.json",
        run_dir / "trajectories" / "trajectory.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def extract_answer_from_trajectory_obj(trajectory: Any) -> str:
    """Extract final answer from in-memory Trajectory object."""
    dialogs = getattr(trajectory, "dialogs", None)
    if not dialogs:
        return ""
    last_dialog = dialogs[-1]
    messages = getattr(last_dialog, "messages", None) or []
    for message in reversed(messages):
        role = getattr(
            getattr(message, "role", None), "value", getattr(message, "role", "")
        )
        if role != "assistant":
            continue
        tool_calls = getattr(message, "tool_calls", None) or []
        finish_msg = _extract_finish_message(tool_calls)
        if finish_msg:
            return finish_msg
        content = getattr(message, "content", "") or ""
        if content:
            return str(content)
    return ""


def _extract_finish_message(tool_calls: Any) -> str:
    for tool_call in tool_calls:
        function = getattr(tool_call, "function", tool_call)
        name = getattr(function, "name", None)
        if name != "finish":
            continue
        arguments = getattr(function, "arguments", "{}")
        try:
            payload = json.loads(arguments) if isinstance(arguments, str) else arguments
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            message = payload.get("message", "")
            if message:
                return str(message)
    return ""


def extract_answer_from_trajectory_file(
    path: Path, *, task_id: str | None = None
) -> str:
    """Extract final answer from trajectory JSON file (planner/direct fallback)."""
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""

    entries: list[dict[str, Any]] = []
    if isinstance(content, list):
        entries = [entry for entry in content if isinstance(entry, dict)]
    elif isinstance(content, dict):
        entries = [content]
    else:
        return ""

    for entry in reversed(entries):
        trajectory = entry.get("trajectory", entry)
        if not isinstance(trajectory, dict):
            continue
        current_task_id = str(trajectory.get("task_id", "") or "")
        if task_id and current_task_id and current_task_id != task_id:
            continue
        answer = _extract_answer_from_trajectory_dict(trajectory)
        if answer:
            return answer
    return ""


def _extract_answer_from_trajectory_dict(trajectory: dict[str, Any]) -> str:
    # Preferred path: assistant_message from recorded step
    steps = trajectory.get("steps")
    if isinstance(steps, list):
        for step in reversed(steps):
            if not isinstance(step, dict):
                continue
            assistant_message = step.get("assistant_message")
            if isinstance(assistant_message, dict):
                finish_msg = _extract_finish_message_from_dict(
                    assistant_message.get("tool_calls", [])
                )
                if finish_msg:
                    return finish_msg
                content = assistant_message.get("content")
                if isinstance(content, str) and content.strip():
                    return content

    # Fallback path: last assistant in dialogs/messages.
    dialogs = trajectory.get("dialogs")
    if isinstance(dialogs, list):
        for dialog in reversed(dialogs):
            messages = dialog.get("messages") if isinstance(dialog, dict) else None
            if not isinstance(messages, list):
                continue
            for message in reversed(messages):
                if not isinstance(message, dict):
                    continue
                if message.get("role") != "assistant":
                    continue
                finish_msg = _extract_finish_message_from_dict(
                    message.get("tool_calls", [])
                )
                if finish_msg:
                    return finish_msg
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content
    return ""


def _extract_finish_message_from_dict(tool_calls: Any) -> str:
    if not isinstance(tool_calls, list):
        return ""
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function", {})
        if not isinstance(function, dict):
            continue
        if function.get("name") != "finish":
            continue
        arguments = function.get("arguments", {})
        try:
            payload = json.loads(arguments) if isinstance(arguments, str) else arguments
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                return message
    return ""


def extract_tool_calls_from_trajectory_file(
    path: Path, *, task_id: str | None = None
) -> list[dict[str, Any]]:
    """Extract a flat list of tool call records from a trajectory JSON file.

    Each record contains the tool name, parsed arguments, and whether the
    tool execution succeeded.  Only non-lifecycle tool calls are included
    (``finish``, ``peek_file`` are excluded).
    """
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    entries: list[dict[str, Any]] = []
    if isinstance(content, list):
        entries = [e for e in content if isinstance(e, dict)]
    elif isinstance(content, dict):
        entries = [content]
    else:
        return []

    _SKIP_TOOLS = {"finish", "peek_file"}
    records: list[dict[str, Any]] = []

    for entry in entries:
        trajectory = entry.get("trajectory", entry)
        if not isinstance(trajectory, dict):
            continue
        current_task_id = str(trajectory.get("task_id", "") or "")
        if task_id and current_task_id and current_task_id != task_id:
            continue

        steps = trajectory.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            step_id = step.get("step_id", 0)
            assistant_msg = step.get("assistant_message")
            if not isinstance(assistant_msg, dict):
                continue

            tool_responses = step.get("tool_responses", [])
            if not isinstance(tool_responses, list):
                tool_responses = []
            response_by_id: dict[str, dict[str, Any]] = {}
            for tr in tool_responses:
                if isinstance(tr, dict):
                    tid = tr.get("tool_call_id", "")
                    if tid:
                        response_by_id[tid] = tr

            for tc in assistant_msg.get("tool_calls", []):
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function", {})
                if not isinstance(fn, dict):
                    continue
                tool_name = fn.get("name", "")
                if not tool_name or tool_name in _SKIP_TOOLS:
                    continue
                raw_args = fn.get("arguments", "{}")
                try:
                    tool_args = (
                        json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    )
                except Exception:
                    tool_args = {}
                if not isinstance(tool_args, dict):
                    tool_args = {}

                call_id = tc.get("id", "")
                resp = response_by_id.get(call_id, {})
                success = _parse_tool_success(resp)

                records.append(
                    {
                        "step": step_id,
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                        "success": success,
                    }
                )
    return records


def _parse_tool_success(response: dict[str, Any]) -> bool:
    """Determine whether a tool response indicates success."""
    meta_info = (response.get("meta") or {}).get("info", {})
    if isinstance(meta_info, dict) and "success" in meta_info:
        return bool(meta_info["success"])
    content = response.get("content", "")
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                status = parsed.get("status", "")
                return str(status).lower() == "success"
        except Exception:
            pass
    return True
