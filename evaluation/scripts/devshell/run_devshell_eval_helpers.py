"""Helpers for ``run_devshell_eval.py`` (keeps the CLI entry under the line-count limit)."""

from __future__ import annotations

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
