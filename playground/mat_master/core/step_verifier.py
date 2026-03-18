"""Minimal deterministic verifier for explicit step contracts only."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class StepContract:
    """Explicit contract for what a step should produce."""

    expected_artifacts: list[str] = field(default_factory=list)
    semantic_target: str = ''
    semantic_anti_targets: list[str] = field(default_factory=list)
    allow_partial: bool = True


def verify_step_deterministic(
    contract: StepContract,
    workspace_path: str | Path,
    produced_files: list[str] | None = None,
    *,
    journal_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run deterministic checks for explicit expected artifacts only.

    Args:
        contract: What the step was supposed to produce.
        workspace_path: Workspace root to look for files.
        produced_files: Optional list of file paths known to have been produced.
        journal_entries: Optional execution journal entries (tool results with saved_path).

    Returns:
        dict with keys: artifact_match (bool), produced_artifacts (list),
        missing_artifacts (list), completion_ratio (float 0..1), drift_reason (always "").
    """
    workspace = Path(workspace_path) if workspace_path else Path('.')
    if not workspace.is_absolute():
        workspace = workspace.resolve()

    produced: list[str] = list(produced_files or [])
    if journal_entries:
        for e in journal_entries:
            path = e.get('saved_path') or e.get('auto_saved_path')
            if path and path not in produced:
                produced.append(path)
            for f in e.get('downloaded_files') or []:
                if f not in produced:
                    produced.append(f)

    produced_basenames = {Path(p).name for p in produced}
    produced_paths = set(produced)
    if workspace.exists() and contract.expected_artifacts:
        for name in contract.expected_artifacts:
            base = Path(name).name
            if base in produced_basenames:
                continue
            cand = workspace / name
            if cand.exists() and cand.is_file():
                produced_paths.add(str(cand))
                produced_basenames.add(base)
                continue
            cand = workspace / base
            if cand.exists() and cand.is_file():
                produced_paths.add(str(cand))
                produced_basenames.add(base)

    missing: list[str] = []
    for name in contract.expected_artifacts:
        base = Path(name).name
        if base in produced_basenames:
            continue
        if any(name in p for p in produced_paths):
            continue
        if (workspace / name).exists() or (workspace / base).exists():
            continue
        missing.append(name)

    total = len(contract.expected_artifacts) or 1
    delivered = total - len(missing)
    completion_ratio = delivered / total if total else 1.0
    artifact_match = len(missing) == 0

    return {
        'artifact_match': artifact_match,
        'produced_artifacts': list(produced)[:50],
        'missing_artifacts': missing,
        'completion_ratio': completion_ratio,
        'drift_reason': '',
    }
