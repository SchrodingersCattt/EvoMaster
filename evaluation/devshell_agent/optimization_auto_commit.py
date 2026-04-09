"""Orchestrator-side git add/commit after optimization sub-rounds."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from evaluation.devshell_agent.path_policy import is_path_committable_for_optimization


def _run(
    repo_root: Path,
    argv: list[str],
    *,
    timeout: float = 300.0,
) -> tuple[int, str, str]:
    p = subprocess.run(
        argv,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    return p.returncode, out, err


def git_dirty_paths(repo_root: Path) -> list[str]:
    """Union of unstaged, staged, and untracked paths (repo-relative)."""
    paths: set[str] = set()
    for cmd in (
        ["git", "diff", "--name-only"],
        ["git", "diff", "--cached", "--name-only"],
        ["git", "ls-files", "-o", "--exclude-standard"],
    ):
        rc, out, _ = _run(repo_root, cmd, timeout=60.0)
        if rc != 0:
            continue
        for line in out.splitlines():
            line = line.strip().replace("\\", "/")
            if line:
                paths.add(line)
    return sorted(paths)


def _staged_exp_names(repo_root: Path) -> list[str]:
    rc, out, _ = _run(
        repo_root, ["git", "diff", "--cached", "--name-only"], timeout=60.0
    )
    if rc != 0:
        return []
    names: list[str] = []
    for line in out.splitlines():
        line = line.strip().replace("\\", "/")
        if line.startswith("matmaster/exps/") and line.endswith(".toml"):
            stem = Path(line).stem
            if stem and stem not in names:
                names.append(stem)
    return names


def run_exp_prompt_budget_checks(
    repo_root: Path,
    *,
    exp_names: list[str],
    log: TextIO | None,
) -> tuple[bool, str]:
    """Run ``exp_prompt_budget`` for each exp; all must exit 0."""
    for exp in exp_names:
        argv = [
            "uv",
            "run",
            "python",
            "-m",
            "evaluation.devshell_agent.exp_prompt_budget",
            exp,
        ]
        rc, out, err = _run(repo_root, argv, timeout=600.0)
        tail = f"{out}\n{err}".strip()[-4000:]
        if rc != 0:
            msg = f"exp_prompt_budget failed for exp={exp!r} rc={rc}\n{tail}"
            if log:
                print(msg, file=log, flush=True)
            return False, msg
    return True, ""


def _commit_message_line(
    *, iteration_index: int, optimization_round: int, slug: str
) -> str:
    """First line must satisfy ``.git/hooks/commit-msg`` (feat|fix|docs|chore|...)."""
    safe = re.sub(r"[^\w\-.:/\s\u4e00-\u9fff]", "", slug).strip()
    if not safe:
        safe = "optimization"
    if len(safe) > 80:
        safe = safe[:77] + "..."
    return f"chore(devshell): iter={iteration_index} round={optimization_round} {safe}"


@dataclass(frozen=True)
class OptimizationCommitResult:
    ok: bool
    commit_sha: str | None
    message: str
    paths_committed: list[str]


def commit_optimization_changes(
    repo_root: Path,
    session_dir: Path,
    *,
    iteration_index: int,
    optimization_round: int,
    slug: str,
    skip_exp_budget: bool,
    log: TextIO | None,
) -> OptimizationCommitResult:
    """Stage committable product paths, optionally run exp budget, then ``git commit``."""
    repo_root = repo_root.resolve()
    dirty = git_dirty_paths(repo_root)
    allowed = [p for p in dirty if is_path_committable_for_optimization(repo_root, p)]
    if not allowed:
        msg = "optimization auto-commit: no committable dirty paths; skipping"
        if log:
            print(msg, file=log, flush=True)
        return OptimizationCommitResult(
            ok=True,
            commit_sha=None,
            message=msg,
            paths_committed=[],
        )

    rc, _, err = _run(
        repo_root,
        ["git", "add", "--"] + allowed,
        timeout=120.0,
    )
    if rc != 0:
        msg = f"git add failed rc={rc}: {err}"
        if log:
            print(msg, file=log, flush=True)
        return OptimizationCommitResult(False, None, msg, [])

    rc_names, names_out, _ = _run(
        repo_root, ["git", "diff", "--cached", "--name-only"], timeout=60.0
    )
    if rc_names != 0 or not (names_out or "").strip():
        msg = "optimization auto-commit: nothing staged (paths ignored or unchanged); skipping"
        if log:
            print(msg, file=log, flush=True)
        return OptimizationCommitResult(
            ok=True,
            commit_sha=None,
            message=msg,
            paths_committed=[],
        )

    if not skip_exp_budget:
        staged_exps = _staged_exp_names(repo_root)
        if staged_exps:
            ok, bmsg = run_exp_prompt_budget_checks(
                repo_root,
                exp_names=staged_exps,
                log=log,
            )
            if not ok:
                _run(repo_root, ["git", "reset", "HEAD", "--"], timeout=60.0)
                return OptimizationCommitResult(False, None, bmsg, [])

    first = _commit_message_line(
        iteration_index=iteration_index,
        optimization_round=optimization_round,
        slug=slug,
    )
    rc, _, err = _run(
        repo_root,
        ["git", "commit", "-m", first],
        timeout=300.0,
    )
    if rc != 0:
        msg = f"git commit failed rc={rc}: {err}"
        if log:
            print(msg, file=log, flush=True)
        return OptimizationCommitResult(False, None, msg, allowed)

    rc2, sha_out, _ = _run(repo_root, ["git", "rev-parse", "HEAD"], timeout=30.0)
    sha = sha_out.strip() if rc2 == 0 and sha_out.strip() else None
    msg = f"optimization auto-commit: ok sha={sha} paths={allowed!r}"
    if log:
        print(msg, file=log, flush=True)

    row = {
        "iteration_index": iteration_index,
        "optimization_round": optimization_round,
        "commit_sha": sha,
        "paths_committed": allowed,
        "message_first_line": first,
    }
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / "optimization_auto_commits.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    return OptimizationCommitResult(True, sha, msg, allowed)
