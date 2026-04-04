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
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from matmaster.core.hooks import Hook
from matmaster.tools.tool_registry import ToolRegistry
from matmaster.types.events import RunResultEvent
from matmaster.types.messages import Message

from .guards import Guard
from .llm_provider import LLMProvider


class CompactionConfig(BaseModel):
    """Context compaction 配置。"""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    context_window_tokens: int = 128_000
    trigger_ratio: float = 0.9
    strategy: str = "summary"  # 'sliding_window' | 'summary' | 'latest_half'
    compaction_llm: str | None = None  # key in config.llm


class AgentRuntimeSpec(BaseModel):
    """Exp 层输出的 agent 运行时规格契约。

    由 Exp.assemble(ctx: PlaygroundContext) 构建，
    传递给 AgentKernel.run(spec, task)。
    frozen=True 保证 kernel 运行期间规格不变。
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    # LLM (Phase 2: typed as LLMProvider Protocol)
    # None is allowed during the assemble phase (ctx.llm_provider may be None);
    # build_runtime guarantees a real provider before kernel execution.
    llm_provider: LLMProvider | None = None

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
    compactor: Any | None = None

    # Extensible metadata bag (prompt templates, MCP/skill config, etc.)
    meta: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class KernelResult:
    """AgentKernel.run() 的终止结果摘要。

    内核层专属，不参与总线传输。总线事件 RunResultEvent
    由上层（service / runner）从 KernelResult 按需构造。

    num_turns 语义：已完成 LLM 调用的轮数。cancelled 路径在 turn 递增前退出，
    所以 num_turns 反映的是已完成的轮数，不含被中断的当前轮。

    usage：各轮 LLM 调用的标量用量累加（prompt / completion / total / cache_read 等）。
    usage_vendor_by_turn：按 LLM 调用顺序，每轮一条供应商原生 usage（无则为 ``{}``），
    与 ``num_turns`` 已完成的轮次一一对应。
    """

    status: str
    reason: str
    final_content: str | None = None
    num_turns: int = 0
    stop_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    usage_vendor_by_turn: tuple[dict[str, Any], ...] = ()

    def to_run_result_event(self, source: str = "agent") -> RunResultEvent:
        """构造总线事件。上层发总线时统一走这个方法。"""
        return RunResultEvent(
            source=source,
            status=self.status,
            reason=self.reason,
            final_content=self.final_content,
        )


@dataclass(frozen=True)
class AgentRuntime:
    """Runtime bundle returned by Exp.build_runtime().

    Holds the kernel, assembled spec, and cleanup callable.
    frozen=True guarantees the bundle is not mutated after construction.
    """

    kernel: Any  # AgentKernel (avoid circular import)
    spec: AgentRuntimeSpec
    cleanup: Callable[[], Any]


@dataclass(frozen=True)
class KernelRunResult:
    """Return value of AgentKernel.run().

    Bundles the terminal result with the full message transcript,
    enabling callers to extract conversation history for multi-turn.
    """

    result: KernelResult
    messages: list[Message]
