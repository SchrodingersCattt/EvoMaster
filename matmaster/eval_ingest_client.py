"""Client for matmaster-tools-server evaluation ingest API.

See ``docs/apifox-evaluation-openapi.json`` in matmaster-tools-server (or repo docs)
for the contract: POST with ``run_id``, optional ``git_commit``, ``items`` (≥1).

Ingest POST URL is ``MATMASTER_TOOLS_SERVER`` + ``EVAL_INGEST_API_PATH``（在 **首次 import**
本模块时按 ``utils.env`` 解析；与配额等共用同一 host）。见 ``EVAL_INGEST_URL``。
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

import httpx

import utils.env

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
                "num_turns": summary.get("num_turns"),
                "model": summary.get("model"),
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
    if duration_ms is not None and duration_ms >= 0:
        item["duration_ms"] = int(duration_ms)
    if tokens is not None:
        item["tokens"] = tokens
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
