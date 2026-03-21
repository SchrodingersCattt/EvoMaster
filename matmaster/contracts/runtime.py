"""AgentRuntimeSpec and CompactionConfig frozen models -- Layer 2 boundary contract.

Exp layer output: agent runtime specification built by Exp.assemble(ctx)
and consumed by AgentKernel.run(spec, task). frozen=True guarantees
immutability during kernel execution.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .guards import Guard


class CompactionConfig(BaseModel):
    """Context compaction 配置。"""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    context_window_tokens: int = 128_000
    trigger_ratio: float = 0.7
    strategy: str = "sliding_window"  # 'sliding_window' | 'summary' | 'latest_half'
    compaction_llm: str | None = None  # key in config.llm


class AgentRuntimeSpec(BaseModel):
    """Exp 层输出的 agent 运行时规格契约。

    由 Exp.assemble(ctx: PlaygroundContext) 构建，
    传递给 AgentKernel.run(spec, task)。
    frozen=True 保证 kernel 运行期间规格不变。
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    # LLM (Phase 2 defines LLMProvider Protocol)
    llm_provider: Any

    # Tools (Phase 3 defines ToolRegistry)
    tool_registry: Any

    # Guards
    guards: list[Guard] = Field(default_factory=list)

    # Termination (CONT-05: simplified to max_turns field)
    max_turns: int = 100

    # Hooks (Phase 2 defines Hook Protocol)
    hooks: list[Any] = Field(default_factory=list)

    # Context
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    system_prompt: str = ""

    # Mode
    mode: str = "direct"  # 'direct' | 'planner'
