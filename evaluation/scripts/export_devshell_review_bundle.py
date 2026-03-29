#!/usr/bin/env python3
"""Pack a ``devshell_eval`` run directory into one Markdown file for Claude / Cursor @-review.

Reads ``manifest.json`` + ``raw_runs.jsonl``, resolves workspace / logs / prompt paths,
optionally inlines MATTER question text from the question bank, and writes
``claude_review.md`` (default) next to the run.

Usage::

    uv run python evaluation/scripts/export_devshell_review_bundle.py --run-dir results/devshell_eval_20260327_170233
    uv run python evaluation/scripts/export_devshell_review_bundle.py --raw-runs results/.../raw_runs.jsonl --out /tmp/review.md

Then in Cursor: @ ``claude_review.md`` (and @ workspace folders if needed) for grading or analysis.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_SKIP_DIR_NAMES = {".cache", ".git", "__pycache__", ".venv", "node_modules"}


def _list_workspace_files(workspace: Path, *, max_files: int = 300) -> list[str]:
    out: list[str] = []
    if not workspace.is_dir():
        return out
    for p in sorted(workspace.rglob("*")):
        if p.is_dir():
            continue
        parts = set(p.relative_to(workspace).parts)
        if parts & _SKIP_DIR_NAMES:
            continue
        if any(x in p.parts for x in _SKIP_DIR_NAMES):
            continue
        out.append(str(p.relative_to(workspace)))
        if len(out) >= max_files:
            out.append(f"... ({max_files}+ files, truncated)")
            break
    return out


def _find_events_jsonl(log_dir: Path) -> Path | None:
    if not log_dir.is_dir():
        return None
    matches = sorted(log_dir.glob("events_*.jsonl"))
    return matches[0] if matches else None


def _load_question_seed(bank_dir: Path, question_id: str) -> str | None:
    """Return ``human_prompt_seed`` for ``question_id`` if found in v5 banks."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from evaluation.core.runner import _flatten_banks, load_question_banks
    except ImportError:
        return None
    try:
        banks = load_question_banks(bank_dir)
    except (OSError, ValueError):
        return None
    for q in _flatten_banks(banks):
        if q.id == question_id:
            return q.human_prompt_seed
    return None


def _md_escape_fence(s: str) -> str:
    if "```" in s:
        return s.replace("```", "``\u200b`")
    return s


def build_markdown(
    run_dir: Path,
    *,
    include_question_seed: bool,
) -> str:
    manifest_path = run_dir / "manifest.json"
    raw_path = run_dir / "raw_runs.jsonl"
    if not raw_path.is_file():
        raise FileNotFoundError(f"Missing raw_runs.jsonl: {raw_path}")

    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    lines: list[str] = []
    lines.append("# DevShell 评测打包（给 Claude / Cursor 用）\n")
    lines.append(
        f"生成时间（UTC）：`{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}`\n"
    )
    lines.append("## 怎么用\n")
    lines.append(
        "1. 在对话里 **@ 本文件** `claude_review.md`，必要时再 @ 下面各任务的 **Workspace** 目录。\n"
        "2. 说明你的目标（例如：按 MATTER checklist 判是否通过、写简短结论、找 bug）。\n"
        "3. 本文件**不含** BinaryEvaluator 自动判分；需要严格对齐线上判分时请再走 `run_evaluation` 或人工对照题库。\n"
    )

    lines.append("## Run 元数据\n")
    lines.append(f"- **run 目录**：`{run_dir.resolve()}`\n")
    if manifest:
        for k in (
            "run_label",
            "started_at_utc",
            "question_bank_dir",
            "eval_config",
            "model",
            "plan_count",
        ):
            if k in manifest:
                lines.append(f"- **{k}**：`{manifest[k]}`\n")

    bank_dir: Path | None = None
    if manifest.get("question_bank_dir"):
        bank_dir = Path(str(manifest["question_bank_dir"])).resolve()

    rows: list[dict[str, Any]] = []
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))

    lines.append(f"\n## 任务列表（共 {len(rows)} 条）\n")

    for i, row in enumerate(rows, start=1):
        task_id = row.get("task_id", "?")
        qid = row.get("question_id", "?")
        lines.append(f"### {i}. `{task_id}`\n")
        lines.append(f"- **question_id**：`{qid}`\n")
        lines.append(
            f"- **mode / repeat**：`{row.get('mode')}` / `{row.get('repeat_idx')}`\n"
        )
        lines.append(
            f"- **capability / domain**：`{row.get('capability')}` / `{row.get('domain')}`\n"
        )
        lines.append(f"- **devshell_exit_code**：`{row.get('devshell_exit_code')}`\n")

        summary_path = row.get("devshell_summary_path")
        if summary_path:
            lines.append(f"- **_devshell_summary.json**：`{summary_path}`\n")

        # Derive workspace from summary path or conventional layout
        ws: Path | None = None
        if summary_path:
            sp = Path(str(summary_path))
            if sp.name == "_devshell_summary.json":
                ws = sp.parent
        if ws is None:
            cand = run_dir / "workspaces" / str(task_id)
            if cand.is_dir():
                ws = cand
        if ws is not None:
            lines.append(f"- **Workspace**：`{ws.resolve()}`\n")
            prompt_f = ws / "_devshell_prompt.txt"
            if prompt_f.is_file():
                lines.append(f"- **Prompt 文件**：`{prompt_f.resolve()}`\n")
            log_d = run_dir / "logs" / str(task_id)
            ev = _find_events_jsonl(log_d)
            if ev is not None:
                lines.append(f"- **events JSONL**：`{ev.resolve()}`\n")

        if include_question_seed and bank_dir is not None and isinstance(qid, str):
            seed = _load_question_seed(bank_dir, qid)
            if seed:
                lines.append("\n#### 题库 human_prompt_seed（MATTER）\n")
                lines.append("```text\n")
                lines.append(_md_escape_fence(seed))
                lines.append("\n```\n")

        summ = row.get("devshell_summary")
        if isinstance(summ, dict):
            lines.append("\n#### devshell_summary（JSON）\n")
            lines.append("```json\n")
            lines.append(json.dumps(summ, ensure_ascii=False, indent=2))
            lines.append("\n```\n")
            fc = summ.get("final_content")
            if isinstance(fc, str) and fc.strip():
                lines.append("\n#### final_content（便于速读）\n")
                lines.append("```text\n")
                lines.append(_md_escape_fence(fc.strip()))
                lines.append("\n```\n")

        if ws is not None:
            files = _list_workspace_files(ws)
            if files:
                lines.append("\n#### Workspace 文件列表（节选）\n")
                for rel in files[:80]:
                    lines.append(f"- `{rel}`\n")
                if len(files) > 80:
                    lines.append(f"- … 共 {len(files)} 条（见上脚本截断规则）\n")

        lines.append("\n---\n")

    return "".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Export devshell_eval run to a single claude_review.md for @-review.",
    )
    p.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="Directory containing manifest.json + raw_runs.jsonl (e.g. results/devshell_eval_*)",
    )
    p.add_argument(
        "--raw-runs",
        type=Path,
        default=None,
        help="Path to raw_runs.jsonl (run-dir defaults to parent)",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output Markdown path (default: <run-dir>/claude_review.md)",
    )
    p.add_argument(
        "--with-questions",
        action="store_true",
        help="Inline human_prompt_seed from question bank (needs manifest question_bank_dir)",
    )
    args = p.parse_args()

    run_dir = args.run_dir
    if args.raw_runs is not None:
        rp = args.raw_runs.resolve()
        if not rp.is_file():
            print(f"Error: not a file: {rp}", file=sys.stderr)
            return 1
        run_dir = rp.parent if run_dir is None else run_dir.resolve()
    if run_dir is None:
        print("Error: pass --run-dir or --raw-runs", file=sys.stderr)
        return 1

    run_dir = run_dir.resolve()
    out = args.out
    if out is None:
        out = run_dir / "claude_review.md"
    else:
        out = out.resolve()

    try:
        md = build_markdown(run_dir, include_question_seed=args.with_questions)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"Wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
