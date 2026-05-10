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
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from matmaster.core.context_builder import ContextBuilder
from matmaster.core.hooks import HookExecutor

from .llm_provider import LLMProvider


class CompactionConfig(BaseModel):
    """Context compaction configuration."""

    model_config = ConfigDict(frozen=True)

    context_limit: int = 200_000
    trigger_ratio: float = 0.9
    strategy: str = "summary"  # 'sliding_window' | 'summary' | 'latest_half'
    compaction_llm: str | None = None  # key in config.llm
    reserved_summary_tokens: int = 20_000
    auto_compact_buffer_tokens: int = 13_000

    @property
    def auto_threshold(self) -> int:
        return max(
            0,
            self.context_limit
            - self.reserved_summary_tokens
            - self.auto_compact_buffer_tokens,
        )


class AgentRuntimeSpec(BaseModel):
    """Agent runtime spec contract emitted by the Exp layer.

    Built by Exp.assemble(ctx: PlaygroundContext) and passed to
    AgentKernel.run_stream(spec, task). frozen=True ensures the spec
    is immutable during kernel execution.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    # None is allowed during the assemble phase (ctx.llm_provider may be None);
    # build_runtime guarantees a real provider before kernel execution.
    llm_provider: LLMProvider | None = None

    max_turns: int = 100

    hook_executor: HookExecutor | None = None

    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    system_prompt: str = ""
    compactor: Any | None = None
    context_builder: ContextBuilder

    # Extensible metadata bag (prompt templates, MCP/skill config, etc.)
    meta: dict[str, Any] = Field(default_factory=dict)

    # Annotations are Any to avoid circular imports across the runtime stack.
    # The model_validator below enforces runtime type contracts.
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

        if not isinstance(self.context_builder, ContextBuilder):
            raise ValueError(
                "context_builder must be ContextBuilder, "
                f"got {type(self.context_builder).__name__}"
            )

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
    """Terminal result summary produced internally by AgentKernel.run_stream.

    Kernel-layer only; not transported over the event bus. The bus event
    ``RunResultEvent`` is constructed directly from ``_TerminalItem`` inside
    ``run_stream()``.

    ``num_turns`` counts completed LLM calls. The cancelled path exits before
    incrementing the turn counter, so it reflects completed turns only and
    does not include the interrupted current turn.

    ``usage`` accumulates scalar usage fields across LLM calls
    (prompt / completion / total / cache_read, etc.).
    ``usage_vendor_by_turn`` holds vendor-native usage snapshots in LLM-call
    order, one entry per turn (``{}`` when missing), aligned with
    ``num_turns`` completed turns.
    """

    status: str
    reason: str
    final_content: str | None = None
    num_turns: int = 0
    stop_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    usage_vendor_by_turn: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class AgentRuntime:
    """Runtime bundle returned by Exp.build_runtime().

    Holds the kernel, assembled spec, and cleanup callable.
    frozen=True guarantees the bundle is not mutated after construction.
    """

    kernel: Any  # AgentKernel (avoid circular import)
    spec: AgentRuntimeSpec
    cleanup: Callable[[], Any]
