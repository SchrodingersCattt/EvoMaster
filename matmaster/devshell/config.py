"""DevConfig model and YAML loading for mm-devshell.

LLM 连接由 ``matmaster_config/llm_config.yaml`` + ``build_provider`` 解析（与线上一致），
不在此文件中配置 api_key / base_url / model。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from matmaster.config.loader import _expand_env_vars


class AgentConfig(BaseModel):
    """Agent behavior settings."""

    name: str = "general"
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
    """Top-level devshell configuration (agent / session / tools only)."""

    model_config = ConfigDict(extra="ignore")

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
