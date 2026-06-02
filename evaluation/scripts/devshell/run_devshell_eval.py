#!/usr/bin/env python3
"""Batch-run MATTER v5 question bank through ``mm-devshell run`` (matmaster kernel).

Reads the same ``question_bank/`` layout as ``evaluation``,
stages data files per task workspace, then invokes (inherit terminal; ``--json-out`` for aggregation)::

    python -u -m matmaster.devshell run ... --prompt-file ... --json-out .../_devshell_summary.json

Aggregate output: ``raw_runs.jsonl`` + ``manifest.json`` + by default ``claude_review.md`` (for Cursor @-review).
``manifest.json`` carries ``eval_tooling`` (default: same as interactive ``mm-devshell`` without ``--exp`` —
``direct`` from ``matmaster/exps/direct.toml``).
The same snapshot is attached to each ingest item as ``extra.eval_tooling`` for downstream analysis.
When ``logs/<task_id>/events_*.jsonl`` exists, ingest ``extra`` also includes ``events_timeline`` (ordered
labels: tool names from ``tool_call``, ``response``, ``run_result``; ``tool_result`` lines are omitted).

**Per-task wall clock**: each ``mm-devshell`` subprocess is limited by ``--task-timeout``
(default **1200** seconds = 20 minutes). This is the reliable cap when a turn/tool blocks
without tripping the LLM per-request timeout; use ``0`` to disable.

Optional **per-task ingest** to matmaster-tools-server (after each devshell run).
POST URL is fixed: ``MATMASTER_TOOLS_SERVER`` + ``/api/v1/evaluation/ingest`` (see ``evaluation.eval_ingest_client``).
Each item includes ``score`` (explicit from summary or 100/0 pass-fail proxy) and, when OSS env is set,
top-level ``artifact`` for **that task only**: bundle download + manifest + file tree for
``workspaces/<task_id>/`` and ``logs/<task_id>/`` under the shared ``devshell_eval_*`` run folder.
Upload is always attempted when ingest is enabled (no skip flag).

``raw_runs.jsonl`` rows record ``duration_ms`` and, when ingest is on, ``eval_ingest_*`` fields
(including ``eval_ingest_artifact`` after a successful OSS upload).

With ``--eval-ingest-pending-only``, no POST is sent; each task writes ``pending_ingest/<task_id>.json``
(ingest payload without ``score``). Prefer scoring later with
``evaluation/scripts/devshell/score_devshell_tasks.py`` (same BinaryEvaluator as MATTER);
use ``eval_ingest_submit_pending.py`` only when you need to add / override ``suggestion`` manually.

Override host with ``MATMASTER_TOOLS_SERVER`` / ``SERVICE_ENV`` as needed. Use ``--no-eval-ingest`` to skip POSTs.

By default, the repository ``results/`` directory is emptied before the run; use ``--no-clean-results`` to keep prior outputs.

See matmaster-tools-server ``docs/apifox-evaluation-openapi.json`` for the schema.

Usage (from repository root)::

    uv run python evaluation/scripts/devshell/run_devshell_eval.py --limit 3
    # Defaults: --model bedrock-claude-opus, --fallback-model global.anthropic.claude-opus-4-6-v1
    uv run python evaluation/scripts/devshell/run_devshell_eval.py --slices structure_construction --limit 3
    uv run python evaluation/scripts/devshell/run_devshell_eval.py --no-clean-results --limit 5   # keep previous results/ contents
    uv run python evaluation/scripts/devshell/run_devshell_eval.py --no-export-review --limit 3   # skip Markdown bundle
    uv run python evaluation/scripts/devshell/export_devshell_review_bundle.py --run-dir results/devshell_eval_*  # manual only
    uv run python evaluation/scripts/devshell/run_devshell_eval.py --help

**Claude Code baseline（不跑 devshell）**：``--prepare-cc-baseline`` 只搭 ``workspaces/`` 与
``_eval_task_meta.json``；在 IDE 里跑完题并写好 ``_devshell_summary.json`` 后执行
``evaluation/scripts/baseline/finalize_external_baseline_ingest.py``。说明见
``evaluation/docs/baseline/baseline_cc_eval.md``.

This does **not** run MATTER's BinaryEvaluator or Playground ``run_mat_task``; it only
collects devshell JSON summaries for downstream review or custom scoring.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Repo root = evaluation/scripts/devshell/../../..
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _run_devshell_task(*args: Any, **kwargs: Any) -> tuple[int, int, dict[str, Any]]:
    """Delegate to helpers (module-level name stays patchable in tests)."""
    sys.path.insert(0, str(REPO_ROOT))
    from evaluation.scripts.devshell.run_devshell_eval_helpers import (
        _run_devshell_task as _impl,
    )

    return _impl(*args, **kwargs)


def main() -> int:
    # Load .env files so OSS / SERVICE_ENV / etc. are available to post-processing
    # (same logic as matmaster.devshell.cli)
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    current_env = os.environ.get("SERVICE_ENV", "test")
    env_file = find_dotenv(f".env.{current_env}")
    if env_file:
        load_dotenv(env_file, override=True)

    parser = argparse.ArgumentParser(
        description="Run MATTER question bank through mm-devshell (matmaster devshell run).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--eval-config",
        type=Path,
        default=REPO_ROOT / "evaluation/config.yaml",
        help="MATTER eval YAML (filters: capabilities, question ids, use_seed_prompt, …)",
    )
    parser.add_argument(
        "--question-bank-dir",
        type=Path,
        default=None,
        help="Override question bank directory (default from eval config)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: results/devshell_eval_<UTC timestamp>)",
    )
    parser.add_argument(
        "--run-label",
        type=str,
        default="devshell_eval",
        help="Prefix for the run folder name",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="bedrock-claude-opus",
        help=(
            "LLM route key passed to ``mm-devshell run --model`` (see llm_config.yaml routes; "
            "default: bedrock-claude-opus)"
        ),
    )
    parser.add_argument(
        "--fallback-model",
        type=str,
        default="global.anthropic.claude-opus-4-6-v1",
        metavar="ROUTE_KEY",
        help=(
            "Second LLM route for one retry per task when logs look like a Bedrock/botocore "
            "transport error (read timeout, etc.). Default: "
            "global.anthropic.claude-opus-4-6-v1 (LiteLLM). "
            "Use the same value as --model to disable fallback retries. "
            "Each new task still starts with --model."
        ),
    )
    parser.add_argument(
        "--exp",
        type=str,
        default=None,
        help=(
            "Forwarded to ``mm-devshell run --exp`` when set. Omit this flag (default) to use the "
            "same ``direct`` exp as interactive ``mm-devshell`` (``load_exp_config('direct')``). "
            "Eval tooling snapshots use ``matmaster/exps/{exp}.toml`` (e.g. full "
            "``matmaster/skills`` tree for ``direct``)."
        ),
    )
    parser.add_argument(
        "--slices",
        default=None,
        metavar="EXPR",
        help=(
            "OR-of-slices filter: cap cap[dom] cap[d1,d2] (whitespace separates "
            'slices; no spaces inside "[...]") '
            '(e.g. "workflow_orchestration[polymer] input_generation")'
        ),
    )
    parser.add_argument(
        "--questions",
        nargs="+",
        default=None,
        help="Only run these question IDs",
    )
    parser.add_argument(
        "--exclude-question-ids",
        nargs="+",
        default=None,
        help="Exclude these question IDs from the run (applied after --questions/--slices)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of plan items to run (after expand); for smoke tests",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=3,
        metavar="N",
        help=(
            "Repeat each question N times (repeat_idx 0..N-1); overrides ``k`` in "
            "--eval-config (default: %(default)s)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan only, do not invoke devshell",
    )
    parser.add_argument(
        "--no-clean-results",
        action="store_true",
        help=(
            "Do not delete contents of the repository ``results/`` folder before this run "
            "(default: empty ``results/`` so each run starts from a clean tree)."
        ),
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first non-zero devshell exit code",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="How many tasks to run in parallel (default: 1)",
    )
    parser.add_argument(
        "--verbose",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Whether to pass --verbose to inner ``matmaster.devshell run`` so "
            "INFO-level logs are emitted to terminal / devshell_console.log "
            "(default: on; use --no-verbose to disable)."
        ),
    )
    parser.add_argument(
        "--python",
        type=Path,
        default=None,
        help="Python executable (default: sys.executable)",
    )
    parser.add_argument(
        "--no-export-review",
        action="store_true",
        help="Do not write claude_review.md after the run (default: write it via export_devshell_review_bundle).",
    )
    parser.add_argument(
        "--export-review-with-questions",
        action="store_true",
        help="When writing claude_review.md, include human_prompt_seed from the question bank.",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send Feishu notification with scoring summary after all tasks complete.",
    )
    parser.add_argument(
        "--no-eval-ingest",
        action="store_true",
        help="Disable evaluation ingest (no POST to tools-server ingest API).",
    )
    parser.add_argument(
        "--eval-ingest-pending-only",
        action="store_true",
        help=(
            "Do not POST ingest; write pending_ingest/<task_id>.json with full item except "
            "score. Score later with evaluation/scripts/devshell/score_devshell_tasks.py "
            "(preferred) or manually via eval_ingest_submit_pending.py."
        ),
    )
    parser.add_argument(
        "--eval-ingest-timeout",
        type=float,
        default=30.0,
        help="HTTP timeout seconds for each ingest POST (default: 30).",
    )
    parser.add_argument(
        "--eval-ingest-run-id",
        type=str,
        default=None,
        metavar="UUID",
        help=(
            "Use this value as eval ingest run_id in manifest and pending_ingest "
            "(tools-server groups items by run_id). If omitted, a new UUID is generated. "
            "P0-gate orchestration passes one id for both p0_gate and remaining phases."
        ),
    )
    parser.add_argument(
        "--task-timeout",
        type=float,
        default=1200.0,
        help=(
            "Per-task wall-clock limit in seconds for each mm-devshell subprocess "
            "(default: 1200 = 20 min). Use 0 to disable."
        ),
    )
    parser.add_argument(
        "--eval-ingest-strict",
        action="store_true",
        help="Exit non-zero if ingest fails (default: log warning and continue).",
    )
    parser.add_argument(
        "--prepare-cc-baseline",
        action="store_true",
        help=(
            "Only stage workspaces (prompt + data + _eval_task_meta.json); do not run "
            "mm-devshell. After Claude Code completes each task, run "
            "evaluation/scripts/baseline/finalize_external_baseline_ingest.py on the same run directory."
        ),
    )
    parser.add_argument(
        "--baseline-channel",
        choices=("claude_code", "cursor", "codex"),
        default="claude_code",
        help=(
            "With --prepare-cc-baseline: stored in manifest for ingest "
            "(EvalIngestRequest.baseline_channel; default: claude_code)."
        ),
    )
    parser.add_argument(
        "--exclude-subagents",
        nargs="*",
        default=["verification"],
        metavar="NAME",
        help="Subagent exp names to exclude from Agent tool (default: verification).",
    )
    args = parser.parse_args()

    if args.no_eval_ingest and args.eval_ingest_pending_only:
        print(
            "error: --no-eval-ingest and --eval-ingest-pending-only cannot be used together",
            file=sys.stderr,
        )
        return 2
    if args.prepare_cc_baseline and args.dry_run:
        print(
            "error: --prepare-cc-baseline and --dry-run cannot be used together",
            file=sys.stderr,
        )
        return 2
    if args.jobs < 1:
        print("error: --jobs must be >= 1", file=sys.stderr)
        return 2
    if args.k < 1:
        print("error: --k must be >= 1", file=sys.stderr)
        return 2
    py = args.python or Path(sys.executable)

    sys.path.insert(0, str(REPO_ROOT))
    from evaluation.core.slice_parser import parse_slices_expression
    from evaluation.scripts.devshell.run_devshell_eval_helpers import (
        _cc_baseline_readme_markdown,
        _clean_results_directory,
        _eval_tooling_snapshot_for_exp_cli,
        _merge_eval_config,
        _normalize_mm_devshell_exp_cli,
        build_mm_devshell_run_cmd,
        devshell_console_indicates_provider_fallback,
    )

    slices_override = None
    if args.slices is not None:
        slices_override = [s.model_dump() for s in parse_slices_expression(args.slices)]

    merge_overrides: dict = {
        "question_bank_dir": (
            str(args.question_bank_dir) if args.question_bank_dir else None
        ),
        "include_slices": slices_override,
        "include_question_ids": args.questions,
        "exclude_question_ids": args.exclude_question_ids,
    }
    merge_overrides["k"] = int(args.k)

    cfg_dict = _merge_eval_config(
        args.eval_config if args.eval_config.is_file() else None,
        merge_overrides,
    )

    # Lazy imports after potential chdir
    from evaluation.core.runner import (
        _apply_filters,
        _flatten_banks,
        _resolve_to_project_root,
        _stage_data_files,
        expand_run_plan,
        load_question_banks,
    )
    from evaluation.core.schemas import EvalConfig
    from evaluation.core.simulator import HumanSimulator

    cfg = EvalConfig.model_validate(cfg_dict)
    bank_dir = Path(_resolve_to_project_root(cfg.question_bank_dir))

    question_banks = load_question_banks(bank_dir)
    questions = _flatten_banks(question_banks)
    questions = _apply_filters(questions, cfg)
    run_plan = expand_run_plan(questions=questions, config=cfg)

    if args.limit is not None:
        run_plan = run_plan[: max(0, args.limit)]

    if not run_plan:
        print(
            "No tasks in plan (check filters, --limit).",
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        print(f"Planned tasks: {len(run_plan)}", file=sys.stderr)
        for item in run_plan:
            q = item["question"]
            mode = item["mode"]
            ridx = item["repeat_idx"]
            tid = f"{q.id}_{mode}_r{ridx}"
            print(f"  [dry-run] {tid} capability={q.capability}", file=sys.stderr)
        return 0

    results_root = REPO_ROOT / "results"
    if not args.no_clean_results:
        _clean_results_directory(results_root)
        print(f"Cleaned directory: {results_root}", file=sys.stderr)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = args.output_dir
    if out is None:
        out = REPO_ROOT / "results" / f"{args.run_label}_{ts}"
    else:
        if not out.is_absolute():
            out = (REPO_ROOT / out).resolve()
    run_dir = out
    run_dir.mkdir(parents=True, exist_ok=True)
    raw_path = run_dir / "raw_runs.jsonl"

    from evaluation.eval_ingest_client import (
        EVAL_INGEST_URL,
        build_ingest_item,
        git_head_commit,
        load_devshell_events_timeline,
        post_eval_ingest,
        upload_eval_task_artifacts_to_oss,
    )

    pending_only = args.eval_ingest_pending_only
    ingest_url = None if args.no_eval_ingest else EVAL_INGEST_URL
    if pending_only:
        ingest_url = EVAL_INGEST_URL
        if not (ingest_url or "").strip():
            print(
                "error: --eval-ingest-pending-only requires MATMASTER_TOOLS_SERVER "
                "(or ingest URL) to be set so pending JSON contains ingest_url",
                file=sys.stderr,
            )
            return 2

    if ingest_url:
        rid = (args.eval_ingest_run_id or "").strip()
        eval_ingest_run_id = rid if rid else str(uuid.uuid4())
    else:
        eval_ingest_run_id = str(uuid.uuid4())

    git_commit = git_head_commit(REPO_ROOT)

    exp_cli = _normalize_mm_devshell_exp_cli(args.exp)
    eval_tooling_snapshot = _eval_tooling_snapshot_for_exp_cli(
        repo_root=REPO_ROOT, exp_cli=exp_cli
    )

    manifest: dict[str, Any] = {
        "run_label": args.run_label,
        "started_at_utc": ts,
        "question_bank_dir": str(bank_dir),
        "eval_config": str(args.eval_config),
        "model": args.model,
        "plan_count": len(run_plan),
        "jobs": args.jobs,
        "task_timeout_sec": args.task_timeout,
        "dry_run": False,
        "eval_tooling": eval_tooling_snapshot,
    }
    fb = (args.fallback_model or "").strip()
    if fb:
        manifest["fallback_model"] = fb
    if ingest_url:
        manifest["eval_ingest_url"] = ingest_url
        manifest["eval_ingest_run_id"] = eval_ingest_run_id
        if pending_only:
            manifest["eval_ingest_pending_only"] = True
            manifest["eval_ingest_pending_dir"] = str(run_dir / "pending_ingest")
        if git_commit:
            manifest["git_commit"] = git_commit
    if args.prepare_cc_baseline:
        manifest["prepare_cc_baseline"] = True
        manifest["eval_runner"] = "claude_code_baseline"
        manifest["baseline_channel"] = args.baseline_channel
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    simulator = HumanSimulator(
        llm_cfg=cfg.simulator_llm,
        use_seed_prompt=cfg.use_seed_prompt,
    )

    print(f"Run directory: {run_dir}", file=sys.stderr)
    print(f"Planned tasks: {len(run_plan)}", file=sys.stderr)
    print(f"Parallel jobs: {args.jobs}", file=sys.stderr)
    if fb:
        print(
            "Provider fallback: tasks whose logs look like Bedrock/botocore transport "
            f"errors will retry once with --model {fb}",
            file=sys.stderr,
        )
    if args.task_timeout and args.task_timeout > 0:
        print(
            f"Per-task timeout: {args.task_timeout:g}s ({args.task_timeout / 60:g} min)",
            file=sys.stderr,
        )
    else:
        print("Per-task timeout: disabled", file=sys.stderr)

    any_failed = False
    ingest_failed = False
    env = os.environ.copy()
    # Child stdout is a pipe (not a TTY) → CPython uses block buffering; streaming
    # from DevStreamHook would not appear until buffer fills unless unbuffered.
    env.setdefault("PYTHONUNBUFFERED", "1")
    # Ensure subprocess finds matmaster_config / .env relative to cwd
    cwd = str(REPO_ROOT)
    prepared_tasks: list[dict[str, Any]] = []
    for item in run_plan:
        question = item["question"]
        mode: str = item["mode"]
        repeat_idx: int = item["repeat_idx"]
        task_id = f"{question.id}_{mode}_r{repeat_idx}"

        task = simulator.formulate(question)
        prompt = task.prompt
        workspace_path = run_dir / "workspaces" / task_id
        workspace_path.mkdir(parents=True, exist_ok=True)
        log_dir = run_dir / "logs" / task_id
        log_dir.mkdir(parents=True, exist_ok=True)

        prompt = _stage_data_files(question, bank_dir, workspace_path, prompt)

        prompt_file = workspace_path / "_devshell_prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        summary_file = workspace_path / "_devshell_summary.json"
        console_log_file = log_dir / "devshell_console.log"

        primary_route = (args.model or "").strip() or None
        inject_failure = (
            question.inject_failure_message
            if getattr(question, 'inject_bohrium_failure', False)
            else None
        )
        cmd = build_mm_devshell_run_cmd(
            py=py,
            workspace_path=workspace_path,
            log_dir=log_dir,
            prompt_file=prompt_file,
            summary_file=summary_file,
            model=primary_route,
            exp_cli=exp_cli,
            verbose=bool(args.verbose),
            exclude_subagents=args.exclude_subagents,
            inject_bohrium_failure=inject_failure,
        )

        prepared_tasks.append(
            {
                "question": question,
                "mode": mode,
                "repeat_idx": repeat_idx,
                "task_id": task_id,
                "prompt": prompt,
                "workspace_path": workspace_path,
                "log_dir": log_dir,
                "prompt_file": prompt_file,
                "summary_file": summary_file,
                "console_log_file": console_log_file,
                "cmd": cmd,
                "primary_model": primary_route,
                "fallback_model": fb or None,
                "mm_py": py,
                "exp_cli": exp_cli,
                "verbose": bool(args.verbose),
                "inject_bohrium_failure": inject_failure,
            }
        )

    if args.prepare_cc_baseline:
        doc_rel = "evaluation/docs/baseline/baseline_cc_eval.md"
        for prepared in prepared_tasks:
            q = prepared["question"]
            tid = str(prepared["task_id"])
            meta = {
                "schema": "matmaster_eval_task_meta_v1",
                "task_id": tid,
                "question_id": q.id,
                "capability": q.capability,
                "domain": q.domain,
                "mode": prepared["mode"],
                "repeat_idx": prepared["repeat_idx"],
                "prompt": prepared["prompt"],
            }
            meta_path = Path(prepared["workspace_path"]) / "_eval_task_meta.json"
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        readme = run_dir / "CC_BASELINE.md"
        readme.write_text(
            _cc_baseline_readme_markdown(run_dir=run_dir, doc_rel=doc_rel),
            encoding="utf-8",
        )
        print(
            f"Prepared {len(prepared_tasks)} workspace(s) for Claude Code baseline; see {readme}",
            file=sys.stderr,
        )
        return 0

    def _finalize_task(prepared: dict[str, Any]) -> dict[str, Any]:
        question = prepared["question"]
        task_id = str(prepared["task_id"])
        summary_file = Path(prepared["summary_file"])
        console_log_file = Path(prepared["console_log_file"])
        primary_model = prepared.get("primary_model")
        fallback_model = prepared.get("fallback_model")

        rc, duration_ms, summary = _run_devshell_task(
            cmd=prepared["cmd"],
            cwd=cwd,
            env=env,
            summary_file=summary_file,
            console_log_file=console_log_file,
            timeout_sec=args.task_timeout,
            tee_stderr=args.jobs <= 1,
            console_log_append=False,
        )

        attempts: list[dict[str, Any]] = [
            {
                "model_route": primary_model,
                "devshell_exit_code": rc,
                "duration_ms": duration_ms,
            }
        ]
        used_fallback = False

        if (
            rc != 0
            and fallback_model
            and primary_model != fallback_model
            and devshell_console_indicates_provider_fallback(console_log_file)
        ):
            print(
                f"  [provider-fallback] {task_id} retry once with --model {fallback_model}",
                file=sys.stderr,
                flush=True,
            )
            try:
                if summary_file.is_file():
                    summary_file.unlink()
            except OSError:
                pass
            console_log_file.parent.mkdir(parents=True, exist_ok=True)
            with console_log_file.open("a", encoding="utf-8") as bf:
                bf.write(
                    "\n\n===== mm-devshell retry (provider transport fallback) "
                    f"model={fallback_model} =====\n\n"
                )

            cmd_fb = build_mm_devshell_run_cmd(
                py=prepared["mm_py"],
                workspace_path=Path(prepared["workspace_path"]),
                log_dir=Path(prepared["log_dir"]),
                prompt_file=Path(prepared["prompt_file"]),
                summary_file=summary_file,
                model=fallback_model,
                exp_cli=prepared["exp_cli"],
                verbose=bool(prepared["verbose"]),
                exclude_subagents=args.exclude_subagents,
                inject_bohrium_failure=prepared.get("inject_bohrium_failure"),
            )
            rc2, d2, summary2 = _run_devshell_task(
                cmd=cmd_fb,
                cwd=cwd,
                env=env,
                summary_file=summary_file,
                console_log_file=console_log_file,
                timeout_sec=args.task_timeout,
                tee_stderr=args.jobs <= 1,
                console_log_append=True,
            )
            used_fallback = True
            duration_ms = duration_ms + d2
            rc = rc2
            summary = summary2
            attempts.append(
                {
                    "model_route": fallback_model,
                    "devshell_exit_code": rc2,
                    "duration_ms": d2,
                }
            )

        row: dict[str, Any] = {
            "task_id": task_id,
            "question_id": question.id,
            "capability": question.capability,
            "domain": question.domain,
            "mode": prepared["mode"],
            "repeat_idx": prepared["repeat_idx"],
            "devshell_exit_code": rc,
            "devshell_summary_path": str(summary_file),
            "devshell_summary": summary,
            "duration_ms": duration_ms,
            "devshell_console_log_path": str(console_log_file),
            "llm_route_attempts": attempts,
            "llm_model_route_used": attempts[-1]["model_route"],
            "llm_provider_fallback_used": used_fallback,
        }

        ingest_status: dict[str, Any] | None = None
        ingest_failed_local = False
        if ingest_url:
            artifact = upload_eval_task_artifacts_to_oss(run_dir, task_id)
            events_tl = load_devshell_events_timeline(run_dir / "logs" / task_id)
            ingest_item = build_ingest_item(
                question_id=question.id,
                task_id=task_id,
                mode=str(prepared["mode"]),
                repeat_idx=int(prepared["repeat_idx"]),
                devshell_exit_code=rc,
                summary=summary if isinstance(summary, dict) else {},
                duration_ms=duration_ms,
                artifact=artifact,
                eval_tooling=eval_tooling_snapshot,
                events_timeline=events_tl,
            )
            if pending_only:
                pending_dir = run_dir / "pending_ingest"
                pending_dir.mkdir(parents=True, exist_ok=True)
                pend_path = pending_dir / f"{task_id}.json"
                item_body = {k: v for k, v in ingest_item.items() if k != "score"}
                envelope: dict[str, Any] = {
                    "schema": "matmaster_eval_pending_ingest_v1",
                    "ingest_url": ingest_url,
                    "run_id": eval_ingest_run_id,
                    "run_kind": "iteration",
                    "task_id": task_id,
                    "instructions_zh": (
                        "推荐在仓库根执行自动评分脚本: uv run python "
                        "evaluation/scripts/devshell/score_devshell_tasks.py "
                        f"--run-dir {run_dir} --tasks {task_id} --submit 。"
                        "若仅需手动补 suggestion，才再用: uv run python "
                        f"evaluation/scripts/eval_ingest_submit_pending.py --pending {pend_path} "
                        '--score <已有分数> [--suggestion "..."]'
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
                ingest_status = {
                    "kind": "pending",
                    "path": str(pend_path),
                }
            else:
                body: dict[str, Any] = {
                    "run_id": eval_ingest_run_id,
                    "run_kind": "iteration",
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
                ingest_status = {
                    "kind": "posted",
                    "ok": ok,
                    "message": msg,
                }
                ingest_failed_local = not ok

        return {
            "task_id": task_id,
            "rc": rc,
            "row": row,
            "ingest_status": ingest_status,
            "ingest_failed": ingest_failed_local,
        }

    def _emit_completion(result: dict[str, Any]) -> None:
        task_id = str(result["task_id"])
        ingest_status = result.get("ingest_status")
        if isinstance(ingest_status, dict):
            if ingest_status.get("kind") == "pending":
                pend_path = Path(str(ingest_status["path"]))
                rel = pend_path.relative_to(run_dir)
                print(
                    f"  [ingest-pending] {task_id} wrote {rel}",
                    file=sys.stderr,
                    flush=True,
                )
            elif ingest_status.get("kind") == "posted":
                if bool(ingest_status.get("ok")):
                    print(
                        f"  [ingest] {task_id} ok ({ingest_status.get('message')})",
                        file=sys.stderr,
                        flush=True,
                    )
                else:
                    print(
                        f"  [ingest] {task_id} failed: {ingest_status.get('message')}",
                        file=sys.stderr,
                        flush=True,
                    )
        status = "ok" if int(result["rc"]) == 0 else "fail"
        print(
            f"  [{status}] {task_id} exit={result['rc']}", file=sys.stderr, flush=True
        )

    if args.jobs == 1:
        for prepared in prepared_tasks:
            print(
                f"  [running] {prepared['task_id']} (terminal + "
                f"{Path(prepared['console_log_file']).name}; summary -> "
                f"{Path(prepared['summary_file']).name})...",
                file=sys.stderr,
                flush=True,
            )
            result = _finalize_task(prepared)
            with raw_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(result["row"], ensure_ascii=False) + "\n")
            if bool(result["ingest_failed"]):
                ingest_failed = True
            if int(result["rc"]) != 0:
                any_failed = True
                _emit_completion(result)
                if args.fail_fast:
                    break
            else:
                _emit_completion(result)
    else:
        if args.fail_fast:
            print(
                "Fail-fast in parallel mode stops scheduling new tasks after the first failure; already running tasks will finish.",
                file=sys.stderr,
                flush=True,
            )
        task_iter = iter(prepared_tasks)
        stop_scheduling = False
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            inflight: dict[
                concurrent.futures.Future[dict[str, Any]], dict[str, Any]
            ] = {}

            def _submit_next() -> bool:
                if stop_scheduling:
                    return False
                try:
                    prepared = next(task_iter)
                except StopIteration:
                    return False
                console_log = prepared["console_log_file"]
                detail = (
                    f"console -> {Path(console_log).name}, "
                    f"summary -> {Path(prepared['summary_file']).name}"
                )
                print(
                    f"  [queued] {prepared['task_id']} ({detail})...",
                    file=sys.stderr,
                    flush=True,
                )
                future = pool.submit(_finalize_task, prepared)
                inflight[future] = prepared
                return True

            for _ in range(args.jobs):
                if not _submit_next():
                    break

            while inflight:
                done, _ = concurrent.futures.wait(
                    inflight,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in done:
                    prepared = inflight.pop(future)
                    try:
                        result = future.result()
                    except Exception as exc:  # noqa: BLE001
                        result = {
                            "task_id": str(prepared["task_id"]),
                            "rc": 1,
                            "row": {
                                "task_id": str(prepared["task_id"]),
                                "question_id": prepared["question"].id,
                                "capability": prepared["question"].capability,
                                "domain": prepared["question"].domain,
                                "mode": prepared["mode"],
                                "repeat_idx": prepared["repeat_idx"],
                                "devshell_exit_code": 1,
                                "devshell_summary_path": str(prepared["summary_file"]),
                                "devshell_summary": {
                                    "parse_error": True,
                                    "error": f"task runner exception: {exc}",
                                },
                                "duration_ms": 0,
                            },
                            "ingest_status": None,
                            "ingest_failed": False,
                        }
                    with raw_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(result["row"], ensure_ascii=False) + "\n")
                    if bool(result["ingest_failed"]):
                        ingest_failed = True
                    if int(result["rc"]) != 0:
                        any_failed = True
                        if args.fail_fast:
                            stop_scheduling = True
                    _emit_completion(result)
                while len(inflight) < args.jobs and not stop_scheduling:
                    if not _submit_next():
                        break

    print(f"Wrote {raw_path}", file=sys.stderr)

    if not args.no_export_review:
        export_script = (
            REPO_ROOT
            / "evaluation"
            / "scripts"
            / "devshell"
            / "export_devshell_review_bundle.py"
        )
        review_md = run_dir / "claude_review.md"
        export_cmd: list[str | Path] = [
            py,
            export_script,
            "--run-dir",
            run_dir,
            "--out",
            review_md,
        ]
        if args.export_review_with_questions:
            export_cmd.append("--with-questions")
        er = subprocess.run(export_cmd, cwd=cwd, env=env)
        if er.returncode == 0:
            print(f"Wrote claude_review: {review_md}", file=sys.stderr)
        else:
            print(
                "Warning: claude_review.md export failed; run manually:\n"
                f"  uv run python evaluation/scripts/devshell/export_devshell_review_bundle.py --run-dir {run_dir}",
                file=sys.stderr,
            )
    else:
        print(
            f"Pack for Claude (skipped): uv run python evaluation/scripts/devshell/export_devshell_review_bundle.py --run-dir {run_dir}",
            file=sys.stderr,
        )

    if args.notify:
        from evaluation.devshell_agent.feishu_round_notify import (
            notify_after_scoring_async,
        )

        notify_after_scoring_async(
            run_dir=run_dir,
            ingest_result={
                "attempted": True,
                "ok": not ingest_failed,
                "stderr_tail": "",
            },
        )

    if any_failed:
        return 1
    if args.eval_ingest_strict and ingest_failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
