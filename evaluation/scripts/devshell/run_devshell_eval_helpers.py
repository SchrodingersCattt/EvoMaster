"""Helpers for ``run_devshell_eval.py`` (keeps the CLI entry under the line-count limit)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
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


def build_mm_devshell_run_cmd(
    *,
    py: str | Path,
    workspace_path: Path,
    log_dir: Path,
    prompt_file: Path,
    summary_file: Path,
    model: str | None,
    exp_cli: str | None,
    verbose: bool,
    exclude_subagents: list[str] | None = None,
    inject_bohrium_failure: str | None = None,
    billing_mode: str | None = None,
    invocation_id: str | None = None,
) -> list[str | Path]:
    """Build ``python -m matmaster.devshell run ...`` argv (single task)."""
    cmd: list[str | Path] = [
        py,
        "-u",
        "-m",
        "matmaster.devshell",
        "run",
        "--workdir",
        workspace_path,
        "--log-dir",
        log_dir,
        "--prompt-file",
        prompt_file,
        "--json-out",
        summary_file,
    ]
    if model:
        cmd.extend(["--model", model])
    cmd.extend(_mm_devshell_exp_cmd_suffix(exp_cli))
    if exclude_subagents:
        cmd.extend(["--exclude-subagents", *exclude_subagents])
    if inject_bohrium_failure:
        cmd.extend(["--inject-bohrium-failure", inject_bohrium_failure])
    if billing_mode:
        cmd.extend(["--billing-mode", billing_mode])
        if invocation_id:
            cmd.extend(["--invocation-id", invocation_id])
    if verbose:
        cmd.append("--verbose")
    return cmd


def text_indicates_devshell_provider_transport_failure(text: str) -> bool:
    """Heuristic: Bedrock/botocore transport errors worth retrying with a fallback route.

    Matches console output from boto3/botocore (e.g. ReadTimeoutError on bedrock-runtime)
    and similar lines copied into ``devshell_console.log``.
    """
    t = text.lower()
    compact = t.replace("_", "")
    if "readtimeouterror" in compact:
        return True
    if "connecttimeouterror" in compact and "bedrock" in t:
        return True
    if "endpointconnectionerror" in compact and "bedrock" in t:
        return True
    if "connectionclosederror" in compact and "bedrock" in t:
        return True
    if "readtimeout" in compact and "bedrock-runtime" in t:
        return True
    if "read timeout" in t and (
        "bedrock-runtime" in t or "converse-stream" in t or "/converse" in t
    ):
        return True
    if "llm stream failed" in t and (
        "readtimeout" in compact or "read timeout" in t or "timeout" in t
    ):
        return True
    return False


def devshell_console_indicates_provider_fallback(
    log_path: Path, *, max_bytes: int = 4_194_304
) -> bool:
    """Return True if ``devshell_console.log`` suggests a transient provider transport failure."""
    if not log_path.is_file():
        return False
    try:
        data = log_path.read_bytes()
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        text = data.decode("utf-8", errors="replace")
    except OSError:
        return False
    return text_indicates_devshell_provider_transport_failure(text)


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


def _kill_process_group(proc: subprocess.Popen[Any]) -> None:
    """Terminate the child process tree as well as the platform allows."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            pass
        proc.wait()


def _run_devshell_task(
    *,
    cmd: list[str | Path],
    cwd: str,
    env: dict[str, str],
    summary_file: Path,
    console_log_file: Path | None,
    timeout_sec: float | None,
    tee_stderr: bool = False,
    console_log_append: bool = False,
) -> tuple[int, int, dict[str, Any]]:
    t0 = time.monotonic()
    timeout = None if timeout_sec is None or timeout_sec <= 0 else float(timeout_sec)
    log_mode = "a" if console_log_append else "w"
    try:
        if console_log_file is None:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                start_new_session=True,
            )
            proc.communicate(timeout=timeout)
        else:
            with console_log_file.open(log_mode, encoding="utf-8") as f:
                if tee_stderr:
                    proc = subprocess.Popen(
                        cmd,
                        cwd=cwd,
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        start_new_session=True,
                    )
                    output, _ = proc.communicate(timeout=timeout)
                    if output:
                        f.write(output)
                        f.flush()
                        sys.stderr.write(output)
                        sys.stderr.flush()
                else:
                    proc = subprocess.Popen(
                        cmd,
                        cwd=cwd,
                        env=env,
                        stdout=f,
                        stderr=subprocess.STDOUT,
                        text=True,
                        start_new_session=True,
                    )
                    proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        duration_ms = int((time.monotonic() - t0) * 1000)
        summary = _load_summary_file(summary_file)
        if not isinstance(summary, dict):
            summary = {}
        summary = {
            **summary,
            "task_wall_timeout": True,
            "timeout_seconds": float(timeout_sec or 0),
        }
        return 124, duration_ms, summary

    duration_ms = int((time.monotonic() - t0) * 1000)
    summary = _load_summary_file(summary_file)
    return proc.returncode, duration_ms, summary


def build_devshell_eval_arg_parser(
    *,
    repo_root: Path,
    default_model_route: str,
    default_fallback_model_route: str,
) -> argparse.ArgumentParser:
    """Construct the ``run_devshell_eval.py`` CLI parser.

    Extracted from the CLI entry point to keep that module under the 1000-line
    pre-commit limit. ``repo_root`` and the two route defaults are injected so the
    parser definition stays free of module-level constants.
    """
    parser = argparse.ArgumentParser(
        description="Run MATTER question bank through mm-devshell (matmaster devshell run).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--eval-config",
        type=Path,
        default=repo_root / "evaluation/config.yaml",
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
        default=default_model_route,
        help=(
            "LLM route key passed to ``mm-devshell run --model`` (see llm_config.yaml routes; "
            f"default: {default_model_route})"
        ),
    )
    parser.add_argument(
        "--fallback-model",
        type=str,
        default=default_fallback_model_route,
        metavar="ROUTE_KEY",
        help=(
            "Second LLM route for one retry per task when logs look like a Bedrock/botocore "
            f"transport error (read timeout, etc.). Default: "
            f"{default_fallback_model_route} (LiteLLM). "
            "Use the same value as --model to disable fallback retries. "
            "Each new task still starts with --model."
        ),
    )
    parser.add_argument(
        "--eval-billing-mode",
        type=str,
        default=None,
        metavar="MODE",
        help=(
            "Enable per-call usage reporting to tools-server with this billing_mode "
            "(e.g. 'eval'). 'eval'/'byok' record + price but do NOT debit credits. "
            "Per-call cost is back-filled into ingest extra.per_call_usage. "
            "Omit to disable reporting."
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
    return parser
