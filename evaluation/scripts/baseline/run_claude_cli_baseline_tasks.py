#!/usr/bin/env python3
"""Run **external** baseline tasks via the Anthropic ``claude`` CLI (``claude -p``, non-interactive).

This automates only the **Claude Code CLI** path. The same run directory and
``finalize_external_baseline_ingest.py`` apply to Cursor (or other) baselines if you
produce ``_devshell_summary.json`` yourself and set ``manifest`` / ``baseline_channel``
accordingly.

For each workspace under ``RUN_DIR/workspaces/``:

1. Mark task start (``_cc_baseline_task_start.json``)
2. Run ``claude -p`` with the task prompt, capturing JSON output
3. Parse token usage from CLI JSON (last embedded ``usage`` on ``messages`` / similar when present; else top-level ``usage``)
4. Write ``_devshell_summary.json``

Requires ``claude`` CLI in ``PATH`` and ``--dangerously-skip-permissions``.

Examples::

    # After prepare, run all tasks
    uv run python evaluation/scripts/baseline/run_claude_cli_baseline_tasks.py \\
      --run-dir "$RUN_DIR"

    # Run specific tasks only
    uv run python evaluation/scripts/baseline/run_claude_cli_baseline_tasks.py \\
      --run-dir "$RUN_DIR" --tasks SC_struct_007_direct_r0

    # Use a specific model
    uv run python evaluation/scripts/baseline/run_claude_cli_baseline_tasks.py \\
      --run-dir "$RUN_DIR" --model opus

    # Serial (default is --jobs 4)
    uv run python evaluation/scripts/baseline/run_claude_cli_baseline_tasks.py \\
      --run-dir "$RUN_DIR" -j 1

    # Auto-detect RUN_DIR from results/ and also finalize
    uv run python evaluation/scripts/baseline/run_claude_cli_baseline_tasks.py \\
      --run-label baseline_cc_struct --finalize --eval-ingest-pending-only
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _find_run_dir(run_label: str) -> Path | None:
    """Auto-detect the latest RUN_DIR matching *run_label* under results/."""
    results = REPO_ROOT / "results"
    if not results.is_dir():
        return None
    candidates = sorted(results.glob(f"{run_label}_*"))
    dirs = [c for c in candidates if c.is_dir()]
    return dirs[-1] if dirs else None


def _mark_task_start(workspace: Path) -> Path:
    """Write ``_cc_baseline_task_start.json`` and return its path."""
    out = workspace / "_cc_baseline_task_start.json"
    ms = time.time_ns() // 1_000_000
    payload = {
        "started_at_unix_ms": ms,
        "schema": "matmaster_cc_baseline_task_start_v1",
    }
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out


def _run_claude_p(
    workspace: Path,
    prompt: str,
    *,
    model: str | None = None,
    max_turns: int = 50,
    timeout_seconds: int = 600,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    """Run ``claude -p`` and return the parsed JSON output."""
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
        "--max-turns",
        str(max_turns),
        "--bare",
    ]
    if model:
        cmd.extend(["--model", model])
    if extra_args:
        cmd.extend(extra_args)

    result = subprocess.run(
        cmd,
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )

    stdout = result.stdout.strip()
    if not stdout:
        return {
            "type": "result",
            "subtype": "error",
            "is_error": True,
            "result": f"claude -p produced no stdout; stderr: {result.stderr[:500]}",
            "usage": {},
            "modelUsage": {},
        }

    # claude -p --output-format json writes one JSON object to stdout
    # but shell init noise may precede it; find the last JSON object
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {
        "type": "result",
        "subtype": "error",
        "is_error": True,
        "result": f"no JSON in claude -p stdout ({len(stdout)} chars); stderr: {result.stderr[:300]}",
        "usage": {},
        "modelUsage": {},
    }


def _extract_model_name(cli_output: dict[str, Any], model_arg: str | None) -> str:
    """Best-effort model name from claude -p JSON output."""
    mu = cli_output.get("modelUsage")
    if isinstance(mu, dict) and mu:
        # Use first key (usually the only model)
        raw = next(iter(mu))
        # Simplify: "us.anthropic.claude-opus-4-6-v1[1m]" -> "claude-opus-4-6"
        # Strip provider prefix and version/context suffixes
        name = raw
        if "." in name:
            parts = name.split(".")
            # Take the part that starts with "claude"
            for p in parts:
                if p.startswith("claude"):
                    name = p
                    break
        # Strip trailing version/context like "-v1[1m]"
        for sep in ("-v1", "-v2", "["):
            if sep in name:
                name = name[: name.index(sep)]
                break
        return name
    if model_arg:
        return model_arg
    return "claude_code"


def _extract_last_turn_usage_raw(cli_output: dict[str, Any]) -> dict[str, Any] | None:
    """If CLI embeds per-message / per-turn usage, return the last non-empty ``usage`` dict."""
    for key in ("messages", "conversation", "turns", "history"):
        arr = cli_output.get(key)
        if not isinstance(arr, list):
            continue
        for msg in reversed(arr):
            if not isinstance(msg, dict):
                continue
            u = msg.get("usage")
            if isinstance(u, dict) and u:
                return u
    return None


def _build_usage(cli_output: dict[str, Any]) -> dict[str, Any]:
    """Build usage dict from claude -p JSON (last agent turn when available).

    Prefer the last embedded ``usage`` on ``messages`` / similar; otherwise use
    top-level ``usage`` (Claude Code: typically the last model request in the run).
    """
    raw_usage = (
        _extract_last_turn_usage_raw(cli_output) or cli_output.get("usage") or {}
    )
    model_usage = cli_output.get("modelUsage") or {}

    input_tokens = int(raw_usage.get("input_tokens") or 0)
    cache_creation = int(raw_usage.get("cache_creation_input_tokens") or 0)
    cache_read = int(raw_usage.get("cache_read_input_tokens") or 0)
    output_tokens = int(raw_usage.get("output_tokens") or 0)

    prompt_tokens = input_tokens + cache_creation + cache_read
    total_tokens = prompt_tokens + output_tokens

    usage: dict[str, Any] = {
        # Backward-compatible aggregated fields
        "prompt_tokens": prompt_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": total_tokens,
        # Detailed breakdown
        "input_tokens": input_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "output_tokens": output_tokens,
    }

    total_cost = cli_output.get("total_cost_usd")
    if total_cost is not None:
        usage["total_cost_usd"] = total_cost

    # Per-model breakdown (preserve all fields)
    if model_usage:
        usage["model_usage"] = model_usage

    return usage


def _build_summary(
    cli_output: dict[str, Any], *, model_arg: str | None
) -> dict[str, Any]:
    """Build ``_devshell_summary.json`` content from claude -p JSON output."""
    is_error = cli_output.get("is_error", False)
    result_text = cli_output.get("result", "")
    num_turns = cli_output.get("num_turns", 0)

    model = _extract_model_name(cli_output, model_arg)
    usage = _build_usage(cli_output)

    status = "error" if is_error else "completed"
    reason = "error" if is_error else "natural"

    summary: dict[str, Any] = {
        "model": model,
        "profile_key": "claude_code",
        "route_key": None,
        "status": status,
        "reason": reason,
        "final_content": result_text[:8000] if result_text else "",
        "num_turns": num_turns,
        "usage": usage,
    }

    # Store full CLI metadata for traceability
    cli_meta: dict[str, Any] = {}
    for key in (
        "duration_ms",
        "duration_api_ms",
        "session_id",
        "stop_reason",
        "total_cost_usd",
    ):
        if key in cli_output:
            cli_meta[key] = cli_output[key]
    if cli_meta:
        summary["claude_cli_meta"] = cli_meta

    return summary


def _run_single_task(
    task_id: str,
    workspace: Path,
    *,
    model: str | None,
    max_turns: int,
    timeout_seconds: int,
    extra_args: list[str] | None,
) -> tuple[bool, dict[str, Any]]:
    """Run one task. Returns (success, summary_dict)."""
    prompt_file = workspace / "_devshell_prompt.txt"
    if not prompt_file.is_file():
        return False, {"error": f"missing _devshell_prompt.txt in {workspace}"}

    prompt = prompt_file.read_text(encoding="utf-8").strip()
    if not prompt:
        return False, {"error": "empty prompt"}

    # 1. Mark start
    _mark_task_start(workspace)

    # 2. Run claude -p
    cli_output = _run_claude_p(
        workspace,
        prompt,
        model=model,
        max_turns=max_turns,
        timeout_seconds=timeout_seconds,
        extra_args=extra_args,
    )

    # 3. Build and write summary
    summary = _build_summary(cli_output, model_arg=model)
    summary_path = workspace / "_devshell_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    is_error = cli_output.get("is_error", False)
    return not is_error, summary


def _stderr_line(msg: str, lock: threading.Lock | None) -> None:
    if lock is not None:
        with lock:
            print(msg, file=sys.stderr)
    else:
        print(msg, file=sys.stderr)


def _execute_task_slot(
    slot: int,
    n_total: int,
    task_id: str,
    workspace: Path,
    *,
    model: str | None,
    max_turns: int,
    timeout_seconds: int,
    extra_args: list[str] | None,
    print_lock: threading.Lock | None,
) -> dict[str, Any]:
    """Run one non-skipped task; return the same shape as entries in ``results``."""
    progress = f"[{slot}/{n_total}]"
    _stderr_line(f"{progress} {task_id}: running...", print_lock)
    t0 = time.monotonic()
    try:
        ok, summary = _run_single_task(
            task_id,
            workspace,
            model=model,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
            extra_args=extra_args,
        )
    except subprocess.TimeoutExpired:
        _stderr_line(
            f"{progress} {task_id}: TIMEOUT ({timeout_seconds}s)",
            print_lock,
        )
        return {"task_id": task_id, "status": "timeout"}
    except Exception as exc:
        _stderr_line(f"{progress} {task_id}: ERROR: {exc}", print_lock)
        return {"task_id": task_id, "status": "error", "error": str(exc)}

    elapsed = time.monotonic() - t0
    usage = summary.get("usage", {})
    cost = usage.get("total_cost_usd", "?")
    tag = "OK" if ok else "FAIL"
    _stderr_line(
        f"{progress} {task_id}: {tag} "
        f"({elapsed:.0f}s, turns={summary.get('num_turns', '?')}, cost=${cost})",
        print_lock,
    )
    if ok:
        return {
            "task_id": task_id,
            "status": "ok",
            "elapsed_s": round(elapsed, 1),
            "num_turns": summary.get("num_turns"),
            "cost_usd": usage.get("total_cost_usd"),
        }
    return {
        "task_id": task_id,
        "status": "fail",
        "elapsed_s": round(elapsed, 1),
        "num_turns": summary.get("num_turns"),
        "cost_usd": usage.get("total_cost_usd"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run external baseline tasks via claude -p (Claude CLI automation only)."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--run-dir",
        type=Path,
        help="Run directory (contains workspaces/). Explicit path.",
    )
    group.add_argument(
        "--run-label",
        type=str,
        help="Auto-detect RUN_DIR from results/<run_label>_* (latest by name sort).",
    )
    parser.add_argument(
        "--tasks",
        nargs="*",
        help="Specific task_id(s) to run. Default: all tasks in workspaces/.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Model override for claude -p (e.g. 'opus', 'sonnet', 'claude-opus-4-6').",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=50,
        help="Max conversation turns per task (default: 50).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Timeout in seconds per task (default: 600).",
    )
    parser.add_argument(
        "--skip-completed",
        action="store_true",
        help="Skip tasks that already have _devshell_summary.json.",
    )
    parser.add_argument(
        "--finalize",
        action="store_true",
        help="Run finalize_external_baseline_ingest.py after all tasks complete.",
    )
    parser.add_argument(
        "--eval-ingest-pending-only",
        action="store_true",
        help="Pass --eval-ingest-pending-only to finalize (requires --finalize).",
    )
    parser.add_argument(
        "--baseline-channel",
        choices=("claude_code", "cursor", "codex"),
        default=None,
        help=(
            "If set with --finalize, pass to finalize as EvalIngestRequest.baseline_channel "
            "(overrides manifest; default from manifest or claude_code)."
        ),
    )
    parser.add_argument(
        "--claude-extra-args",
        nargs="*",
        default=None,
        help="Extra arguments to pass to claude -p.",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=4,
        metavar="N",
        help=(
            "Concurrent claude -p processes (default: 4). "
            "Each task uses its own workspace cwd; set to 1 to force serial. "
            "Watch API rate limits when increasing."
        ),
    )
    args = parser.parse_args()
    if args.jobs < 1:
        print("error: --jobs must be >= 1", file=sys.stderr)
        return 1

    # Resolve RUN_DIR
    if args.run_dir:
        run_dir = args.run_dir.resolve()
    else:
        run_dir_maybe = _find_run_dir(args.run_label)
        if run_dir_maybe is None:
            print(
                f"error: no directory matching results/{args.run_label}_* found",
                file=sys.stderr,
            )
            return 1
        run_dir = run_dir_maybe
    if not run_dir.is_dir():
        print(f"error: not a directory: {run_dir}", file=sys.stderr)
        return 1
    print(f"RUN_DIR: {run_dir}", file=sys.stderr)

    # Check claude CLI
    if shutil.which("claude") is None:
        print("error: 'claude' CLI not found in PATH", file=sys.stderr)
        return 1

    # Discover tasks
    workspaces_root = run_dir / "workspaces"
    if not workspaces_root.is_dir():
        print(f"error: missing workspaces/ in {run_dir}", file=sys.stderr)
        return 1

    all_tasks: list[tuple[str, Path]] = []
    for ws in sorted(workspaces_root.iterdir()):
        if not ws.is_dir():
            continue
        if not (ws / "_eval_task_meta.json").is_file():
            continue
        all_tasks.append((ws.name, ws))

    if args.tasks:
        task_set = set(args.tasks)
        selected = [(tid, ws) for tid, ws in all_tasks if tid in task_set]
        missing = task_set - {tid for tid, _ in selected}
        if missing:
            print(f"warning: tasks not found: {missing}", file=sys.stderr)
    else:
        selected = all_tasks

    if not selected:
        print("error: no tasks to run", file=sys.stderr)
        return 1

    n_sel = len(selected)
    print(f"Tasks to run: {n_sel}/{len(all_tasks)}", file=sys.stderr)
    if args.jobs > 1:
        print(f"Parallel jobs: {args.jobs}", file=sys.stderr)

    results: list[dict[str, Any] | None] = [None] * n_sel
    to_run: list[tuple[int, str, Path]] = []

    for i, (task_id, workspace) in enumerate(selected):
        summary_exists = (workspace / "_devshell_summary.json").is_file()
        if args.skip_completed and summary_exists:
            slot = i + 1
            print(
                f"[{slot}/{n_sel}] {task_id}: skipped (summary exists)",
                file=sys.stderr,
            )
            results[i] = {"task_id": task_id, "status": "skipped"}
        else:
            to_run.append((i, task_id, workspace))

    print_lock: threading.Lock | None = threading.Lock() if args.jobs > 1 else None

    if to_run and (args.jobs <= 1 or len(to_run) == 1):
        for i, task_id, workspace in to_run:
            results[i] = _execute_task_slot(
                i + 1,
                n_sel,
                task_id,
                workspace,
                model=args.model,
                max_turns=args.max_turns,
                timeout_seconds=args.timeout,
                extra_args=args.claude_extra_args,
                print_lock=print_lock,
            )
    elif to_run:
        workers = min(args.jobs, len(to_run))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {
                pool.submit(
                    _execute_task_slot,
                    i + 1,
                    n_sel,
                    task_id,
                    workspace,
                    model=args.model,
                    max_turns=args.max_turns,
                    timeout_seconds=args.timeout,
                    extra_args=args.claude_extra_args,
                    print_lock=print_lock,
                ): i
                for i, task_id, workspace in to_run
            }
            for fut in as_completed(future_map):
                idx = future_map[fut]
                results[idx] = fut.result()

    n_ok = n_fail = 0
    for r in results:
        if r is None:
            continue
        st = r.get("status")
        if st == "ok":
            n_ok += 1
        elif st in ("fail", "timeout", "error"):
            n_fail += 1

    print(
        f"\nDone: {n_ok} ok, {n_fail} failed, "
        f"{n_sel - n_ok - n_fail} skipped out of {n_sel}",
        file=sys.stderr,
    )

    # Finalize
    if args.finalize:
        print("\nRunning finalize...", file=sys.stderr)
        finalize_cmd = [
            sys.executable,
            str(
                REPO_ROOT
                / "evaluation"
                / "scripts"
                / "baseline"
                / "finalize_external_baseline_ingest.py"
            ),
            "--run-dir",
            str(run_dir),
        ]
        if args.eval_ingest_pending_only:
            finalize_cmd.append("--eval-ingest-pending-only")
        if args.baseline_channel:
            finalize_cmd.extend(["--baseline-channel", str(args.baseline_channel)])
        rc = subprocess.run(finalize_cmd, cwd=str(REPO_ROOT)).returncode
        if rc != 0:
            print(f"finalize exited with code {rc}", file=sys.stderr)
            return rc

    return 1 if n_fail > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
