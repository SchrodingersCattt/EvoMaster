"""Shared dataclasses for DevShell agent loop (no ``claude-agent-sdk`` import)."""

from __future__ import annotations

from dataclasses import dataclass
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
