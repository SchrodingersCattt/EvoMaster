"""Git HEAD 快照与按轮次回滚（DevShell agent 外层循环）。"""

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


def git_reset_hard(*, repo_root: Path, rev: str) -> tuple[bool, str]:
    """执行 ``git reset --hard <rev>``。返回 ``(ok, message)``。"""
    try:
        p = subprocess.run(
            ["git", "reset", "--hard", rev],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if p.returncode != 0:
            err = (p.stderr or p.stdout or "").strip()
            return False, err or f"exit {p.returncode}"
        return True, (p.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)


def append_iteration_head(*, session_dir: Path, iteration: int, head: str) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / "git_iteration_heads.jsonl"
    row = {"iteration": iteration, "head_at_start": head}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def head_at_iteration_start(session_dir: Path, iteration: int) -> str | None:
    """读取某轮开始时记录的 ``head_at_start``（取该 iteration 最后一条记录）。"""
    path = session_dir / "git_iteration_heads.jsonl"
    if not path.is_file():
        return None
    last: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if int(obj.get("iteration", -1)) == iteration:
            h = obj.get("head_at_start")
            if isinstance(h, str) and h:
                last = h
    return last
