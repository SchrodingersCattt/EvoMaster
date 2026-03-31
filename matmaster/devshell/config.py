"""DevConfig model and YAML loading for mm-devshell.

LLM 连接由 ``matmaster_config/llm_config.yaml`` + ``build_provider`` 解析（与线上一致），
不在此文件中配置 api_key / base_url / model。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

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


class LLMConfig(BaseModel):
    """LLM connection settings."""

    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    temperature: float = 0.7
    max_tokens: int | None = None
    timeout: float = 300.0
    stream_timeout: float | None = None
    stream_idle_timeout: float | None = None
    max_retries: int = 3
    retry_delay: float = 1.0



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
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)


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
