"""matmaster.config -- Typed configuration models for the MatMaster system.

Public API::

    from matmaster.config import (
        # Root config
        MatMasterRootConfig,
        # Section configs
        LLMConfig, LLMProfileConfig,
        AgentsConfig, AgentProfileConfig, CompactionConfig, ContextConfig,
        MatMasterDomainConfig, CRPConfig, ExecutionConfig, PlannerConfig,
        MCPConfig, ServerExecutorConfig, ExecutorConfig,
        SessionConfig, LocalSessionConfig, DockerSessionConfig,
        PlaygroundSectionConfig, ArchivalConfig,
        SkillsConfig,
        # Loaders
        load_llm_config, load_exp_config,
    )

Config file layout (configs/mat_master/)::

    config.yaml          -- main config (all sections)
    mcp_config.json      -- MCP server transport endpoints (prod)
    mcp_config.test.json -- MCP server transport endpoints (test)
    mcp_config.uat.json  -- MCP server transport endpoints (UAT)
"""

# Root
from .root import LoggingConfig, LLMOutputConfig, MatMasterRootConfig

# LLM
from .llm import LLMConfig, LLMProfileConfig

# Agent
from .agent import (
    AgentProfileConfig,
    AgentsConfig,
    CompactionConfig,
    ContextConfig,
    ToolsConfig,
)

# Mat Master domain
from .mat_master import (
    AskHumanConfig,
    CapabilitiesConfig,
    CRPConfig,
    ExecutionConfig,
    MatMasterDomainConfig,
    MonitorJobConfig,
    PlannerConfig,
    QualityGatesConfig,
    SkillEvolutionConfig,
    SubAgentConfig,
)

# MCP
from .mcp import (
    ExecutorConfig,
    ExecutorMachine,
    ExecutorResources,
    MCPConfig,
    RemoteProfile,
    ServerExecutorConfig,
)

# Session
from .session import (
    DockerSessionConfig,
    LocalSessionConfig,
    SessionConfig,
    SSHSessionConfig,
)

# Playground
from .playground import ArchivalConfig, PlaygroundSectionConfig

# Skills
from .skills import SkillsConfig

# Loaders
from .loader import load_exp_config, load_llm_config

__all__ = [
    # Root
    "MatMasterRootConfig",
    "LoggingConfig",
    "LLMOutputConfig",
    # LLM
    "LLMConfig",
    "LLMProfileConfig",
    # Agent
    "AgentsConfig",
    "AgentProfileConfig",
    "CompactionConfig",
    "ContextConfig",
    "ToolsConfig",
    # Mat Master domain
    "MatMasterDomainConfig",
    "CRPConfig",
    "ExecutionConfig",
    "PlannerConfig",
    "QualityGatesConfig",
    "SubAgentConfig",
    "CapabilitiesConfig",
    "AskHumanConfig",
    "MonitorJobConfig",
    "SkillEvolutionConfig",
    # MCP
    "MCPConfig",
    "ServerExecutorConfig",
    "ExecutorConfig",
    "ExecutorMachine",
    "ExecutorResources",
    "RemoteProfile",
    # Session
    "SessionConfig",
    "LocalSessionConfig",
    "DockerSessionConfig",
    "SSHSessionConfig",
    # Playground
    "PlaygroundSectionConfig",
    "ArchivalConfig",
    # Skills
    "SkillsConfig",
    # Loaders
    "load_llm_config",
    "load_exp_config",
]
