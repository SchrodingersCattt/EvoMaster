"""DevConfig model and YAML loading for mm-devshell."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from matmaster.config.exp import ExpSkillsConfig
from matmaster.types.runtime import CompactionConfig

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _expand_env_vars(value: Any) -> Any:
    """Recursively expand ${VAR} patterns in strings."""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    return value


class AgentConfig(BaseModel):
    """Agent behavior settings."""

    model_config = ConfigDict(extra="forbid")

    name: str = "general"
    max_turns: int = 20
    identity: str | None = None
    system_prompt: str = ""


class SessionConfig(BaseModel):
    """Session type selection."""

    model_config = ConfigDict(extra="forbid")

    type: str = "local"


class ToolsConfig(BaseModel):
    """Tool registration settings."""

    model_config = ConfigDict(extra="forbid")

    builtin: list[str] = Field(default_factory=lambda: ["*"])


class DevConfig(BaseModel):
    """Top-level devshell configuration (agent / session / tools only)."""

    model_config = ConfigDict(extra="forbid")

    agent: AgentConfig = Field(default_factory=AgentConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    skills: ExpSkillsConfig = Field(default_factory=ExpSkillsConfig)
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)


def load_dev_config(path: Path) -> DevConfig:
    """Load DevConfig from a YAML file with env var expansion."""
    import yaml

    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Config file not found: {resolved}")

    with open(resolved) as f:
        raw = yaml.safe_load(f) or {}

    expanded = _expand_env_vars(raw)
    return DevConfig.model_validate(expanded)
