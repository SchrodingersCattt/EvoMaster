"""AgentRuntimeSpec, CompactionConfig, and AgentRuntime frozen models.

Layer 2 boundary contracts:
- AgentRuntimeSpec: Exp layer output built by Exp.assemble(ctx), consumed by
  AgentKernel.run_stream(spec, task). frozen=True guarantees immutability
  during kernel execution.
- AgentRuntime: runtime bundle returned by Exp.build_runtime(). Holds the
  kernel, assembled spec, and cleanup callable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from matmaster.core.hooks import HookExecutor

from .llm_provider import LLMProvider

if TYPE_CHECKING:
    pass


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
    传递给 AgentKernel.run_stream(spec, task)。
    frozen=True 保证 kernel 运行期间规格不变。
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    # LLM (Phase 2: typed as LLMProvider Protocol)
    # None is allowed during the assemble phase (ctx.llm_provider may be None);
    # build_runtime guarantees a real provider before kernel execution.
    llm_provider: LLMProvider | None = None

    # Termination (CONT-05: simplified to max_turns field)
    max_turns: int = 100

    # Hook executor
    hook_executor: HookExecutor | None = None

    # Context
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    system_prompt: str = ""
    compactor: Any | None = None

    # Extensible metadata bag (prompt templates, MCP/skill config, etc.)
    meta: dict[str, Any] = Field(default_factory=dict)

    # ── Tool Runtime v2 fields (Phase 32, all optional for backward compat) ──
    # Annotations are Any to avoid circular imports across the runtime stack.
    # TYPE_CHECKING block provides static typing; model_validator below
    # enforces runtime contracts.
    tool_runner: Any | None = None
    tool_catalog: Any | None = None
    runtime_topology: Any | None = None
    capability_policy: Any | None = None  # Phase 33 defines CapabilityPolicy Protocol
    structural_validation: Any | None = None  # Phase 33 defines StructuralValidation

    @model_validator(mode="after")
    def _check_v2_field_types(self) -> AgentRuntimeSpec:
        """Lazy-import runtime checks for v2 fields (avoids circular import)."""
        from matmaster.core.tool_runner import ToolRunner
        from matmaster.tools.tool_catalog import ToolCatalog
        from matmaster.types.topology import RuntimeTopology

        checks: list[tuple[str, Any, type]] = [
            ("tool_runner", self.tool_runner, ToolRunner),
            ("tool_catalog", self.tool_catalog, ToolCatalog),
            ("runtime_topology", self.runtime_topology, RuntimeTopology),
        ]
        for name, value, expected in checks:
            if value is not None and not isinstance(value, expected):
                msg = f"{name} must be {expected.__name__}, got {type(value).__name__}"
                raise ValueError(msg)
        return self


@dataclass(frozen=True)
class KernelResult:
    """AgentKernel 的终止结果摘要，由 run_stream 内部产生。

    内核层专属，不参与总线传输。总线事件 RunResultEvent
    在 run_stream() 中从 _TerminalItem 直接构造。

    num_turns 语义：已完成 LLM 调用的轮数。cancelled 路径在 turn 递增前退出，
    所以 num_turns 反映的是已完成的轮数，不含被中断的当前轮。
    """

    status: str
    reason: str
    final_content: str | None = None
    num_turns: int = 0
    stop_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentRuntime:
    """Runtime bundle returned by Exp.build_runtime().

    Holds the kernel, assembled spec, and cleanup callable.
    frozen=True guarantees the bundle is not mutated after construction.
    """

    kernel: Any  # AgentKernel (avoid circular import)
    spec: AgentRuntimeSpec
    cleanup: Callable[[], Any]
