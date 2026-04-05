"""Minimal deterministic verifier for explicit step contracts only.

This module is kept as a narrow compatibility shim for tests and any remaining
callers that still import the old ``playground.mat_master.core.step_verifier``
path after the runtime moved out of the legacy playground package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class StepContract:
    """Explicit contract for what a step should produce."""

    expected_artifacts: list[str] = field(default_factory=list)
    semantic_target: str = ""
    semantic_anti_targets: list[str] = field(default_factory=list)
    allow_partial: bool = True


def verify_step_deterministic(
    contract: StepContract,
    workspace_path: str | Path,
    produced_files: list[str] | None = None,
    *,
    journal_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run deterministic checks for explicit expected artifacts only."""
    workspace = Path(workspace_path) if workspace_path else Path(".")
    if not workspace.is_absolute():
        workspace = workspace.resolve()

    produced: list[str] = list(produced_files or [])
    if journal_entries:
        for entry in journal_entries:
            path = entry.get("saved_path") or entry.get("auto_saved_path")
            if path and path not in produced:
                produced.append(path)
            for downloaded in entry.get("downloaded_files") or []:
                if downloaded not in produced:
                    produced.append(downloaded)

    produced_basenames = {Path(path).name for path in produced}
    produced_paths = set(produced)
    if workspace.exists() and contract.expected_artifacts:
        for expected in contract.expected_artifacts:
            base = Path(expected).name
            if base in produced_basenames:
                continue
            candidate = workspace / expected
            if candidate.exists() and candidate.is_file():
                produced_paths.add(str(candidate))
                produced_basenames.add(base)
                continue
            candidate = workspace / base
            if candidate.exists() and candidate.is_file():
                produced_paths.add(str(candidate))
                produced_basenames.add(base)

    missing: list[str] = []
    for expected in contract.expected_artifacts:
        base = Path(expected).name
        if base in produced_basenames:
            continue
        if any(expected in path for path in produced_paths):
            continue
        if (workspace / expected).exists() or (workspace / base).exists():
            continue
        missing.append(expected)

    total = len(contract.expected_artifacts) or 1
    delivered = total - len(missing)
    completion_ratio = delivered / total if total else 1.0
    artifact_match = len(missing) == 0

    return {
        "artifact_match": artifact_match,
        "produced_artifacts": list(produced)[:50],
        "missing_artifacts": missing,
        "completion_ratio": completion_ratio,
        "drift_reason": "",
    }
