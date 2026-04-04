"""Shared dataclasses for DevShell agent loop (no ``claude-agent-sdk`` import)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DevshellAgentCliDefaults:
    """CLI defaults merged into ``run_devshell_eval`` when the model omits fields."""

    modes: list[str]
    jobs: int
    limit: int | None
    questions: list[str] | None
    capabilities: list[str] | None
    model: str | None
    exp: str | None
    eval_ingest_pending_only: bool
    no_export_review: bool
    task_timeout_sec: float
    eval_config: Path | None
    extra_args: list[str]


@dataclass
class AgentLoopSharedState:
    repo_root: Path
    session_dir: Path
    outcomes: list[dict[str, Any]]
    defaults: DevshellAgentCliDefaults
    last_eval_output_dir: Path | None = None
    #: Every ``run_devshell_eval`` output dir this outer iteration (ordered); used so
    #: we ``--submit`` each run before checklist may change the question bank, and so
    #: intermediate tags (e.g. iter_01) are not dropped when a later tag overwrites
    #: ``last_eval_output_dir``.
    eval_output_dirs: list[Path] = field(default_factory=list)
    checklist_escalations_pending: list[dict[str, Any]] = field(default_factory=list)
    checklist_revision_reports: list[dict[str, Any]] = field(default_factory=list)
