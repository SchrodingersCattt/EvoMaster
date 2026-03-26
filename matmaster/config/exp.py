"""Exp assembly configuration models.

Typed config for ``matmaster.core.exp.Exp``. Loaded from toml files
in ``matmaster/exps/``.

Usage::

    from matmaster.config.loader import load_exp_config
    cfg = load_exp_config("direct")
    exp = Exp(cfg)
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from matmaster.types.runtime import CompactionConfig


class ExpToolsConfig(BaseModel):
    """Tool registration settings for Exp."""

    # Default "*" means "all tools". Explicit list of tool names also supported.
    builtin: list[str] = Field(default_factory=lambda: ["*"])
    mcp: str = "*"


class ExpSkillsConfig(BaseModel):
    """Skill registration and lazy MCP loading settings."""

    enabled: bool = False
    skills_root: str | list[str] = ""
    cache_dir: str = ""
    config_dir: str = ""
    mcp_config_file: str = ""
    mcp_runtime_file: str = "mcp.yaml"


class ExpConfig(BaseModel):
    """Exp assembly configuration.

    Loaded from ``matmaster/exps/{name}.toml``. Default values are fallbacks
    when fields are absent from the toml file.

    ``extra="ignore"`` allows forward-compatible loading when toml files
    contain fields not yet modeled.
    """

    name: str = "direct"
    description: str = ""
    max_turns: int = 100
    guards: list[str] = Field(default_factory=list)
    tools: ExpToolsConfig = Field(default_factory=ExpToolsConfig)
    skills: ExpSkillsConfig = Field(default_factory=ExpSkillsConfig)
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    system_prompt: str = ""
    developer_instructions: str = ""

    model_config = ConfigDict(extra="ignore")
