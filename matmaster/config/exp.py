"""Exp assembly configuration models.

Typed config for ``matmaster.core.exp.Exp``. Loaded from toml files
in ``matmaster/exps/``.

Usage::

    from matmaster.config.loader import load_exp_config
    cfg = load_exp_config("direct")
    exp = Exp(cfg)
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from matmaster.types.runtime import CompactionConfig

ExpContextMode = Literal["fresh", "inherit"]
ExpResultStyle = Literal["summary", "findings", "completion"]


class ExpToolsConfig(BaseModel):
    """Tool registration settings for Exp."""

    model_config = ConfigDict(extra="forbid")

    # Default "*" means "all tools". Explicit list of tool names also supported.
    builtin: list[str] = Field(default_factory=lambda: ["*"])
    mcp: str = "*"


class ExpSkillsConfig(BaseModel):
    """Skill registration and lazy MCP loading settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    skills_root: str | list[str] = ""
    # 非空：只注册这些 frontmatter ``name:``。空列表 / 未配置：不过滤，与线上一致（roots 下全量）。
    skill_names: list[str] = Field(default_factory=list)
    disabled_skill_names: list[str] = Field(default_factory=list)
    cache_dir: str = ""
    config_dir: str = ""
    mcp_config_file: str = ""
    mcp_runtime_file: str = "mcp.yaml"
    # Merged into loaded mcp runtime YAML (after read from config_dir). Used by
    # devshell to narrow per-server tools without duplicating mcp.yaml.
    mcp_runtime_patch: dict[str, Any] = Field(default_factory=dict)


class ExpSubagentMeta(BaseModel):
    """Model-visible subset of exp metadata used by AgentTool."""

    name: str
    description: str = ""
    when_to_use: str = ""
    read_only: bool = False
    visible_as_subagent: bool = True
    context_mode: ExpContextMode = "fresh"
    result_style: ExpResultStyle = "summary"
    tools_summary: str = ""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ExpConfig(BaseModel):
    """Exp assembly configuration.

    Loaded from ``matmaster/exps/{name}.toml``. Default values are fallbacks
    when fields are absent from the toml file.

    Unknown fields are rejected so stale exp TOML does not silently change
    runtime behavior.
    """

    name: str = "direct"
    description: str = ""
    when_to_use: str = ""
    read_only: bool = False
    visible_as_subagent: bool = True
    context_mode: ExpContextMode = "fresh"
    result_style: ExpResultStyle = "summary"
    max_turns: int = 100
    tools: ExpToolsConfig = Field(default_factory=ExpToolsConfig)
    skills: ExpSkillsConfig = Field(default_factory=ExpSkillsConfig)
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    system_prompt: str = ""
    developer_instructions: str = ""
    # subagent 专用：作为 subagent 被 spawn 时使用的 LLM profile_key。
    # None = 继承父 agent。作为 UI root mode 运行时不读此字段。
    llm: str | None = None

    model_config = ConfigDict(extra="forbid")


# UI-level mode whitelist. `POST /chat/.../stream` 的 ChatSendRequest.mode 必须
# 落在这个集合里；超出时 stream_service / worker 会归一化为 DEFAULT_MODE。
#
# 与 `visible_as_subagent`（由 AgentTool 决定哪个 exp 可作为 subagent enum 暴露
# 给 LLM）正交：explore.toml 是 subagent-only（不作为 UI mode），planner.toml 是
# UI-only（visible_as_subagent=false，不作为 subagent），direct.toml 同时出现
# 在两个集合里。
SUPPORTED_MODES: frozenset[str] = frozenset({"direct", "planner"})
DEFAULT_MODE: str = "direct"
