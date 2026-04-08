"""Shared path rules for DevShell optimization agent and orchestrator git add."""

from __future__ import annotations

from pathlib import Path


def is_under(path: Path, root: Path) -> bool:
    """True if ``path`` is ``root`` or a path inside ``root``."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def is_path_committable_for_optimization(repo_root: Path, rel: str) -> bool:
    """Paths safe to ``git add`` after an optimization sub-round.

    Matches optimization write policy plus exclusion of ``results/`` (session outputs).
    """
    rel = rel.replace("\\", "/").strip()
    if not rel or rel.startswith("../") or "/../" in rel:
        return False
    rr = repo_root.resolve()
    abs_path = (rr / rel).resolve()
    if not is_under(abs_path, rr):
        return False
    evaluation_root = (rr / "evaluation").resolve()
    if is_under(abs_path, evaluation_root):
        return False
    results_root = (rr / "results").resolve()
    if is_under(abs_path, results_root):
        return False
    git_meta = (rr / ".git").resolve()
    if is_under(abs_path, git_meta):
        return False
    return True
