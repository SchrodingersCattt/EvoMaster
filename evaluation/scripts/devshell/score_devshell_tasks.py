#!/usr/bin/env python3
"""Programmatic scoring of devshell task workspaces using BinaryEvaluator.

This is the devshell counterpart of
``evaluation/scripts/baseline/score_baseline_tasks.py``:

1. Load ``raw_runs.jsonl`` for question/task metadata.
2. Rebuild evaluator evidence from ``workspaces/<task_id>/`` and
   ``logs/<task_id>/events_*.jsonl``.
3. Run ``BinaryEvaluator`` with the same validator stack used by MATTER.
4. Write score / score_reason back to ``pending_ingest/*.json`` or submit them.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from evaluation.core.evaluator import BinaryEvaluator
from evaluation.core.evaluator_helpers import token_usage_record_from_evidence
from evaluation.core.evidence import (
    ArtifactRecord,
    EventRecord,
    EvidenceBundle,
    EvidenceExtractor,
    TokenUsage,
    ToolCallRecord,
)
from evaluation.core.schemas import LLMRuntimeConfig, QuestionItem
from evaluation.scripts.baseline.score_baseline_tasks import (
    _build_evaluator_llm_cfg,
    _build_question_map,
    _load_eval_config,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_QUESTION_BANK_DIR = REPO_ROOT / "evaluation" / "question_bank"
_EVIDENCE_MAPPING_PATH = REPO_ROOT / "evaluation" / "core" / "evidence_mapping.yaml"
_META_FILENAMES = frozenset(
    {
        "_eval_task_meta.json",
        "_devshell_summary.json",
        "_devshell_prompt.txt",
        "_cc_baseline_task_start.json",
    }
)


def _build_workspace_file_listing(workspace: Path) -> str:
    files = []
    for p in sorted(workspace.iterdir()):
        if p.is_file() and p.name not in _META_FILENAMES:
            files.append(f"  {p.name} ({p.stat().st_size} bytes)")
    if not files:
        return "(no deliverable files found in workspace)"
    return "\n".join(files)


def _build_answer(workspace: Path, summary: dict[str, Any]) -> str:
    parts: list[str] = []
    final_content = summary.get("final_content", "")
    if isinstance(final_content, str) and final_content.strip():
        parts.append(final_content.strip())
    parts.append(
        f"\n[Workspace deliverable files]\n{_build_workspace_file_listing(workspace)}"
    )
    return "\n\n".join(p for p in parts if p)


def _load_raw_run_rows(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return rows
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("task_id"):
            rows[str(row["task_id"])] = row
    return rows


def _load_latest_events_log(log_dir: Path) -> Path | None:
    if not log_dir.is_dir():
        return None
    candidates = sorted(p for p in log_dir.glob("events_*.jsonl") if p.is_file())
    return candidates[-1] if candidates else None


def _normalize_tool_name(tool_name: str) -> str:
    mapping = {
        "bash": "execute_bash",
        "edit_file": "str_replace_editor",
    }
    return mapping.get(tool_name, tool_name)


def _tool_calls_payload(evidence: EvidenceBundle) -> list[dict[str, Any]]:
    return [
        {
            "tool_name": tc.tool_name,
            "tool_args": tc.args,
            "step": tc.step,
        }
        for tc in evidence.tool_calls
    ]


def _load_summary_from_file(workspace: Path) -> dict[str, Any]:
    path = workspace / "_devshell_summary.json"
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8").strip()
        last_line = text.splitlines()[-1].strip() if text else "{}"
        payload = json.loads(last_line)
    except (OSError, IndexError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _build_artifacts(workspace: Path) -> list[ArtifactRecord]:
    artifacts: list[ArtifactRecord] = []
    for p in sorted(workspace.iterdir()):
        if p.is_file() and p.name not in _META_FILENAMES:
            artifacts.append(
                ArtifactRecord(
                    path=p.name,
                    artifact_type=p.suffix.lstrip(".").lower() or "unknown",
                    size_bytes=p.stat().st_size,
                )
            )
    return artifacts


def _load_events(
    *,
    log_dir: Path,
) -> tuple[list[ToolCallRecord], list[EventRecord], str]:
    log_path = _load_latest_events_log(log_dir)
    if log_path is None:
        return [], [], "unknown"

    extractor = EvidenceExtractor(mapping_path=_EVIDENCE_MAPPING_PATH)
    lines: list[dict[str, Any]] = []
    for raw in log_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            lines.append(rec)

    payload_by_call_id: dict[str, dict[str, Any]] = {}
    for rec in lines:
        if rec.get("type") != "tool_result":
            continue
        call_id = str(rec.get("call_id") or "")
        if call_id:
            payload_by_call_id[call_id] = rec

    tool_calls: list[ToolCallRecord] = []
    events: list[EventRecord] = []
    run_status = "unknown"
    step = 0

    for rec in lines:
        rec_type = rec.get("type")
        if rec_type == "run_result":
            run_status = str(rec.get("status") or run_status)
            continue
        if rec_type != "tool_call":
            continue

        step += 1
        call_id = str(rec.get("call_id") or "")
        raw_tool_name = str(rec.get("tool") or "")
        tool_name = _normalize_tool_name(raw_tool_name)
        args = rec.get("args") if isinstance(rec.get("args"), dict) else {}
        response = payload_by_call_id.get(call_id, {})
        status = extractor._parse_call_status(response)
        observation_excerpt = extractor._make_excerpt(response)

        tool_call = ToolCallRecord(
            step=step,
            call_index=0,
            tool_name=tool_name,
            tool_description="",
            args=args,
            status=status,
            observation_excerpt=observation_excerpt,
        )
        tool_calls.append(tool_call)

        event = extractor._map_tool_to_event(
            tool_name=tool_name,
            args=args,
            step=step,
            status=status,
        )
        if event is not None:
            events.append(event)

    return tool_calls, events, run_status


def _build_evidence(
    *,
    task_id: str,
    workspace: Path,
    summary: dict[str, Any],
    answer: str,
    duration_ms: int,
    log_dir: Path,
) -> EvidenceBundle:
    summary_usage = (
        TokenUsage.from_usage_dict(summary["usage"])
        if isinstance(summary.get("usage"), dict)
        else TokenUsage()
    )
    last_turn_usage = summary_usage
    uvt = summary.get("usage_vendor_by_turn")
    if isinstance(uvt, list) and uvt:
        last = uvt[-1]
        if isinstance(last, dict):
            last_turn_usage = TokenUsage.from_usage_dict(last)

    tool_calls, events, run_status = _load_events(log_dir=log_dir)
    total_steps = int(summary.get("num_turns") or 0) or len(tool_calls)
    model_name = summary.get("model")
    summary_status = summary.get("status")

    return EvidenceBundle(
        task_id=task_id,
        final_answer=answer,
        events=events,
        tool_calls=tool_calls,
        artifacts=_build_artifacts(workspace),
        model_name=model_name if isinstance(model_name, str) else None,
        token_usage_last_turn=last_turn_usage,
        token_usage_run=summary_usage,
        total_steps=total_steps,
        run_status=str(summary_status or run_status or "unknown"),
        duration_ms=duration_ms,
        workspace_dir=str(workspace.resolve()),
    )


_AXIS_SECTION_ORDER = ("correctness", "grounding", "efficiency")


def _format_score_reason(record: Any) -> str:
    by_axis: dict[str, list[tuple[str, Any]]] = {}
    for cid, result in record.criteria_results.items():
        by_axis.setdefault(result.axis, []).append((cid, result))
    for axis in by_axis:
        by_axis[axis].sort(key=lambda x: x[0])

    ordered_axes: list[str] = []
    for axis in _AXIS_SECTION_ORDER:
        if axis in by_axis:
            ordered_axes.append(axis)
    for axis in sorted(by_axis):
        if axis not in _AXIS_SECTION_ORDER:
            ordered_axes.append(axis)

    lines: list[str] = []
    for axis in ordered_axes:
        lines.append(f"### {axis.replace('_', ' ').title()}")
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
    return round(record.overall_weighted_score * 100)


def _update_pending_with_score(
    pending_path: Path,
    score: int,
    score_reason: str,
) -> bool:
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


def _submit_pending(
    pending_path: Path,
    *,
    timeout: float = 120.0,
) -> tuple[bool, str]:
    from evaluation.scripts.baseline.score_baseline_tasks import (
        _submit_pending as _baseline_submit_pending,
    )

    return _baseline_submit_pending(pending_path, timeout=timeout)


def score_task(
    *,
    row: dict[str, Any],
    run_dir: Path,
    question: QuestionItem,
    evaluator: BinaryEvaluator,
) -> dict[str, Any]:
    task_id = str(row.get("task_id") or "")
    workspace = run_dir / "workspaces" / task_id
    log_dir = run_dir / "logs" / task_id
    summary = row.get("devshell_summary")
    if not isinstance(summary, dict) or not summary:
        summary = _load_summary_from_file(workspace)
    answer = _build_answer(workspace, summary)
    duration_ms = int(row.get("duration_ms") or 0)
    evidence = _build_evidence(
        task_id=task_id,
        workspace=workspace,
        summary=summary,
        answer=answer,
        duration_ms=duration_ms,
        log_dir=log_dir,
    )
    token_usage = token_usage_record_from_evidence(evidence)
    tool_calls = _tool_calls_payload(evidence)
    mode = str(row.get("mode") or "direct")
    repeat_idx = int(row.get("repeat_idx") or 0)
    prompt = str(row.get("prompt") or question.human_prompt_seed or "")
    run_status = str(summary.get("status") or evidence.run_status or "unknown")

    try:
        record = evaluator.evaluate(
            question=question,
            answer=answer,
            tool_calls=tool_calls,
            evidence=evidence,
            mode=mode,
            repeat_idx=repeat_idx,
            prompt=prompt,
            run_status=run_status,
            model_name=evidence.model_name,
            token_usage=token_usage,
            duration_ms=duration_ms,
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

    return {
        "task_id": task_id,
        "question_id": question.id,
        "score": _score_to_int(record),
        "score_reason": _format_score_reason(record),
        "record": record,
        "error": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Score devshell task workspaces using BinaryEvaluator "
            "(same validators as MatMaster auto-evaluation)."
        )
    )
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--run-label", type=str, default=None)
    parser.add_argument("--eval-config", type=Path, default=None)
    parser.add_argument("--question-bank-dir", type=Path, default=None)
    parser.add_argument("--tasks", nargs="*", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--eval-ingest-timeout", type=float, default=120.0)
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

    if args.run_dir is not None:
        run_dir = args.run_dir.resolve()
    elif args.run_label:
        candidates = sorted(
            d for d in (REPO_ROOT / "results").glob(f"{args.run_label}_*") if d.is_dir()
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

    raw_runs_path = run_dir / "raw_runs.jsonl"
    rows_by_task = _load_raw_run_rows(raw_runs_path)
    if not rows_by_task:
        print(
            f"error: missing or empty raw_runs.jsonl under {run_dir}", file=sys.stderr
        )
        return 1

    config_path = args.eval_config or (REPO_ROOT / "evaluation" / "config.yaml")
    config = _load_eval_config(config_path)
    llm_cfg: LLMRuntimeConfig | None = _build_evaluator_llm_cfg(config)
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

    bank_dir = args.question_bank_dir or _QUESTION_BANK_DIR
    try:
        question_map = _build_question_map(Path(bank_dir))
    except Exception as exc:
        print(f"error: failed to load question banks: {exc}", file=sys.stderr)
        return 1

    task_ids = sorted(rows_by_task)
    if args.tasks:
        task_filter = set(args.tasks)
        task_ids = [task_id for task_id in task_ids if task_id in task_filter]
        if not task_ids:
            print(
                f"error: none of --tasks {args.tasks} matched raw_runs", file=sys.stderr
            )
            return 1

    pending_dir = run_dir / "pending_ingest"
    results: list[dict[str, Any]] = []
    n_ok = 0
    n_err = 0

    print(
        f"Scoring {len(task_ids)} task(s) in {run_dir} "
        f"(score-jobs={score_jobs}, parallel-checklist-workers={parallel_checklist})",
        file=sys.stderr,
    )

    skip_tasks: dict[str, str] = {}
    scored_by_task: dict[str, dict[str, Any]] = {}

    if score_jobs <= 1:
        evaluator = _build_evaluator()
        for task_id in task_ids:
            row = rows_by_task[task_id]
            question_id = str(row.get("question_id") or "")
            question = question_map.get(question_id)
            if question is None:
                skip_tasks[task_id] = question_id
                continue
            scored_by_task[task_id] = score_task(
                row=row,
                run_dir=run_dir,
                question=question,
                evaluator=evaluator,
            )
    else:

        def _score_one_task(task_id: str) -> tuple[str, dict[str, Any] | None, str]:
            row = rows_by_task[task_id]
            question_id = str(row.get("question_id") or "")
            question = question_map.get(question_id)
            if question is None:
                return task_id, None, question_id
            ev = _build_evaluator()
            return (
                task_id,
                score_task(
                    row=row,
                    run_dir=run_dir,
                    question=question,
                    evaluator=ev,
                ),
                "",
            )

        with ThreadPoolExecutor(max_workers=score_jobs) as pool:
            future_map = {pool.submit(_score_one_task, tid): tid for tid in task_ids}
            for fut in as_completed(future_map):
                tid, res, miss_q = fut.result()
                if res is None:
                    skip_tasks[tid] = miss_q
                else:
                    scored_by_task[tid] = res

    for task_id in task_ids:
        if task_id in skip_tasks:
            qid = skip_tasks[task_id]
            print(
                f"  [skip] {task_id}: question_id {qid!r} not found in bank",
                file=sys.stderr,
            )
            n_err += 1
            continue

        result = scored_by_task[task_id]
        row = rows_by_task[task_id]
        question_id = str(row.get("question_id") or "")
        results.append(result)

        if result["error"]:
            print(f"  [error] {task_id}: {result['error']}", file=sys.stderr)
            n_err += 1
        else:
            print(
                f"  [scored] {task_id}: {result['score']}/100 (q={question_id})",
                file=sys.stderr,
            )
            n_ok += 1

        if args.dry_run:
            print(f"\n--- {task_id} score_reason ---")
            print(result["score_reason"])
            print()
            continue

        pending_path = pending_dir / f"{task_id}.json"
        if pending_path.is_file():
            ok = _update_pending_with_score(
                pending_path,
                score=result["score"],
                score_reason=result["score_reason"],
            )
            if ok:
                print(f"  [pending] updated {pending_path.name}", file=sys.stderr)
            if args.submit and ok and not result["error"]:
                submit_ok, msg = _submit_pending(
                    pending_path,
                    timeout=args.eval_ingest_timeout,
                )
                if submit_ok:
                    print(f"  [ingest] {task_id} ok ({msg})", file=sys.stderr)
                else:
                    print(f"  [ingest] {task_id} failed: {msg}", file=sys.stderr)
        elif args.submit:
            print(
                f"  [warn] {task_id}: no pending_ingest/{task_id}.json found",
                file=sys.stderr,
            )

    if results:
        print(
            "\n{:<45} {:>8} {:>8}".format("task_id", "score", "status"), file=sys.stderr
        )
        print("-" * 65, file=sys.stderr)
        scores_valid: list[int] = []
        for result in results:
            status = "error" if result["error"] else "ok"
            print(
                "{:<45} {:>8} {:>8}".format(result["task_id"], result["score"], status),
                file=sys.stderr,
            )
            if not result["error"]:
                scores_valid.append(int(result["score"]))
        if scores_valid:
            avg = round(sum(scores_valid) / len(scores_valid))
            print(
                f"\n  Average score: {avg}/100 ({len(scores_valid)} task(s))",
                file=sys.stderr,
            )
        print(f"\nDone: {n_ok} scored, {n_err} errors", file=sys.stderr)
        if args.dry_run:
            print("(dry-run: no files modified, no submissions made)", file=sys.stderr)

    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
