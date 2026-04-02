"""DevConfig — local shell for mm-devshell (session, compaction).

Agent name / max_turns / tools here are only used when building :class:`~matmaster.config.exp.ExpConfig`
via :meth:`~matmaster.devshell.runner.DevRunner._build_exp_config` (tests and callers without
``exp_config``). Normal CLI always injects ``load_exp_config(...)`` from ``matmaster/exps/*.toml``.

LLM 连接由 ``matmaster_config/llm_config.yaml`` + ``build_provider`` 解析（与线上一致），
不在此模块配置。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from matmaster.types.runtime import CompactionConfig


class AgentConfig(BaseModel):
    """Agent behavior settings (fallback path only)."""

    name: str = "general"
    max_turns: int = 20
    identity: str | None = None
    system_prompt: str = ""


class SessionConfig(BaseModel):
    """Session type selection."""

    type: str = "local"


class ToolsConfig(BaseModel):
    """Tool registration settings (fallback path only)."""

    builtin: list[str] = Field(default_factory=lambda: ["*"])


class DevConfig(BaseModel):
    """Top-level devshell shell config (session, optional agent/tools for fallback Exp)."""

    model_config = ConfigDict(extra="ignore")

    agent: AgentConfig = Field(default_factory=AgentConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
