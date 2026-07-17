#!/usr/bin/env python3
"""Convert EvoMaster trajectory.json → frontend history.jsonl (WebSocket event schema).

Produces a history.jsonl file that the MatMaster web service can load via
persistence._load_persisted_sessions() and serve at /api/share/<session_id>.

Usage:
    python -m playground.mat_master.cli.trajectory_to_history \
        --trajectory path/to/trajectory.json \
        --session-id hero_si_pivot \
        [--output path/to/history.jsonl]     # default: stdout or auto-place in .state/
        [--install]                          # place directly into runs/.state/<session_id>/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _extract_new_messages(data: list[dict]) -> list[dict]:
    """Extract only NEW messages added at each step (trajectory stores cumulative)."""
    prev_count = 0
    result = []
    for entry in data:
        traj = entry.get("trajectory", {})
        dialogs = traj.get("dialogs", [])
        if not dialogs:
            continue
        messages = dialogs[0].get("messages", [])
        new_msgs = messages[prev_count:]
        for msg in new_msgs:
            msg["_step"] = entry.get("steps", 0)
        result.extend(new_msgs)
        prev_count = len(messages)
    return result


def convert(trajectory_path: Path, session_id: str) -> list[dict]:
    """Convert trajectory.json to a list of LogEntry dicts matching WebSocket event schema."""
    with open(trajectory_path) as f:
        data = json.load(f)

    messages = _extract_new_messages(data)
    events: list[dict] = []
    msg_id = 0
    user_seen = False

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls", [])

        if role == "system":
            # System prompts are not rendered in the UI
            continue

        elif role == "user" and not user_seen:
            # First user message → "query" event
            user_seen = True
            events.append({
                "source": "User",
                "type": "query",
                "content": content,
                "mode": "direct",
                "session_id": session_id,
            })
            # Also emit the "Initializing" status
            events.append({
                "source": "System",
                "type": "status",
                "content": "Initializing (direct)...",
                "session_id": session_id,
            })

        elif role == "user":
            # Subsequent user messages (shouldn't appear in direct mode, but handle)
            events.append({
                "source": "User",
                "type": "query",
                "content": content,
                "mode": "direct",
                "session_id": session_id,
            })

        elif role == "assistant":
            # Emit thought if there's text content
            if content and content.strip():
                msg_id += 1
                events.append({
                    "msg_id": msg_id,
                    "source": "MatMaster",
                    "type": "thought",
                    "content": content,
                    "session_id": session_id,
                })

            # Emit tool_call events
            for tc in tool_calls:
                func = tc.get("function", {})
                name = func.get("name", "unknown")
                args_raw = func.get("arguments", "{}")

                # Parse arguments
                if isinstance(args_raw, str):
                    try:
                        args = json.loads(args_raw)
                    except json.JSONDecodeError:
                        args = {"_raw": args_raw}
                else:
                    args = args_raw

                msg_id += 1
                events.append({
                    "msg_id": msg_id,
                    "source": "MatMaster",
                    "type": "tool_call",
                    "content": {
                        "id": tc.get("id", f"call_{msg_id}"),
                        "name": name,
                        "args": args,
                    },
                    "session_id": session_id,
                })

        elif role == "tool":
            tool_call_id = msg.get("tool_call_id", "")

            # Try to parse content as structured data
            if isinstance(content, str):
                try:
                    parsed = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    parsed = {"message": content}
            else:
                parsed = content if content else {"message": ""}

            # Find the matching tool_call name by looking backward
            tool_name = "unknown"
            for ev in reversed(events):
                if ev.get("type") == "tool_call":
                    c = ev.get("content", {})
                    if c.get("id") == tool_call_id:
                        tool_name = c.get("name", "unknown")
                        break

            msg_id += 1
            events.append({
                "msg_id": msg_id,
                "source": "MatMaster",
                "type": "tool_result",
                "content": {
                    "id": tool_call_id,
                    "name": tool_name,
                    "result": parsed,
                },
                "session_id": session_id,
            })

    # Add finish event at the end
    events.append({
        "source": "System",
        "type": "finish",
        "content": "Done",
        "session_id": session_id,
    })

    return events


def main():
    parser = argparse.ArgumentParser(
        description="Convert EvoMaster trajectory.json → frontend history.jsonl"
    )
    parser.add_argument(
        "--trajectory", "-t", required=True,
        help="Path to trajectory.json from an EvoMaster run",
    )
    parser.add_argument(
        "--session-id", "-s", required=True,
        help="Session ID for the replay (used in file placement and event metadata)",
    )
    parser.add_argument(
        "--output", "-o",
        help="Output path for history.jsonl. If --install is set, this is ignored.",
    )
    parser.add_argument(
        "--install", action="store_true",
        help="Install directly into runs/mat_master_web/.state/<session_id>/history.jsonl",
    )
    parser.add_argument(
        "--run-dir",
        help="Override the run directory (default: auto-detect from EvoMaster project)",
    )
    args = parser.parse_args()

    trajectory_path = Path(args.trajectory).resolve()
    if not trajectory_path.exists():
        print(f"ERROR: trajectory file not found: {trajectory_path}", file=sys.stderr)
        sys.exit(1)

    # Convert
    events = convert(trajectory_path, args.session_id)
    print(f"Converted: {len(events)} events from {trajectory_path.name}", file=sys.stderr)

    # Determine output path
    if args.install:
        if args.run_dir:
            run_dir = Path(args.run_dir).resolve()
        else:
            # Auto-detect: look for EvoMaster/runs/mat_master_web/
            here = Path(__file__).resolve()
            project_root = here.parent.parent.parent.parent  # cli/ -> mat_master/ -> playground/ -> EvoMaster/
            run_dir = project_root / "runs" / "mat_master_web"

        state_dir = run_dir / ".state" / args.session_id
        state_dir.mkdir(parents=True, exist_ok=True)
        output_path = state_dir / "history.jsonl"

        # Also write meta.json
        meta = {"last_task_id": f"task_0"}
        (state_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Installed meta.json → {state_dir / 'meta.json'}", file=sys.stderr)
    elif args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        # Write to stdout
        for ev in events:
            print(json.dumps(ev, ensure_ascii=False))
        return

    # Write JSONL
    with open(output_path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    print(f"Written: {output_path} ({len(events)} events)", file=sys.stderr)


if __name__ == "__main__":
    main()
