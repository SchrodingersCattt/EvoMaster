"""Shared path rules for DevShell optimization agent and orchestrator git add."""

from __future__ import annotations

from pathlib import Path

# ``matmaster/exps/``: never auto-commit; optimization agent must not edit in-loop.
# Human-reviewed proposals go next to the session (see ``PROPOSED_MATMASTER_EXPS_CHANGES_NAME``).
MATMASTER_EXPS_PREFIX = "matmaster/exps"

# Per-run snapshots live under ``evaluation/<segment>/<session_name>/``; ``index.jsonl`` is under ``<segment>/``.
DEVSHELL_AGENT_HISTORY_SEGMENT = "devshell_agent_history"

# Optimization agent writes proposals under the **results** session dir only (human review).
PROPOSED_MATMASTER_EXPS_CHANGES_NAME = "proposed_matmaster_exps_changes.md"
PROPOSED_OPTIMIZATION_CHANGES_NAME = "proposed_optimization_changes.md"

# Checklist agent writes this filename under the session dir only (human-reviewed YAML / core edits).
PROPOSED_QUESTION_BANK_CHANGES_NAME = "proposed_question_bank_changes.md"


def devshell_main_agent_history_root(repo_root: Path) -> Path:
    """Read-only root for the DevShell main agent: entire ``evaluation/devshell_agent_history/`` tree."""
    return (
        repo_root.resolve() / "evaluation" / DEVSHELL_AGENT_HISTORY_SEGMENT
    ).resolve()


def is_under(path: Path, root: Path) -> bool:
    """True if ``path`` is ``root`` or a path inside ``root``."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def is_blocked_matmaster_exps_path(repo_root: Path, path: Path) -> bool:
    """True if *path* is under ``matmaster/exps/`` (resolved under *repo_root*)."""
    rr = repo_root.resolve()
    try:
        key = str(path.resolve().relative_to(rr)).replace("\\", "/")
    except ValueError:
        return False
    return key == MATMASTER_EXPS_PREFIX or key.startswith(f"{MATMASTER_EXPS_PREFIX}/")


def is_path_committable_for_optimization(repo_root: Path, rel: str) -> bool:
    """Always False: optimization agent uses proposal-only mode (no auto-commit)."""
    return False
