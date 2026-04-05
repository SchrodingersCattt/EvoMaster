#!/usr/bin/env python3
"""Programmatic scoring of external baseline tasks using the same BinaryEvaluator
as the MatMaster auto-evaluation pipeline.

After phase 1 (the baseline agent completes tasks and ``finalize_external_baseline_ingest.py``
has been run), this script replaces the manual phase 2 (human/LLM reading workspace files
and assigning scores). Instead it:

1. Loads the question bank to find the ``QuestionItem`` for each task.
2. Builds a minimal ``EvidenceBundle`` from the workspace directory and
   ``_devshell_summary.json`` (no EvoMaster trajectory is available).
3. Runs ``BinaryEvaluator.evaluate()`` — which calls the same validators used by
   MatMaster (pymatgen-backed ``struct_file_*``, ``llm_binary_judge``, etc.).
4. Converts the ``EvalRunRecord`` to a 0-100 integer score + per-criterion
   ``score_reason``.
5. Either prints a dry-run report, writes scores back to ``pending_ingest/*.json``
   for deferred submission, or submits directly to the ingest API.

Usage::

    # Dry-run: print scores without submitting
    uv run python evaluation/scripts/baseline/score_baseline_tasks.py \\
        --run-dir "$RUN_DIR" --dry-run

    # Write scores into pending_ingest/*.json (still needs eval_ingest_submit_pending.py)
    uv run python evaluation/scripts/baseline/score_baseline_tasks.py \\
        --run-dir "$RUN_DIR"

    # Auto-submit to ingest API
    uv run python evaluation/scripts/baseline/score_baseline_tasks.py \\
        --run-dir "$RUN_DIR" --submit

    # With explicit eval config (for evaluator_llm needed by llm_binary_judge)
    uv run python evaluation/scripts/baseline/score_baseline_tasks.py \\
        --run-dir "$RUN_DIR" --eval-config evaluation/config.yaml --submit

    # Run only specific tasks
    uv run python evaluation/scripts/baseline/score_baseline_tasks.py \\
        --run-dir "$RUN_DIR" --tasks SC_struct_007_direct_r0 SC_struct_008_direct_r0

Notes
-----
* verify types that require trajectory data (``tool_called``, ``tool_args_match``,
  ``batch_*``, ``event_type_called``, ``source_type_used``, ``no_retries``)
  will naturally fail with reason ``no trajectory available for baseline``.
  This is intentional — these grounding/efficiency checks measure use of MatMaster
  tools and should not pass for external baselines.
* ``struct_file_*`` validators use pymatgen to read CIF/POSCAR in the workspace.
  Install optional deps: ``uv sync --extra calculation``.
* ``llm_binary_judge`` requires ``evaluator_llm`` in config (or env vars for the LLM).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Imports (after sys.path setup)
# ---------------------------------------------------------------------------

import yaml  # noqa: E402

from evaluation.core.evaluator import BinaryEvaluator  # noqa: E402
from evaluation.core.evaluator_helpers import (  # noqa: E402
    token_usage_record_from_evidence,
)
from evaluation.core.evidence import (  # noqa: E402
    EvidenceBundle,
    TokenUsage,
    approximate_last_turn_usage_from_run_summary,
)
from evaluation.core.runner import load_question_banks  # noqa: E402
from evaluation.core.schemas import (  # noqa: E402
    LLMRuntimeConfig,
    QuestionItem,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_QUESTION_BANK_DIR = REPO_ROOT / "evaluation" / "question_bank"
_TRAJECTORY_DEPENDENT_VERIFY = frozenset(
    {
        "tool_called",
        "tool_args_match",
        "tool_observation_field",
        "event_type_called",
        "source_type_used",
        "call_count_range",
        "no_retries",
        "artifact_exists",
        "batch_single_variable_sweep",
        "batch_tool_args_constant",
        "batch_consistent_calls",
    }
)

# Meta files written by the framework — exclude from workspace file listing
_META_FILENAMES = frozenset(
    {
        "_eval_task_meta.json",
        "_devshell_summary.json",
        "_devshell_prompt.txt",
        "_cc_baseline_task_start.json",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_eval_config(config_path: Path | None) -> dict[str, Any]:
    """Load evaluation config YAML, falling back to defaults."""
    default_config: dict[str, Any] = {
        "axis_weights": {"correctness": 1.0, "grounding": 1.0, "efficiency": 1.0},
    }
    if config_path is None or not config_path.is_file():
        return default_config
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return default_config
    return raw


def _build_evaluator_llm_cfg(
    config: dict[str, Any],
) -> LLMRuntimeConfig | None:
    """Build LLMRuntimeConfig from eval config dict (resolves env vars)."""
    raw = config.get("evaluator_llm")
    if not isinstance(raw, dict):
        return None
    # Resolve ${ENV_VAR} placeholders
    resolved: dict[str, Any] = {}
    for k, v in raw.items():
        if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
            env_key = v[2:-1]
            resolved[k] = os.environ.get(env_key, "")
        else:
            resolved[k] = v
    provider = resolved.get("provider", "openai")
    model = resolved.get("model", "")
    api_key = resolved.get("api_key", "")
    if not model or not api_key:
        return None
    return LLMRuntimeConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=resolved.get("base_url") or None,
        temperature=float(resolved.get("temperature", 0.0)),
        max_tokens=int(resolved.get("max_tokens", 4096)),
        timeout=int(resolved.get("timeout", 180)),
    )


def _build_question_map(bank_dir: Path) -> dict[str, QuestionItem]:
    """Load all question banks and return a {question_id: QuestionItem} map."""
    banks = load_question_banks(bank_dir)
    return {q.id: q for bank in banks for q in bank.questions}


def _load_summary(workspace: Path) -> dict[str, Any]:
    """Load ``_devshell_summary.json`` from workspace, return {} on error."""
    path = workspace / "_devshell_summary.json"
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8").strip()
        last_line = text.splitlines()[-1].strip() if text else "{}"
        result = json.loads(last_line)
        return result if isinstance(result, dict) else {}
    except (OSError, json.JSONDecodeError, IndexError):
        return {}


def _compute_duration_ms(workspace: Path, summary_path: Path) -> int:
    """Compute wall-clock duration from _cc_baseline_task_start.json → summary mtime."""
    start_path = workspace / "_cc_baseline_task_start.json"
    if not start_path.is_file() or not summary_path.is_file():
        return 0
    try:
        raw = json.loads(start_path.read_text(encoding="utf-8"))
        started = int(raw.get("started_at_unix_ms", 0))
        if started <= 0:
            return 0
        end_ms = int(summary_path.stat().st_mtime * 1000)
        delta = end_ms - started
        return max(0, delta)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0


def _build_workspace_file_listing(workspace: Path) -> str:
    """Return a newline-separated listing of non-meta files in workspace."""
    files = []
    for p in sorted(workspace.iterdir()):
        if p.is_file() and p.name not in _META_FILENAMES:
            size = p.stat().st_size
            files.append(f"  {p.name} ({size} bytes)")
    if not files:
        return "(no deliverable files found in workspace)"
    return "\n".join(files)


def _build_answer(workspace: Path, summary: dict[str, Any]) -> str:
    """Construct the answer string from available baseline data.

    Concatenates:
    - The agent's self-reported summary (final_content from _devshell_summary.json)
    - A workspace file listing (for context in llm_binary_judge)
    """
    parts: list[str] = []

    final_content = summary.get("final_content", "")
    if final_content and isinstance(final_content, str):
        parts.append(final_content.strip())

    file_listing = _build_workspace_file_listing(workspace)
    parts.append(f"\n[Workspace deliverable files]\n{file_listing}")

    return "\n\n".join(p for p in parts if p)


def _build_evidence(
    *,
    task_id: str,
    workspace: Path,
    summary: dict[str, Any],
    answer: str,
) -> EvidenceBundle:
    """Build a minimal EvidenceBundle from workspace + summary data."""
    summary_path = workspace / "_devshell_summary.json"
    duration_ms = _compute_duration_ms(workspace, summary_path)

    # Build token usage from summary (last vendor round vs whole-run aggregate)
    usage_raw = summary.get("usage", {})
    if isinstance(usage_raw, dict):
        summary_usage = TokenUsage.from_usage_dict(usage_raw)
    else:
        summary_usage = TokenUsage()

    last_turn_usage = summary_usage
    uvt = summary.get("usage_vendor_by_turn")
    if isinstance(uvt, list) and uvt:
        ld = uvt[-1]
        if isinstance(ld, dict) and ld:
            last_turn_usage = TokenUsage.from_usage_dict(ld)
    else:
        approx_last_turn = approximate_last_turn_usage_from_run_summary(
            usage_raw if isinstance(usage_raw, dict) else None,
            summary.get("num_turns"),
        )
        if approx_last_turn is not None:
            last_turn_usage = approx_last_turn

    # Collect workspace artifacts for artifact_exists checks
    from evaluation.core.evidence import ArtifactRecord

    artifacts = []
    for p in sorted(workspace.iterdir()):
        if p.is_file() and p.name not in _META_FILENAMES:
            artifacts.append(
                ArtifactRecord(
                    path=p.name,
                    artifact_type=p.suffix.lstrip(".").lower() or "unknown",
                    size_bytes=p.stat().st_size,
                )
            )

    model_name = summary.get("model")

    return EvidenceBundle(
        task_id=task_id,
        final_answer=answer,
        workspace_dir=str(workspace.resolve()),
        duration_ms=duration_ms,
        token_usage_last_turn=last_turn_usage,
        token_usage_run=summary_usage,
        artifacts=artifacts,
        model_name=model_name if isinstance(model_name, str) else None,
        # No events or tool_calls — trajectory-dependent checks will fail gracefully
        events=[],
        tool_calls=[],
    )


_AXIS_SECTION_ORDER = ("correctness", "grounding", "efficiency")


def _format_score_reason(record: Any) -> str:
    """Format per-criterion results into a Markdown score_reason string."""
    by_axis: dict[str, list[tuple[str, Any]]] = {}
    for cid, result in record.criteria_results.items():
        by_axis.setdefault(result.axis, []).append((cid, result))
    for axis in by_axis:
        by_axis[axis].sort(key=lambda x: x[0])

    ordered_axes: list[str] = []
    for a in _AXIS_SECTION_ORDER:
        if a in by_axis:
            ordered_axes.append(a)
    for a in sorted(by_axis.keys()):
        if a not in _AXIS_SECTION_ORDER:
            ordered_axes.append(a)

    lines: list[str] = []
    for axis in ordered_axes:
        title = axis.replace("_", " ").title()
        lines.append(f"### {title}")
        lines.append("")
        for cid, result in by_axis[axis]:
            status = "✓ pass" if result.passed else "✗ fail"
            lines.append(
                f"- **`{cid}`** (`{result.verify_method}`): {status} — {result.reason}"
            )
        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()

    lines.append("")
    lines.append(
        f"**Overall weighted score:** {record.overall_weighted_score:.3f} "
        f"({record.passed_count}/{record.total_count} criteria passed)"
    )
    return "\n".join(lines)


def _score_to_int(record: Any) -> int:
    """Convert EvalRunRecord overall_weighted_score to 0-100 integer."""
    return round(record.overall_weighted_score * 100)


# ---------------------------------------------------------------------------
# Per-task scoring
# ---------------------------------------------------------------------------


def score_task(
    *,
    task_id: str,
    workspace: Path,
    question: QuestionItem,
    evaluator: BinaryEvaluator,
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Score one baseline task workspace using BinaryEvaluator.

    Returns a dict with keys:
    - task_id, question_id, score (0-100 int), score_reason (str),
      record (EvalRunRecord), error (str|None)
    """
    summary = _load_summary(workspace)
    answer = _build_answer(workspace, summary)
    evidence = _build_evidence(
        task_id=task_id,
        workspace=workspace,
        summary=summary,
        answer=answer,
    )

    usage_record = token_usage_record_from_evidence(evidence)

    mode = str(meta.get("mode", "direct"))
    repeat_idx = int(meta.get("repeat_idx", 0))
    run_status = "completed" if summary.get("status") == "completed" else "failed"

    try:
        record = evaluator.evaluate(
            question=question,
            answer=answer,
            tool_calls=[],  # No trajectory — tool-dependent checks fail gracefully
            evidence=evidence,
            mode=mode,
            repeat_idx=repeat_idx,
            prompt=str(meta.get("prompt", "")),
            run_status=run_status,
            model_name=evidence.model_name,
            token_usage=usage_record,
            duration_ms=evidence.duration_ms,
        )
    except Exception as exc:
        return {
            "task_id": task_id,
            "question_id": question.id,
            "score": 0,
            "score_reason": f"BinaryEvaluator raised an exception: {exc}",
            "record": None,
            "error": str(exc),
        }

    score = _score_to_int(record)
    score_reason = _format_score_reason(record)

    return {
        "task_id": task_id,
        "question_id": question.id,
        "score": score,
        "score_reason": score_reason,
        "record": record,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Pending ingest update
# ---------------------------------------------------------------------------


def _update_pending_with_score(
    pending_path: Path,
    score: int,
    score_reason: str,
) -> bool:
    """Write score + score_reason into a pending_ingest/*.json file."""
    try:
        envelope = json.loads(pending_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  [error] cannot read {pending_path.name}: {exc}", file=sys.stderr)
        return False

    item = envelope.get("item", {})
    if not isinstance(item, dict):
        item = {}
        envelope["item"] = item

    item["score"] = score
    item["score_reason"] = score_reason[:16384]
    item["auto_scored"] = True
    item["auto_scorer"] = "BinaryEvaluator"

    # Strip the large instructions_zh field now that scoring is done
    envelope.pop("instructions_zh", None)

    try:
        pending_path.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return True
    except OSError as exc:
        print(f"  [error] cannot write {pending_path.name}: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Submit helpers
# ---------------------------------------------------------------------------


def _submit_pending(
    pending_path: Path,
    *,
    timeout: float = 120.0,
) -> tuple[bool, str]:
    """POST a scored pending_ingest JSON to the ingest API."""
    try:
        envelope = json.loads(pending_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"read error: {exc}"

    ingest_url = envelope.get("ingest_url", "").strip()
    if not ingest_url:
        return False, "missing ingest_url in pending file"

    from evaluation.eval_ingest_client import post_eval_ingest

    item = envelope.get("item", {})
    if "score" not in item:
        return False, "pending item has no score — run scoring first"

    run_id = envelope.get("run_id", "")
    run_kind = envelope.get("run_kind", "baseline")
    baseline_channel = envelope.get("baseline_channel")

    body: dict[str, Any] = {
        "run_id": run_id,
        "run_kind": run_kind,
        "items": [item],
    }
    if baseline_channel:
        body["baseline_channel"] = baseline_channel

    return post_eval_ingest(ingest_url, body, timeout=timeout)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Score baseline task workspaces using BinaryEvaluator "
            "(same validators as MatMaster auto-evaluation)."
        )
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="Run directory (contains workspaces/ and pending_ingest/).",
    )
    parser.add_argument(
        "--run-label",
        type=str,
        default=None,
        help=(
            "Auto-detect RUN_DIR from results/<run-label>_* (latest match). "
            "Used when --run-dir is not given."
        ),
    )
    parser.add_argument(
        "--eval-config",
        type=Path,
        default=None,
        help=(
            "Path to evaluation config YAML (for evaluator_llm used by "
            "llm_binary_judge). Defaults to evaluation/config.yaml."
        ),
    )
    parser.add_argument(
        "--question-bank-dir",
        type=Path,
        default=None,
        help="Override question bank directory (default: evaluation/question_bank).",
    )
    parser.add_argument(
        "--tasks",
        nargs="*",
        default=None,
        help="Only score these task IDs (space-separated). Scores all by default.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print scores/reasons without modifying pending files or submitting.",
    )
    parser.add_argument(
        "--submit",
        action="store_true",
        help="After scoring, submit each pending file to the ingest API.",
    )
    parser.add_argument(
        "--eval-ingest-timeout",
        type=float,
        default=120.0,
        help="HTTP timeout seconds for each ingest POST (default: 120).",
    )
    parser.add_argument(
        "--score-jobs",
        type=int,
        default=4,
        metavar="N",
        help=(
            "Parallel threads for scoring distinct tasks (default: 4). "
            "Each task uses its own BinaryEvaluator when N>1."
        ),
    )
    parser.add_argument(
        "--parallel-checklist-workers",
        type=int,
        default=8,
        metavar="N",
        help=(
            "Max parallel threads for scoring_checklist items per task (default: 8). "
            "LLM judge calls on one client are serialized with a lock."
        ),
    )
    args = parser.parse_args()

    # --- Resolve RUN_DIR ---
    if args.run_dir is not None:
        run_dir = args.run_dir.resolve()
    elif args.run_label:
        results_dir = REPO_ROOT / "results"
        candidates = sorted(
            [d for d in results_dir.glob(f"{args.run_label}_*") if d.is_dir()]
        )
        if not candidates:
            print(
                f"error: no results/{args.run_label}_* directory found",
                file=sys.stderr,
            )
            return 1
        run_dir = candidates[-1].resolve()
        print(f"Auto-detected RUN_DIR: {run_dir}", file=sys.stderr)
    else:
        parser.error("provide --run-dir or --run-label")
        return 2

    if not run_dir.is_dir():
        print(f"error: not a directory: {run_dir}", file=sys.stderr)
        return 1

    workspaces_root = run_dir / "workspaces"
    if not workspaces_root.is_dir():
        print(
            f"error: missing workspaces/ under {run_dir}",
            file=sys.stderr,
        )
        return 1

    pending_dir = run_dir / "pending_ingest"

    # --- Load eval config ---
    config_path = args.eval_config
    if config_path is None:
        config_path = REPO_ROOT / "evaluation" / "config.yaml"
    config = _load_eval_config(config_path)
    llm_cfg = _build_evaluator_llm_cfg(config)
    axis_weights: dict[str, float] = config.get(
        "axis_weights", {"correctness": 1.0, "grounding": 1.0, "efficiency": 1.0}
    )
    if llm_cfg is None:
        print(
            "warning: no evaluator_llm configured — llm_binary_judge checks will "
            "return (False, 'no evaluator LLM configured')",
            file=sys.stderr,
        )

    score_jobs = max(1, int(args.score_jobs))
    parallel_checklist = max(1, int(args.parallel_checklist_workers))

    def _build_evaluator() -> BinaryEvaluator:
        return BinaryEvaluator(
            llm_cfg=llm_cfg,
            axis_weights=axis_weights,
            parallel_checklist_workers=parallel_checklist,
        )

    # --- Load question bank ---
    bank_dir = args.question_bank_dir or _QUESTION_BANK_DIR
    try:
        question_map = _build_question_map(Path(bank_dir))
    except Exception as exc:
        print(f"error: failed to load question banks: {exc}", file=sys.stderr)
        return 1
    print(
        f"Loaded {len(question_map)} questions from {bank_dir}",
        file=sys.stderr,
    )

    # --- Enumerate task workspaces ---
    tasks: list[tuple[str, Path]] = []
    for ws in sorted(workspaces_root.iterdir()):
        if not ws.is_dir():
            continue
        meta_path = ws / "_eval_task_meta.json"
        if not meta_path.is_file():
            continue
        tasks.append((ws.name, ws))

    if not tasks:
        print(
            f"error: no task workspaces with _eval_task_meta.json under {workspaces_root}",
            file=sys.stderr,
        )
        return 1

    # Apply --tasks filter
    if args.tasks:
        task_filter = set(args.tasks)
        tasks = [(tid, ws) for tid, ws in tasks if tid in task_filter]
        if not tasks:
            print(
                f"error: none of --tasks {args.tasks} matched workspaces",
                file=sys.stderr,
            )
            return 1

    print(
        f"Scoring {len(tasks)} task(s) in {run_dir} "
        f"(score-jobs={score_jobs}, parallel-checklist-workers={parallel_checklist})",
        file=sys.stderr,
    )

    # --- Score each task ---
    results: list[dict[str, Any]] = []
    n_ok = 0
    n_err = 0

    skip_tasks: dict[str, str] = {}
    scored_by_task: dict[str, dict[str, Any]] = {}
    meta_by_task: dict[str, dict[str, Any]] = {}

    if score_jobs <= 1:
        evaluator = _build_evaluator()
        for task_id, workspace in tasks:
            meta_path = workspace / "_eval_task_meta.json"
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                skip_tasks[task_id] = f"bad _eval_task_meta.json: {exc}"
                continue

            qid = meta.get("question_id", "")
            question = question_map.get(qid)
            if question is None:
                skip_tasks[task_id] = f"question_id {qid!r} not found in bank"
                continue

            meta_by_task[task_id] = meta
            scored_by_task[task_id] = score_task(
                task_id=task_id,
                workspace=workspace,
                question=question,
                evaluator=evaluator,
                meta=meta,
            )
    else:

        def _score_one(
            task_id: str,
            workspace: Path,
        ) -> tuple[str, dict[str, Any] | None, str, dict[str, Any] | None]:
            meta_path = workspace / "_eval_task_meta.json"
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return task_id, None, f"bad _eval_task_meta.json: {exc}", None

            qid = meta.get("question_id", "")
            question = question_map.get(qid)
            if question is None:
                return task_id, None, f"question_id {qid!r} not found in bank", None

            ev = _build_evaluator()
            res = score_task(
                task_id=task_id,
                workspace=workspace,
                question=question,
                evaluator=ev,
                meta=meta,
            )
            return task_id, res, "", meta

        with ThreadPoolExecutor(max_workers=score_jobs) as pool:
            future_map = {pool.submit(_score_one, tid, ws): tid for tid, ws in tasks}
            for fut in as_completed(future_map):
                tid, res, reason, meta = fut.result()
                if res is None:
                    skip_tasks[tid] = reason
                else:
                    scored_by_task[tid] = res
                    meta_by_task[tid] = meta  # meta set whenever res is not None

    for task_id, _workspace in tasks:
        if task_id in skip_tasks:
            print(
                f"  [skip] {task_id}: {skip_tasks[task_id]}",
                file=sys.stderr,
            )
            n_err += 1
            continue

        result = scored_by_task[task_id]
        meta = meta_by_task[task_id]
        qid = meta.get("question_id", "")
        results.append(result)

        score = result["score"]
        err = result["error"]

        if err:
            print(f"  [error] {task_id}: {err}", file=sys.stderr)
            n_err += 1
        else:
            n_ok += 1
            print(
                f"  [scored] {task_id}: {score}/100 " f"(q={qid})",
                file=sys.stderr,
            )

        if args.dry_run:
            print(f"\n--- {task_id} score_reason ---")
            print(result["score_reason"])
            print()
            continue

        # Write score into pending_ingest/<task_id>.json
        pending_path = pending_dir / f"{task_id}.json"
        if pending_path.is_file():
            ok = _update_pending_with_score(
                pending_path,
                score=result["score"],
                score_reason=result["score_reason"],
            )
            if ok:
                print(
                    f"  [pending] updated {pending_path.name}",
                    file=sys.stderr,
                )
            if args.submit and ok and not err:
                submit_ok, msg = _submit_pending(
                    pending_path,
                    timeout=args.eval_ingest_timeout,
                )
                if submit_ok:
                    print(
                        f"  [ingest] {task_id} ok ({msg})",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"  [ingest] {task_id} failed: {msg}",
                        file=sys.stderr,
                    )
        else:
            if args.submit:
                print(
                    f"  [warn] {task_id}: no pending_ingest/{task_id}.json — "
                    f"run finalize_external_baseline_ingest.py first",
                    file=sys.stderr,
                )

    # --- Summary table ---
    if results:
        print(
            "\n{:<45} {:>8} {:>8}".format("task_id", "score", "status"),
            file=sys.stderr,
        )
        print("-" * 65, file=sys.stderr)
        scores_valid = []
        for r in results:
            status = "error" if r["error"] else "ok"
            print(
                "{:<45} {:>8} {:>8}".format(r["task_id"], r["score"], status),
                file=sys.stderr,
            )
            if not r["error"]:
                scores_valid.append(r["score"])
        if scores_valid:
            avg = round(sum(scores_valid) / len(scores_valid))
            print(
                "\n  Average score: {}/100 ({} task(s))".format(avg, len(scores_valid)),
                file=sys.stderr,
            )
        print(
            f"\nDone: {n_ok} scored, {n_err} errors",
            file=sys.stderr,
        )
        if args.dry_run:
            print(
                "(dry-run: no files modified, no submissions made)",
                file=sys.stderr,
            )

    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
