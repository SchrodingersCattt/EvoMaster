#!/usr/bin/env python3
"""After **external** baseline runs (Claude Code, Cursor, …): build ``raw_runs.jsonl`` + eval ingest (or ``pending_ingest``).

Expects a run directory created with::

    uv run python evaluation/scripts/devshell/run_devshell_eval.py --prepare-cc-baseline ...

``baseline_channel`` on ingest (``claude_code`` | ``cursor`` | ``codex``) comes from ``manifest.json`` or
``--baseline-channel``.

Each task workspace must contain:

- ``_eval_task_meta.json`` (written by --prepare-cc-baseline)
- ``_devshell_summary.json`` (one JSON object, same schema as mm-devshell ``--json-out``; ``duration_ms`` in it is ignored)
- ``_cc_baseline_task_start.json`` (from ``mark_external_baseline_task_start.py``) if you need ingest ``duration_ms``

See ``evaluation/docs/baseline/baseline_cc_eval.md``.

Examples::

    uv run python evaluation/scripts/baseline/finalize_external_baseline_ingest.py --run-dir results/baseline_cc_20260328_120000
    uv run python evaluation/scripts/baseline/finalize_external_baseline_ingest.py --run-dir results/... --eval-ingest-pending-only
    uv run python evaluation/scripts/baseline/finalize_external_baseline_ingest.py --run-dir results/... --only-tasks SC_struct_007_direct_r0
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _load_summary_file(summary_file: Path) -> dict[str, Any]:
    if summary_file.is_file():
        try:
            text = summary_file.read_text(encoding="utf-8").strip()
            if not text:
                return {"parse_error": True, "empty_file": True}
            last_line = text.splitlines()[-1].strip()
            return json.loads(last_line)
        except (json.JSONDecodeError, OSError) as exc:
            return {"parse_error": True, "error": str(exc)}
    return {"parse_error": True, "missing_file": str(summary_file)}


def _exit_code_from_summary(summary: dict[str, Any]) -> int:
    if summary.get("parse_error"):
        return 1
    if summary.get("reason") == "natural":
        return 0
    return 1


CC_BASELINE_TASK_START_NAME = "_cc_baseline_task_start.json"


def _load_raw_runs_by_task(path: Path) -> dict[str, dict[str, Any]]:
    """Parse ``raw_runs.jsonl`` into ``task_id -> row dict`` for merge writes."""
    out: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        tid = obj.get("task_id")
        if isinstance(tid, str) and tid:
            out[tid] = obj
    return out


def _write_raw_runs_merged(path: Path, rows: dict[str, dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as raw_f:
        for tid in sorted(rows.keys()):
            raw_f.write(json.dumps(rows[tid], ensure_ascii=False) + "\n")


def _duration_ms_from_cc_baseline_clock(
    workspace: Path, summary_path: Path
) -> tuple[int | None, str | None]:
    """Duration from ``started_at_unix_ms`` in ``_cc_baseline_task_start.json`` to summary mtime.

    Returns ``(duration_ms, "cc_baseline_clock")`` or ``(None, None)`` if the marker is
    missing, invalid, or end < start.
    """
    start_path = workspace / CC_BASELINE_TASK_START_NAME
    if not start_path.is_file() or not summary_path.is_file():
        return None, None
    try:
        raw = json.loads(start_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    if not isinstance(raw, dict):
        return None, None
    started = raw.get("started_at_unix_ms")
    if started is None:
        return None, None
    try:
        start_ms = int(started)
    except (TypeError, ValueError):
        return None, None
    if start_ms < 0:
        return None, None
    end_ms = int(summary_path.stat().st_mtime * 1000)
    delta = end_ms - start_ms
    if delta < 0:
        return None, None
    return delta, "cc_baseline_clock"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Finalize external baseline eval run: OSS zip per task, "
            "raw_runs.jsonl, ingest POST or pending_ingest/*.json."
        )
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        required=True,
        help="Run directory (contains manifest.json, workspaces/)",
    )
    parser.add_argument(
        "--eval-ingest-pending-only",
        action="store_true",
        help=(
            "Write pending_ingest/<task_id>.json without score (same as devshell "
            "pending flow); otherwise POST immediately if ingest URL is available."
        ),
    )
    parser.add_argument(
        "--no-eval-ingest",
        action="store_true",
        help="Only write raw_runs.jsonl (no OSS, no POST, no pending files).",
    )
    parser.add_argument(
        "--eval-ingest-timeout",
        type=float,
        default=30.0,
        help="HTTP timeout seconds for each ingest POST (default: 30).",
    )
    parser.add_argument(
        "--eval-ingest-strict",
        action="store_true",
        help="Exit non-zero if any ingest POST fails.",
    )
    parser.add_argument(
        "--baseline-channel",
        choices=("claude_code", "cursor", "codex"),
        default=None,
        help=(
            "EvalIngestRequest.baseline_channel (tools-server; required for run_kind=baseline). "
            "Default: manifest baseline_channel or claude_code."
        ),
    )
    parser.add_argument(
        "--only-tasks",
        nargs="+",
        metavar="TASK_ID",
        default=None,
        help=(
            "Process only these workspace task_id(s). "
            "Merges their rows into existing raw_runs.jsonl (other task rows preserved). "
            "Use for per-task finalize after incremental baseline runs."
        ),
    )
    args = parser.parse_args()

    if args.no_eval_ingest and args.eval_ingest_pending_only:
        print(
            "error: --no-eval-ingest and --eval-ingest-pending-only cannot be used together",
            file=sys.stderr,
        )
        return 2

    run_dir = args.run_dir.resolve()
    if not run_dir.is_dir():
        print(f"not a directory: {run_dir}", file=sys.stderr)
        return 1

    manifest_path = run_dir / "manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    sys.path.insert(0, str(REPO_ROOT))
    from evaluation.eval_ingest_client import (
        EVAL_INGEST_URL,
        build_ingest_item,
        git_head_commit,
        normalize_baseline_channel,
        post_eval_ingest,
        upload_eval_task_artifacts_to_oss,
    )
    from evaluation.eval_tooling_snapshot import snapshot_devshell_eval_tooling

    ingest_url: str | None = None
    if not args.no_eval_ingest:
        if args.eval_ingest_pending_only:
            ingest_url = (manifest.get("eval_ingest_url") or "").strip() or (
                EVAL_INGEST_URL or ""
            ).strip()
            if not ingest_url:
                print(
                    "error: --eval-ingest-pending-only needs ingest URL "
                    "(manifest eval_ingest_url or MATMASTER_TOOLS_SERVER)",
                    file=sys.stderr,
                )
                return 2
        else:
            ingest_url = (manifest.get("eval_ingest_url") or "").strip() or (
                EVAL_INGEST_URL or ""
            ).strip()

    run_id = (manifest.get("eval_ingest_run_id") or "").strip()
    if not run_id and ingest_url and not args.no_eval_ingest:
        run_id = str(uuid.uuid4())
        print(
            f"warning: manifest missing eval_ingest_run_id; generated {run_id}",
            file=sys.stderr,
        )

    git_commit = manifest.get("git_commit")
    if not isinstance(git_commit, str) or not git_commit.strip():
        git_commit = git_head_commit(REPO_ROOT)

    eval_tooling = manifest.get("eval_tooling")
    if not isinstance(eval_tooling, dict):
        eval_tooling = snapshot_devshell_eval_tooling(repo_root=REPO_ROOT)

    is_cc_baseline_manifest = bool(manifest.get("prepare_cc_baseline")) or (
        manifest.get("eval_runner") == "claude_code_baseline"
    )
    if (
        is_cc_baseline_manifest
        and not args.no_eval_ingest
        and not args.eval_ingest_pending_only
        and (ingest_url or "").strip()
    ):
        print(
            "warning: external baseline + immediate POST → item.score is automated (often 100 if exit 0), "
            "not checklist-based. Prefer: finalize with --eval-ingest-pending-only, then "
            "eval_ingest_submit_pending.py with rubric-aligned --score / --score-reason.",
            file=sys.stderr,
        )

    if args.baseline_channel is not None:
        baseline_channel = normalize_baseline_channel(
            args.baseline_channel, default="claude_code"
        )
    else:
        baseline_channel = normalize_baseline_channel(
            manifest.get("baseline_channel"), default="claude_code"
        )

    workspaces_root = run_dir / "workspaces"
    if not workspaces_root.is_dir():
        print(f"missing workspaces/: {workspaces_root}", file=sys.stderr)
        return 1

    tasks: list[tuple[str, Path]] = []
    for ws in sorted(workspaces_root.iterdir()):
        if not ws.is_dir():
            continue
        if not (ws / "_eval_task_meta.json").is_file():
            continue
        tasks.append((ws.name, ws))

    if not tasks:
        print(
            f"no task workspaces with _eval_task_meta.json under {workspaces_root}",
            file=sys.stderr,
        )
        return 1

    only_set: set[str] | None = None
    if args.only_tasks is not None:
        only_set = set(args.only_tasks)
        tasks = [(tid, ws) for tid, ws in tasks if tid in only_set]
        missing = only_set - {t[0] for t in tasks}
        if missing:
            print(
                f"error: --only-tasks not found under workspaces/: {sorted(missing)}",
                file=sys.stderr,
            )
            return 1
        if not tasks:
            print("error: --only-tasks matched no workspaces", file=sys.stderr)
            return 1

    merge_raw = only_set is not None
    raw_path = run_dir / "raw_runs.jsonl"
    pending_dir = run_dir / "pending_ingest"
    if args.eval_ingest_pending_only and ingest_url:
        pending_dir.mkdir(parents=True, exist_ok=True)

    any_ingest_fail = False
    n_written = 0
    merged_rows: dict[str, dict[str, Any]] | None = None
    raw_f = None
    if merge_raw:
        merged_rows = _load_raw_runs_by_task(raw_path)
    else:
        raw_f = raw_path.open("w", encoding="utf-8")

    try:
        for task_id, ws in tasks:
            meta_path = ws / "_eval_task_meta.json"
            summary_path = ws / "_devshell_summary.json"
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                print(
                    f"[skip] {task_id}: bad _eval_task_meta.json: {e}", file=sys.stderr
                )
                continue
            if not isinstance(meta, dict):
                print(
                    f"[skip] {task_id}: _eval_task_meta.json not an object",
                    file=sys.stderr,
                )
                continue
            qid = meta.get("question_id")
            if not isinstance(qid, str) or not qid:
                print(f"[skip] {task_id}: missing question_id in meta", file=sys.stderr)
                continue

            summary = _load_summary_file(summary_path)
            rc = _exit_code_from_summary(summary)
            duration_ms, clock_tag = _duration_ms_from_cc_baseline_clock(
                ws, summary_path
            )
            duration_source = (
                clock_tag if duration_ms is not None else "no_cc_baseline_clock"
            )
            if duration_ms is None:
                print(
                    f"[external_baseline] {task_id}: duration_ms omitted — run "
                    f"evaluation/scripts/baseline/mark_external_baseline_task_start.py "
                    f"--workspace <this task dir> before work, then finalize again.",
                    file=sys.stderr,
                )

            artifact: dict[str, Any] | None = None
            if not args.no_eval_ingest:
                artifact = upload_eval_task_artifacts_to_oss(run_dir, task_id)

            ingest_item = build_ingest_item(
                question_id=qid,
                task_id=task_id,
                mode=str(meta.get("mode") or "direct"),
                repeat_idx=int(meta.get("repeat_idx") or 0),
                devshell_exit_code=rc,
                summary=summary if isinstance(summary, dict) else {},
                duration_ms=duration_ms,
                artifact=artifact,
                eval_tooling=eval_tooling,
            )
            extra = ingest_item.get("extra")
            if isinstance(extra, dict):
                extra["eval_runner"] = "claude_code_baseline"
                extra["matter_eval_source"] = "claude_code_baseline"
                extra["baseline_duration_source"] = duration_source
                extra["baseline_task_id"] = task_id
                extra["baseline_question_id"] = qid
                rl = manifest.get("run_label")
                if isinstance(rl, str) and rl.strip():
                    extra["baseline_manifest_run_label"] = rl.strip()[:200]
                cap = meta.get("capability")
                if isinstance(cap, str) and cap.strip():
                    extra["question_capability"] = cap.strip()
                dom = meta.get("domain")
                if isinstance(dom, str) and dom.strip():
                    extra["question_domain"] = dom.strip()
                # Propagate detailed token usage from claude -p runs
                usage = summary.get("usage") if isinstance(summary, dict) else None
                if isinstance(usage, dict):
                    detail_keys = (
                        "input_tokens",
                        "output_tokens",
                        "cache_creation_input_tokens",
                        "cache_read_input_tokens",
                        "total_cost_usd",
                        "model_usage",
                    )
                    usage_detail = {k: usage[k] for k in detail_keys if k in usage}
                    if usage_detail:
                        extra["usage_detail"] = usage_detail
                cli_meta = (
                    summary.get("claude_cli_meta")
                    if isinstance(summary, dict)
                    else None
                )
                if isinstance(cli_meta, dict) and cli_meta:
                    extra["claude_cli_meta"] = cli_meta

            row: dict[str, Any] = {
                "task_id": task_id,
                "question_id": qid,
                "capability": meta.get("capability"),
                "domain": meta.get("domain"),
                "mode": meta.get("mode"),
                "repeat_idx": meta.get("repeat_idx"),
                "devshell_exit_code": rc,
                "devshell_summary_path": str(summary_path),
                "devshell_summary": summary,
                "duration_ms": duration_ms,
                "baseline_duration_source": duration_source,
            }
            log_dir = run_dir / "logs" / task_id
            console_log = log_dir / "devshell_console.log"
            if console_log.is_file():
                row["devshell_console_log_path"] = str(console_log)

            if args.no_eval_ingest:
                row["eval_ingest_ok"] = None
                row["eval_ingest_message"] = "skipped_no_eval_ingest"
            elif args.eval_ingest_pending_only and ingest_url:
                pend_path = pending_dir / f"{task_id}.json"
                item_body = {k: v for k, v in ingest_item.items() if k != "score"}
                envelope: dict[str, Any] = {
                    "schema": "matmaster_eval_pending_ingest_v1",
                    "ingest_url": ingest_url,
                    "run_id": run_id,
                    "run_kind": "baseline",
                    "baseline_channel": baseline_channel,
                    "task_id": task_id,
                    "instructions_zh": (
                        "【外部 Baseline】勿随手给 100 分。请读题库 YAML 的 scoring_checklist，按 "
                        "devshell_claude_code_eval.md 第 3 节算百分制；--score-reason 须逐条对照 checklist "
                        "说明证据（可引用 raw_runs / OSS zip 内路径）；--suggestion 写可执行改进；"
                        "耗时：仅认客观墙钟 — workspace 须有 _cc_baseline_task_start.json（见 "
                        "mark_external_baseline_task_start.py），duration_ms = 该文件时间戳至 _devshell_summary.json "
                        "mtime；缺则 item 无 duration_ms。"
                        " tokens 以 summary.usage 为准；若缺失须在 score_reason 中说明。"
                        "上报命令: uv run python "
                        f"evaluation/scripts/eval_ingest_submit_pending.py --pending {pend_path} "
                        "--score <0-100> --score-reason \"...\" [--suggestion \"...\"]"
                    ),
                    "item": item_body,
                }
                pend_path.write_text(
                    json.dumps(envelope, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                row["eval_ingest_pending_path"] = str(pend_path)
                row["eval_ingest_ok"] = None
                row["eval_ingest_message"] = "pending_score"
                if artifact:
                    row["eval_ingest_artifact"] = artifact
                print(
                    f"  [ingest-pending] {task_id} -> {pend_path.name}", file=sys.stderr
                )
            elif ingest_url:
                body: dict[str, Any] = {
                    "run_id": run_id,
                    "run_kind": "baseline",
                    "baseline_channel": baseline_channel,
                    "items": [ingest_item],
                }
                ok, msg = post_eval_ingest(
                    ingest_url,
                    body,
                    timeout=float(args.eval_ingest_timeout),
                )
                row["eval_ingest_ok"] = ok
                row["eval_ingest_message"] = msg
                if artifact:
                    row["eval_ingest_artifact"] = artifact
                if ok:
                    print(f"  [ingest] {task_id} ok ({msg})", file=sys.stderr)
                else:
                    print(f"  [ingest] {task_id} failed: {msg}", file=sys.stderr)
                    any_ingest_fail = True
            else:
                row["eval_ingest_ok"] = None
                row["eval_ingest_message"] = "no_ingest_url"
                if artifact:
                    row["eval_ingest_artifact"] = artifact

            line_payload = json.dumps(row, ensure_ascii=False) + "\n"
            if merge_raw:
                assert merged_rows is not None
                merged_rows[task_id] = row
            else:
                assert raw_f is not None
                raw_f.write(line_payload)
            n_written += 1
    finally:
        if raw_f is not None:
            raw_f.close()

    if merge_raw:
        assert merged_rows is not None
        _write_raw_runs_merged(raw_path, merged_rows)
        print(
            f"Wrote {raw_path} (merged {n_written} task(s); "
            f"{len(merged_rows)} total row(s))",
            file=sys.stderr,
        )
    else:
        print(
            f"Wrote {raw_path} ({n_written}/{len(tasks)} task row(s))", file=sys.stderr
        )
    if args.eval_ingest_strict and any_ingest_fail:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
