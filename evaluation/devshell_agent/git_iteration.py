"""Git HEAD 快照（DevShell agent 外层循环每轮开局记录）。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def git_rev_parse_head(*, repo_root: Path) -> str | None:
    """返回当前 ``HEAD`` 完整 SHA；非 git 仓库或失败时返回 ``None``。"""
    try:
        p = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if p.returncode != 0:
            return None
        sha = p.stdout.strip()
        return sha or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def append_iteration_head(*, session_dir: Path, iteration: int, head: str) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / "git_iteration_heads.jsonl"
    row = {"iteration": iteration, "head_at_start": head}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
