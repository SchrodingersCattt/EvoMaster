"""Kernel runtime contracts: CompactionConfig and the kernel runtime trio.

Layer 2 boundary contracts:
- AgentKernelSpec: pure configuration + identity the kernel needs (no live
  resources). frozen.
- AgentKernelResources: the live runtime resources the kernel calls
  (provider, tool runner/catalog, hooks, compactor, runtime ports). frozen.
- AgentKernelRuntime: ``spec + resources``; the single object
  ``AgentKernel.run_stream(kernel_runtime, task)`` consumes. frozen.
- AgentRuntime: runtime bundle returned by ``Exp.build_runtime()``. Holds the
  kernel, the kernel_runtime, cleanup, and non-kernel context assembly
  lifecycle objects.

Context assembly internals (assembler, session event/job ports, user
instructions loader) are intentionally NOT exposed here -- they are owned by
``ContextCompactor`` / ``ContextAssemblyRuntime`` and reached only through
``AgentKernelResources.compactor``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from matmaster.context.sources.turn_input import TurnInput

from .llm_provider import LLMProvider
from .run_metadata import RunIdentity
from .runtime_ports import KernelRuntimePorts


class CompactionConfig(BaseModel):
    """Context compaction configuration."""

    model_config = ConfigDict(frozen=True)

    context_limit: int = 200_000
    trigger_ratio: float = 0.9
    strategy: str = "summary"  # 'sliding_window' | 'summary' | 'latest_half'
    reserved_summary_tokens: int = 8_000
    summary_safety_margin_tokens: int = 2_000
    auto_compact_buffer_tokens: int = 13_000

    @property
    def auto_threshold(self) -> int:
        return max(
            0,
            self.context_limit
            - self.reserved_summary_tokens
            - self.auto_compact_buffer_tokens,
        )


@dataclass(frozen=True)
class AgentKernelSpec:
    """Kernel-facing configuration + identity. No live resources.

    Built once by ``Exp.build_runtime()`` and read by ``AgentKernel`` for
    system prompt, turn budget, compaction config, run identity, and the
    per-turn ``turn_input`` (preflight compaction + image inputs).
    """

    system_prompt: str
    max_turns: int
    compaction: CompactionConfig
    run_identity: RunIdentity
    turn_input: TurnInput | None = None
    prompt_submit_rewrite_enabled: bool = True
    llm_model: str | None = None
    llm_model_profile: str | None = None
    llm_model_route: str | None = None


@dataclass(frozen=True)
class AgentKernelResources:
    """Kernel-facing live resources the kernel directly calls.

    Annotations are ``Any`` for the tool/hook/compactor objects to avoid
    circular imports across the runtime stack; ``Exp.build_runtime()`` is the
    sole constructor and guarantees real instances.
    """

    llm_provider: LLMProvider
    runtime_ports: KernelRuntimePorts
    tool_runner: Any
    tool_catalog: Any
    runtime_topology: Any
    hook_executor: Any | None = None
    compactor: Any | None = None
    capability_policy: Any | None = None
    structural_validation: Any | None = None


@dataclass(frozen=True)
class AgentKernelRuntime:
    """``spec + resources`` -- the object AgentKernel.run_stream consumes."""

    spec: AgentKernelSpec
    resources: AgentKernelResources


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

    Holds the kernel, the assembled kernel_runtime, cleanup, and non-kernel
    context assembly lifecycle objects needed by Exp.run_stream.
    frozen=True guarantees the bundle is not mutated after construction.
    """

    kernel: Any  # AgentKernel (avoid circular import)
    kernel_runtime: AgentKernelRuntime
    cleanup: Callable[[], Any]
    context_runtime: Any | None = None
