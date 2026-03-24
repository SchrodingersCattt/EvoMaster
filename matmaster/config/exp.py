"""Exp assembly configuration models.

Typed config for ``matmaster.core.exp.Exp``. Models only the fields that
``Exp.assemble()`` actually reads, replacing the hardcoded dict in
``agent_run_service.py``.

Usage::

    cfg = ExpConfig.model_validate(agents_general_dict)
    exp = Exp(cfg.model_dump())
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExpToolsConfig(BaseModel):
    """Tool registration settings for Exp."""

    builtin: list[str] = Field(default_factory=lambda: ["*"])
    mcp: str = "*"


class ExpConfig(BaseModel):
    """Exp assembly configuration.

    Default values match what ``Exp.assemble()`` and ``agent_run_service.py``
    previously hardcoded. When loaded from YAML ``agents.general``, YAML
    values override these defaults.

    ``extra="ignore"`` allows loading from the full ``agents.general`` dict,
    discarding fields not consumed by Exp (context, compaction,
    system_prompt_file, etc.).
    """

    name: str = "direct"
    mode: str = "direct"
    max_turns: int = 100
    guards: list[str] = Field(default_factory=list)
    tools: ExpToolsConfig = Field(default_factory=ExpToolsConfig)
    skills: dict[str, Any] = Field(default_factory=dict)
    mcp: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")
