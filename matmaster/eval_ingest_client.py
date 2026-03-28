"""Client for matmaster-tools-server evaluation ingest API.

See ``docs/apifox-evaluation-openapi.json`` in matmaster-tools-server for the contract:
``EvalIngestRequest`` with ``run_id``, optional ``git_commit``, ``items`` (≥1).
Each ``EvalItemIn`` requires ``question_id``; ``model`` / ``num_turns`` / ``score`` /
``result_oss_url`` belong on the item top level. ``score`` is taken from the devshell
summary when present, else a 100/0 pass-fail proxy.
For deferred ingest, ``run_devshell_eval.py --eval-ingest-pending-only`` writes
``pending_ingest/*.json`` without ``score``; then ``scripts/eval_ingest_submit_pending.py`` POSTs.
``result_oss_url`` is set after zipping **only the current task** under that run:
``workspaces/<task_id>`` and ``logs/<task_id>`` (see :func:`upload_eval_task_artifacts_to_oss`).
The parent ``devshell_eval_*`` folder is shared by all tasks in the batch; it is not uploaded whole.
``extra`` is stored as opaque JSON.

Ingest POST URL is ``MATMASTER_TOOLS_SERVER`` + ``EVAL_INGEST_API_PATH``（在 **首次 import**
本模块时按 ``utils.env`` 解析；与配额等共用同一 host）。见 ``EVAL_INGEST_URL``。
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import httpx

import utils.env

logger = logging.getLogger(__name__)

# Direct tools-server path (not the gateway ``/bohrapi/v1/matmaster-tools-server/...`` prefix).
EVAL_INGEST_API_PATH = "/api/v1/evaluation/ingest"

_base = (utils.env.MATMASTER_TOOLS_SERVER or "").strip().rstrip("/")
EVAL_INGEST_URL: str | None = f"{_base}{EVAL_INGEST_API_PATH}" if _base else None


def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def extract_total_tokens(usage: Any) -> int | None:
    if not usage or not isinstance(usage, dict):
        return None
    raw = usage.get("total_tokens")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    pt = usage.get("prompt_tokens")
    ct = usage.get("completion_tokens")
    if pt is not None and ct is not None:
        try:
            return int(pt) + int(ct)
        except (TypeError, ValueError):
            pass
    return None


def score_for_eval_ingest(
    summary: dict[str, Any] | None,
    devshell_exit_code: int,
) -> float:
    """Numeric score for tools-server ``EvalItemIn.score``.

    Prefer an explicit value from the devshell summary (``score``, ``eval_score``,
    ``weighted_score``). Otherwise use a simple proxy: **100** when the run exited 0
    and the summary is not a parse error; **0** otherwise. (DevShell does not run
    MATTER BinaryEvaluator, so there is no automatic checklist score unless the
    kernel adds one of the keys above.)
    """
    if isinstance(summary, dict):
        for key in ("score", "eval_score", "weighted_score"):
            raw = summary.get(key)
            if raw is None:
                continue
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
        if summary.get("parse_error"):
            return 0.0
    if devshell_exit_code == 0:
        return 100.0
    return 0.0


def eval_run_zip_should_skip_arcname(arcname: str) -> bool:
    """True if a path under the run directory should not be packed (noise / bytecode)."""
    parts = arcname.replace("\\", "/").split("/")
    if "__pycache__" in parts:
        return True
    lower = arcname.lower()
    if lower.endswith(".pyc") or lower.endswith(".pyo"):
        return True
    if arcname.endswith(".DS_Store"):
        return True
    return False


def upload_eval_task_artifacts_to_oss(
    run_dir: Path,
    task_id: str,
    *,
    oss_prefix: str = "matmaster/evaluation",
) -> str | None:
    """Zip **only one task** under a devshell batch run dir, upload to OSS.

    A run root like ``results/devshell_eval_*`` holds **all** questions; each task has
    ``workspaces/<task_id>/`` and ``logs/<task_id>/``. This packs just those two
    subtrees (paths inside the zip look like ``workspaces/<task_id>/...`` and
    ``logs/<task_id>/...``). Skips ``__pycache__``, ``*.pyc`` / ``*.pyo``,
    ``.DS_Store``.

    Returns public HTTPS URL, or ``None`` if nothing to pack, OSS env is missing,
    or upload fails. Configure ``OSS_*`` like calculation MCP.
    """
    root = Path(run_dir).resolve()
    if not root.is_dir():
        return None

    safe_tid = task_id.replace("/", "_").replace("\\", "_")[:200] or "task"
    subroots = [
        root / "workspaces" / task_id,
        root / "logs" / task_id,
    ]

    files: list[tuple[Path, str]] = []
    for base in subroots:
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            try:
                rel = p.relative_to(root).as_posix()
            except ValueError:
                continue
            if eval_run_zip_should_skip_arcname(rel):
                continue
            files.append((p, rel))

    if not files:
        return None

    run_name = root.name.replace("/", "_").replace("\\", "_")[:120] or "eval_run"
    zip_name = f"{run_name}_{safe_tid}_task.zip"
    fd, tmp = tempfile.mkstemp(suffix=".zip", prefix="eval_ingest_")
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fs_path, arcname in sorted(files, key=lambda t: t[1]):
                zf.write(fs_path, arcname=arcname)
        from evomaster.adaptors.calculation.oss_io import upload_file_to_oss

        url = upload_file_to_oss(
            tmp_path,
            tmp_path.parent,
            oss_prefix=oss_prefix,
            object_basename=zip_name,
        )
        return url[:2048] if url else None
    except (OSError, RuntimeError, ValueError, ImportError) as e:
        logger.warning("eval ingest OSS task artifacts upload failed: %s", e)
        return None
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def git_head_commit(repo_root: Path, *, max_len: int = 64) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 0:
            return None
        h = proc.stdout.strip()
        if len(h) < 7:
            return None
        return h[:max_len]
    except (OSError, subprocess.TimeoutExpired):
        return None


def build_ingest_item(
    *,
    question_id: str,
    prompt: str,
    task_id: str,
    mode: str,
    repeat_idx: int,
    devshell_exit_code: int,
    summary: dict[str, Any] | None,
    duration_ms: int | None,
    result_oss_url: str | None = None,
) -> dict[str, Any]:
    usage = summary.get("usage") if isinstance(summary, dict) else None
    tokens = extract_total_tokens(usage)
    preview: str | None = None
    if isinstance(summary, dict):
        fc = summary.get("final_content")
        if isinstance(fc, str) and fc:
            preview = fc[:2000]

    extra: dict[str, Any] = {
        "task_id": task_id,
        "devshell_exit_code": devshell_exit_code,
        "mode": mode,
        "repeat_idx": repeat_idx,
    }
    if isinstance(summary, dict):
        extra.update(
            {
                "status": summary.get("status"),
                "reason": summary.get("reason"),
                "profile_key": summary.get("profile_key"),
            }
        )
        if summary.get("parse_error"):
            extra["parse_error"] = True
            for k in ("error", "missing_file", "empty_file"):
                if k in summary:
                    extra[k] = summary[k]
    if preview is not None:
        extra["final_content_preview"] = preview

    item: dict[str, Any] = {
        "question_id": question_id,
        "question_sha256": prompt_sha256(prompt),
        "extra": extra,
    }
    if isinstance(summary, dict):
        nt = summary.get("num_turns")
        if nt is not None:
            try:
                nti = int(nt)
                if nti >= 0:
                    item["num_turns"] = nti
            except (TypeError, ValueError):
                pass
        mod = summary.get("model")
        if isinstance(mod, str) and mod.strip():
            item["model"] = mod.strip()[:256]

    if duration_ms is not None and duration_ms >= 0:
        item["duration_ms"] = int(duration_ms)
    if tokens is not None:
        item["tokens"] = tokens

    item["score"] = score_for_eval_ingest(
        summary if isinstance(summary, dict) else None,
        devshell_exit_code,
    )
    if result_oss_url and str(result_oss_url).strip():
        item["result_oss_url"] = str(result_oss_url).strip()[:2048]
    return item


def post_eval_ingest(
    url: str,
    body: dict[str, Any],
    *,
    timeout: float = 30.0,
) -> tuple[bool, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        return False, str(exc)

    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}: {resp.text[:500]}"

    try:
        data = resp.json()
    except Exception:
        return False, f"non-JSON response: {resp.text[:200]}"

    if data.get("code") != 0:
        return False, str(data.get("msg", data))
    return True, str(data.get("msg", "success"))
