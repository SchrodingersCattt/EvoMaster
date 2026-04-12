"""Helpers for ``run_devshell_eval.py`` (keeps the CLI entry under the line-count limit)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, TextIO


def _cc_baseline_readme_markdown(*, run_dir: Path, doc_rel: str) -> str:
    return (
        "# 外部 Baseline 测评（本 run）\n\n"
        "本目录由 `uv run python evaluation/scripts/devshell/run_devshell_eval.py --prepare-cc-baseline` 生成，"
        "**未**运行 mm-devshell（Claude Code / Cursor 等同布局均可）。\n\n"
        "若 prepare 时**未**使用 `--no-clean-results`，脚本已在创建本 run **之前**清空仓库根 "
        "`results/` 下全部内容（默认行为，避免旧测评与本次 baseline 混在一起）。\n\n"
        "## 步骤\n\n"
        f"1. 阅读仓库内 **`{doc_rel}`**（外部 baseline 提示词与 `_devshell_summary.json` 约定）。\n"
        "2. 对每个 `workspaces/<task_id>/`：**开工前须**执行 "
        "`uv run python evaluation/scripts/baseline/mark_external_baseline_task_start.py --workspace <该目录>`，"
        "否则 finalize 不会写入 `duration_ms`（见 `baseline_cc_eval.md`）。"
        "以该目录为工作目录，读取 `_devshell_prompt.txt` 完成任务，并按文档写入 `_devshell_summary.json`。\n"
        "3. 可选：将对话/终端记录保存到 `logs/<task_id>/devshell_console.log`，便于与 DevShell 产物对齐。\n"
        "4. 全部完成后在仓库根执行：\n\n"
        "```bash\n"
        f"uv run python evaluation/scripts/baseline/finalize_external_baseline_ingest.py --run-dir {run_dir.resolve()}\n"
        "```\n\n"
        "需要「先判分再入库」时，在 finalize 时加 `--eval-ingest-pending-only`，再对生成的 "
        "`pending_ingest/*.json` 使用 `evaluation/scripts/eval_ingest_submit_pending.py`（与 DevShell 流程相同）。\n"
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _clean_results_directory(results_root: Path) -> None:
    """Remove all children of ``results_root`` (the repo ``results/`` folder)."""
    if not results_root.is_dir():
        return
    for child in results_root.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _merge_eval_config(path: Path | None, overrides: dict[str, Any]) -> dict[str, Any]:
    base: dict[str, Any] = {}
    if path and path.is_file():
        base = _load_yaml(path)
    for k, v in overrides.items():
        if v is not None:
            base[k] = v
    return base


def _normalize_mm_devshell_exp_cli(raw: str | None) -> str | None:
    """Normalize ``--exp``: None/blank → omit mm-devshell flag (direct default)."""
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def _mm_devshell_exp_cmd_suffix(exp_cli: str | None) -> list[str]:
    """Extra argv for ``matmaster.devshell run``; omit ``--exp`` when using direct default."""
    if exp_cli is None:
        return []
    return ["--exp", exp_cli]


def _eval_tooling_snapshot_for_exp_cli(
    *, repo_root: Path, exp_cli: str | None
) -> dict[str, Any]:
    """Resolve ``--exp`` to the ``matmaster/exps/{name}.toml`` snapshot (default: ``direct``)."""
    from evaluation.eval_tooling_snapshot import snapshot_eval_tooling

    name = (exp_cli or "").strip() or "direct"
    return snapshot_eval_tooling(repo_root=repo_root, exp_name=name)


class _TeeTextIO:
    """Write the same decoded text to multiple text streams (e.g. log file + stderr)."""

    __slots__ = ("_streams",)

    def __init__(self, *streams: TextIO) -> None:
        self._streams = streams

    def write(self, data: str) -> int:
        for s in self._streams:
            s.write(data)
            s.flush()
        return len(data)

    def flush(self) -> None:
        for s in self._streams:
            s.flush()


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


def _run_devshell_task(
    *,
    cmd: list[str | Path],
    cwd: str,
    env: dict[str, str],
    summary_file: Path,
    console_log_file: Path | None,
    timeout_sec: float | None,
    tee_stderr: bool = False,
) -> tuple[int, int, dict[str, Any]]:
    t0 = time.monotonic()
    timeout = None if timeout_sec is None or timeout_sec <= 0 else float(timeout_sec)
    try:
        if console_log_file is None:
            proc = subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout)
        else:
            with console_log_file.open("w", encoding="utf-8") as f:
                out = _TeeTextIO(f, sys.stderr) if tee_stderr else f
                proc = subprocess.run(
                    cmd,
                    cwd=cwd,
                    env=env,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=timeout,
                )
    except subprocess.TimeoutExpired as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        summary = _load_summary_file(summary_file)
        if not isinstance(summary, dict):
            summary = {}
        lim = (
            float(e.timeout) if getattr(e, "timeout", None) else float(timeout_sec or 0)
        )
        summary = {
            **summary,
            "task_wall_timeout": True,
            "timeout_seconds": lim,
        }
        # Same convention as coreutils `timeout` / common CI (timed out)
        return 124, duration_ms, summary

    duration_ms = int((time.monotonic() - t0) * 1000)
    summary = _load_summary_file(summary_file)
    return proc.returncode, duration_ms, summary
