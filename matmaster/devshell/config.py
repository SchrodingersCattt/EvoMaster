"""DevConfig model and YAML loading for mm-devshell."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from matmaster.config.loader import _expand_env_vars


class LLMConfig(BaseModel):
    """LLM connection settings."""

    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    temperature: float = 0.7
    max_tokens: int | None = None


class AgentConfig(BaseModel):
    """Agent behavior settings."""

    name: str = "general"
    mode: str = "direct"
    max_turns: int = 20
    identity: str | None = None
    system_prompt: str = ""


class SessionConfig(BaseModel):
    """Session type selection."""

    type: str = "local"


class ToolsConfig(BaseModel):
    """Tool registration settings."""

    builtin: list[str] = Field(default_factory=lambda: ["*"])


class DevConfig(BaseModel):
    """Top-level devshell configuration."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)


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
