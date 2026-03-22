"""AgentRuntimeSpec, CompactionConfig, and AgentRuntime frozen models.

Layer 2 boundary contracts:
- AgentRuntimeSpec: Exp layer output built by Exp.assemble(ctx), consumed by
  AgentKernel.run(spec, task). frozen=True guarantees immutability during
  kernel execution.
- AgentRuntime: runtime bundle returned by Exp.build_runtime(). Holds the
  kernel, assembled spec, and cleanup callable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from matmaster.tools.tool_registry import ToolRegistry
from matmaster.core.hooks import Hook
from .llm_provider import LLMProvider

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

    # LLM (Phase 2: typed as LLMProvider Protocol)
    llm_provider: LLMProvider

    # Tools (Phase 3: typed as ToolRegistry)
    tool_registry: ToolRegistry | None = None

    # Guards
    guards: list[Guard] = Field(default_factory=list)

    # Termination (CONT-05: simplified to max_turns field)
    max_turns: int = 100

    # Hooks (Phase 2: typed as Hook Protocol)
    hooks: list[Hook] = Field(default_factory=list)

    # Context
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    system_prompt: str = ""

    # Mode
    mode: str = "direct"  # 'direct' | 'planner'


@dataclass(frozen=True)
class AgentRuntime:
    """Runtime bundle returned by Exp.build_runtime().

    Holds the kernel, assembled spec, and cleanup callable.
    frozen=True guarantees the bundle is not mutated after construction.
    """

    kernel: Any  # AgentKernel (avoid circular import)
    spec: AgentRuntimeSpec
    cleanup: Callable[[], None]
