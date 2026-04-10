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


def run_git_revert_commits_after_base(
    *, repo_root: Path, base_sha: str, timeout_sec: float = 300.0
) -> tuple[bool, str, list[str]]:
    """Revert every commit in ``base_sha..HEAD`` from **newest to oldest** using ``git revert``.

    Uses ``git revert --no-edit`` per commit; on failure (e.g. merge), retries once with
    ``-m 1``. Does **not** use ``git reset``.

    Returns:
        ``(ok, message, reverted_commit_shas)``.
    """
    base = base_sha.strip()
    if not base:
        return False, "empty base_sha", []

    verify = subprocess.run(
        ["git", "rev-parse", "--verify", f"{base}^{{commit}}"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if verify.returncode != 0:
        return (
            False,
            f"base_sha not a valid commit: {verify.stderr.strip() or verify.stdout}",
            [],
        )

    listed = subprocess.run(
        ["git", "rev-list", f"{base}..HEAD"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=int(timeout_sec),
    )
    if listed.returncode != 0:
        return False, listed.stderr.strip() or "rev-list failed", []

    shas = [ln.strip() for ln in listed.stdout.splitlines() if ln.strip()]
    if not shas:
        return True, "no commits after base (nothing to revert)", []

    reverted: list[str] = []
    for sha in shas:
        r = subprocess.run(
            ["git", "revert", "--no-edit", sha],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=int(timeout_sec),
        )
        if r.returncode == 0:
            reverted.append(sha)
            continue
        subprocess.run(
            ["git", "revert", "--abort"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        r2 = subprocess.run(
            ["git", "revert", "--no-edit", "-m", "1", sha],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=int(timeout_sec),
        )
        if r2.returncode != 0:
            subprocess.run(
                ["git", "revert", "--abort"],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            detail = (r2.stderr or r.stderr or "").strip()
            return (
                False,
                f"revert failed for {sha}: {detail}",
                reverted,
            )
        reverted.append(sha)

    return True, f"reverted {len(reverted)} commit(s)", reverted


def append_iteration_head(*, session_dir: Path, iteration: int, head: str) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / "git_iteration_heads.jsonl"
    row = {"iteration": iteration, "head_at_start": head}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
